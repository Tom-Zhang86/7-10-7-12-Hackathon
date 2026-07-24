import logging
import os
import tkinter as tk

from application import ApplicationController
from application.config import load_application_environment
from application.context import ContextCollector, MacOSContextProvider
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
    load_application_environment()

    api = AIDeskPresenceAPI()
    collector = ContextCollector(api, MacOSContextProvider())
    controller = ApplicationController(api, collector)
    summary_store = SummaryStore()
    provider_settings = ProviderSettings()
    configurable_llm_client = ConfigurableLLMClient(provider_settings)
    summary_service = ManualSummaryService(
        aggregator=DailyDataAggregator(api),
        llm_client=configurable_llm_client,
        store=summary_store,
    )
    serial_adapter = SerialPresenceAdapter(
        api=api,
        port=os.getenv("AI_DESK_SERIAL_PORT") or None,
        baudrate=int(os.getenv("AI_DESK_SERIAL_BAUD", "115200")),
    )

    root = tk.Tk()
    app = DashboardApp(
        root=root,
        api=api,
        controller=controller,
        summary_service=summary_service,
        summary_store=summary_store,
        presence_adapter=serial_adapter,
        provider_settings=provider_settings,
        configurable_llm_client=configurable_llm_client,
    )
    app.run()


if __name__ == "__main__":
    main()
