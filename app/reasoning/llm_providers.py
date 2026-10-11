"""
LLM provider abstraction (Section 28-30 principle applied to vendor choice
too: the reasoning layer shouldn't be welded to one provider).

Anything calling into this module (llm_reasoning.py) only ever sees
generate_json() — it doesn't know or care whether the call went to
Anthropic, OpenAI, or a self-hosted OpenAI-compatible endpoint. Switching
providers is a config change (LLM_PROVIDER env var), not a code change.

Adding a new provider: subclass LLMProvider, implement generate_json(),
register it in get_provider(). That's the whole contract.
"""
from abc import ABC, abstractmethod
from app.core.config import get_settings


class LLMProvider(ABC):
    @abstractmethod
    def generate_json(self, system_prompt: str, user_content: str, max_tokens: int = 400) -> str:
        """Returns the raw text response — caller is responsible for JSON parsing/validation."""
        raise NotImplementedError

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        from anthropic import Anthropic
        self._client = Anthropic(api_key=api_key)
        self._model = model

    @property
    def name(self) -> str:
        return f"anthropic:{self._model}"

    def generate_json(self, system_prompt: str, user_content: str, max_tokens: int = 400) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        return "".join(block.text for block in response.content if hasattr(block, "text"))


class OpenAIProvider(LLMProvider):
    """
    Also handles any OpenAI-compatible endpoint (Groq, Together, DeepSeek,
    local Ollama's /v1 endpoint, etc.) via base_url — same wire protocol,
    same SDK, just a different host and API key.
    """
    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        if not api_key:
            key_name = "OPENAI_API_KEY" if not base_url else "LLM_API_KEY"
            raise RuntimeError(f"{key_name} is not configured")
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
        self._model = model
        self._base_url = base_url

    @property
    def name(self) -> str:
        return f"openai_compatible:{self._model}" if self._base_url else f"openai:{self._model}"

    def generate_json(self, system_prompt: str, user_content: str, max_tokens: int = 400) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        return response.choices[0].message.content or ""


# Sensible current default per provider — callers can still override via
# LLM_MODEL in settings.
_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o-mini",
    "openai_compatible": None,  # must be set explicitly — no safe default for an arbitrary endpoint
}


def get_provider() -> LLMProvider:
    settings = get_settings()
    provider_name = (settings.LLM_PROVIDER or "anthropic").lower()
    model = settings.LLM_MODEL or _DEFAULT_MODELS.get(provider_name)

    if provider_name == "anthropic":
        return AnthropicProvider(api_key=settings.ANTHROPIC_API_KEY, model=model)

    if provider_name == "openai":
        return OpenAIProvider(api_key=settings.OPENAI_API_KEY, model=model)

    if provider_name == "openai_compatible":
        if not model:
            raise RuntimeError("LLM_MODEL must be set when LLM_PROVIDER=openai_compatible")
        if not settings.LLM_BASE_URL:
            raise RuntimeError("LLM_BASE_URL must be set when LLM_PROVIDER=openai_compatible")
        return OpenAIProvider(api_key=settings.LLM_API_KEY, model=model, base_url=settings.LLM_BASE_URL)

    raise RuntimeError(
        f"Unknown LLM_PROVIDER '{provider_name}'. Supported: anthropic, openai, openai_compatible"
    )
