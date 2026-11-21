"""Stable error codes surfaced by the API."""
from __future__ import annotations


class NiptaError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 409, detail: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.http_status = http_status
        self.detail = detail or {}
