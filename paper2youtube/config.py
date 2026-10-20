"""Runtime configuration via environment variables (all optional)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


# Generic OpenAI-compatible default. Local / private endpoints MUST come from env
# (PAPER2YOUTUBE_BASE_URL) — never hardcode internal IPs or hostnames here.
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_SMALL_MODEL = "gpt-4o-mini"
DEFAULT_TTS_VOICE = "zh-TW-HsiaoChenNeural"
DEFAULT_LANG = "zh-TW"


def _env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        val = os.environ.get(name)
        if val:
            return val
    return default


@dataclass
class Settings:
    """All knobs the pipeline needs. Prefer env overrides over hardcoding."""

    api_key: str | None = None
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    small_model: str = DEFAULT_SMALL_MODEL
    vlm_model: str | None = None
    github_token: str | None = None
    tts_voice: str = DEFAULT_TTS_VOICE
    tts_backend: str = "edge"  # edge | silent
    lang: str = DEFAULT_LANG
    request_timeout: float = 120.0
    max_figure_passes: int = 40
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.vlm_model:
            self.vlm_model = self.model
        if not self.api_key:
            self.api_key = _env(
                "PAPER2YOUTUBE_API_KEY",
                "OPENAI_API_KEY",
                "MIMO_API_KEY",
            )

    @property
    def has_llm(self) -> bool:
        return bool(self.api_key)

    @property
    def has_github(self) -> bool:
        # Anonymous GitHub API works with low rate limits; token just helps.
        return True


def load_settings(**overrides: object) -> Settings:
    """Build Settings from environment, then apply keyword overrides."""
    kwargs: dict = {
        "api_key": _env("PAPER2YOUTUBE_API_KEY", "OPENAI_API_KEY", "MIMO_API_KEY"),
        "base_url": _env("PAPER2YOUTUBE_BASE_URL", "OPENAI_BASE_URL", default=DEFAULT_BASE_URL),
        "model": _env("PAPER2YOUTUBE_MODEL", "OPENAI_MODEL", default=DEFAULT_MODEL),
        "small_model": _env("PAPER2YOUTUBE_SMALL_MODEL", default=DEFAULT_SMALL_MODEL),
        "vlm_model": _env("PAPER2YOUTUBE_VLM_MODEL"),
        "github_token": _env("PAPER2YOUTUBE_GITHUB_TOKEN", "GITHUB_TOKEN"),
        "tts_voice": _env("PAPER2YOUTUBE_TTS_VOICE", default=DEFAULT_TTS_VOICE),
        "tts_backend": _env("PAPER2YOUTUBE_TTS_BACKEND", default="edge"),
        "lang": _env("PAPER2YOUTUBE_LANG", default=DEFAULT_LANG),
        "request_timeout": float(_env("PAPER2YOUTUBE_TIMEOUT", default="120") or "120"),
        "max_figure_passes": int(_env("PAPER2YOUTUBE_MAX_FIGURES", default="40") or "40"),
    }
    clean = {k: v for k, v in kwargs.items() if v is not None or k in ("api_key", "vlm_model", "github_token")}
    clean.update(overrides)  # type: ignore[arg-type]
    return Settings(**clean)  # type: ignore[arg-type]


def ensure_workdir(path: str | Path) -> Path:
    """Create and return the literature work directory."""
    workdir = Path(path).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    for sub in ("source", "figures", "slides", "audio"):
        (workdir / sub).mkdir(exist_ok=True)
    return workdir