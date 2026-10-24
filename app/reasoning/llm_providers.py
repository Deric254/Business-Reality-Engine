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
        self._client = Anthropic(api_key=api_key, timeout=25.0, max_retries=0)   # fail fast so the next provider is tried at once
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
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=25.0, max_retries=0) if base_url else OpenAI(api_key=api_key, timeout=25.0, max_retries=0)
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


class FallbackProvider(LLMProvider):
    """Tries each provider in order; the first one that answers wins. Raises the last error if all fail."""
    def __init__(self, providers: list[LLMProvider]):
        self._providers = providers

    @property
    def name(self) -> str:
        return "+".join(p.name for p in self._providers)

    def generate_json(self, system_prompt: str, user_content: str, max_tokens: int = 400) -> str:
        last: Exception | None = None
        for p in self._providers:
            try:
                return p.generate_json(system_prompt, user_content, max_tokens)
            except Exception as exc:  # rate limit, outage, bad model name: move on to the next provider
                last = exc
        raise RuntimeError(f"All connected AI providers failed: {last}")


# Sensible current default per provider; a stored model or LLM_MODEL overrides it.
_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-4o-mini",
    "openai_compatible": None,  # must be set explicitly: no safe default for an arbitrary endpoint
}


def build_provider(kind: str, api_key: str, model: str | None, base_url: str | None = None) -> LLMProvider:
    kind = (kind or "").lower()
    model = model or _DEFAULT_MODELS.get(kind)
    if kind == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model)
    if kind == "openai":
        return OpenAIProvider(api_key=api_key, model=model)
    if kind == "openai_compatible":
        if not model:
            raise RuntimeError("A model name is required for this provider")
        if not base_url:
            raise RuntimeError("A base URL is required for this provider")
        return OpenAIProvider(api_key=api_key, model=model, base_url=base_url)
    raise RuntimeError(f"Unknown provider type '{kind}'. Supported: anthropic, openai, openai_compatible")


def _env_provider() -> LLMProvider:
    s = get_settings()
    kind = (s.LLM_PROVIDER or "anthropic").lower()
    key = {"anthropic": s.ANTHROPIC_API_KEY, "openai": s.OPENAI_API_KEY}.get(kind, s.LLM_API_KEY)
    return build_provider(kind, key, s.LLM_MODEL, s.LLM_BASE_URL or None)


def get_provider(db=None, tenant_id: str | None = None) -> LLMProvider:
    """The business's own connected providers (tried in priority order), else the server's env settings."""
    if db is not None and tenant_id:
        from app.core.secrets import decrypt_secret
        from app.models.config import AIProvider
        rows = (db.query(AIProvider).filter(AIProvider.tenant_id == tenant_id, AIProvider.is_active.is_(True))
                .order_by(AIProvider.priority, AIProvider.created_at).all())
        built = []
        for r in rows:
            try:
                built.append(build_provider(r.kind, decrypt_secret(r.api_key), r.model, r.base_url))
            except Exception:
                continue  # an unreadable row must not take the others down
        if built:
            return built[0] if len(built) == 1 else FallbackProvider(built)
    return _env_provider()
