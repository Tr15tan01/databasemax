from __future__ import annotations

import base64
import hashlib
import hmac
import os


def verifier(password: str, iterations: int = 4096) -> str:
    salt = os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored = hashlib.sha256(client_key).digest()
    server = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b = lambda x: base64.b64encode(x).decode()  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b(salt)}${b(stored)}:{b(server)}"


def admin_password(secret: str, instance_id: str) -> str:
    """Per-instance superuser password, derived — never stored anywhere."""
    return hmac.new(secret.encode(), f"dmx-admin:{instance_id}".encode(), hashlib.sha256).hexdigest()
