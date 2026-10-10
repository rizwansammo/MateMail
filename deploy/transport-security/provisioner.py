#!/usr/bin/env python3
"""P4-C.C: separate root-owned dynamic MTA-STS HTTPS worker.

Polls a purpose-scoped loopback API. Never changes DNS or mail delivery.
The existing Hub/PostBox custom-host edge is not shared as a virtual host.
"""
from __future__ import annotations
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

API = os.getenv("MATEMAIL_TRANSPORT_API", "http://127.0.0.1:8020/api/internal/transport-security").rstrip("/")
SECRET = os.getenv("TRANSPORT_SECURITY_PROVISIONER_SECRET", "")
SITES = Path(os.getenv("MATEMAIL_TRANSPORT_SITES", "/etc/nginx/sites-available"))
ENABLED = Path(os.getenv("MATEMAIL_TRANSPORT_ENABLED", "/etc/nginx/sites-enabled"))
POLICIES = Path(os.getenv("MATEMAIL_TRANSPORT_POLICIES", "/var/www/matemail/transport-sts"))
WEBROOT = Path(os.getenv("MATEMAIL_TRANSPORT_WEBROOT", "/var/www/html"))
PUBLIC_IP = os.getenv("MATEMAIL_TRANSPORT_EDGE_IPV4", "")
LOCK = Path(os.getenv("MATEMAIL_TRANSPORT_LOCK", "/run/lock/matemail-transport-sts.lock"))
RECEIPTS = Path(os.getenv("MATEMAIL_TRANSPORT_RETIRE_JOURNAL", "/var/lib/matemail/transport-sts-retirement"))
HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
MARKER = "# Managed by MateMail P4-C dynamic MTA-STS"


class EdgeError(Exception):
    pass


def hostname(name):
    if not isinstance(name, str):
        raise EdgeError("Invalid hostname")
    name = name.rstrip(".").lower()
    if not HOST_RE.fullmatch(name):
        raise EdgeError("Invalid hostname")
    return name


def job(data):
    try:
        result = {
            "id": str(uuid.UUID(data["id"])),
            "tenant_id": str(uuid.UUID(data["tenant_id"])),
            "domain": hostname(data["domain"]),
            "hostname": hostname(data["hostname"]),
            "edge": hostname(data["edge"]),
            "mx": hostname(data["mx"]),
            "policy_id": data["policy_id"],
        }
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise EdgeError("Invalid backend job") from exc
    if (result["hostname"] != "mta-sts." + result["domain"]
            or not isinstance(result["policy_id"], str)
            or not re.fullmatch(r"[a-f0-9]{24}", result["policy_id"])
            or result["hostname"] == result["edge"]):
        raise EdgeError("Invalid backend hostname or policy")
    return result


def run(*argv):
    try:
        proc = subprocess.run(argv, timeout=120, check=True, capture_output=True, text=True)
        return proc.stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise EdgeError("Host operation failed: " + Path(argv[0]).name) from exc


def api(method, suffix, payload=None):
    if not SECRET or not API.startswith("http://127.0.0.1:"):
        raise EdgeError("Private backend API credential or endpoint unavailable")
    req = urllib.request.Request(
        API + "/" + suffix.lstrip("/"),
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "X-MateMail-Transport-Secret": SECRET,
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=12) as response:
            data = json.loads(response.read(65536))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise EdgeError("Private backend API unavailable") from exc
    if not isinstance(data, dict):
        raise EdgeError("Invalid backend response")
    return data


def authorize(data):
    reply = api("POST", "authorize/", {"id": data["id"]})
    if reply.get("approved") is not True:
        raise EdgeError("Ownership or DNS is no longer authorized")
    for field in ("id", "tenant_id", "domain", "hostname", "policy_id", "mx", "edge"):
        if str(reply.get(field)) != data[field]:
            raise EdgeError("Authorization job mismatch")


def dns(kind, host):
    return run("/usr/bin/dig", "@1.1.1.1", "+short", "+tries=1", "+time=4", kind, host).splitlines()


