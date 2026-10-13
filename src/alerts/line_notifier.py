"""Async LINE Messaging API push/reply + webhook signature verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import uuid
from typing import Any, Self

import httpx

from src.config import settings
from src.retry import request_with_retry

MAX_MESSAGES_PER_PUSH = 5  # LINE API limit


def verify_signature(body: bytes, signature: str, channel_secret: str) -> bool:
    digest = hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


class LineNotifier:
    def __init__(
        self,
        *,
        access_token: str | None = None,
        cfg: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.cfg = cfg or settings()["line"]
        token = access_token if access_token is not None else os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
        if not token:
            raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN is not set")
        self._client = httpx.AsyncClient(
            timeout=self.cfg["timeout_s"],
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def push(self, user_id: str, messages: list[dict[str, Any]], retry_key: str | None = None) -> str:
        """Push up to 5 messages to one user. Returns the retry key used.

        X-Line-Retry-Key makes retries idempotent, so a retried push is not delivered twice.
        """
        if not 1 <= len(messages) <= MAX_MESSAGES_PER_PUSH:
            raise ValueError(f"LINE push accepts 1-{MAX_MESSAGES_PER_PUSH} messages")
        retry_key = retry_key or str(uuid.uuid4())
        await request_with_retry(
            self._client,
            "POST",
            self.cfg["push_url"],
            json={"to": user_id, "messages": messages},
            headers={"X-Line-Retry-Key": retry_key},
            max_retries=3,
        )
        return retry_key

    async def reply(self, reply_token: str, messages: list[dict[str, Any]], reply_url: str) -> None:
        """Answer a webhook event. Replies are free on every LINE plan (pushes count against quota)."""
        if not 1 <= len(messages) <= MAX_MESSAGES_PER_PUSH:
            raise ValueError(f"LINE reply accepts 1-{MAX_MESSAGES_PER_PUSH} messages")
        # No retries: a reply token is single-use, a retried request after a timeout would 400.
        await request_with_retry(
            self._client, "POST", reply_url, json={"replyToken": reply_token, "messages": messages}, max_retries=0
        )
