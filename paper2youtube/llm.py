"""OpenAI-compatible chat / vision client with offline-safe fallbacks."""

from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

from .config import Settings


class LLMError(RuntimeError):
    pass


class LLMClient:
    """Thin wrapper over the OpenAI SDK. Works with MiMo / any compatible base URL."""

    def __init__(self, settings: Settings, client: Any | None = None):
        self.settings = settings
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise LLMError("openai package is required for live LLM calls") from exc
            if not self.settings.api_key:
                raise LLMError("No API key. Set PAPER2YOUTUBE_API_KEY or MIMO_API_KEY.")
            self._client = OpenAI(
                api_key=self.settings.api_key,
                base_url=self.settings.base_url,
                timeout=self.settings.request_timeout,
            )
        return self._client

    def chat(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        json_mode: bool = False,
        images: list[str | Path] | None = None,
        temperature: float = 0.4,
    ) -> str:
        """One-shot chat. `images` enables vision when the model supports it."""
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        if images:
            content: list[dict] = [{"type": "text", "text": prompt}]
            for img in images:
                content.append({"type": "image_url", "image_url": {"url": _data_url(img)}})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": prompt})

        kwargs: dict = {
            "model": model or self.settings.model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            # Vision may fail on text-only models — retry without images.
            if images:
                return self.chat(
                    prompt,
                    system=system,
                    model=model,
                    json_mode=json_mode,
                    images=None,
                    temperature=temperature,
                )
            raise LLMError(f"LLM call failed: {exc}") from exc

        text = resp.choices[0].message.content or ""
        return text.strip()

    def chat_json(self, prompt: str, **kwargs: Any) -> Any:
        raw = self.chat(prompt, json_mode=True, **kwargs)
        return extract_json(raw)


def extract_json(text: str) -> Any:
    """Parse JSON from a model reply, tolerating code fences and prose."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # First {...} or [...] block
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"Could not parse JSON from model output: {text[:200]!r}")


def _data_url(path: str | Path) -> str:
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


class ScriptedLLM:
    """Deterministic stub used in unit tests and `--offline` dry runs."""

    def __init__(self, replies: list[str] | None = None, settings: Settings | None = None):
        self.replies = list(replies or [])
        self.calls: list[dict] = []
        self.settings = settings or Settings()

    def chat(self, prompt: str, **kwargs: Any) -> str:
        self.calls.append({"prompt": prompt, **kwargs})
        if self.replies:
            return self.replies.pop(0)
        return "{}"

    def chat_json(self, prompt: str, **kwargs: Any) -> Any:
        return extract_json(self.chat(prompt, **kwargs))