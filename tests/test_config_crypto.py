"""
taurus/config_crypto.py Unit test

test内容:
1. derive_fernet_key - 从主Secret key派生 Fernet Secret key
2. get_fernet - Fetch Fernet instance
3. encrypt_value / decrypt_value - Encryption/Decryption
4. is_encrypted - 判断YesNo已Encryption
5. auto_decrypt - 自动Decryption
6. generate_master_key - Generate master key
"""
import os
import pytest

from taurus.config_crypto import (
    derive_fernet_key,
    get_fernet,
    encrypt_value,
    decrypt_value,
    is_encrypted,
    auto_decrypt,
    generate_master_key,
    FERNET_PREFIX,
)


class TestDeriveFernetKey:
    def test_returns_bytes(self):
        result = derive_fernet_key("test-key")
        assert isinstance(result, bytes)

    def test_deterministic(self):
        key1 = derive_fernet_key("same-key")
        key2 = derive_fernet_key("same-key")
        assert key1 == key2

    def test_different_keys_produce_different_results(self):
        key1 = derive_fernet_key("key-a")
        key2 = derive_fernet_key("key-b")
        assert key1 != key2

    def test_length_is_44_bytes_base64(self):
        result = derive_fernet_key("any-key")
        assert len(result) == 44

    def test_empty_string_key(self):
        result = derive_fernet_key("")
        assert isinstance(result, bytes)
        assert len(result) == 44


class TestGetFernet:
    def test_with_explicit_master_key(self):
        f = get_fernet("test-master-key")
        assert f is not None

    def test_with_valid_fernet_key(self):
        from cryptography.fernet import Fernet
        valid_key = Fernet.generate_key().decode()
        f = get_fernet(valid_key)
        assert f is not None

    def test_without_master_key_and_env_raises(self):
        os.environ.pop("CONFIG_ENCRYPTION_KEY", None)
        with pytest.raises(ValueError, match="CONFIG_ENCRYPTION_KEY"):
            get_fernet(None)

    def test_from_env_variable(self):
        os.environ["CONFIG_ENCRYPTION_KEY"] = "env-test-key"
        try:
            f = get_fernet()
            assert f is not None
        finally:
            os.environ.pop("CONFIG_ENCRYPTION_KEY", None)


class TestEncryptDecryptValue:
    def test_encrypt_returns_prefixed_string(self):
        encrypted = encrypt_value("hello", master_key="test-key")
        assert encrypted.startswith(FERNET_PREFIX)

    def test_decrypt_restores_original(self):
        original = "my-secret-password"
        encrypted = encrypt_value(original, master_key="test-key")
        decrypted = decrypt_value(encrypted, master_key="test-key")
        assert decrypted == original

    def test_decrypt_with_different_key_raises(self):
        encrypted = encrypt_value("secret", master_key="key-a")
        with pytest.raises(ValueError, match="主密钥不匹配"):
            decrypt_value(encrypted, master_key="key-b")

    def test_encrypt_empty_string(self):
        assert encrypt_value("", master_key="key") == ""

    def test_decrypt_empty_string(self):
        assert decrypt_value("", master_key="key") == ""

    def test_decrypt_non_encrypted_returns_as_is(self):
        result = decrypt_value("plain-text", master_key="key")
        assert result == "plain-text"

    def test_roundtrip_unicode(self):
        original = "中文密码 🔐"
        encrypted = encrypt_value(original, master_key="test-key")
        decrypted = decrypt_value(encrypted, master_key="test-key")
        assert decrypted == original

    def test_roundtrip_long_value(self):
        original = "x" * 10000
        encrypted = encrypt_value(original, master_key="test-key")
        decrypted = decrypt_value(encrypted, master_key="test-key")
        assert decrypted == original


class TestIsEncrypted:
    def test_encrypted_value(self):
        encrypted = encrypt_value("hello", master_key="key")
        assert is_encrypted(encrypted) is True

    def test_plain_value(self):
        assert is_encrypted("plain-text") is False

    def test_empty_string(self):
        assert is_encrypted("") is False

    def test_none_value(self):
        assert is_encrypted(None) is False

    def test_prefix_only(self):
        assert is_encrypted(FERNET_PREFIX) is True


class TestAutoDecrypt:
    def test_decrypts_encrypted_value(self):
        original = "secret-value"
        encrypted = encrypt_value(original, master_key="key")
        os.environ.pop("CONFIG_ENCRYPTION_KEY", None)
        os.environ["CONFIG_ENCRYPTION_ENABLED"] = "true"
        try:
            result = auto_decrypt(encrypted, master_key="key")
            assert result == original
        finally:
            os.environ.pop("CONFIG_ENCRYPTION_ENABLED", None)

    def test_returns_plain_value_unchanged(self):
        os.environ["CONFIG_ENCRYPTION_ENABLED"] = "true"
        try:
            result = auto_decrypt("plain-text", master_key="key")
            assert result == "plain-text"
        finally:
            os.environ.pop("CONFIG_ENCRYPTION_ENABLED", None)

    def test_disabled_returns_original(self):
        original = "secret"
        encrypted = encrypt_value(original, master_key="key")
        os.environ["CONFIG_ENCRYPTION_ENABLED"] = "false"
        try:
            result = auto_decrypt(encrypted, master_key="key")
            assert result == encrypted
        finally:
            os.environ.pop("CONFIG_ENCRYPTION_ENABLED", None)

    def test_empty_value(self):
        assert auto_decrypt("", master_key="key") == ""

    def test_none_value(self):
        assert auto_decrypt(None, master_key="key") is None

    def test_failed_decryption_returns_original(self):
        encrypted = encrypt_value("secret", master_key="correct-key")
        os.environ["CONFIG_ENCRYPTION_ENABLED"] = "true"
        os.environ.pop("CONFIG_ENCRYPTION_KEY", None)
        try:
            result = auto_decrypt(encrypted, master_key="wrong-key")
            assert result == encrypted
        finally:
            os.environ.pop("CONFIG_ENCRYPTION_ENABLED", None)


class TestGenerateMasterKey:
    def test_returns_string(self):
        key = generate_master_key()
        assert isinstance(key, str)

    def test_key_is_valid_fernet_key(self):
        key = generate_master_key()
        f = get_fernet(key)
        assert f is not None

    def test_keys_are_unique(self):
        key1 = generate_master_key()
        key2 = generate_master_key()
        assert key1 != key2