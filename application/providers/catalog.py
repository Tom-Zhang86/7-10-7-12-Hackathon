from dataclasses import dataclass


@dataclass(frozen=True)
class ModelOption:
    id: str
    label: str


@dataclass(frozen=True)
class ProviderOption:
    id: str
    label: str
    models: tuple[ModelOption, ...]
    default_model: str
    key_hint: str
    console_url: str


MODEL_PROVIDERS: tuple[ProviderOption, ...] = (
    ProviderOption(
        id="openai",
        label="OpenAI",
        models=(
            ModelOption("gpt-5.5", "GPT-5.5"),
            ModelOption("gpt-5.4", "GPT-5.4"),
            ModelOption("gpt-5.4-mini", "GPT-5.4 mini（推荐）"),
            ModelOption("gpt-4o", "GPT-4o"),
            ModelOption("gpt-4o-mini", "GPT-4o mini"),
        ),
        default_model="gpt-5.4-mini",
        key_hint="sk-…",
        console_url="https://platform.openai.com/api-keys",
    ),
    ProviderOption(
        id="deepseek",
        label="DeepSeek",
        models=(
            ModelOption("deepseek-v4-flash", "DeepSeek V4 Flash（推荐）"),
            ModelOption("deepseek-v4-pro", "DeepSeek V4 Pro"),
        ),
        default_model="deepseek-v4-flash",
        key_hint="sk-…",
        console_url="https://platform.deepseek.com/api_keys",
    ),
    ProviderOption(
        id="anthropic",
        label="Anthropic",
        models=(
            ModelOption("claude-sonnet-5", "Claude Sonnet 5（推荐）"),
            ModelOption("claude-opus-4-8", "Claude Opus 4.8"),
            ModelOption("claude-sonnet-4-6", "Claude Sonnet 4.6"),
            ModelOption("claude-haiku-4-5", "Claude Haiku 4.5"),
        ),
        default_model="claude-sonnet-5",
        key_hint="sk-ant-…",
        console_url="https://console.anthropic.com/settings/keys",
    ),
    ProviderOption(
        id="google",
        label="Google Gemini",
        models=(
            ModelOption("gemini-3.6-flash", "Gemini 3.6 Flash（推荐）"),
            ModelOption("gemini-3.5-flash", "Gemini 3.5 Flash"),
            ModelOption("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"),
        ),
        default_model="gemini-3.6-flash",
        key_hint="AIza…",
        console_url="https://aistudio.google.com/app/apikey",
    ),
)


def get_provider(provider_id: str) -> ProviderOption:
    for provider in MODEL_PROVIDERS:
        if provider.id == provider_id:
            return provider
    raise ValueError(f"Unsupported model provider: {provider_id}")
