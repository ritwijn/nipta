"""Shared httpx client factory. Tests install an httpx.MockTransport per provider name."""
from __future__ import annotations

import httpx

TRANSPORTS: dict[str, httpx.BaseTransport] = {}
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


def client(provider: str, base_url: str, timeout: httpx.Timeout | None = None) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        timeout=timeout or DEFAULT_TIMEOUT,
        transport=TRANSPORTS.get(provider),
    )
