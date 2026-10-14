"""Shared async HTTP retry with exponential backoff and Retry-After support."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _retry_after_s(resp: httpx.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # HTTP-date form not supported; fall back to backoff


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_retries: int = 3,
    backoff_base_s: float = 1.0,
    max_sleep_s: float = 60.0,
    **kwargs: Any,
) -> httpx.Response:
    """Send a request, retrying on transport errors and RETRYABLE_STATUS.

    Raises httpx.HTTPStatusError on a final non-2xx response. Logs only the host: some URLs carry
    user coordinates in the path (OSRM).
    """
    for attempt in range(max_retries + 1):
        try:
            resp = await client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            if attempt == max_retries:
                raise
            delay = backoff_base_s * 2**attempt
            log.warning("transport error %s on %s, retry in %.1fs", type(exc).__name__, httpx.URL(url).host, delay)
        else:
            if resp.status_code not in RETRYABLE_STATUS or attempt == max_retries:
                resp.raise_for_status()
                return resp
            delay = _retry_after_s(resp) or backoff_base_s * 2**attempt
            log.warning("HTTP %d on %s, retry in %.1fs", resp.status_code, httpx.URL(url).host, delay)
        await asyncio.sleep(min(delay, max_sleep_s))
    raise AssertionError("unreachable")
