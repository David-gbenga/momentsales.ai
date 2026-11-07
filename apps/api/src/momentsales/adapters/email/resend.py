"""Resend adapter. Endpoints and fields per docs/integrations/resend.md."""

from __future__ import annotations

from typing import Any

import httpx

from momentsales.adapters.email.base import EmailMessage, EmailSendError, SendResult

RESEND_URL = "https://api.resend.com/emails"
_RETRYABLE = {429, 500, 503}


class ResendEmailAdapter:
    name = "resend"

    def __init__(self, api_key: str, sender: str, client: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self._from = sender
        self._client = client

    async def send(self, message: EmailMessage) -> SendResult:
        body: dict[str, Any] = {
            "from": self._from,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
        }
        if message.html:
            body["html"] = message.html
        if message.reply_to:
            body["reply_to"] = message.reply_to
        if message.tags:
            body["tags"] = [{"name": k, "value": v} for k, v in message.tags.items()]
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if message.idempotency_key:
            headers["Idempotency-Key"] = message.idempotency_key[:256]
        try:
            resp = await self._client.post(RESEND_URL, json=body, headers=headers, timeout=10)
        except httpx.HTTPError as exc:
            raise EmailSendError(self.name, f"transport error: {exc}", retryable=True) from exc
        if resp.status_code >= 300:
            raise EmailSendError(
                self.name,
                f"HTTP {resp.status_code}",
                retryable=resp.status_code in _RETRYABLE,
            )
        data = resp.json()
        return SendResult(provider=self.name, message_id=str(data["id"]))
