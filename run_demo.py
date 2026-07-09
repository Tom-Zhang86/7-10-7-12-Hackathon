import logging
import tkinter as tk

from application import ApplicationController
from application.context import ContextCollector, MacOSContextProvider
from application.summary import (
    DailyDataAggregator,
    ManualSummaryService,
    OpenAIResponsesClient,
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
    collector = ContextCollector(api, MacOSContextProvider())
    controller = ApplicationController(api, collector)
    summary_store = SummaryStore()
    summary_service = ManualSummaryService(
        aggregator=DailyDataAggregator(api),
        llm_client=OpenAIResponsesClient(),
        store=summary_store,
    )

    root = tk.Tk()
    app = DashboardApp(
        root=root,
        api=api,
        controller=controller,
        summary_service=summary_service,
        summary_store=summary_store,
    )
    app.run()


if __name__ == "__main__":
    main()
