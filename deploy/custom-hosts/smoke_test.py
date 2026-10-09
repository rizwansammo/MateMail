#!/usr/bin/env python3
"""
Non-destructive production smoke test for MateMail custom Hub/PostBox hostnames.

Run after the feature has been deployed and the chosen pilot hostnames are
ACTIVE. It never authenticates, mutates application state, changes DNS, or
writes nginx/Certbot files.

Example:
    python3 deploy/custom-hosts/smoke_test.py \
        --hub-host manage.customer.com \
        --postbox-host mail.customer.com
"""
from __future__ import annotations

import argparse
import http.client
import shutil
import socket
import ssl
import subprocess
import sys
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass
class CheckResult:
    ok: bool
    label: str
    detail: str = ""


def result(ok: bool, label: str, detail: str = "") -> CheckResult:
    return CheckResult(ok=ok, label=label, detail=detail)


def cname_of(hostname: str) -> str:
    dig = shutil.which("dig")
    if not dig:
        raise RuntimeError("dig is required for the DNS smoke test")
    proc = subprocess.run(
        [dig, "+short", "CNAME", hostname],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        raise RuntimeError("dig failed")
    answers = [
        line.strip().rstrip(".").lower()
        for line in proc.stdout.splitlines()
        if line.strip()
    ]
    return answers[0] if answers else ""


def https_request(hostname: str, path: str) -> tuple[int, dict[str, str]]:
    context = ssl.create_default_context()
    connection = http.client.HTTPSConnection(
        hostname,
        443,
        timeout=12,
        context=context,
    )
    try:
        connection.request(
            "GET",
            path,
            headers={
                "User-Agent": "MateMail-Custom-Host-Smoke/1.0",
                "Accept": "text/html,application/json",
            },
        )
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        response.read(4096)
        return response.status, headers
    finally:
        connection.close()


def tls_check(hostname: str) -> None:
    context = ssl.create_default_context()
    with socket.create_connection((hostname, 443), timeout=12) as raw:
        with context.wrap_socket(raw, server_hostname=hostname):
            # create_default_context + server_hostname performs CA and hostname
            # validation. A successful handshake is the check.
            pass


def check_host(
    hostname: str,
    *,
    surface: str,
    cname_target: str,
    skip_dns: bool,
) -> list[CheckResult]:
    checks: list[CheckResult] = []

    if not skip_dns:
        try:
            cname = cname_of(hostname)
            checks.append(
                result(
                    cname == cname_target,
                    f"{hostname}: CNAME",
                    cname or "no CNAME answer",
                )
            )
        except Exception as exc:
            checks.append(result(False, f"{hostname}: CNAME", type(exc).__name__))

    try:
        tls_check(hostname)
        checks.append(result(True, f"{hostname}: trusted HTTPS certificate"))
    except Exception as exc:
        checks.append(
            result(False, f"{hostname}: trusted HTTPS certificate", str(exc)[:180])
        )
        return checks

    try:
        status, headers = https_request(hostname, "/")
        location = headers.get("location", "")
        # Both a canonical MateMail redirect and a redirect to any unrelated
        # host violate custom-host browser URL preservation. Relative redirects
        # and same-host absolute redirects remain valid.
        parsed_redirect = urlsplit(location)
        redirect_leaked = bool(
            parsed_redirect.netloc
            and (parsed_redirect.hostname or "").rstrip(".").lower()
            != hostname.rstrip(".").lower()
        )
        checks.append(
            result(
                status < 500,
                f"{hostname}: root responds",
                f"HTTP {status}",
            )
        )
        checks.append(
            result(
                not redirect_leaked,
                f"{hostname}: browser URL is preserved",
                location or "no external Location header",
            )
        )

        hsts = headers.get("strict-transport-security", "")
        checks.append(
            result(
                bool(hsts) and "includesubdomains" not in hsts.lower(),
                f"{hostname}: customer-safe HSTS",
                hsts or "missing",
            )
        )
    except Exception as exc:
        checks.append(result(False, f"{hostname}: root request", str(exc)[:180]))
        return checks

    if surface == "hub":
        blocked = (
            "/api/internal/health/",
            "/api/platform/tenants/",
            "/api/postbox/auth/me/",
            "/signup",
        )
    else:
        blocked = (
            "/api/internal/health/",
            "/api/platform/tenants/",
            "/api/workspaces/",
        )

    for path in blocked:
        try:
            status, _ = https_request(hostname, path)
            checks.append(
                result(
                    status == 404,
                    f"{hostname}: blocks {path}",
                    f"HTTP {status}",
                )
            )
        except Exception as exc:
            checks.append(
                result(False, f"{hostname}: blocks {path}", str(exc)[:180])
            )

    return checks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub-host", required=True)
    parser.add_argument("--postbox-host", required=True)
    parser.add_argument(
        "--cname-target",
        default="custom.matemail.pro",
    )
    parser.add_argument(
        "--skip-dns",
        action="store_true",
        help="Skip direct-CNAME assertions (use only for a documented legacy pilot).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = args.cname_target.strip().rstrip(".").lower()
    all_checks = [
        *check_host(
            args.hub_host.strip().rstrip(".").lower(),
            surface="hub",
            cname_target=target,
            skip_dns=args.skip_dns,
        ),
        *check_host(
            args.postbox_host.strip().rstrip(".").lower(),
            surface="postbox",
            cname_target=target,
            skip_dns=args.skip_dns,
        ),
    ]

    failures = 0
    for check in all_checks:
        marker = "PASS" if check.ok else "FAIL"
        suffix = f" — {check.detail}" if check.detail else ""
        print(f"[{marker}] {check.label}{suffix}")
        if not check.ok:
            failures += 1

    print()
    if failures:
        print(f"Custom-host smoke test FAILED: {failures} check(s) failed.")
        return 1

    print("Custom-host smoke test PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
