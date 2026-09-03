"""
taurus/utils/signing.py Unit test

test内容:
1. generate_signature - Generate HMAC-SHA256 Signature
2. verify_signature - validateSignature(时间戳, nonce, Signaturematch)
"""
import time
from unittest.mock import patch

import pytest

from taurus.utils.signing import generate_signature, verify_signature


class TestGenerateSignature:
    def test_returns_hex_string(self):
        sig = generate_signature("host-1", "secret", 1000, "nonce1")
        assert isinstance(sig, str)
        assert len(sig) == 64

    def test_deterministic(self):
        sig1 = generate_signature("host-1", "secret", 1000, "nonce1", "body")
        sig2 = generate_signature("host-1", "secret", 1000, "nonce1", "body")
        assert sig1 == sig2

    def test_different_params_produce_different_signatures(self):
        sig1 = generate_signature("host-1", "secret", 1000, "nonce1")
        sig2 = generate_signature("host-2", "secret", 1000, "nonce1")
        assert sig1 != sig2

    def test_different_secrets_produce_different_signatures(self):
        sig1 = generate_signature("host-1", "secret-a", 1000, "nonce1")
        sig2 = generate_signature("host-1", "secret-b", 1000, "nonce1")
        assert sig1 != sig2

    def test_different_timestamps_produce_different_signatures(self):
        sig1 = generate_signature("host-1", "secret", 1000, "nonce1")
        sig2 = generate_signature("host-1", "secret", 2000, "nonce1")
        assert sig1 != sig2

    def test_different_nonces_produce_different_signatures(self):
        sig1 = generate_signature("host-1", "secret", 1000, "nonce1")
        sig2 = generate_signature("host-1", "secret", 1000, "nonce2")
        assert sig1 != sig2

    def test_body_affects_signature(self):
        sig1 = generate_signature("host-1", "secret", 1000, "nonce1", "body-a")
        sig2 = generate_signature("host-1", "secret", 1000, "nonce1", "body-b")
        assert sig1 != sig2

    def test_empty_body(self):
        sig = generate_signature("host-1", "secret", 1000, "nonce1", "")
        assert isinstance(sig, str)
        assert len(sig) == 64


class TestVerifySignature:
    def test_valid_signature(self):
        host_id = "host-1"
        secret = "my-secret"
        timestamp = int(time.time())
        nonce = "unique-nonce-123"
        body = '{"key": "value"}'

        sig = generate_signature(host_id, secret, timestamp, nonce, body)
        valid, msg = verify_signature(host_id, secret, timestamp, nonce, sig, body)
        assert valid is True
        assert msg == ""

    def test_expired_timestamp(self):
        host_id = "host-1"
        secret = "my-secret"
        old_timestamp = int(time.time()) - 600
        nonce = "unique-nonce-expired"
        sig = generate_signature(host_id, secret, old_timestamp, nonce)

        valid, msg = verify_signature(host_id, secret, old_timestamp, nonce, sig, max_age=300)
        assert valid is False
        assert "过期" in msg

    def test_wrong_signature(self):
        host_id = "host-1"
        secret = "my-secret"
        timestamp = int(time.time())
        nonce = "unique-nonce-wrong"

        valid, msg = verify_signature(host_id, secret, timestamp, nonce, "badsignature123")
        assert valid is False
        assert "不匹配" in msg

    def test_wrong_secret(self):
        host_id = "host-1"
        secret = "correct-secret"
        timestamp = int(time.time())
        nonce = "unique-nonce-secret"
        sig = generate_signature(host_id, secret, timestamp, nonce)

        valid, msg = verify_signature(host_id, "wrong-secret", timestamp, nonce, sig)
        assert valid is False
        assert "不匹配" in msg

    def test_replay_attack_blocked(self):
        host_id = "host-1"
        secret = "my-secret"
        timestamp = int(time.time())
        nonce = "replay-nonce-123"
        sig = generate_signature(host_id, secret, timestamp, nonce)

        valid1, _ = verify_signature(host_id, secret, timestamp, nonce, sig)
        assert valid1 is True

        valid2, msg = verify_signature(host_id, secret, timestamp, nonce, sig)
        assert valid2 is False
        assert "Nonce" in msg

    def test_custom_max_age(self):
        host_id = "host-1"
        secret = "my-secret"
        timestamp = int(time.time()) - 100
        nonce = "custom-age-nonce"
        sig = generate_signature(host_id, secret, timestamp, nonce)

        valid, _ = verify_signature(host_id, secret, timestamp, nonce, sig, max_age=200)
        assert valid is True

    def test_with_body(self):
        host_id = "host-1"
        secret = "my-secret"
        timestamp = int(time.time())
        nonce = "body-nonce-123"
        body = '{"data": "test"}'

        sig = generate_signature(host_id, secret, timestamp, nonce, body)
        valid, _ = verify_signature(host_id, secret, timestamp, nonce, sig, body)
        assert valid is True

    def test_wrong_body_fails(self):
        host_id = "host-1"
        secret = "my-secret"
        timestamp = int(time.time())
        nonce = "wrong-body-nonce"
        body = '{"data": "original"}'

        sig = generate_signature(host_id, secret, timestamp, nonce, body)
        valid, msg = verify_signature(host_id, secret, timestamp, nonce, sig, '{"data": "tampered"}')
        assert valid is False