"""Verify LIFF ID tokens with LINE Login, so a web report carries the same userId as the chat flow.

The LIFF app must belong to a LINE Login channel under the *same provider* as the Messaging API
channel; otherwise `sub` differs from the webhook userId and rate limits split per channel.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from src.retry import request_with_retry


class InvalidIdToken(Exception):
    pass


class IdTokenClaims(BaseModel):
    sub: str  # LINE userId
    aud: str
    exp: int


async def verify_id_token(
    id_token: str, channel_id: str, verify_url: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> IdTokenClaims:
    """LINE checks signature, expiry and audience; a 400 means the token is not valid for our channel."""
    async with httpx.AsyncClient(timeout=10, transport=transport) as client:
        try:
            resp = await request_with_retry(
                client, "POST", verify_url, data={"id_token": id_token, "client_id": channel_id}, max_retries=2
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400:
                raise InvalidIdToken from exc
            raise
    claims = IdTokenClaims.model_validate(resp.json())
    if claims.aud != channel_id:
        raise InvalidIdToken
    return claims
