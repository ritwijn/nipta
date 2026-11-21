"""Cashfree Relay voice agent. We only POST to its inbound webhook; Relay places the call in the vendor's
language and posts the outcome back to /api/relay/vendor-call-result. Setup: docs/relay-vendor-call.md."""
from __future__ import annotations

from ..config import get_settings
from .http import client

PROVIDER = "relay"


def start_vendor_call(payload: dict) -> None:
    url = get_settings().relay_vendor_call_url
    assert url, "caller checks settings.relay_live"
    with client(PROVIDER, "") as c:
        c.post(url, json=payload).raise_for_status()
