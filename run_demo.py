import logging
import os
import tkinter as tk

from application import ApplicationController
from application.activity import (
    ActivityCoordinator,
    ActivityPrivacyStore,
    ActivityStore,
    MacOSAccessibilitySource,
    MacOSWindowSource,
)
from application.context import (
    MacOSAccessibilityProvider,
    MacOSContextProvider,
)
from application.presence import SerialPresenceAdapter
from application.providers import ConfigurableLLMClient, ProviderSettings
from application.summary import (
    DailyDataAggregator,
    ManualSummaryService,
    SummaryStore,
)
from application.ui import DashboardApp
from services.ai_desk_api import AIDeskPresenceAPI


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    api = AIDeskPresenceAPI()
    activity_store = ActivityStore(api.database)
    privacy_store = ActivityPrivacyStore()
    privacy_policy = privacy_store.load()
    sources = [MacOSWindowSource(MacOSContextProvider())]
    if os.getenv("AIDESK_ENABLE_ACCESSIBILITY", "").lower() in {
        "1",
        "true",
        "yes",
    }:
        sources.append(MacOSAccessibilitySource(MacOSAccessibilityProvider()))
    collector = ActivityCoordinator(
        api=api,
        sources=sources,
        store=activity_store,
        poll_seconds=10.0,
        persistence_heartbeat_seconds=60.0,
        privacy_policy=privacy_policy,
    )
    controller = ApplicationController(api, collector)
    provider_settings = ProviderSettings()
    summary_store = SummaryStore()
    summary_client = ConfigurableLLMClient(provider_settings)
    summary_service = ManualSummaryService(
        aggregator=DailyDataAggregator(api),
        llm_client=summary_client,
        store=summary_store,
    )
    presence_adapter = SerialPresenceAdapter(
        api,
        port=os.getenv("AIDESK_SERIAL_PORT") or None,
    )

    root = tk.Tk()
    app = DashboardApp(
        root=root,
        api=api,
        controller=controller,
        summary_service=summary_service,
        summary_store=summary_store,
        presence_adapter=presence_adapter,
        provider_settings=provider_settings,
        configurable_llm_client=summary_client,
    )
    app.run()


if __name__ == "__main__":
    main()
