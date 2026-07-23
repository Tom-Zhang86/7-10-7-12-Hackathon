import logging
import tkinter as tk

from application import ApplicationController
from application.activity import (
    ActivityCoordinator,
    ActivityFusionService,
    ActivityPrivacyStore,
    ActivityStore,
    MacOSAccessibilitySource,
    MacOSWindowSource,
)
from application.classification import (
    ActivityClassificationService,
    ConfigurableClassificationClient,
)
from application.context import (
    MacOSAccessibilityProvider,
    MacOSContextProvider,
)
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
    collector = ActivityCoordinator(
        api=api,
        sources=[
            MacOSWindowSource(MacOSContextProvider()),
            MacOSAccessibilitySource(MacOSAccessibilityProvider()),
        ],
        store=activity_store,
        privacy_policy=privacy_policy,
    )
    controller = ApplicationController(api, collector)
    provider_settings = ProviderSettings()
    classification_service = ActivityClassificationService(
        store=activity_store,
        remote_client=ConfigurableClassificationClient(provider_settings),
        allow_remote=privacy_policy.allow_remote_classification,
    )
    activity_service = ActivityFusionService(
        api,
        activity_store,
        classifier=classification_service,
    )
    summary_store = SummaryStore()
    summary_client = ConfigurableLLMClient(provider_settings)
    summary_service = ManualSummaryService(
        aggregator=DailyDataAggregator(api),
        llm_client=summary_client,
        store=summary_store,
    )

    root = tk.Tk()
    app = DashboardApp(
        root=root,
        api=api,
        controller=controller,
        summary_service=summary_service,
        summary_store=summary_store,
        activity_service=activity_service,
        privacy_policy=privacy_policy,
        privacy_store=privacy_store,
        provider_settings=provider_settings,
        provider_validator=summary_client.validate,
    )
    app.run()


if __name__ == "__main__":
    main()
