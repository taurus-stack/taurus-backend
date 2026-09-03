"""
Taurus Auth Client: encapsulates all interactions with the taurus-auth service
"""
import logging
from typing import Optional, Dict, Any
from django.conf import settings
import aiohttp

from .auth_jwt import AuthClientJWTGenerator

logger = logging.getLogger(__name__)


class TaurusAuthClient:
    """Taurus Auth Client"""

    def __init__(self):
        self.base_url = getattr(settings, "TAURUS_AUTH_URL", "http://localhost:8001")
        self.jwt_generator = AuthClientJWTGenerator()
        self.timeout = aiohttp.ClientTimeout(total=10)

    def _get_headers(self) -> Dict[str, str]:
        """Get authentication request headers"""
        return self.jwt_generator.get_auth_headers()

    async def _request(
        self,
        method: str,
        path: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Send request to taurus-auth"""
        url = f"{self.base_url}{path}"
        headers = self._get_headers()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=data,
                    params=params,
                    timeout=self.timeout,
                ) as response:
                    response_text = await response.text()

                    if response.status != 200:
                        logger.error(
                            f"taurus-auth request failed: {method} {url}, "
                            f"status={response.status}, response={response_text}"
                        )
                        raise Exception(
                            f"taurus-auth returned error (HTTP {response.status}): {response_text}"
                        )

                    import json
                    result = json.loads(response_text)
                    return result

        except aiohttp.ClientError as e:
            logger.error(f"taurus-auth service unavailable: {method} {url}, error={e}")
            raise Exception(f"taurus-auth service unavailable: {str(e)}")

    async def generate_ticket(
        self,
        host_uuid: str,
        action: str = "execute_command",
        command: Optional[str] = None,
        expires_minutes: int = 5,
        metadata: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Generate ticket"""
        data = {
            "host_uuid": host_uuid,
            "action": action,
            "command": command,
            "expires_minutes": expires_minutes,
        }

        if metadata:
            data["metadata"] = metadata

        result = await self._request("POST", "/api/v1/tickets/generate", data=data)
        return result.get("data", {})

    async def revoke_ticket(
        self,
        ticket_id: str,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Revoke ticket"""
        data = {}
        if reason:
            data["reason"] = reason

        result = await self._request(
            "POST", f"/api/v1/tickets/{ticket_id}/revoke", data=data
        )
        return result

    async def list_tickets(
        self,
        host_uuid: Optional[str] = None,
        status: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Query ticket list"""
        params = {}
        if host_uuid:
            params["host_uuid"] = host_uuid
        if status is not None:
            params["status"] = status

        result = await self._request("GET", "/api/v1/tickets/list", params=params)
        return result.get("data", [])

    async def get_audit_logs(
        self,
        ticket_id: Optional[str] = None,
        event: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query audit log"""
        params = {}
        if ticket_id:
            params["ticket_id"] = ticket_id
        if event:
            params["event"] = event

        result = await self._request(
            "GET", "/api/v1/tickets/audit-logs", params=params
        )
        return result.get("data", [])