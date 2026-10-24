"""The cloud model providers Will It Run can call, and how a cloud model is named.

A cloud model goes wherever an LM Studio model key goes (a workflow slot, a chat, an
investigation), spelled ``cloud:<provider>/<model>``: ``cloud:anthropic/claude-opus-5-5``,
``cloud:deepseek/deepseek-v4-pro``. Nothing is loaded for it and no VRAM is spent; the
gateway hands it out at once and the request goes over the Internet with the user's key.

Three of the four providers speak the OpenAI chat-completions dialect (OpenAI itself,
DeepSeek, and Gemini through Google's compatibility endpoint). Claude is called through
Anthropic's own SDK, because tool use with thinking has rules the compatibility layers
do not keep (see ``anthropic_wire``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

PREFIX = "cloud:"


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    # "openai": chat completions over HTTP. "anthropic": the Anthropic SDK.
    wire: str
    base_url: str
    # Environment variables read when no key is saved in the app, in order.
    env_keys: tuple[str, ...]
    # Where a person gets a key.
    console_url: str
    # Shown first in the model list when the provider has it, best first. The live list decides.
    preferred: tuple[str, ...] = ()
    # Model ids the provider lists that cannot hold a conversation (embeddings, images, audio).
    excluded: tuple[str, ...] = ()
    # Context window by model-id prefix, longest prefix first, for providers whose list does not say.
    contexts: tuple[tuple[str, int], ...] = ()
    default_context: int = 128_000
    # Whether the provider's chat models accept pictures in a message.
    vision: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)


PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in (
        Provider(
            "anthropic", "Claude (Anthropic)", "anthropic", "https://api.anthropic.com",
            ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"), "https://platform.claude.com/settings/keys",
            preferred=("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"),
            default_context=200_000,
        ),
        Provider(
            "openai", "ChatGPT (OpenAI)", "openai", "https://api.openai.com/v1",
            ("OPENAI_API_KEY",), "https://platform.openai.com/api-keys",
            preferred=("gpt-5.5", "gpt-5.5-mini", "gpt-5", "gpt-5-mini", "gpt-4.1"),
            excluded=("audio", "realtime", "transcribe", "tts", "image", "embedding", "moderation", "search",
                      "instruct", "dall-e", "whisper", "davinci", "babbage", "codex-mini", "-pro"),
            contexts=(("gpt-5.5", 1_050_000), ("gpt-5", 400_000), ("gpt-4.1", 1_000_000), ("o", 200_000), ("gpt-4o", 128_000)),
        ),
        Provider(
            "deepseek", "DeepSeek", "openai", "https://api.deepseek.com",
            ("DEEPSEEK_API_KEY",), "https://platform.deepseek.com/api_keys",
            preferred=("deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"),
            contexts=(("deepseek-v4", 1_000_000),),
            vision=False,
        ),
        Provider(
            "gemini", "Gemini (Google)", "openai", "https://generativelanguage.googleapis.com/v1beta/openai",
            ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "https://aistudio.google.com/apikey",
            preferred=("gemini-3.8-pro", "gemini-3.8-flash", "gemini-3.1-pro-preview", "gemini-3-pro-preview", "gemini-2.5-pro"),
            excluded=("embedding", "image", "imagen", "veo", "aqa", "tts", "audio", "live", "robotics", "learnlm", "gemma"),
            default_context=1_000_000,
        ),
    )
}


def is_cloud(model_key: str | None) -> bool:
    return bool(model_key) and str(model_key).startswith(PREFIX)


def make_key(provider: str, model: str) -> str:
    return f"{PREFIX}{provider}/{model}"


def parse_key(model_key: str) -> tuple[Provider, str]:
    """``cloud:deepseek/deepseek-v4-pro`` -> (the DeepSeek provider, ``deepseek-v4-pro``)."""
    if not is_cloud(model_key):
        raise ValueError(f"Not a cloud model: {model_key}")
    rest = model_key[len(PREFIX):]
    provider_id, _, model = rest.partition("/")
    provider = PROVIDERS.get(provider_id)
    if provider is None or not model:
        raise ValueError(f"Unknown cloud model: {model_key}")
    return provider, model


def chat_model(provider: Provider, model_id: str) -> bool:
    """Whether a model the provider lists can hold a conversation."""
    lowered = model_id.lower()
    return not any(word in lowered for word in provider.excluded)


def context_of(provider: Provider, model_id: str, listed: int | None = None) -> int:
    """The context window of a model: what the provider listed, else the table, else the provider's default."""
    if listed:
        return int(listed)
    for prefix, tokens in sorted(provider.contexts, key=lambda item: -len(item[0])):
        if model_id.startswith(prefix):
            return tokens
    return provider.default_context


def label_of(model_key: str) -> str:
    """What a person reads for a cloud model: ``deepseek-v4-pro · DeepSeek``."""
    try:
        provider, model = parse_key(model_key)
    except ValueError:
        return model_key
    return f"{model} · {provider.label}"
