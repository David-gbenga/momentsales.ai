"""Postmark adapter. Endpoints and fields per docs/integrations/postmark.md."""

from __future__ import annotations

from typing import Any

import httpx

from momentsales.adapters.email.base import EmailMessage, EmailSendError, SendResult

POSTMARK_URL = "https://api.postmarkapp.com/email"
_RETRYABLE = {429, 500, 503}


class PostmarkEmailAdapter:
    name = "postmark"

    def __init__(
        self,
        server_token: str,
        sender: str,
        client: httpx.AsyncClient,
        message_stream: str = "outbound",
    ) -> None:
        self._token = server_token
        self._from = sender
        self._client = client
        self._stream = message_stream

    async def send(self, message: EmailMessage) -> SendResult:
        body: dict[str, Any] = {
            "From": self._from,
            "To": message.to,
            "Subject": message.subject,
            "TextBody": message.text,
            "MessageStream": self._stream,
        }
        if message.html:
            body["HtmlBody"] = message.html
        if message.reply_to:
            body["ReplyTo"] = message.reply_to
        if message.tags:
            body["Metadata"] = dict(message.tags)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Postmark-Server-Token": self._token,
        }
        try:
            resp = await self._client.post(POSTMARK_URL, json=body, headers=headers, timeout=10)
        except httpx.HTTPError as exc:
            raise EmailSendError(self.name, f"transport error: {exc}", retryable=True) from exc
        if resp.status_code != 200:
            code = _error_code(resp)
            raise EmailSendError(
                self.name,
                f"HTTP {resp.status_code} ErrorCode {code}",
                retryable=resp.status_code in _RETRYABLE,
            )
        data = resp.json()
        if data.get("ErrorCode", 0) != 0:
            raise EmailSendError(self.name, f"ErrorCode {data['ErrorCode']}", retryable=False)
        return SendResult(provider=self.name, message_id=str(data["MessageID"]))


def _error_code(resp: httpx.Response) -> object:
    try:
        return resp.json().get("ErrorCode")
    except ValueError:
        return None
