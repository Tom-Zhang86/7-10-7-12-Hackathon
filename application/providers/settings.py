from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
from typing import Protocol

from application.providers.catalog import get_provider


@dataclass(frozen=True)
class ProviderSelection:
    provider_id: str = "openai"
    model_id: str = "gpt-5.4-mini"


class SecretStore(Protocol):
    def get(self, provider_id: str) -> str | None: ...

    def set(self, provider_id: str, api_key: str) -> None: ...

    def delete(self, provider_id: str) -> None: ...


class MacOSKeychain:
    """Store provider API keys in the signed-in user's macOS Keychain."""

    SERVICE = "AI Desk API Key"

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/usr/bin/security", *arguments],
            capture_output=True,
            text=True,
            check=False,
        )

    def get(self, provider_id: str) -> str | None:
        result = self._run(
            "find-generic-password",
            "-a",
            provider_id,
            "-s",
            self.SERVICE,
            "-w",
        )
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        return value or None

    def set(self, provider_id: str, api_key: str) -> None:
        value = api_key.strip()
        if not value:
            raise ValueError("API key cannot be empty.")
        result = self._run(
            "add-generic-password",
            "-U",
            "-a",
            provider_id,
            "-s",
            self.SERVICE,
            "-w",
            value,
        )
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip() or "Could not save API key to Keychain."
            )

    def delete(self, provider_id: str) -> None:
        self._run(
            "delete-generic-password",
            "-a",
            provider_id,
            "-s",
            self.SERVICE,
        )


class ProviderSettings:
    """Persist non-secret selection separately from Keychain credentials."""

    def __init__(
        self,
        path: Path | None = None,
        secrets: SecretStore | None = None,
    ) -> None:
        self.path = path or (
            Path.home()
            / "Library"
            / "Application Support"
            / "AI Desk"
            / "settings.json"
        )
        self.secrets = secrets or MacOSKeychain()

    def load(self) -> ProviderSelection:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            selection = ProviderSelection(
                provider_id=str(value["provider_id"]),
                model_id=str(value["model_id"]),
            )
            provider = get_provider(selection.provider_id)
            if selection.model_id not in {model.id for model in provider.models}:
                return ProviderSelection(
                    provider.id,
                    provider.default_model,
                )
            return selection
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return ProviderSelection()

    def save(self, selection: ProviderSelection) -> None:
        provider = get_provider(selection.provider_id)
        if selection.model_id not in {model.id for model in provider.models}:
            raise ValueError("The selected model does not belong to the provider.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(asdict(selection), indent=2),
            encoding="utf-8",
        )

    def get_api_key(self, provider_id: str) -> str | None:
        get_provider(provider_id)
        return self.secrets.get(provider_id)

    def save_api_key(self, provider_id: str, api_key: str) -> None:
        get_provider(provider_id)
        self.secrets.set(provider_id, api_key)

    def delete_api_key(self, provider_id: str) -> None:
        get_provider(provider_id)
        self.secrets.delete(provider_id)
