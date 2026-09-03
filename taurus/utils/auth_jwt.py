"""
JWT utility class: used by taurus-backend to generate JWT tokens for accessing taurus-auth
"""
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict
from django.conf import settings


class AuthClientJWTGenerator:
    """Auth Client JWT Generateserver"""

    def __init__(self):
        self.secret = getattr(settings, "TAURUS_AUTH_JWT_SECRET", "your-jwt-secret-key-change-in-production")
        self.algorithm = "HS256"
        self.expires_minutes = int(getattr(settings, "TAURUS_AUTH_JWT_EXPIRES_MINUTES", 60))
        self.service_id = getattr(settings, "TAURUS_AUTH_SERVICE_ID", "taurus-backend")

    def generate_token(
        self,
        ip: Optional[str] = None,
        extra_claims: Optional[Dict] = None,
    ) -> str:
        now = datetime.utcnow()

        payload = {
            "sub": self.service_id,
            "iat": now,
            "exp": now + timedelta(minutes=self.expires_minutes),
            "nbf": now,
        }

        if ip:
            payload["ip"] = ip

        if extra_claims:
            payload.update(extra_claims)

        token = jwt.encode(payload, self.secret, algorithm=self.algorithm)

        return token

    def get_auth_headers(self) -> Dict[str, str]:
        token = self.generate_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }