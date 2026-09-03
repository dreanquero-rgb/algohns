"""Centralised configuration.

All runtime configuration is read from environment variables (optionally loaded
from a local ``.env`` file). Nothing secret is ever hard-coded. The settings
object is cached so every module shares the same instance.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# ---------------------------------------------------------------------------
# Optional .env loading (python-dotenv is a light, optional dependency).
# ---------------------------------------------------------------------------
try:  # pragma: no cover - trivial import guard
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "algohns" / "data"
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Immutable settings snapshot for the whole platform."""

    # --- Alpaca (Module 2) ---------------------------------------------------
    alpaca_api_key: str = field(default_factory=lambda: os.getenv("ALPACA_API_KEY", ""))
    alpaca_secret_key: str = field(default_factory=lambda: os.getenv("ALPACA_SECRET_KEY", ""))
    # HARD RULE inherited from Algohns V11: paper trading only.
    alpaca_paper: bool = field(default_factory=lambda: _env_bool("ALPACA_PAPER", True))
    alpaca_base_url: str = field(
        default_factory=lambda: os.getenv(
            "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
        )
    )
    alpaca_data_url: str = field(
        default_factory=lambda: os.getenv(
            "ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"
        )
    )

    # --- Async worker (Module 2) --------------------------------------------
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    celery_broker_url: str = field(
        default_factory=lambda: os.getenv("CELERY_BROKER_URL", "")
    )
    celery_result_backend: str = field(
        default_factory=lambda: os.getenv("CELERY_RESULT_BACKEND", "")
    )

    # --- SEC EDGAR (Modules 4 & 5) ------------------------------------------
    # The SEC requires a descriptive User-Agent with a contact email.
    sec_user_agent: str = field(
        default_factory=lambda: os.getenv(
            "SEC_USER_AGENT", "Algohns Research contact@example.com"
        )
    )

    # --- Tax defaults (Module 1) --------------------------------------------
    default_tax_residence: str = field(
        default_factory=lambda: os.getenv("DEFAULT_TAX_RESIDENCE", "IT")
    )

    # --- Paths ---------------------------------------------------------------
    data_dir: Path = DATA_DIR
    cache_dir: Path = CACHE_DIR

    # ------------------------------------------------------------------ helpers
    @property
    def alpaca_configured(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_secret_key)

    @property
    def celery_broker(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def celery_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    def masked(self) -> dict[str, str]:
        """Return a safe-to-display view (secrets masked)."""

        def mask(v: str) -> str:
            if not v:
                return "—"
            return v[:4] + "…" + v[-2:] if len(v) > 6 else "•••"

        return {
            "ALPACA_API_KEY": mask(self.alpaca_api_key),
            "ALPACA_SECRET_KEY": mask(self.alpaca_secret_key),
            "ALPACA_PAPER": str(self.alpaca_paper),
            "ALPACA_BASE_URL": self.alpaca_base_url,
            "REDIS_URL": self.redis_url,
            "SEC_USER_AGENT": self.sec_user_agent,
            "DEFAULT_TAX_RESIDENCE": self.default_tax_residence,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the shared, cached settings instance."""
    return Settings()