def check_dns(data):
    try:
        ipaddress.IPv4Address(PUBLIC_IP)
    except ipaddress.AddressValueError as exc:
        raise EdgeError("Edge IP missing or invalid") from exc
    cname = [name.rstrip(".").lower() for name in dns("CNAME", data["hostname"]) if name.strip()]
    if cname != [data["edge"]]:
        raise EdgeError("CNAME must match approved gateway")
    mx_lines = [line.split() for line in dns("MX", data["domain"])]
    if any(len(line) != 2 for line in mx_lines):
        raise EdgeError("Unexpected MX response")
    mx = [line[1].rstrip(".").lower() for line in mx_lines]
    if not mx or any(name != data["mx"] for name in mx):
        raise EdgeError("MX does not match provider")
    ips = set(dns("A", data["edge"]))
    if ips != {PUBLIC_IP}:
        raise EdgeError("Gateway IPv4 does not match approved edge IP")


def site_file(host):
    return SITES / ("matemail-transport-sts-" + host + ".conf")


def enabled_file(host):
    return ENABLED / ("matemail-transport-sts-" + host + ".conf")


def collision_check(host):
    path, link = site_file(host), enabled_file(host)
    if path.exists() and not path.read_text().startswith(MARKER):
        raise EdgeError("Refusing to replace unmanaged site")
    if link.is_symlink() and link.resolve() != path.resolve():
        raise EdgeError("Refusing unexpected symlink")
    if link.exists() and not link.is_symlink():
        raise EdgeError("Refusing unmanaged enabled entry")
    for other in ENABLED.iterdir():
        if other == link:
            continue
        try:
            body = other.read_text()
        except (OSError, UnicodeError) as exc:
            raise EdgeError("Cannot inspect Nginx site") from exc
        if re.search(r"(?m)^\s*server_name\s+" + re.escape(host) + r"\s*;", body):
            raise EdgeError("Another Nginx site already owns this hostname")


