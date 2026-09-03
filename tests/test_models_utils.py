"""
taurus/models.py HeartbeatServer 和 RegistrationToken Unit test

test内容:
1. HeartbeatServer.load_ratio - Load比率compute
2. HeartbeatServer.assign_for_host - HeartbeatServiceserver分配策略
3. RegistrationToken.hash_token / generate_token - Token工具method
"""
import uuid
import pytest
from django.test import TestCase

from taurus.models import HeartbeatServer, RegistrationToken


class HeartbeatServerLoadRatioTest(TestCase):
    def test_zero_connections(self):
        server = HeartbeatServer(
            name="test",
            address="http://localhost:8000",
            max_connections=100,
            current_connections=0,
        )
        assert server.load_ratio == 0.0

    def test_half_connections(self):
        server = HeartbeatServer(
            name="test",
            address="http://localhost:8000",
            max_connections=100,
            current_connections=50,
        )
        assert server.load_ratio == 0.5

    def test_full_connections(self):
        server = HeartbeatServer(
            name="test",
            address="http://localhost:8000",
            max_connections=100,
            current_connections=100,
        )
        assert server.load_ratio == 1.0

    def test_over_capacity_capped(self):
        server = HeartbeatServer(
            name="test",
            address="http://localhost:8000",
            max_connections=100,
            current_connections=150,
        )
        assert server.load_ratio == 1.0

    def test_zero_max_connections(self):
        server = HeartbeatServer(
            name="test",
            address="http://localhost:8000",
            max_connections=0,
            current_connections=0,
        )
        assert server.load_ratio == 1.0


class HeartbeatServerAssignTest(TestCase):
    def test_no_active_servers_returns_none(self):
        result = HeartbeatServer.assign_for_host("192.168.1.1")
        assert result is None

    def test_assigns_to_subnet_match(self):
        server = HeartbeatServer.objects.create(
            name="subnet-server",
            address="http://10.0.0.1:8000",
            subnet="10.0.0.0/24",
            weight=100,
            max_connections=1000,
            current_connections=0,
            is_active=True,
        )
        result = HeartbeatServer.assign_for_host("10.0.0.50")
        assert result is not None
        assert result.id == server.id

    def test_assigns_to_fallback_by_weight(self):
        server1 = HeartbeatServer.objects.create(
            name="low-weight",
            address="http://s1:8000",
            subnet="",
            weight=50,
            max_connections=1000,
            current_connections=0,
            is_active=True,
        )
        server2 = HeartbeatServer.objects.create(
            name="high-weight",
            address="http://s2:8000",
            subnet="",
            weight=200,
            max_connections=1000,
            current_connections=0,
            is_active=True,
        )
        result = HeartbeatServer.assign_for_host("172.16.0.1")
        assert result is not None
        assert result.id == server2.id

    def test_assigns_to_least_loaded(self):
        HeartbeatServer.objects.create(
            name="busy-server",
            address="http://busy:8000",
            subnet="",
            weight=100,
            max_connections=100,
            current_connections=90,
            is_active=True,
        )
        HeartbeatServer.objects.create(
            name="idle-server",
            address="http://idle:8000",
            subnet="",
            weight=100,
            max_connections=100,
            current_connections=10,
            is_active=True,
        )
        result = HeartbeatServer.assign_for_host("172.16.0.1")
        assert result is not None
        assert result.name == "idle-server"

    def test_skips_inactive_servers(self):
        HeartbeatServer.objects.create(
            name="inactive",
            address="http://inactive:8000",
            subnet="",
            weight=100,
            max_connections=100,
            current_connections=0,
            is_active=False,
        )
        result = HeartbeatServer.assign_for_host("172.16.0.1")
        assert result is None

    def test_most_specific_subnet_wins(self):
        HeartbeatServer.objects.create(
            name="broad",
            address="http://broad:8000",
            subnet="10.0.0.0/8",
            weight=100,
            max_connections=1000,
            current_connections=0,
            is_active=True,
        )
        HeartbeatServer.objects.create(
            name="narrow",
            address="http://narrow:8000",
            subnet="10.0.1.0/24",
            weight=100,
            max_connections=1000,
            current_connections=0,
            is_active=True,
        )
        result = HeartbeatServer.assign_for_host("10.0.1.50")
        assert result is not None
        assert result.name == "narrow"

    def test_invalid_ip_uses_fallback(self):
        HeartbeatServer.objects.create(
            name="fallback",
            address="http://fallback:8000",
            subnet="",
            weight=100,
            max_connections=1000,
            current_connections=0,
            is_active=True,
        )
        result = HeartbeatServer.assign_for_host("not-an-ip")
        assert result is not None
        assert result.name == "fallback"


class RegistrationTokenTest(TestCase):
    def test_hash_token_deterministic(self):
        hash1 = RegistrationToken.hash_token("test-token")
        hash2 = RegistrationToken.hash_token("test-token")
        assert hash1 == hash2

    def test_hash_token_different_inputs(self):
        hash1 = RegistrationToken.hash_token("token-a")
        hash2 = RegistrationToken.hash_token("token-b")
        assert hash1 != hash2

    def test_hash_token_is_sha256_hex(self):
        result = RegistrationToken.hash_token("test")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_generate_token_has_prefix(self):
        token = RegistrationToken.generate_token()
        assert token.startswith(RegistrationToken.TOKEN_PREFIX)

    def test_generate_token_uniqueness(self):
        token1 = RegistrationToken.generate_token()
        token2 = RegistrationToken.generate_token()
        assert token1 != token2

    def test_generate_token_length(self):
        token = RegistrationToken.generate_token()
        assert len(token) > 20