from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_application_environment(
    env_path: str | Path | None = None,
) -> bool:
    """Load project configuration without overriding process variables."""

    path = Path(env_path) if env_path is not None else PROJECT_ROOT / ".env"
    return load_dotenv(dotenv_path=path, override=False)
