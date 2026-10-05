#!/usr/bin/env python3
"""
Root-owned MateMail custom-host HTTPS provisioner.

The application owns hostname -> tenant -> surface state. This process owns only
host nginx/Certbot mutation. It polls the loopback-only backend API, rechecks
authorization immediately before touching the edge, installs an exact-host
bootstrap vhost, obtains a Let's Encrypt certificate through the existing
webroot, installs a TLS-ready staging vhost, then reports READY.

Phase 3 intentionally does NOT proxy customer traffic to MateMail. The staging
vhost returns 503 until Phase 4 makes application routing/authentication safe.

No customer value is ever executed as shell. subprocess is always argv-based.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

API_BASE = os.environ.get(
    "MATEMAIL_CUSTOM_HOST_API",
    "http://127.0.0.1:8020/api/internal/custom-hostnames",
).rstrip("/")
SECRET = os.environ.get("CUSTOM_HOST_PROVISIONER_SECRET", "")
SITES_AVAILABLE = Path(
    os.environ.get("MATEMAIL_CUSTOM_HOST_SITES_AVAILABLE", "/etc/nginx/sites-available")
)
SITES_ENABLED = Path(
    os.environ.get("MATEMAIL_CUSTOM_HOST_SITES_ENABLED", "/etc/nginx/sites-enabled")
)
WEBROOT = Path(os.environ.get("MATEMAIL_CUSTOM_HOST_ACME_WEBROOT", "/var/www/html"))
CERTBOT = os.environ.get("MATEMAIL_CUSTOM_HOST_CERTBOT", "/usr/bin/certbot")
NGINX = os.environ.get("MATEMAIL_CUSTOM_HOST_NGINX", "/usr/sbin/nginx")
SYSTEMCTL = os.environ.get("MATEMAIL_CUSTOM_HOST_SYSTEMCTL", "/usr/bin/systemctl")
OPENSSL = os.environ.get("MATEMAIL_CUSTOM_HOST_OPENSSL", "/usr/bin/openssl")
LOCK_PATH = Path(
    os.environ.get(
        "MATEMAIL_CUSTOM_HOST_LOCK",
        "/run/lock/matemail-custom-host-provisioner.lock",
    )
)
HTTP_TIMEOUT = float(os.environ.get("MATEMAIL_CUSTOM_HOST_HTTP_TIMEOUT", "10"))
GENERATED_MARKER = "# MATEMAIL CUSTOM HOST v1"
HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


class ProvisioningError(RuntimeError):
    pass


def log(message: str) -> None:
    print(message, flush=True)


def validate_hostname(value: object) -> str:
    if not isinstance(value, str):
        raise ProvisioningError("backend returned a non-string hostname")
    hostname = value.strip().rstrip(".").lower()
    try:
        hostname.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ProvisioningError("backend returned a non-ASCII hostname") from exc
    if not HOST_RE.fullmatch(hostname):
        raise ProvisioningError("backend returned an invalid hostname")
    if hostname == "matemail.online" or hostname.endswith(".matemail.online"):
        raise ProvisioningError("refusing a MateMail-owned hostname")
    return hostname


def validate_job(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ProvisioningError("backend returned an invalid job")
    try:
        job_id = str(uuid.UUID(str(raw["id"])))
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise ProvisioningError("backend returned an invalid job id") from exc
    hostname = validate_hostname(raw.get("hostname"))
    surface = raw.get("surface")
    if surface not in {"hub", "postbox"}:
        raise ProvisioningError("backend returned an invalid surface")
    tenant_id = str(raw.get("tenant_id", ""))
    try:
        tenant_id = str(uuid.UUID(tenant_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProvisioningError("backend returned an invalid tenant id") from exc
    return {
        "id": job_id,
        "hostname": hostname,
        "surface": surface,
        "tenant_id": tenant_id,
    }


def api_json(method: str, path: str, payload: dict | None = None) -> dict:
    if not SECRET:
        raise ProvisioningError("CUSTOM_HOST_PROVISIONER_SECRET is not configured")
    body = None
    headers = {
        "Accept": "application/json",
        "X-MateMail-Custom-Host-Secret": SECRET,
    }
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        f"{API_BASE}/{path.lstrip('/')}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            data = response.read(1_000_000)
            if response.status < 200 or response.status >= 300:
                raise ProvisioningError(f"backend returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        # Never echo the response body: a future backend error could contain
        # internal detail. The status code is enough for the operator.
        raise ProvisioningError(f"backend returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProvisioningError(f"backend request failed: {type(exc).__name__}") from exc

    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvisioningError("backend returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ProvisioningError("backend returned an invalid JSON object")
    return value


def authorize(job: dict[str, str]) -> None:
    answer = api_json("POST", "authorize/", {"hostname": job["hostname"]})
    if answer.get("approved") is not True:
        raise ProvisioningError("backend did not authorize this hostname")
    for key in ("id", "hostname", "surface", "tenant_id"):
        if str(answer.get(key, "")) != job[key]:
            raise ProvisioningError(f"backend authorization mismatch for {key}")


def post_state(
    job_id: str,
    provisioning_status: str,
    *,
    certificate_status: str | None = None,
    last_error: str = "",
) -> None:
    payload: dict[str, str] = {"provisioning_status": provisioning_status}
    if certificate_status is not None:
        payload["certificate_status"] = certificate_status
    if last_error:
        payload["last_error"] = last_error[:1000]
    api_json("POST", f"{job_id}/state/", payload)


def run(argv: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip().splitlines()
        suffix = f": {detail[-1][:300]}" if detail else ""
        raise ProvisioningError(f"{Path(argv[0]).name} failed{suffix}") from exc
    except OSError as exc:
        raise ProvisioningError(f"could not execute {Path(argv[0]).name}") from exc


def site_path(hostname: str) -> Path:
    return SITES_AVAILABLE / f"matemail-custom-{hostname}.conf"


def enabled_path(hostname: str) -> Path:
    return SITES_ENABLED / f"matemail-custom-{hostname}.conf"


def _assert_generated_or_absent(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        old = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProvisioningError("could not read existing nginx site") from exc
    if not old.startswith(GENERATED_MARKER + "\n"):
        raise ProvisioningError(
            f"refusing to overwrite non-MateMail nginx file {path.name}"
        )
    return old


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _ensure_symlink(link: Path, target: Path) -> bool:
    """Return True when this call created the enabled symlink."""
    if link.is_symlink():
        try:
            current = Path(os.readlink(link))
        except OSError as exc:
            raise ProvisioningError("could not inspect nginx enabled symlink") from exc
        if not current.is_absolute():
            current = (link.parent / current).resolve()
        if current != target.resolve():
            raise ProvisioningError(
                f"refusing to replace unexpected nginx symlink {link.name}"
            )
        return False
    if link.exists():
        raise ProvisioningError(
            f"refusing to replace non-symlink nginx entry {link.name}"
        )
    link.symlink_to(target)
    return True


def install_site(hostname: str, content: str) -> None:
    """
    Atomically install one generated vhost and reload only after nginx -t.

    If validation fails, restore the previous generated file/symlink state so a
    later unrelated nginx reload is not poisoned by a broken candidate.
    """
    path = site_path(hostname)
    link = enabled_path(hostname)
    previous = _assert_generated_or_absent(path)
    link_created = False
    try:
        _atomic_write(path, content)
        link_created = _ensure_symlink(link, path)
        run([NGINX, "-t"])
    except Exception:
        if previous is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        else:
            _atomic_write(path, previous)
        if link_created:
            try:
                link.unlink()
            except FileNotFoundError:
                pass
        raise

    run([SYSTEMCTL, "reload", "nginx"], capture=False)


def bootstrap_vhost(hostname: str) -> str:
    return f"""{GENERATED_MARKER}
