"""Encrypted secret storage (AES-256-GCM) in the `secrets` table.

- 96-bit random nonce per encryption, never reused.
- AAD binds each ciphertext to its secret id and purpose, so ciphertexts can't be swapped
  between rows.
- Key ids allow rotation: new secrets use DMX_SECRETS_ACTIVE_KEY, old keys still decrypt.
- `SecretStore` is the seam for an OCI Vault implementation.
"""

from __future__ import annotations

import os
import uuid
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Secret, utcnow


class SecretStore(Protocol):
    def put(self, db: Session, plaintext: str, purpose: str) -> uuid.UUID: ...
    def get(self, db: Session, secret_id: uuid.UUID) -> str: ...
    def destroy(self, db: Session, secret_id: uuid.UUID) -> None: ...


class SecretNotFound(Exception):
    pass


def _aad(secret_id: uuid.UUID, purpose: str) -> bytes:
    return f"dmx-secret:v1:{secret_id}:{purpose}".encode()


class LocalEncryptedSecretStore:
    def put(self, db: Session, plaintext: str, purpose: str) -> uuid.UUID:
        s = get_settings()
        keys = s.secrets_key_map()
        kid = s.secrets_active_key
        sid = uuid.uuid4()
        nonce = os.urandom(12)
        ct = AESGCM(keys[kid]).encrypt(nonce, plaintext.encode(), _aad(sid, purpose))
        db.add(Secret(id=sid, key_id=kid, nonce=nonce, ciphertext=ct, purpose=purpose))
        db.flush()
        return sid

    def get(self, db: Session, secret_id: uuid.UUID) -> str:
        row = db.get(Secret, secret_id)
        if row is None or row.destroyed_at is not None:
            raise SecretNotFound(str(secret_id))
        keys = get_settings().secrets_key_map()
        key = keys.get(row.key_id)
        if key is None:
            raise SecretNotFound(f"key {row.key_id} unavailable")
        return AESGCM(key).decrypt(row.nonce, row.ciphertext, _aad(row.id, row.purpose)).decode()

    def destroy(self, db: Session, secret_id: uuid.UUID) -> None:
        row = db.get(Secret, secret_id)
        if row is not None and row.destroyed_at is None:
            row.ciphertext = b""
            row.nonce = b"\x00" * 12
            row.destroyed_at = utcnow()

    def reencrypt_all(self, db: Session) -> int:
        """Re-encrypt every live secret with the active key (key rotation)."""
        s = get_settings()
        n = 0
        for row in db.query(Secret).filter(Secret.destroyed_at.is_(None), Secret.key_id != s.secrets_active_key):
            pt = self.get(db, row.id)
            nonce = os.urandom(12)
            row.ciphertext = AESGCM(s.secrets_key_map()[s.secrets_active_key]).encrypt(
                nonce, pt.encode(), _aad(row.id, row.purpose)
            )
            row.nonce = nonce
            row.key_id = s.secrets_active_key
            n += 1
        return n


secret_store = LocalEncryptedSecretStore()
