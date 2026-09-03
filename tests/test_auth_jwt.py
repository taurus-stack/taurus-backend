"""
taurus/utils/auth_jwt.py Unit test

test内容:
1. AuthClientJWTGenerator.generate_token - Generate JWT Token
2. AuthClientJWTGenerator.get_auth_headers - Fetch认证头
"""
import jwt
from unittest.mock import patch

from django.test import TestCase

from taurus.utils.auth_jwt import AuthClientJWTGenerator


class AuthClientJWTGeneratorTest(TestCase):
    def setUp(self):
        self.generator = AuthClientJWTGenerator()

    def test_generate_token_returns_string(self):
        token = self.generator.generate_token()
        assert isinstance(token, str)

    def test_generate_token_contains_required_claims(self):
        token = self.generator.generate_token()
        payload = jwt.decode(
            token,
            self.generator.secret,
            algorithms=[self.generator.algorithm],
        )
        assert "sub" in payload
        assert "iat" in payload
        assert "exp" in payload
        assert "nbf" in payload

    def test_generate_token_default_service_id(self):
        token = self.generator.generate_token()
        payload = jwt.decode(
            token,
            self.generator.secret,
            algorithms=[self.generator.algorithm],
        )
        assert payload["sub"] == "taurus-backend"

    def test_generate_token_with_ip(self):
        token = self.generator.generate_token(ip="192.168.1.1")
        payload = jwt.decode(
            token,
            self.generator.secret,
            algorithms=[self.generator.algorithm],
        )
        assert payload["ip"] == "192.168.1.1"

    def test_generate_token_without_ip(self):
        token = self.generator.generate_token()
        payload = jwt.decode(
            token,
            self.generator.secret,
            algorithms=[self.generator.algorithm],
        )
        assert "ip" not in payload

    def test_generate_token_with_extra_claims(self):
        token = self.generator.generate_token(extra_claims={"role": "admin"})
        payload = jwt.decode(
            token,
            self.generator.secret,
            algorithms=[self.generator.algorithm],
        )
        assert payload["role"] == "admin"

    def test_generate_token_is_verifiable(self):
        token = self.generator.generate_token()
        payload = jwt.decode(
            token,
            self.generator.secret,
            algorithms=[self.generator.algorithm],
        )
        assert payload is not None

    def test_generate_token_wrong_secret_fails(self):
        token = self.generator.generate_token()
        with self.assertRaises(jwt.InvalidSignatureError):
            jwt.decode(token, "wrong-secret", algorithms=[self.generator.algorithm])

    def test_get_auth_headers(self):
        headers = self.generator.get_auth_headers()
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")
        assert "Content-Type" in headers
        assert headers["Content-Type"] == "application/json"

    def test_get_auth_headers_token_is_valid(self):
        headers = self.generator.get_auth_headers()
        token = headers["Authorization"].replace("Bearer ", "")
        payload = jwt.decode(
            token,
            self.generator.secret,
            algorithms=[self.generator.algorithm],
        )
        assert payload["sub"] == "taurus-backend"

    @patch("taurus.utils.auth_jwt.settings")
    def test_custom_settings(self, mock_settings):
        mock_settings.TAURUS_AUTH_JWT_SECRET = "custom-secret"
        mock_settings.TAURUS_AUTH_SERVICE_ID = "custom-service"
        mock_settings.TAURUS_AUTH_JWT_EXPIRES_MINUTES = 30

        gen = AuthClientJWTGenerator()
        gen.secret = "custom-secret"
        gen.service_id = "custom-service"
        gen.expires_minutes = 30

        token = gen.generate_token()
        payload = jwt.decode(token, "custom-secret", algorithms=["HS256"])
        assert payload["sub"] == "custom-service"