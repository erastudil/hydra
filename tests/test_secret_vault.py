"""
Integration test suite for Hydra Desktop Secure Secret Vault.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import os
import tempfile
import pytest

from desktop.secret_vault import (
    SecretVault,
    derive_vault_key,
    encrypt_payload,
    decrypt_payload,
    get_secret_vault,
    reset_secret_vault,
)


@pytest.fixture
def vault():
    v = SecretVault(master_passphrase="sovereign_master_pass_123")
    yield v
    v.clear()


def test_crypto_primitives_encrypt_decrypt():
    """Verify PBKDF2 derivation and AES-GCM roundtrip encryption."""
    salt = os.urandom(16)
    key = derive_vault_key("master_test", salt)
    assert len(key) == 32

    raw_text = "sk-live-super-secret-api-key-999"
    encrypted = encrypt_payload(raw_text, key, salt)
    assert "ciphertext_b64" in encrypted
    assert "nonce_b64" in encrypted

    decrypted = decrypt_payload(encrypted, key)
    assert decrypted == raw_text

    # Decryption with wrong key fails
    wrong_key = derive_vault_key("wrong_password", salt)
    with pytest.raises(Exception):
        decrypt_payload(encrypted, wrong_key)


def test_vault_lock_unlock_lifecycle():
    """Verify lock state machine denies operations when locked."""
    v = SecretVault()
    assert v.is_unlocked is False

    with pytest.raises(PermissionError) as exc:
        v.set_secret("k1", "v1")
    assert "Vault is locked" in str(exc.value)

    with pytest.raises(PermissionError):
        v.get_secret("k1")

    v.unlock("pass123")
    assert v.is_unlocked is True
    v.set_secret("k1", "v1")
    assert v.get_secret("k1") == "v1"

    v.lock()
    assert v.is_unlocked is False
    with pytest.raises(PermissionError):
        v.get_secret("k1")


def test_crud_secrets(vault: SecretVault):
    """Verify storing, listing, retrieving, and deleting secrets."""
    vault.set_secret("anthropic_key", "sk-ant-12345", metadata={"provider": "anthropic"})
    vault.set_secret("openai_key", "sk-proj-67890", metadata={"provider": "openai"})

    assert vault.total_secrets == 2
    assert vault.has_secret("anthropic_key") is True
    assert vault.has_secret("nonexistent") is False
    assert vault.list_keys() == ["anthropic_key", "openai_key"]

    assert vault.get_secret("anthropic_key") == "sk-ant-12345"
    assert vault.get_secret("openai_key") == "sk-proj-67890"

    assert vault.delete_secret("openai_key") is True
    assert vault.total_secrets == 1
    assert vault.has_secret("openai_key") is False


def test_runtime_credential_injection(vault: SecretVault):
    """Verify injecting decrypted secrets into os.environ at runtime."""
    test_env_var = "HYDRA_TEST_INJECTED_TOKEN"
    if test_env_var in os.environ:
        del os.environ[test_env_var]

    vault.set_secret("test_token", "sovereign_runtime_auth_value")
    injected = vault.inject_into_environ({"test_token": test_env_var})

    assert test_env_var in injected
    assert os.environ.get(test_env_var) == "sovereign_runtime_auth_value"

    # Clean up
    del os.environ[test_env_var]


def test_passphrase_re_encryption(vault: SecretVault):
    """Verify changing master passphrase re-encrypts stored records."""
    vault.set_secret("db_pass", "super_secret_db_pass")
    vault.change_passphrase("new_stronger_passphrase_456")

    assert vault.get_secret("db_pass") == "super_secret_db_pass"

    vault.lock()
    vault.unlock("new_stronger_passphrase_456")
    assert vault.get_secret("db_pass") == "super_secret_db_pass"


def test_disk_persistence_and_json_export(vault: SecretVault):
    """Verify saving to disk and loading encrypted vault from file."""
    vault.set_secret("api_token", "token_value_abc")

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        tmp_path = tf.name

    try:
        vault.save_to_disk(tmp_path)
        assert os.path.getsize(tmp_path) > 0

        # Load into separate vault instance
        new_vault = SecretVault(master_passphrase="sovereign_master_pass_123", storage_path=tmp_path)
        assert new_vault.total_secrets == 1
        assert new_vault.get_secret("api_token") == "token_value_abc"
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_global_singleton_vault():
    """Verify singleton lifecycle for SecretVault."""
    v1 = get_secret_vault()
    v2 = get_secret_vault()
    assert v1 is v2

    v3 = reset_secret_vault()
    assert v3 is not v1
    assert get_secret_vault() is v3
