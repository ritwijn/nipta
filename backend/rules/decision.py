"""Aggregate open findings into ELIGIBLE / REVIEW / BLOCKED."""
from __future__ import annotations

from typing import Iterable, Literal


def decide(findings: Iterable) -> Literal["ELIGIBLE", "REVIEW", "BLOCKED"]:
    """Accepts Finding models or findings rows (dicts with 'severity' and optional 'status')."""
    severities = set()
    for f in findings:
        status = f.get("status", "OPEN") if isinstance(f, dict) else "OPEN"
        if status != "OPEN":
            continue
        severities.add(f["severity"] if isinstance(f, dict) else f.severity)
    if "BLOCK" in severities:
        return "BLOCKED"
    if "REVIEW" in severities:
        return "REVIEW"
    return "ELIGIBLE"
