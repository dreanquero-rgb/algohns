"""Central configuration, read from the environment.

Secrets never get defaults. A missing credential raises at the point of use
with a message naming the variable, rather than silently degrading to a
half-working client.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

__all__ = ["Settings", "settings", "ConfigError"]

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """Raised when a required setting is absent or invalid."""


def _env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name, default)
    return val.strip() if isinstance(val, str) else val


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # --- Alpaca (paper only; see alpaca_execution.PAPER_HOSTS) ---
    alpaca_key_id: str | None = None
    alpaca_secret_key: str | None = None
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_url: str = "https://data.alpaca.markets"
    allow_live_trading: bool = False  # hard-locked off; see require_paper()

    # --- SEC EDGAR ---
    # SEC requires a descriptive UA with contact info or it returns 403.
    sec_user_agent: str = "algohns-research/1.0 (contact: set SEC_USER_AGENT)"
    sec_rate_limit_per_sec: float = 8.0  # SEC fair-access ceiling is 10/s

    # --- Async workers ---
    redis_url: str = "redis://localhost:6379/0"
    scheduler_backend: str = "apscheduler"  # "celery" | "apscheduler"

    # --- Storage ---
    cache_dir: Path = PROJECT_ROOT / "data" / "cache"
    cache_ttl_seconds: int = 6 * 3600

    @property
    def has_alpaca(self) -> bool:
        return bool(self.alpaca_key_id and self.alpaca_secret_key)

    def require_alpaca(self) -> tuple[str, str]:
        """Return credentials or explain exactly what is missing."""
        missing = [
            name
            for name, val in (
                ("ALPACA_API_KEY", self.alpaca_key_id),
                ("ALPACA_SECRET_KEY", self.alpaca_secret_key),
            )
            if not val
        ]
        if missing:
            raise ConfigError(
                "Credenziali Alpaca mancanti: "
                + ", ".join(missing)
                + ". Impostale nell'ambiente o in un file .env."
            )
        return self.alpaca_key_id, self.alpaca_secret_key  # type: ignore[return-value]

    def require_sec_user_agent(self) -> str:
        """SEC blocks generic user agents; insist on a real contact string."""
        ua = self.sec_user_agent
        if "set SEC_USER_AGENT" in ua or "@" not in ua:
            raise ConfigError(
                "SEC_USER_AGENT deve contenere un contatto reale, es. "
                "'algohns-research/1.0 (nome@dominio.it)'. "
                "Senza questo EDGAR risponde 403."
            )
        return ua


@lru_cache(maxsize=1)
def _load() -> Settings:
    # Load a .env if python-dotenv is available, without requiring it.
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass

    cache_dir = Path(_env("CACHE_DIR") or (PROJECT_ROOT / "data" / "cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        alpaca_key_id=_env("ALPACA_API_KEY"),
        alpaca_secret_key=_env("ALPACA_SECRET_KEY"),
        alpaca_base_url=_env("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
        alpaca_data_url=_env("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"),
        allow_live_trading=False,
        sec_user_agent=_env(
            "SEC_USER_AGENT",
            "algohns-research/1.0 (contact: set SEC_USER_AGENT)",
        ),
        sec_rate_limit_per_sec=float(_env("SEC_RATE_LIMIT", "8.0")),
        redis_url=_env("REDIS_URL", "redis://localhost:6379/0"),
        scheduler_backend=_env("SCHEDULER_BACKEND", "apscheduler"),
        cache_dir=cache_dir,
        cache_ttl_seconds=int(_env("CACHE_TTL_SECONDS", str(6 * 3600))),
    )


settings = _load()