# Phase 3 bootstrap: ACME only. No MateMail application traffic is served.
server {{
    listen 80;
    listen [::]:80;
    server_name {hostname};

    location /.well-known/acme-challenge/ {{
        root {WEBROOT};
        allow all;
    }}

    location / {{
        default_type text/plain;
        return 503 "MateMail custom domain is being provisioned.\\n";
    }}
}}
"""


def ready_vhost(hostname: str, surface: str) -> str:
    # Surface is recorded for Phase 4 to replace this staging vhost with the
    # correct proxy shape. It is already validated and only appears in a comment.
    return f"""{GENERATED_MARKER}
# Phase 3 TLS-ready staging vhost. surface={surface}
# Phase 4 replaces the 503 with the correct Hub/PostBox upstream routing.
server {{
    listen 80;
    listen [::]:80;
    server_name {hostname};

    location /.well-known/acme-challenge/ {{
        root {WEBROOT};
        allow all;
    }}

    location / {{
        return 301 https://$host$request_uri;
    }}
}}

server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name {hostname};

    ssl_certificate     /etc/letsencrypt/live/{hostname}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{hostname}/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header X-Robots-Tag "noindex, nofollow" always;
    add_header Retry-After "60" always;

    location / {{
        default_type text/plain;
        return 503 "MateMail custom domain is ready for application activation.\\n";
    }}
}}
"""


def active_vhost(hostname: str, surface: str) -> str:
    """
    Final Phase 4 proxy vhost.

    The browser-facing hostname remains unchanged. nginx supplies two private
    routing markers to the shared Next.js process; the frontend trusts them
    only for hosts that are not one of its configured canonical hosts. Django
    never uses these headers for tenant authorization — it resolves the ACTIVE
    hostname from the database.
    """
    common_http = f"""{GENERATED_MARKER}
