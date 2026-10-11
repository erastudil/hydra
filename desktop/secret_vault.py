"""
Hydra Desktop Secure Secret Vault Subsystem.
Implements PBKDF2-derived key generation, AES-256-GCM authenticated encryption,
secure in-memory token lifecycle, and runtime credential injection into agent execution contexts.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

PBKDF2_ITERATIONS = 100_000
SALT_SIZE_BYTES = 16
NONCE_SIZE_BYTES = 12
KEY_SIZE_BYTES = 32


def derive_vault_key(passphrase: str, salt: bytes) -> bytes:
    """Derive 256-bit AES key from passphrase using PBKDF2HMAC-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE_BYTES,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_payload(plaintext: str, key: bytes, salt: bytes) -> Dict[str, str]:
    """
    Encrypt plaintext string with AES-GCM.
    Returns dictionary with base64-encoded ciphertext, salt, and nonce.
    """
    nonce = os.urandom(NONCE_SIZE_BYTES)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return {
        "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "salt_b64": base64.b64encode(salt).decode("ascii"),
    }


def decrypt_payload(payload: Dict[str, str], key: bytes) -> str:
    """Decrypt AES-GCM payload back to plaintext string."""
    ciphertext = base64.b64decode(payload["ciphertext_b64"])
    nonce = base64.b64decode(payload["nonce_b64"])
    aesgcm = AESGCM(key)
    decrypted = aesgcm.decrypt(nonce, ciphertext, None)
    return decrypted.decode("utf-8")


@dataclass
class EncryptedSecretRecord:
    """Persisted encrypted secret entry."""
    key: str
    ciphertext_b64: str
    nonce_b64: str
    salt_b64: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "ciphertext_b64": self.ciphertext_b64,
            "nonce_b64": self.nonce_b64,
            "salt_b64": self.salt_b64,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EncryptedSecretRecord:
        return cls(
            key=data["key"],
            ciphertext_b64=data["ciphertext_b64"],
            nonce_b64=data["nonce_b64"],
            salt_b64=data["salt_b64"],
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            metadata=data.get("metadata", {}),
        )


