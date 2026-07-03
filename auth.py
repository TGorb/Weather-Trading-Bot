"""
RSA-PSS request signing for the Kalshi API.

Kalshi has no bearer-token auth — every authenticated request must carry
a signature over `timestamp + method + path`, signed with the private key
whose matching public key was uploaded to the Kalshi dashboard.
"""

import base64
import time

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def load_private_key(path: str):
    """Load an RSA private key (PEM, no password) from disk."""
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(
            f.read(), password=None, backend=default_backend()
        )


def build_auth_headers(method: str, path: str, key_id: str, private_key) -> dict:
    """
    Build Kalshi RSA-PSS authentication headers.

    method: uppercase HTTP method, e.g. "GET", "POST"
    path: full path including /trade-api/v2 prefix, WITHOUT query string
    """
    timestamp_ms = str(int(time.time() * 1000))
    message = (timestamp_ms + method.upper() + path).encode("utf-8")

    signature = private_key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
    sig_b64 = base64.b64encode(signature).decode("utf-8")

    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "Content-Type": "application/json",
    }
