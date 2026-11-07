"""Email port (FR-15: two live adapters + a mock)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str | None = None
    reply_to: str | None = None
    # Stable key so a retried send is not delivered twice (where the provider supports it).
    idempotency_key: str | None = None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SendResult:
    provider: str
    message_id: str


class EmailSendError(Exception):
    """A send failed. `retryable` means a different provider may succeed (network, 429, 5xx)."""

    def __init__(self, provider: str, message: str, *, retryable: bool) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable


class EmailPort(Protocol):
    name: str

    async def send(self, message: EmailMessage) -> SendResult: ...
