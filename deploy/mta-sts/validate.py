#!/usr/bin/env python3
"""P4-C: validate RFC 8461 test-mode policy (works from a file or stdin)."""
import pathlib
import sys

ALLOWED_DOMAINS = {"mail.matemail.pro", "netamate.com", "matedesk.pro"}
EXPECTED_MX = "mx.matemail.pro"


def validate(text: str, domain: str) -> None:
    assert domain in ALLOWED_DOMAINS, "unapproved receiving domain"
    assert "\x00" not in text, "binary policy"
    rows = [line.strip() for line in text.replace("\r\n", "\n").split("\n") if line.strip()]
    assert len(rows) == 4, f"invalid number of policy fields: {len(rows)}"
    parsed = {}
    for row in rows:
        assert ": " in row, "RFC 8461 policy uses a colon and space delimiter"
        name, value = row.split(": ", 1)
        assert name not in parsed, f"duplicate policy field: {name}"
        parsed[name] = value
    assert parsed == {
        "version": "STSv1",
        "mode": "testing",
        "mx": EXPECTED_MX,
        "max_age": "86400",
    }, f"unexpected MTA-STS policy for {domain}: {parsed}"


def main() -> int:
    assert len(sys.argv) == 3, "usage: validate.py <policy.txt|-> <receiver-domain>"
    source = sys.argv[1]
    content = sys.stdin.read() if source == "-" else pathlib.Path(source).read_text()
    validate(content, sys.argv[2])
    print(f"MTA-STS POLICY VALID: {sys.argv[2]} testing / {EXPECTED_MX}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, ValueError) as error:
        print(f"MTA-STS policy validation FAILED: {error}", file=sys.stderr)
        raise SystemExit(1)