# ACTIVE MateMail custom hostname. surface={surface}
server {{
    listen 80;
    listen [::]:80;
    server_name {hostname};

    location /.well-known/acme-challenge/ {{
        root {WEBROOT};
        allow all;
    }}

    location / {{
        return 301 https://$host$request_uri;
    }}
}}

server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name {hostname};

    ssl_certificate     /etc/letsencrypt/live/{hostname}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{hostname}/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    add_header X-Robots-Tag "noindex, nofollow" always;

"""

    if surface == "hub":
        body = """    client_max_body_size 25m;

    location ^~ /api/internal/ { return 404; }
    location ^~ /api/platform/ { return 404; }
    location ^~ /api/postbox/ { return 404; }
    location ^~ /django-admin/ { return 404; }

    # Customer Hub custom hosts must not become alternate addresses for other
    # frontend surfaces either.
    location ^~ /platform { return 404; }
    location ^~ /admin { return 404; }
    location ^~ /postbox { return 404; }

    location /api/ {
        add_header Strict-Transport-Security "max-age=31536000" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
        add_header Content-Security-Policy "default-src 'none'; frame-ancestors 'none'; base-uri 'none'" always;

        proxy_pass             http://matemail_backend;
        # Django's canonical-host HSTS includes includeSubDomains. Never let a
        # customer custom hostname inherit policy for names we do not control.
        proxy_hide_header      Strict-Transport-Security;
        proxy_set_header       Host                     $host;
        proxy_set_header       X-Real-IP                $remote_addr;
        proxy_set_header       X-Forwarded-For          $proxy_add_x_forwarded_for;
        proxy_set_header       X-Forwarded-Proto        https;
        proxy_set_header       X-MateMail-Custom-Host   1;
        proxy_set_header       X-MateMail-Surface       hub;
        proxy_read_timeout     120s;
        proxy_http_version     1.1;
        proxy_set_header       Connection               "";
    }

    location / {
        proxy_pass             http://matemail_frontend;
        proxy_hide_header      Strict-Transport-Security;
        proxy_set_header       Host                     $host;
        proxy_set_header       X-Real-IP                $remote_addr;
        proxy_set_header       X-Forwarded-For          $proxy_add_x_forwarded_for;
        proxy_set_header       X-Forwarded-Proto        https;
        proxy_set_header       X-MateMail-Custom-Host   1;
        proxy_set_header       X-MateMail-Surface       hub;
        proxy_http_version     1.1;
        proxy_set_header       Upgrade                  $http_upgrade;
        proxy_set_header       Connection               "upgrade";
        proxy_read_timeout     60s;
    }
}
"""
        return common_http + body

    if surface == "postbox":
        body = """    client_max_body_size 40m;

    location ^~ /api/internal/ { return 404; }
    location ^~ /api/platform/ { return 404; }
    location ^~ /django-admin/ { return 404; }

    # PostBox custom hosts expose only the mailbox API. Workspace authentication
    # and tenant administration do not exist on this hostname.
    location ^~ /api/postbox/ {
        add_header Strict-Transport-Security "max-age=31536000" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
        add_header Content-Security-Policy "default-src 'none'; frame-ancestors 'none'; base-uri 'none'" always;

        proxy_pass             http://matemail_backend;
        # Django's canonical-host HSTS includes includeSubDomains. Never let a
        # customer custom hostname inherit policy for names we do not control.
        proxy_hide_header      Strict-Transport-Security;
        proxy_set_header       Host                     $host;
        proxy_set_header       X-Real-IP                $remote_addr;
        proxy_set_header       X-Forwarded-For          $proxy_add_x_forwarded_for;
        proxy_set_header       X-Forwarded-Proto        https;
        proxy_set_header       X-MateMail-Custom-Host   1;
        proxy_set_header       X-MateMail-Surface       postbox;
        proxy_read_timeout     180s;
        proxy_send_timeout     180s;
        proxy_http_version     1.1;
        proxy_set_header       Connection               "";
        proxy_buffering        off;
    }

    location ^~ /api/ { return 404; }

    location / {
        proxy_pass             http://matemail_frontend;
        proxy_hide_header      Strict-Transport-Security;
        proxy_set_header       Host                     $host;
        proxy_set_header       X-Real-IP                $remote_addr;
        proxy_set_header       X-Forwarded-For          $proxy_add_x_forwarded_for;
        proxy_set_header       X-Forwarded-Proto        https;
        proxy_set_header       X-MateMail-Custom-Host   1;
        proxy_set_header       X-MateMail-Surface       postbox;
        proxy_read_timeout     120s;
        proxy_http_version     1.1;
        proxy_set_header       Connection               "";
    }
}
"""
        return common_http + body

    raise ProvisioningError("refusing to generate nginx for an unknown surface")


def activate(job: dict[str, str]) -> None:
    """
    Install final Phase 4 routing and only then make the DB mapping ACTIVE.

    The order is deliberate. A crash after the nginx write but before the state
    update leaves Django rejecting the hostname, which fails closed. The READY
    activation queue retries idempotently on the next timer tick.
    """
    authorize(job)
    verify_certificate(job["hostname"])
    install_site(
        job["hostname"],
        active_vhost(job["hostname"], job["surface"]),
    )
    authorize(job)
    post_state(job["id"], "active", certificate_status="active")
    log(f"custom-host: ACTIVE {job['hostname']} ({job['surface']})")


def issue_certificate(hostname: str) -> None:
    run(
        [
            CERTBOT,
            "certonly",
            "--webroot",
            "--webroot-path",
            str(WEBROOT),
            "--domain",
            hostname,
            "--cert-name",
            hostname,
            "--non-interactive",
            "--agree-tos",
            "--keep-until-expiring",
        ],
        capture=False,
    )


def verify_certificate(hostname: str) -> None:
    cert = Path("/etc/letsencrypt/live") / hostname / "fullchain.pem"
    key = Path("/etc/letsencrypt/live") / hostname / "privkey.pem"
    if not cert.is_file() or not key.is_file():
        raise ProvisioningError("Certbot completed but certificate files are missing")
    run([OPENSSL, "x509", "-in", str(cert), "-noout", "-checkhost", hostname])
    run([OPENSSL, "x509", "-in", str(cert), "-noout", "-checkend", "86400"])


def provision(job: dict[str, str]) -> None:
    hostname = job["hostname"]
    log(f"custom-host: provisioning {hostname} ({job['surface']})")

    # Authorization is deliberately checked immediately before host mutation,
    # not only when the pending list was fetched.
    authorize(job)
    post_state(
        job["id"],
        "provisioning",
        certificate_status="issuing",
    )

    install_site(hostname, bootstrap_vhost(hostname))
    issue_certificate(hostname)
    verify_certificate(hostname)
    install_site(hostname, ready_vhost(hostname, job["surface"]))

    # Re-authorize after the slow external operation too. If the organization
    # was suspended or the mapping changed while ACME ran, do not advertise
    # READY to Phase 4.
    authorize(job)
    post_state(job["id"], "ready", certificate_status="active")
    log(f"custom-host: READY {hostname}")


def safe_error(job: dict[str, str], exc: Exception) -> None:
    message = str(exc)
    # Persist only a bounded, customer-safe summary. Command output may contain
    # paths/internal detail, so customers get a generic message while journald
    # has the operator-facing reason.
    log(f"custom-host: ERROR {job['hostname']}: {message}")
    try:
        post_state(
            job["id"],
            "error",
            certificate_status="error",
            last_error=(
                "HTTPS provisioning could not be completed. "
                "Verify the CNAME and try again."
            ),
        )
    except Exception as report_exc:
        log(
            "custom-host: WARNING could not report error state: "
            f"{type(report_exc).__name__}"
        )


def _acquire_lock():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_PATH, "a+", encoding="utf-8")
    os.chmod(LOCK_PATH, 0o600)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return None
        raise
    return handle


def main() -> int:
    if os.geteuid() != 0:
        log("custom-host: must run as root")
        return 2
    if not SECRET:
        log("custom-host: CUSTOM_HOST_PROVISIONER_SECRET is not configured")
        return 2

    lock = _acquire_lock()
    if lock is None:
        log("custom-host: another provisioner instance is already running")
        return 0

    try:
        try:
            payload = api_json("GET", "pending/")
        except ProvisioningError as exc:
            log(f"custom-host: pending query failed: {exc}")
            return 1

        rows = payload.get("results", [])
        if not isinstance(rows, list):
            log("custom-host: pending query returned an invalid result list")
            return 1

        failures = 0
        for raw in rows:
            job = None
            try:
                job = validate_job(raw)
                provision(job)
            except Exception as exc:
                failures += 1
                if job is None:
                    log(f"custom-host: invalid job ignored: {exc}")
                    continue
                safe_error(job, exc)

        # Activation is a separate durable queue. That makes READY a real
        # crash boundary: if the process dies after ACME, the next timer tick
        # installs the final route without reissuing the certificate.
        try:
            activation_payload = api_json("GET", "activation-pending/")
            activation_rows = activation_payload.get("results", [])
            if not isinstance(activation_rows, list):
                raise ProvisioningError(
                    "activation query returned an invalid result list"
                )
        except ProvisioningError as exc:
            log(f"custom-host: activation query failed: {exc}")
            failures += 1
            activation_rows = []

        for raw in activation_rows:
            job = None
            try:
                job = validate_job(raw)
                activate(job)
            except Exception as exc:
                failures += 1
                if job is None:
                    log(f"custom-host: invalid activation job ignored: {exc}")
                else:
                    # Leave the row READY. No CA request is repeated and the
                    # staging vhost remains safe; this queue may retry next tick.
                    log(
                        f"custom-host: ACTIVATION ERROR {job['hostname']}: {exc}"
                    )

        if failures:
            log(f"custom-host: completed with {failures} failed job(s)")
            return 1
        if rows or activation_rows:
            log(
                "custom-host: completed "
                f"{len(rows)} provisioning and {len(activation_rows)} activation job(s)"
            )
        return 0
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
