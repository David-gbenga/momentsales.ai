"""Contract tests for the live email adapters against recorded response shapes
(docs/integrations/resend.md, postmark.md), plus failover."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from momentsales.adapters.email.base import EmailMessage, EmailSendError, SendResult
from momentsales.adapters.email.postmark import POSTMARK_URL, PostmarkEmailAdapter
from momentsales.adapters.email.resend import RESEND_URL, ResendEmailAdapter
from momentsales.modules.providers.registry import FailoverEmailSender

MSG = EmailMessage(
    to="a@example.com", subject="Hi", text="Body", html="<p>Body</p>", idempotency_key="k-1"
)


@respx.mock
async def test_resend_request_shape() -> None:
    route = respx.post(RESEND_URL).mock(
        return_value=httpx.Response(200, json={"id": "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"})
    )
    async with httpx.AsyncClient() as http:
        result = await ResendEmailAdapter("re_test", "Shop <s@example.com>", http).send(MSG)
    assert result.message_id == "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer re_test"
    assert req.headers["Idempotency-Key"] == "k-1"
    body = json.loads(req.content)
    assert body == {
        "from": "Shop <s@example.com>",
        "to": ["a@example.com"],
        "subject": "Hi",
        "text": "Body",
        "html": "<p>Body</p>",
    }


@pytest.mark.parametrize(("status", "retryable"), [(429, True), (500, True), (403, False)])
@respx.mock
async def test_resend_errors(status: int, retryable: bool) -> None:
    respx.post(RESEND_URL).mock(return_value=httpx.Response(status, json={}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(EmailSendError) as exc:
            await ResendEmailAdapter("re_test", "s@example.com", http).send(MSG)
    assert exc.value.retryable is retryable


@respx.mock
async def test_postmark_request_shape() -> None:
    route = respx.post(POSTMARK_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "To": "a@example.com",
                "SubmittedAt": "2014-02-17T07:25:01.4178645-05:00",
                "MessageID": "0a129aee-e1cd-480d-b08d-4f48548ff48d",
                "ErrorCode": 0,
                "Message": "OK",
            },
        )
    )
    async with httpx.AsyncClient() as http:
        result = await PostmarkEmailAdapter("pm_test", "s@example.com", http).send(MSG)
    assert result.message_id == "0a129aee-e1cd-480d-b08d-4f48548ff48d"
    req = route.calls.last.request
    assert req.headers["X-Postmark-Server-Token"] == "pm_test"
    assert req.headers["Accept"] == "application/json"
    body = json.loads(req.content)
    assert body["From"] == "s@example.com" and body["To"] == "a@example.com"
    assert body["TextBody"] == "Body" and body["HtmlBody"] == "<p>Body</p>"
    assert body["MessageStream"] == "outbound"


@respx.mock
async def test_postmark_422_not_retryable() -> None:
    respx.post(POSTMARK_URL).mock(
        return_value=httpx.Response(
            422, json={"ErrorCode": 300, "Message": "Invalid email request"}
        )
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(EmailSendError) as exc:
            await PostmarkEmailAdapter("pm_test", "s@example.com", http).send(MSG)
    assert not exc.value.retryable


class _Fake:
    def __init__(self, name: str, error: EmailSendError | None = None) -> None:
        self.name = name
        self.error = error
        self.calls = 0

    async def send(self, message: EmailMessage) -> SendResult:
        self.calls += 1
        if self.error:
            raise self.error
        return SendResult(self.name, "id")


async def test_failover_on_retryable_error() -> None:
    primary = _Fake("resend", EmailSendError("resend", "503", retryable=True))
    secondary = _Fake("postmark")
    result = await FailoverEmailSender(primary, secondary, auto=True).send(MSG)
    assert result.provider == "postmark"


async def test_no_failover_on_permanent_error_or_manual() -> None:
    permanent = _Fake("resend", EmailSendError("resend", "403", retryable=False))
    secondary = _Fake("postmark")
    with pytest.raises(EmailSendError):
        await FailoverEmailSender(permanent, secondary, auto=True).send(MSG)
    retryable = _Fake("resend", EmailSendError("resend", "503", retryable=True))
    with pytest.raises(EmailSendError):
        await FailoverEmailSender(retryable, secondary, auto=False).send(MSG)
    assert secondary.calls == 0
