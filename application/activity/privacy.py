from typing import Any


_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "cookies",
    "form_value",
    "password",
    "secret",
    "token",
}


def sanitize_activity_data(
    value: dict[str, Any],
    max_string_length: int = 500,
) -> dict[str, Any]:
    """Remove obvious secrets before hashing or persisting watcher data."""

    def sanitize(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                str(key): sanitize(child)
                for key, child in item.items()
                if str(key).lower() not in _SENSITIVE_KEYS
            }
        if isinstance(item, list):
            return [sanitize(child) for child in item[:100]]
        if isinstance(item, str):
            return item[:max_string_length]
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return str(item)[:max_string_length]

    return sanitize(value)
