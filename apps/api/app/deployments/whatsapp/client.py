"""WhatsApp Cloud API client with retries and error mapping (PRD §14.4)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from app.core.config import settings
from app.core.metrics import wa_send_failures_total

log = structlog.get_logger()


class WhatsAppError(Exception):
    def __init__(
        self, code: str, message: str, *, status: int | None = None, fb_code: int | None = None
    ) -> None:
        self.code = code
        self.message = message
        self.status = status
        self.fb_code = fb_code
        super().__init__(f"{code}: {message}")


def _map_error(status: int, body: dict[str, Any]) -> WhatsAppError:
    err = body.get("error") or {}
    fb_code = err.get("code")
    msg = err.get("message") or str(body)[:200]
    if fb_code == 190 or status == 401:
        return WhatsAppError(
            "token_invalid", "Access token expired or invalid", status=status, fb_code=fb_code
        )
    if fb_code in (131030,):
        return WhatsAppError(
            "recipient_not_whitelisted",
            "Recipient is not in the allowed list for this test number",
            status=status,
            fb_code=fb_code,
        )
    if fb_code in (131047, 131056):
        return WhatsAppError(
            "outside_24h_window",
            "Outside the 24-hour customer service window",
            status=status,
            fb_code=fb_code,
        )
    if fb_code in (131026,):
        return WhatsAppError(
            "recipient_unavailable", "Recipient cannot receive messages", status=status, fb_code=fb_code
        )
    if status == 429 or fb_code in (4, 80007, 130429):
        return WhatsAppError("rate_limited", "Graph API rate limit", status=status, fb_code=fb_code)
    if status >= 500:
        return WhatsAppError("server_error", msg, status=status, fb_code=fb_code)
    return WhatsAppError("api_error", msg, status=status, fb_code=fb_code)


class WhatsAppClient:
    def __init__(self, phone_number_id: str, access_token: str, version: str | None = None) -> None:
        self.phone_number_id = phone_number_id
        self.token = access_token
        self.base = f"{settings.wa_graph_base_url}/{version or settings.wa_graph_api_version}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        retries: int = 4,
    ) -> dict[str, Any]:
        delay = 1.0
        last: WhatsAppError | None = None
        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.request(
                        method,
                        f"{self.base}{path}",
                        json=json_body,
                        params=params,
                        headers={"Authorization": f"Bearer {self.token}"},
                    )
            except httpx.HTTPError as e:
                last = WhatsAppError("unreachable", f"Graph API unreachable: {e}")
            else:
                if r.status_code < 300:
                    return r.json() if r.content else {}
                try:
                    body = r.json()
                except ValueError:
                    body = {"error": {"message": r.text[:200]}}
                last = _map_error(r.status_code, body)
                if last.code not in ("rate_limited", "server_error", "unreachable"):
                    raise last
            if attempt < retries:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8)
        assert last is not None
        raise last

    async def get_phone_info(self) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/{self.phone_number_id}",
            params={"fields": "display_phone_number,verified_name,quality_rating"},
            retries=1,
        )

    async def send_text(self, to: str, body: str, reply_to: str | None = None) -> str:
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": body[:4096], "preview_url": False},
        }
        if reply_to:
            payload["context"] = {"message_id": reply_to}
        try:
            data = await self._request("POST", f"/{self.phone_number_id}/messages", json_body=payload)
        except WhatsAppError as e:
            wa_send_failures_total.labels(e.code).inc()
            raise
        msgs = data.get("messages") or [{}]
        return str(msgs[0].get("id", ""))

    async def mark_read(self, message_id: str, typing: bool = True) -> None:
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        if typing:
            payload["typing_indicator"] = {"type": "text"}
        try:
            await self._request("POST", f"/{self.phone_number_id}/messages", json_body=payload, retries=0)
        except WhatsAppError as e:  # best effort
            log.debug("mark_read_failed", error=str(e))
