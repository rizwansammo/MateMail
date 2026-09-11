"""
DEPRECATED (DEC-007r stage 1) — nothing calls this any more.

MateMail no longer generates DKIM keypairs: the Mail Engine does, and it
keeps the private half. This module is retained only so stage 2 has a
reference for what the legacy rows contain, and is removed in stage 3
together with Domain.dkim_private_key. Do not call it from new code.
"""
import base64

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate_dkim_keypair() -> tuple[bytes, str]:
    """
    Generate a 2048-bit RSA DKIM key pair.
    Returns (private_key_pem_bytes, public_key_dns_value).
    - private_key_pem: PEM bytes stored in Domain.dkim_private_key for Phase 6 provisioning
    - public_key_dns_value: base64-encoded DER public key for the DNS TXT record
    """
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, base64.b64encode(public_der).decode()