def atomic(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".matemail-sts-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(contents)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def install_site(host, config):
    collision_check(host)
    path, link = site_file(host), enabled_file(host)
    old = path.read_bytes() if path.exists() else None
    had_link = link.is_symlink()
    try:
        atomic(path, config.encode())
        if not had_link:
            link.symlink_to(path)
        run("/usr/sbin/nginx", "-t")
        run("/usr/bin/systemctl", "reload", "nginx")
    except Exception:
        if old is None:
            path.unlink(missing_ok=True)
        else:
            atomic(path, old)
        if not had_link:
            link.unlink(missing_ok=True)
        run("/usr/sbin/nginx", "-t")
        run("/usr/bin/systemctl", "reload", "nginx")
        raise


def bootstrap(host):
    return f"""{MARKER}
server {{
 listen 80;
 server_name {host};
 location ^~ /.well-known/acme-challenge/ {{
  root {WEBROOT};
  allow all;
 }}
 location / {{ return 404; }}
}}
"""


def https_site(host):
    root = POLICIES / host
    cert = "/etc/letsencrypt/live/" + host
    return bootstrap(host) + f"""server {{
 listen 443 ssl;
 http2 on;
 server_name {host};
 ssl_certificate {cert}/fullchain.pem;
 ssl_certificate_key {cert}/privkey.pem;
 include /etc/letsencrypt/options-ssl-nginx.conf;
 ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
 default_type text/plain;
 add_header X-Content-Type-Options "nosniff" always;
 location = /.well-known/mta-sts.txt {{
  root {root};
  types {{ text/plain txt; }}
 }}
 location / {{ return 404; }}
}}
"""


def policy(mx):
    return ("version: STSv1\r\nmode: testing\r\nmx: " + mx +
            "\r\nmax_age: 86400\r\n").encode("ascii")


def cert_ok(host):
    pem = Path("/etc/letsencrypt/live") / host / "fullchain.pem"
    key = pem.parent / "privkey.pem"
    if not pem.is_file() or not key.is_file():
        return False
    try:
        match = run("/usr/bin/openssl", "x509", "-in", str(pem), "-noout", "-checkhost", host)
        if "does match certificate" not in match or "does NOT match" in match:
            return False
        run("/usr/bin/openssl", "x509", "-in", str(pem), "-noout", "-checkend", "604800")
    except EdgeError:
        return False
    return True


def expose_public_policy_path(host):
    """Let Nginx traverse public policy directories despite systemd UMask=0077.

    The report policy is deliberately public. Keep directories non-listable and
    non-writable to non-root processes; grant execute/traversal only (0711).
    Never relax permissions outside the dedicated policy subtree.
    """
    for directory in (POLICIES, POLICIES / host, POLICIES / host / ".well-known"):
        if directory.is_symlink() or not directory.is_dir():
            raise EdgeError("Unsafe MTA-STS policy directory")
        directory.chmod(0o711)



def policy_http_ok(data, expected=None):
    url = "https://" + data["hostname"] + "/.well-known/mta-sts.txt"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    for attempt in range(8):
        try:
            with opener.open(url, timeout=8) as response:
                if (response.status == 200
                        and response.headers.get_content_type() == "text/plain"
                        and response.read(8192) == (policy(data["mx"]) if expected is None else expected)):
                    return
        except (OSError, urllib.error.URLError, TimeoutError):
            pass
        time.sleep(2)
    raise EdgeError("HTTPS policy not accessible with valid certificate")


def state(data, lifecycle, certificate):
    api("POST", data["id"] + "/state/", {
        "lifecycle": lifecycle, "certificate_status": certificate,
        "policy_id": data["policy_id"],
    })


def provision(data):
    authorize(data)
    check_dns(data)
    host = data["hostname"]
    ready_cert = cert_ok(host)
    state(data, "provisioning", "active" if ready_cert else "not_requested")
    install_site(host, bootstrap(host))
    if not ready_cert:
        state(data, "provisioning", "issuing")
        run("/usr/bin/certbot", "certonly", "--non-interactive", "--webroot",
            "--webroot-path", str(WEBROOT), "--cert-name", host, "-d", host)
        if not cert_ok(host):
            raise EdgeError("Certificate issuance or hostname validation failed")
    destination = POLICIES / host / ".well-known" / "mta-sts.txt"
    atomic(destination, policy(data["mx"]))
    expose_public_policy_path(host)
    authorize(data)
    check_dns(data)
    install_site(host, https_site(host))
    policy_http_ok(data)
    authorize(data)
    state(data, "ready", "active")



# DNS-Phase 3: fully scoped deactivation. Never automatically remove an
# advertised MTA-STS policy, even when the customer clicks Disable.
# MTA-STS max_age is fixed to 86400; require 2x before host removal.


def policy_none():
    return b"version: STSv1\r\nmode: none\r\nmax_age: 86400\r\n"


def retire_authorize(data, action):
    response = api("POST", "retirement/authorize/", {
        "id": data["id"], "policy_id": data["policy_id"], "action": action,
    })
    if response.get("approved") is not True:
        raise EdgeError("Retirement is no longer approved")
    for field in ("id", "tenant_id", "domain", "hostname", "policy_id", "mx", "edge"):
        if str(response.get(field)) != data[field]:
            raise EdgeError("Retirement identity changed")
    return response


def retire_state(data, action, absent=None):
    body = {"action": action, "policy_id": data["policy_id"]}
    if action == "dns-check":
        if type(absent) is not bool:
            raise EdgeError("Invalid DNS observation")
        body["absent"] = absent
    return api("POST", "retirement/" + data["id"] + "/state/", body)


def txt_absent_at(resolver, name):
    # Confirm an authoritative status in the resolver answer, not an empty
    # stdout caused by a SERVFAIL, timeout or transport error.
    answer = run("/usr/bin/dig", "@" + resolver, "+nocmd", "+noall",
                 "+comments", "+answer", "+tries=1", "+time=4", "TXT", name)
    matched = re.search(r"status:\s*(NOERROR|NXDOMAIN|SERVFAIL|REFUSED)", answer)
    if not matched or matched.group(1) not in ("NOERROR", "NXDOMAIN"):
        raise EdgeError("Public DNS result unavailable")
    for line in answer.splitlines():
        if re.search(r"\sIN\sTXT\s", line, re.IGNORECASE):
            return False
        if re.search(r"\sIN\sCNAME\s", line, re.IGNORECASE):
            raise EdgeError("Unexpected TXT alias; manual review required")
    return True


def public_txt_absent(data):
    # Both independent recursive resolvers must see both TXT records absent.
    for resolver in ("1.1.1.1", "8.8.8.8"):
        for name in ("_mta-sts." + data["domain"], "_smtp._tls." + data["domain"]):
            if not txt_absent_at(resolver, name):
                return False
    return True


def managed_site(host):
    path, link = site_file(host), enabled_file(host)
    if path.is_symlink() or not path.is_file() or not path.read_text().startswith(MARKER):
        raise EdgeError("Retirement refuses missing or unmanaged Nginx source")
    if not link.is_symlink() or link.resolve() != path.resolve():
        raise EdgeError("Retirement refuses unexpected Nginx symlink")
    collision_check(host)


def retire_to_none(data):
    retire_authorize(data, "serve-none")
    host = data["hostname"]
    path, link = site_file(host), enabled_file(host)
    # A worker may have failed before writing its first Nginx site.
    # Such a request can retire only when no policy certificate or public
    # security TXT exists, and no colliding unmanaged host owns the name.
    if not path.exists() and not link.is_symlink():
        collision_check(host)
        if (Path("/etc/letsencrypt/renewal").joinpath(host + ".conf").exists()
                or Path("/etc/letsencrypt/live").joinpath(host).exists()
                or (POLICIES / host).exists()
                or not public_txt_absent(data)):
            raise EdgeError("Cannot retire unprovisioned policy without DNS and edge proof")
        retire_authorize(data, "serve-none")
        retire_state(data, "mode-none")
        return
    managed_site(host)
    if cert_ok(host):
        root = POLICIES / host / ".well-known"
        if root.is_symlink() or not root.is_dir():
            raise EdgeError("Policy directory unsafe")
        target = root / "mta-sts.txt"
        if target.is_symlink():
            raise EdgeError("Policy file unsafe")
        atomic(target, policy_none())
        expose_public_policy_path(host)
        # Keep HTTPS available for cached remote MTAs, including if the old
        # provisioner had stopped after issuing the cert but before HTTPS.
        install_site(host, https_site(host))
        policy_http_ok(data, expected=policy_none())
    elif not public_txt_absent(data):
        # A partially installed vhost with no valid TLS cert cannot claim
        # a mode:none policy. Any remaining MTA-STS TXT must be handled first.
        raise EdgeError("HTTPS policy unavailable while public security TXT exists")
    retire_authorize(data, "serve-none")
    retire_state(data, "mode-none")


def receipt_path(host):
    return RECEIPTS / (host + ".json")


def retirement_receipt(data):
    if RECEIPTS.is_symlink():
        raise EdgeError("Unsafe retirement journal directory")
    RECEIPTS.mkdir(parents=True, exist_ok=True, mode=0o700)
    RECEIPTS.chmod(0o700)
    path = receipt_path(data["hostname"])
    if path.is_symlink():
        raise EdgeError("Unsafe retirement journal")
    identity = {key: data[key] for key in ("id", "tenant_id", "domain", "hostname", "policy_id")}
    if path.exists():
        try:
            actual = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise EdgeError("Invalid retirement journal") from exc
        if actual != identity:
            raise EdgeError("Retirement journal ownership mismatch")
    else:
        host = data["hostname"]
        site, enabled = site_file(host), enabled_file(host)
        if not site.exists() and not enabled.is_symlink():
            # Explicitly unprovisioned request. No certificate, host or policy
            # may survive, otherwise require operator review, never guessing.
            collision_check(host)
            if ((POLICIES / host).exists()
                    or (Path("/etc/letsencrypt/live") / host).exists()
                    or (Path("/etc/letsencrypt/renewal") / (host + ".conf")).exists()):
                raise EdgeError("Unprovisioned site still has edge resources")
        else:
            managed_site(host)
        atomic(path, json.dumps(identity, sort_keys=True).encode())
        path.chmod(0o600)
    return path


def drop_managed_nginx(data):
    host = data["hostname"]
    receipt = retirement_receipt(data)
    path, link = site_file(host), enabled_file(host)
    if path.exists() or link.is_symlink():
        managed_site(host)
        if link.is_symlink():
            link.unlink()
        try:
            run("/usr/sbin/nginx", "-t")
            run("/usr/bin/systemctl", "reload", "nginx")
        except EdgeError:
            if not link.exists() and not link.is_symlink():
                link.symlink_to(path)
            run("/usr/sbin/nginx", "-t")
            run("/usr/bin/systemctl", "reload", "nginx")
            raise
        path.unlink()
    return receipt


def delete_dedicated_certificate(host):
    # Certificate removal is restricted to the matching single-host lineage.
    renewal = Path("/etc/letsencrypt/renewal") / (host + ".conf")
    live = Path("/etc/letsencrypt/live") / host
    if not renewal.exists() and not live.exists():
        return
    output = run("/usr/bin/certbot", "certificates", "--cert-name", host)
    if not re.search(r"(?m)^\s*Certificate Name:\s*" + re.escape(host) + r"\s*$", output):
        raise EdgeError("Certificate lineage does not match host")
    match = re.search(r"(?m)^\s*Domains:\s*(.*?)\s*$", output)
    if not match or match.group(1).strip() != host:
        raise EdgeError("Refusing to remove shared or mismatched certificate")
    # Nginx must no longer reference this lineage anywhere.
    if "/etc/letsencrypt/live/" + host + "/" in run("/usr/sbin/nginx", "-T"):
        raise EdgeError("Another active Nginx site still references certificate")
    run("/usr/bin/certbot", "delete", "--cert-name", host, "--non-interactive")
    if renewal.exists() or live.exists():
        raise EdgeError("Certificate cleanup incomplete")


def remove_managed_policy(host):
    path = POLICIES / host
    well_known = path / ".well-known"
    target = well_known / "mta-sts.txt"
    if path.is_symlink() or well_known.is_symlink() or target.is_symlink():
        raise EdgeError("Unsafe policy cleanup path")
    if target.is_file():
        data = target.read_bytes()
        if data not in (policy_none(),):
            raise EdgeError("Policy is not in safe retirement mode")
        target.unlink()
    if well_known.exists():
        well_known.rmdir()
    if path.exists():
        path.rmdir()


def retire_cleanup(data):
    # Both API and resolver checks happen immediately before the first
    # destructive operation, not only when a job was queued 5 minutes earlier.
    retire_authorize(data, "cleanup")
    if not public_txt_absent(data):
        retire_state(data, "dns-check", absent=False)
        raise EdgeError("DNS record returned during retirement; cache timer reset")
    retire_authorize(data, "cleanup")
    receipt = drop_managed_nginx(data)
    delete_dedicated_certificate(data["hostname"])
    remove_managed_policy(data["hostname"])
    retire_state(data, "complete")
    receipt.unlink(missing_ok=True)


def run_retirement_jobs():
    pending = api("GET", "retirement/pending/").get("results")
    if not isinstance(pending, list) or len(pending) > 25:
        raise EdgeError("Invalid retirement job list")
    failures = 0
    for item in pending:
        try:
            data = job(item)
            if item.get("lifecycle") == "deactivating":
                retire_to_none(data)
            elif item.get("lifecycle") == "draining":
                retire_authorize(data, "check-dns")
                absent = public_txt_absent(data)
                retire_state(data, "dns-check", absent=absent)
                if absent and item.get("cleanup_ready") is True:
                    retire_cleanup(data)
            else:
                raise EdgeError("Invalid retirement lifecycle")
        except Exception as exc:
            failures += 1
            print("MTA-STS retirement paused:", type(exc).__name__, flush=True)
    return failures


def main():
    if os.geteuid() != 0 or not SECRET or not PUBLIC_IP:
        return 2
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as file:
        os.chmod(LOCK, 0o600)
        try:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        jobs = api("GET", "pending/").get("results")
        if not isinstance(jobs, list) or len(jobs) > 25:
            raise EdgeError("Unexpected pending job list")
        failures = 0
        for raw in jobs:
            data = None
            try:
                data = job(raw)
                provision(data)
                print("MTA-STS HTTPS READY:", data["hostname"], flush=True)
            except Exception as exc:
                failures += 1
                print("MTA-STS provisioning failed:", type(exc).__name__, flush=True)
                if data is not None:
                    try:
                        state(data, "error", "error")
                    except Exception:
                        print("State update rejected", flush=True)
        failures += run_retirement_jobs()
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
