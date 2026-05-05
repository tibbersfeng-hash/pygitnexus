"""LLM provider: supports OpenAI-compatible HTTP and Anthropic SDK modes."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx


class LLMProviderError(Exception):
    """Raised when LLM API call fails."""


def _load_claude_settings() -> dict:
    """Load model config from ~/.claude/settings.json env block."""
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return {}
    try:
        data = json.loads(settings_path.read_text())
        return data.get("env", {})
    except (json.JSONDecodeError, OSError):
        return {}


def resolve_llm_config(
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> LLMConfig:
    """Resolve LLM config with priority: CLI > ~/.claude/settings.json > env vars > defaults."""
    claude_env = _load_claude_settings()

    # Provider: CLI > env var > auto-detect from URL > "openai"
    prov = provider or os.environ.get("TESTNEXUS_LLM_PROVIDER")
    if not prov:
        # Auto-detect from URL hostname or path
        effective_url = base_url or claude_env.get("ANTHROPIC_BASE_URL") or \
            os.environ.get("ANTHROPIC_BASE_URL") or os.environ.get("TESTNEXUS_LLM_URL", "")
        if effective_url:
            from urllib.parse import urlparse
            parsed = urlparse(effective_url)
            host = parsed.hostname or ""
            # Use anthropic when hostname is api.anthropic.com
            # OR when URL path contains "/anthropic" (DashScope anthropic-compatible endpoint)
            if host == "api.anthropic.com" or "/anthropic" in parsed.path.lower():
                prov = "anthropic"
            else:
                prov = "openai"

    # Base URL: CLI > settings ANTHROPIC_BASE_URL > env TESTNEXUS_LLM_URL > env ANTHROPIC_BASE_URL
    url = (
        base_url
        or os.environ.get("TESTNEXUS_LLM_URL")
        or claude_env.get("ANTHROPIC_BASE_URL")
        or os.environ.get("ANTHROPIC_BASE_URL")
        or ""
    )

    # Apply provider defaults when no explicit value
    key = (
        api_key
        or os.environ.get("TESTNEXUS_API_KEY")
        or claude_env.get("ANTHROPIC_AUTH_TOKEN")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or ""
    )

    # Model: CLI > settings ANTHROPIC_MODEL > env TESTNEXUS_LLM_MODEL > env ANTHROPIC_MODEL
    model_name = (
        model
        or os.environ.get("TESTNEXUS_LLM_MODEL")
        or claude_env.get("ANTHROPIC_MODEL")
        or os.environ.get("ANTHROPIC_MODEL")
        or ""
    )

    # Apply provider defaults when no explicit value
    if prov == "anthropic":
        if not url:
            url = "https://api.anthropic.com"
        if not model_name:
            model_name = "claude-sonnet-4-20250514"
    else:
        if not url:
            url = "https://api.openai.com/v1"
        if not model_name:
            model_name = "gpt-4o-mini"

    return LLMConfig(
        base_url=url,
        api_key=key,
        model=model_name,
        provider=prov,
    )


@dataclass
class LLMConfig:
    """Configuration for LLM provider."""
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    max_tokens: int = 4096
    timeout: int = 600
    provider: str = "openai"  # "openai" | "anthropic"

    @property
    def prompt_hash(self) -> str:
        """Hash of the config for caching/audit."""
        raw = f"{self.provider}:{self.base_url}:{self.model}:{self.temperature}:{self.max_tokens}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


class LLMProvider:
    """LLM chat completions client supporting OpenAI-compatible HTTP and Anthropic SDK."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        if not config.api_key:
            raise LLMProviderError(
                "API key is required. Set TESTNEXUS_API_KEY env var or pass --api-key."
            )
        if config.provider == "anthropic":
            self._mode = "anthropic"
            self._init_anthropic(config)
        else:
            self._mode = "openai"
            self._init_openai(config)

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _init_openai(self, config: LLMConfig) -> None:
        self._client = httpx.Client(
            base_url=config.base_url,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=config.timeout,
        )

    def _init_anthropic(self, config: LLMConfig) -> None:
        try:
            import anthropic
        except ImportError:
            raise LLMProviderError(
                "anthropic package is required for Anthropic mode. "
                "Run: pip install anthropic"
            )
        self._anthropic_client = anthropic.Anthropic(
            api_key=config.api_key,
            timeout=config.timeout,
            base_url=config.base_url if config.base_url != "https://api.openai.com/v1" else None,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(self, system_prompt: str, user_content: str) -> str:
        """Send a chat completion request and return the text response."""
        if self._mode == "anthropic":
            return self._chat_anthropic(system_prompt, user_content)
        return self._chat_openai(system_prompt, user_content)

    def chat_json(self, system_prompt: str, user_content: str) -> dict:
        """Send a chat request and parse the response as JSON."""
        raw = self.chat(system_prompt, user_content)

        # Strip markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)

        cleaned = cleaned.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[len("```json"):].strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise LLMProviderError(
                f"LLM response is not valid JSON: {e}\n"
                f"Response snippet: {cleaned[:500]}"
            )

    def close(self) -> None:
        if self._mode == "openai":
            self._client.close()
        elif self._mode == "anthropic":
            self._anthropic_client.close()

    def __enter__(self) -> "LLMProvider":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    # ------------------------------------------------------------------
    # OpenAI-compatible HTTP mode
    # ------------------------------------------------------------------

    def _chat_openai(self, system_prompt: str, user_content: str) -> str:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }

        last_error = None
        for attempt in range(3):
            try:
                resp = self._client.post("", json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
            except (httpx.HTTPError, KeyError, json.JSONDecodeError) as e:
                last_error = e
                if attempt < 2:
                    time.sleep(2 ** attempt)

        raise LLMProviderError(
            f"LLM API call failed after 3 attempts: {last_error}"
        )

    # ------------------------------------------------------------------
    # Anthropic SDK mode
    # ------------------------------------------------------------------

    def _chat_anthropic(self, system_prompt: str, user_content: str) -> str:
        last_error = None
        for attempt in range(3):
            try:
                message = self._anthropic_client.messages.create(
                    model=self.config.model,
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                    system=system_prompt,
                    messages=[
                        {"role": "user", "content": user_content},
                    ],
                )
                # Extract text from content blocks
                for block in message.content:
                    if block.type == "text":
                        return block.text.strip()
                raise LLMProviderError("No text content in Anthropic response")
            except Exception as e:
                last_error = e
                if attempt < 2:
                    time.sleep(2 ** attempt)

        raise LLMProviderError(
            f"Anthropic API call failed after 3 attempts: {last_error}"
        )
