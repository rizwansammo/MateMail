"""Build a deterministic, secret-free GHCR release digest manifest.

This tool only formats and validates build-push-action output. It never
contacts GHCR, executes Docker, changes deployment files, or deploys anything.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_SOURCE_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IMAGES = {
    "backend": "ghcr.io/rizwansammo/matemail-backend",
    "frontend": "ghcr.io/rizwansammo/matemail-frontend",
}


def build_manifest(source_sha: str, backend_digest: str, frontend_digest: str) -> dict:
    if not isinstance(source_sha, str) or not _SOURCE_SHA.fullmatch(source_sha):
        raise ValueError("Release must identify a full lowercase source commit SHA")
    for label, digest in (("backend", backend_digest), ("frontend", frontend_digest)):
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise ValueError(f"{label}: missing or invalid immutable image digest")
    return {
        "schema_version": 1,
        "source_sha": source_sha,
        "images": {
            name: f"{_IMAGES[name]}@{digest}"
            for name, digest in (("backend", backend_digest), ("frontend", frontend_digest))
        },
    }


def verify_manifest(manifest: dict) -> None:
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version", "source_sha", "images"
    } or manifest["schema_version"] != 1:
        raise ValueError("Unexpected manifest schema")
    images = manifest["images"]
    if not isinstance(images, dict) or set(images) != set(_IMAGES):
        raise ValueError("Unexpected images in manifest")
    digests = []
    for image, prefix in _IMAGES.items():
        reference = images[image]
        if not isinstance(reference, str) or not reference.startswith(prefix + "@"):
            raise ValueError(f"Wrong {image} registry reference")
        digests.append(reference[len(prefix) + 1:])
    if manifest != build_manifest(manifest["source_sha"], *digests):
        raise ValueError("Release digest manifest is not canonical")


def self_test() -> None:
    sha = "a" * 40
    backend = "sha256:" + "b" * 64
    frontend = "sha256:" + "c" * 64
    good = build_manifest(sha, backend, frontend)
    verify_manifest(good)
    assert good["images"]["backend"] == _IMAGES["backend"] + "@" + backend
    assert good["images"]["frontend"] == _IMAGES["frontend"] + "@" + frontend

    for candidate in ("main", "latest", "A" * 40, "a" * 39, "a" * 40 + "\n"):
        try:
            build_manifest(candidate, backend, frontend)
        except ValueError:
            pass
        else:
            raise AssertionError("Accepted a noncanonical source identifier")

    for bad in ("", "sha256:" + "0" * 63, "sha256:" + "G" * 64,
                "sha512:" + "0" * 64, "matemail-backend:main"):
        for image in ("backend", "frontend"):
            try:
                build_manifest(sha, bad if image == "backend" else backend,
                               bad if image == "frontend" else frontend)
            except ValueError:
                pass
            else:
                raise AssertionError("Accepted invalid digest for " + image)

    for mutated in (
        {**good, "source_sha": "main"},
        {**good, "images": {"backend": good["images"]["backend"]}},
        {**good, "images": {**good["images"], "frontend": "docker.io/x@" + frontend}},
        {**good, "schema_version": 2},
        {**good, "extra": "unexpected"},
    ):
        try:
            verify_manifest(mutated)
        except ValueError:
            pass
        else:
            raise AssertionError("Accepted invalid release manifest")

    print("PASS immutable digest manifest schema and negative-input checks")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest="command", required=True)
    command.add_parser("self-test")
    emit = command.add_parser("emit")
    emit.add_argument("--source-sha", required=True)
    emit.add_argument("--backend-digest", required=True)
    emit.add_argument("--frontend-digest", required=True)
    emit.add_argument("--output", required=True)
    verify = command.add_parser("verify")
    verify.add_argument("--input", required=True)
    args = parser.parse_args()

    if args.command == "self-test":
        self_test()
    elif args.command == "emit":
        manifest = build_manifest(
            args.source_sha, args.backend_digest, args.frontend_digest
        )
        verify_manifest(manifest)
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n",
                        encoding="utf-8")
        print("PASS immutable release manifest generated (no secrets)")
    else:
        verify_manifest(json.loads(Path(args.input).read_text(encoding="utf-8")))
        print("PASS release manifest verified")


if __name__ == "__main__":
    main()
