"""Thin client for reusing the existing LMS backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

import httpx


class LMSAPIError(RuntimeError):
    """Raised when an LMS API call fails."""


@dataclass
class LMSClient:
    """HTTP client used by the bot to talk to the LMS REST endpoints."""

    base_url: str
    admin_password: str | None = None
    _timeout: float = 10.0

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=self._timeout)

    async def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        async with self._client() as client:
            try:
                response = await client.request(method, path, **kwargs)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                error = _extract_error_message(exc.response)
                raise LMSAPIError(error) from exc
            except httpx.HTTPError as exc:
                raise LMSAPIError("Network error while contacting LMS API") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise LMSAPIError("Invalid JSON response from LMS") from exc

    async def register_player(self, name: str, whatsapp_number: str | None = None, telegram_id: str | None = None) -> Dict[str, Any]:
        """Call the existing /api/register endpoint."""
        payload = {"name": name}
        if whatsapp_number:
            payload["whatsapp_number"] = whatsapp_number
        if telegram_id:
            payload["telegram_id"] = telegram_id

        data = await self._request("POST", "/api/register", json=payload)
        if not data.get("success"):
            raise LMSAPIError(data.get("error") or "Unknown LMS error")
        return data

    async def schedule_reminders(self, round_id: int) -> Dict[str, Any]:
        """Proxy the admin reminder scheduler."""
        return await self._request("POST", f"/api/admin/schedule-reminders/{round_id}")

    async def get_due_reminders(self) -> Dict[str, Any]:
        """Fetch reminders that are ready to send."""
        return await self._request("GET", "/api/admin/due-reminders")

    async def mark_reminder_sent(self, reminder_id: int) -> Dict[str, Any]:
        """Mark a reminder as sent."""
        return await self._request("POST", f"/api/admin/mark-reminder-sent/{reminder_id}")

    async def get_pick_options(self, pick_token: str) -> Dict[str, Any]:
        """Get available teams for a pick token."""
        data = await self._request("GET", f"/api/picks/options/{pick_token}")
        if not data.get("success"):
            raise LMSAPIError(data.get("error") or "Failed to get pick options")
        return data

    async def submit_pick(self, pick_token: str, team_name: str) -> Dict[str, Any]:
        """Submit a pick via API."""
        payload = {"token": pick_token, "team": team_name}
        data = await self._request("POST", "/api/picks/submit", json=payload)
        if not data.get("success"):
            raise LMSAPIError(data.get("error") or "Failed to submit pick")
        return data

    async def check_new_game(self) -> Dict[str, Any]:
        """Check if a new game can be started."""
        if not self.admin_password:
            raise LMSAPIError("Admin password required for this operation")

        headers = {"X-Admin-Password": self.admin_password}
        return await self._request("GET", "/api/admin/check-new-game", headers=headers)

    async def start_new_game(self, pl_matchday: int | None = None) -> Dict[str, Any]:
        """Start a new game cycle."""
        if not self.admin_password:
            raise LMSAPIError("Admin password required for this operation")

        headers = {"X-Admin-Password": self.admin_password}
        payload = {}
        if pl_matchday is not None:
            payload["pl_matchday"] = pl_matchday

        data = await self._request("POST", "/api/admin/start-new-game", json=payload, headers=headers)
        if not data.get("success"):
            raise LMSAPIError(data.get("error") or "Failed to start new game")
        return data


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
        return payload.get("error") or payload.get("message") or response.text
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
