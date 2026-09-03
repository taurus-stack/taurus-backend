"""
taurus/utils/auth_client.py Unit test

test内容:
1. TaurusAuthClient initialize
2. _get_headers - Fetch认证Request headers
3. _request - sendrequest到 taurus-auth
4. generate_ticket - GenerateTicket
5. revoke_ticket - UndoTicket
6. list_tickets - QueryTicketlist
7. get_audit_logs - QueryauditLog
"""
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from taurus.utils.auth_client import TaurusAuthClient


@pytest.fixture
def client():
    with patch("taurus.utils.auth_client.settings"):
        c = TaurusAuthClient()
        c.base_url = "http://localhost:8001"
        return c


class TestTaurusAuthClientInit:
    @patch("taurus.utils.auth_client.settings")
    def test_default_base_url(self, mock_settings):
        mock_settings.TAURUS_AUTH_URL = "http://auth:8001"
        c = TaurusAuthClient()
        assert c.base_url == "http://auth:8001"

    @patch("taurus.utils.auth_client.settings")
    def test_default_timeout(self, mock_settings):
        mock_settings.TAURUS_AUTH_URL = "http://localhost:8001"
        c = TaurusAuthClient()
        assert c.timeout.total == 10


class TestGetHeaders:
    def test_returns_dict_with_authorization(self, client):
        headers = client._get_headers()
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")


class TestRequest:
    @pytest.mark.asyncio
    async def test_successful_request(self, client):
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value=json.dumps({"data": {"key": "value"}}))

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=mock_ctx)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("taurus.utils.auth_client.aiohttp.ClientSession", return_value=mock_session):
            result = await client._request("GET", "/api/v1/test")
            assert result == {"data": {"key": "value"}}

    @pytest.mark.asyncio
    async def test_error_status_raises(self, client):
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=mock_ctx)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("taurus.utils.auth_client.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(Exception, match="HTTP 500"):
                await client._request("GET", "/api/v1/test")

    @pytest.mark.asyncio
    async def test_connection_error_raises(self, client):
        import aiohttp

        mock_session = MagicMock()
        mock_session.request = MagicMock(side_effect=aiohttp.ClientError("connection refused"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("taurus.utils.auth_client.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(Exception, match="taurus-auth"):
                await client._request("GET", "/api/v1/test")


class TestGenerateTicket:
    @pytest.mark.asyncio
    async def test_generate_ticket_calls_request(self, client):
        expected = {"ticket_id": "ticket_123", "ticket": "abc"}
        client._request = AsyncMock(return_value={"data": expected})

        result = await client.generate_ticket(
            host_uuid="uuid-1234",
            action="execute_command",
        )
        assert result == expected
        client._request.assert_called_once_with(
            "POST",
            "/api/v1/tickets/generate",
            data={
                "host_uuid": "uuid-1234",
                "action": "execute_command",
                "command": None,
                "expires_minutes": 5,
            },
        )

    @pytest.mark.asyncio
    async def test_generate_ticket_with_metadata(self, client):
        expected = {"ticket_id": "ticket_456"}
        client._request = AsyncMock(return_value={"data": expected})

        result = await client.generate_ticket(
            host_uuid="uuid-5678",
            action="deploy",
            metadata={"env": "prod"},
        )
        assert result == expected
        call_data = client._request.call_args[1]["data"]
        assert call_data["metadata"] == {"env": "prod"}


class TestRevokeTicket:
    @pytest.mark.asyncio
    async def test_revoke_with_reason(self, client):
        expected = {"success": True}
        client._request = AsyncMock(return_value=expected)

        result = await client.revoke_ticket("ticket_123", reason="security")
        assert result == expected
        client._request.assert_called_once_with(
            "POST",
            "/api/v1/tickets/ticket_123/revoke",
            data={"reason": "security"},
        )

    @pytest.mark.asyncio
    async def test_revoke_without_reason(self, client):
        expected = {"success": True}
        client._request = AsyncMock(return_value=expected)

        result = await client.revoke_ticket("ticket_456")
        assert result == expected
        client._request.assert_called_once_with(
            "POST",
            "/api/v1/tickets/ticket_456/revoke",
            data={},
        )


class TestListTickets:
    @pytest.mark.asyncio
    async def test_list_with_filters(self, client):
        expected = [{"ticket_id": "t1"}]
        client._request = AsyncMock(return_value={"data": expected})

        result = await client.list_tickets(host_uuid="uuid-1", status=0)
        assert result == expected
        client._request.assert_called_once_with(
            "GET",
            "/api/v1/tickets/list",
            params={"host_uuid": "uuid-1", "status": 0},
        )

    @pytest.mark.asyncio
    async def test_list_without_filters(self, client):
        expected = []
        client._request = AsyncMock(return_value={"data": expected})

        result = await client.list_tickets()
        assert result == expected
        client._request.assert_called_once_with(
            "GET",
            "/api/v1/tickets/list",
            params={},
        )


class TestGetAuditLogs:
    @pytest.mark.asyncio
    async def test_get_logs_with_filters(self, client):
        expected = [{"event": "generated"}]
        client._request = AsyncMock(return_value={"data": expected})

        result = await client.get_audit_logs(ticket_id="t1", event="generated")
        assert result == expected
        client._request.assert_called_once_with(
            "GET",
            "/api/v1/tickets/audit-logs",
            params={"ticket_id": "t1", "event": "generated"},
        )

    @pytest.mark.asyncio
    async def test_get_logs_without_filters(self, client):
        expected = []
        client._request = AsyncMock(return_value={"data": expected})

        result = await client.get_audit_logs()
        assert result == expected
        client._request.assert_called_once_with(
            "GET",
            "/api/v1/tickets/audit-logs",
            params={},
        )