class SecretVault:
    """
    Secure Secret Vault providing authenticated AES-256-GCM encryption,
    PBKDF2 key derivation, lock/unlock state machine, and runtime credential injection.
    """

    def __init__(
        self,
        master_passphrase: Optional[str] = None,
        storage_path: Optional[str] = None,
    ) -> None:
        self.storage_path = storage_path
        self._lock = threading.RLock()
        self._records: Dict[str, EncryptedSecretRecord] = {}
        self._passphrase: Optional[str] = None
        self._derived_key: Optional[bytes] = None
        self._vault_salt: bytes = os.urandom(SALT_SIZE_BYTES)

        if master_passphrase:
            self.unlock(master_passphrase)
        if storage_path and os.path.isfile(storage_path):
            self.load_from_disk(storage_path)

    @property
    def is_unlocked(self) -> bool:
        with self._lock:
            return self._derived_key is not None

    @property
    def total_secrets(self) -> int:
        with self._lock:
            return len(self._records)

    def unlock(self, passphrase: str) -> bool:
        """Derive master encryption key and transition vault to unlocked state."""
        if not passphrase:
            raise ValueError("Master passphrase cannot be empty")
        with self._lock:
            self._passphrase = passphrase
            self._derived_key = derive_vault_key(passphrase, self._vault_salt)
            return True

    def lock(self) -> None:
        """Purge in-memory derived key and lock the vault."""
        with self._lock:
            self._passphrase = None
            self._derived_key = None

    def _require_unlocked(self) -> bytes:
        if self._derived_key is None:
            raise PermissionError("Vault is locked. Call unlock(passphrase) first.")
        return self._derived_key

    def set_secret(
        self,
        key: str,
        value: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Encrypt and store sensitive token or API secret."""
        k = key.strip()
        if not k:
            raise ValueError("Secret key cannot be empty")
        with self._lock:
            enc_key = self._require_unlocked()
            salt = os.urandom(SALT_SIZE_BYTES)
            # Re-derive per-record key for maximal cryptographic isolation
            record_key = derive_vault_key(self._passphrase, salt)
            enc = encrypt_payload(value, record_key, salt)

            now = time.time()
            rec = EncryptedSecretRecord(
                key=k,
                ciphertext_b64=enc["ciphertext_b64"],
                nonce_b64=enc["nonce_b64"],
                salt_b64=enc["salt_b64"],
                created_at=self._records[k].created_at if k in self._records else now,
                updated_at=now,
                metadata=metadata or {},
            )
            self._records[k] = rec

    def get_secret(self, key: str) -> Optional[str]:
        """Decrypt and return secret by key."""
        k = key.strip()
        with self._lock:
            self._require_unlocked()
            if k not in self._records:
                return None
            rec = self._records[k]
            salt = base64.b64decode(rec.salt_b64)
            record_key = derive_vault_key(self._passphrase, salt)
            return decrypt_payload(rec.to_dict(), record_key)

    def has_secret(self, key: str) -> bool:
        """Check if secret exists without decrypting."""
        with self._lock:
            return key.strip() in self._records

    def delete_secret(self, key: str) -> bool:
        """Remove secret from vault."""
        with self._lock:
            return self._records.pop(key.strip(), None) is not None

    def list_keys(self) -> List[str]:
        """Return list of secret names in the vault."""
        with self._lock:
            return sorted(self._records.keys())

    def clear(self) -> None:
        """Purge all records."""
        with self._lock:
            self._records.clear()

    def inject_into_environ(
        self,
        key_mapping: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        """
        Decrypt and inject vault secrets into os.environ.
        key_mapping allows mapping vault_key -> env_var_name (e.g. {'anthropic_key': 'ANTHROPIC_API_KEY'}).
        Returns list of injected environment variables.
        """
        with self._lock:
            self._require_unlocked()
            injected = []
            mapping = key_mapping or {k: k for k in self._records.keys()}

            for vkey, env_var in mapping.items():
                secret_val = self.get_secret(vkey)
                if secret_val is not None:
                    os.environ[env_var] = secret_val
                    injected.append(env_var)
            return injected

    def extract_from_environ(self, env_keys: List[str]) -> int:
        """Extract secrets from current os.environ into vault."""
        with self._lock:
            self._require_unlocked()
            count = 0
            for ek in env_keys:
                val = os.environ.get(ek)
                if val:
                    self.set_secret(ek, val)
                    count += 1
            return count

    def change_passphrase(self, new_passphrase: str) -> None:
        """Re-encrypt all stored secrets with new master passphrase."""
        if not new_passphrase:
            raise ValueError("New master passphrase cannot be empty")
        with self._lock:
            self._require_unlocked()
            # Decrypt all current values
            decrypted_map: Dict[str, Tuple[str, Dict[str, Any]]] = {}
            for k, rec in self._records.items():
                salt = base64.b64decode(rec.salt_b64)
                record_key = derive_vault_key(self._passphrase, salt)
                val = decrypt_payload(rec.to_dict(), record_key)
                decrypted_map[k] = (val, rec.metadata)

            # Re-derive with new passphrase
            self._passphrase = new_passphrase
            self._vault_salt = os.urandom(SALT_SIZE_BYTES)
            self._derived_key = derive_vault_key(new_passphrase, self._vault_salt)

            # Re-encrypt
            self._records.clear()
            for k, (val, meta) in decrypted_map.items():
                self.set_secret(k, val, metadata=meta)

    def export_vault_json(self) -> str:
        """Export encrypted records to JSON string."""
        with self._lock:
            data = {
                "vault_salt_b64": base64.b64encode(self._vault_salt).decode("ascii"),
                "records": [r.to_dict() for r in self._records.values()],
            }
            return json.dumps(data, indent=2)

    def import_vault_json(self, json_str: str) -> int:
        """Import encrypted records from JSON string."""
        with self._lock:
            data = json.loads(json_str)
            if "vault_salt_b64" in data:
                self._vault_salt = base64.b64decode(data["vault_salt_b64"])
            recs = data.get("records", [])
            for r in recs:
                rec = EncryptedSecretRecord.from_dict(r)
                self._records[rec.key] = rec
            return len(recs)

    def save_to_disk(self, filepath: Optional[str] = None) -> str:
        """Persist encrypted vault JSON to file."""
        target = filepath or self.storage_path
        if not target:
            raise ValueError("No storage path specified")
        with self._lock:
            os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(self.export_vault_json())
            return target

    def load_from_disk(self, filepath: Optional[str] = None) -> int:
        """Load encrypted records from file."""
        target = filepath or self.storage_path
        if not target or not os.path.isfile(target):
            raise FileNotFoundError(f"Vault storage file not found: {target}")
        with self._lock:
            with open(target, "r", encoding="utf-8") as f:
                return self.import_vault_json(f.read())


_GLOBAL_VAULT: Optional[SecretVault] = None
_GLOBAL_VAULT_LOCK = threading.RLock()


def get_secret_vault() -> SecretVault:
    """Acquire thread-safe singleton SecretVault."""
    global _GLOBAL_VAULT
    with _GLOBAL_VAULT_LOCK:
        if _GLOBAL_VAULT is None:
            _GLOBAL_VAULT = SecretVault()
        return _GLOBAL_VAULT


def reset_secret_vault() -> SecretVault:
    """Reset singleton SecretVault."""
    global _GLOBAL_VAULT
    with _GLOBAL_VAULT_LOCK:
        _GLOBAL_VAULT = SecretVault()
        return _GLOBAL_VAULT
