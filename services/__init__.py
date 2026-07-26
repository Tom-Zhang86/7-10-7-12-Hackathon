from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.ai_desk_api import AIDeskPresenceAPI
    from services.stats_service import StatsService

__all__ = ["AIDeskPresenceAPI", "StatsService"]


def __getattr__(name: str):
    if name == "AIDeskPresenceAPI":
        from services.ai_desk_api import AIDeskPresenceAPI

        return AIDeskPresenceAPI
    if name == "StatsService":
        from services.stats_service import StatsService

        return StatsService
    raise AttributeError(f"module 'services' has no attribute {name!r}")
