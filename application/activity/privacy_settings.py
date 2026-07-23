from dataclasses import dataclass, field
import json
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class ActivityPrivacyPolicy:
    paused: bool = False
    allow_remote_classification: bool = False
    excluded_apps: set[str] = field(default_factory=set)
    excluded_hosts: set[str] = field(default_factory=set)

    def allows(self, data: dict) -> bool:
        if self.paused or bool(data.get("incognito")):
            return False
        app = str(data.get("app") or "").strip().lower()
        if app and app in {value.lower() for value in self.excluded_apps}:
            return False
        url = str(data.get("url") or "")
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        return not any(
            host == excluded.lower()
            or host.endswith(f".{excluded.lower()}")
            for excluded in self.excluded_hosts
        )


class ActivityPrivacyStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (
            Path.home()
            / "Library"
            / "Application Support"
            / "AI Desk"
            / "activity-privacy.json"
        )

    def load(self) -> ActivityPrivacyPolicy:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return ActivityPrivacyPolicy(
                paused=bool(value.get("paused", False)),
                allow_remote_classification=bool(
                    value.get("allow_remote_classification", False)
                ),
                excluded_apps=set(value.get("excluded_apps", [])),
                excluded_hosts=set(value.get("excluded_hosts", [])),
            )
        except (FileNotFoundError, TypeError, ValueError, json.JSONDecodeError):
            return ActivityPrivacyPolicy()

    def save(self, policy: ActivityPrivacyPolicy) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "paused": policy.paused,
                    "allow_remote_classification": (
                        policy.allow_remote_classification
                    ),
                    "excluded_apps": sorted(policy.excluded_apps),
                    "excluded_hosts": sorted(policy.excluded_hosts),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
