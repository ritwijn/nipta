"""Runtime configuration. Every external provider is off by default."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # pragma: no cover
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _str(name: str, default: str | None = None) -> str | None:
    raw = os.getenv(name)
    return raw if raw not in (None, "") else default


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    sarvam_api_key: str | None = None
    sarvam_base_url: str = "https://api.sarvam.ai"
    sarvam_chat_model: str = "sarvam-105b"
    sarvam_vision_model: str = "sarvam-105b"          # used only when extraction mode is "chat"
    sarvam_extraction_mode: str = "doc_ai"            # "doc_ai" (Sarvam Vision OCR job) or "chat"
    sarvam_doc_ai_timeout_seconds: int = 120
    sarvam_doc_ai_language: str | None = None          # e.g. "kn-IN"; unset = not sent
    sarvam_tts_model: str = "bulbul:v3"
    sarvam_tts_speaker: str | None = None             # e.g. "shubh" (bulbul:v3 default)
    sarvam_stt_model: str = "saaras:v4"
    sarvam_vision_enabled: bool = False
    sarvam_chat_enabled: bool = False
    sarvam_tts_enabled: bool = False
    sarvam_stt_enabled: bool = False

    cashfree_payouts_enabled: bool = False
    cashfree_base_url: str = "https://sandbox.cashfree.com"
    cashfree_payouts_client_id: str | None = None
    cashfree_payouts_client_secret: str | None = None
    cashfree_webhook_secret: str | None = None
    cashfree_2fa_public_key_path: str | None = None
    cashfree_secureid_enabled: bool = False
    cashfree_secureid_client_id: str | None = None
    cashfree_secureid_client_secret: str | None = None
    cashfree_secureid_api_version: str | None = None

    relay_vendor_call_url: str | None = None   # Relay agent's inbound webhook URL
    relay_callback_secret: str | None = None   # Relay sends this back in x-nipta-relay-secret
    public_base_url: str | None = None         # tunnel URL Relay can reach, e.g. https://x.trycloudflare.com
    demo_call_phone: str | None = None         # demo only: route every vendor call to this phone

    min_field_confidence: float = 0.85
    arith_tolerance_paise: int = 0
    verification_max_age_days: int = 30
    require_live_verification: bool = True
    approval_ttl_seconds: int = 600
    agent_max_tool_calls: int = 6
    test_mode: bool = False
    max_upload_bytes: int = 10 * 1024 * 1024

    operator_id: str = "OP-RAGHU"
    buyer_id: str = "BUYER-RAGHU-HW"
    operator_pin: str = "2468"
    session_secret: str = "dev-only-session-secret-change-me"
    session_ttl_seconds: int = 8 * 3600
    database_path: str = "nipta.db"
    upload_dir: str = "uploads"

    @property
    def twofa_key_ok(self) -> bool:
        """No 2FA key configured, or the configured key file exists. A missing file must not reach dispatch."""
        return not self.cashfree_2fa_public_key_path or Path(self.cashfree_2fa_public_key_path).is_file()

    @property
    def payouts_live(self) -> bool:
        return bool(
            self.cashfree_payouts_enabled
            and self.cashfree_payouts_client_id
            and self.cashfree_payouts_client_secret
            and self.twofa_key_ok
        )

    @property
    def secureid_live(self) -> bool:
        return bool(
            self.cashfree_secureid_enabled
            and self.cashfree_secureid_client_id
            and self.cashfree_secureid_client_secret
            and self.twofa_key_ok
        )

    @property
    def vision_live(self) -> bool:
        return bool(self.sarvam_vision_enabled and self.sarvam_api_key
                    and self.sarvam_extraction_mode in ("doc_ai", "chat"))

    @property
    def relay_live(self) -> bool:
        return bool(self.relay_vendor_call_url and self.relay_callback_secret and self.public_base_url)

    @property
    def stt_live(self) -> bool:
        return bool(self.sarvam_stt_enabled and self.sarvam_api_key)

    @property
    def chat_live(self) -> bool:
        return bool(self.sarvam_chat_enabled and self.sarvam_api_key)

    @property
    def tts_live(self) -> bool:
        return bool(self.sarvam_tts_enabled and self.sarvam_api_key)


def load_settings() -> Settings:
    return Settings(
        sarvam_api_key=_str("SARVAM_API_KEY"),
        sarvam_base_url=_str("SARVAM_BASE_URL", "https://api.sarvam.ai"),
        sarvam_chat_model=_str("SARVAM_CHAT_MODEL", "sarvam-105b"),
        sarvam_vision_model=_str("SARVAM_VISION_MODEL", "sarvam-105b"),
        sarvam_extraction_mode=(_str("SARVAM_EXTRACTION_MODE", "doc_ai") or "doc_ai").lower(),
        sarvam_doc_ai_timeout_seconds=_int("SARVAM_DOC_AI_TIMEOUT_SECONDS", 120),
        sarvam_doc_ai_language=_str("SARVAM_DOC_AI_LANGUAGE"),
        sarvam_tts_model=_str("SARVAM_TTS_MODEL", "bulbul:v3"),
        sarvam_tts_speaker=_str("SARVAM_TTS_SPEAKER"),
        sarvam_stt_model=_str("SARVAM_STT_MODEL", "saaras:v4"),
        sarvam_vision_enabled=_bool("SARVAM_VISION_ENABLED", False),
        sarvam_chat_enabled=_bool("SARVAM_CHAT_ENABLED", False),
        sarvam_tts_enabled=_bool("SARVAM_TTS_ENABLED", False),
        sarvam_stt_enabled=_bool("SARVAM_STT_ENABLED", False),
        cashfree_payouts_enabled=_bool("CASHFREE_PAYOUTS_ENABLED", False),
        cashfree_base_url=_str("CASHFREE_BASE_URL", "https://sandbox.cashfree.com"),
        cashfree_payouts_client_id=_str("CASHFREE_PAYOUTS_CLIENT_ID"),
        cashfree_payouts_client_secret=_str("CASHFREE_PAYOUTS_CLIENT_SECRET"),
        cashfree_webhook_secret=_str("CASHFREE_WEBHOOK_SECRET"),
        cashfree_2fa_public_key_path=_str("CASHFREE_2FA_PUBLIC_KEY_PATH"),
        cashfree_secureid_enabled=_bool("CASHFREE_SECUREID_ENABLED", False),
        cashfree_secureid_client_id=_str("CASHFREE_SECUREID_CLIENT_ID"),
        cashfree_secureid_client_secret=_str("CASHFREE_SECUREID_CLIENT_SECRET"),
        cashfree_secureid_api_version=_str("CASHFREE_SECUREID_API_VERSION"),
        relay_vendor_call_url=_str("RELAY_VENDOR_CALL_URL"),
        relay_callback_secret=_str("RELAY_CALLBACK_SECRET"),
        public_base_url=(_str("NIPTA_PUBLIC_URL") or "").rstrip("/") or None,
        demo_call_phone=_str("NIPTA_DEMO_CALL_PHONE"),
        min_field_confidence=_float("NIPTA_MIN_FIELD_CONFIDENCE", 0.85),
        arith_tolerance_paise=_int("NIPTA_ARITH_TOLERANCE_PAISE", 0),
        verification_max_age_days=_int("NIPTA_VERIFICATION_MAX_AGE_DAYS", 30),
        require_live_verification=_bool("NIPTA_REQUIRE_LIVE_VERIFICATION", True),
        approval_ttl_seconds=_int("NIPTA_APPROVAL_TTL_SECONDS", 600),
        agent_max_tool_calls=_int("NIPTA_AGENT_MAX_TOOL_CALLS", 6),
        test_mode=_bool("NIPTA_TEST_MODE", False),
        operator_pin=_str("NIPTA_OPERATOR_PIN", "2468"),
        session_secret=_str("NIPTA_SESSION_SECRET", "dev-only-session-secret-change-me"),
        database_path=_str("NIPTA_DATABASE_PATH", "nipta.db"),
        upload_dir=_str("NIPTA_UPLOAD_DIR", "uploads"),
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def set_settings(settings: Settings) -> None:
    """Used by tests and the seed script to override configuration."""
    global _settings
    _settings = settings


def override(**changes) -> Settings:
    s = replace(get_settings(), **changes)
    set_settings(s)
    return s


def capability_report(s: Settings | None = None) -> dict:
    s = s or get_settings()

    def mode(live: bool, enabled: bool, fallback: str) -> str:
        if live:
            return "LIVE"
        if enabled:
            return "MISCONFIGURED"
        return fallback

    return {
        "sarvam_vision": mode(s.vision_live, s.sarvam_vision_enabled, "OFF_USING_PRE_EXTRACTED_FIXTURES"),
        "sarvam_extraction_mode": s.sarvam_extraction_mode,
        "sarvam_chat": mode(s.chat_live, s.sarvam_chat_enabled, "OFF_USING_KEYWORD_ROUTER"),
        "sarvam_stt": mode(s.stt_live, s.sarvam_stt_enabled, "OFF_TYPED_QUESTIONS_ONLY"),
        "sarvam_tts": mode(s.tts_live, s.sarvam_tts_enabled, "OFF"),
        "cashfree_payouts": mode(s.payouts_live, s.cashfree_payouts_enabled, "OFF_SIMULATED"),
        "cashfree_webhooks": "SIGNATURE_CHECK_READY" if s.cashfree_webhook_secret else "OFF_POLLING_ONLY",
        "cashfree_secure_id": mode(s.secureid_live, s.cashfree_secureid_enabled, "OFF_SEEDED_MOCK"),
        "relay_vendor_call": "LIVE" if s.relay_live else "OFF",
        "cashfree_2fa_signature": ("ON" if s.cashfree_2fa_public_key_path and s.twofa_key_ok
                                   else "KEY_FILE_MISSING" if s.cashfree_2fa_public_key_path else "OFF"),
        "require_live_verification": s.require_live_verification,
        "test_mode": s.test_mode,
    }
