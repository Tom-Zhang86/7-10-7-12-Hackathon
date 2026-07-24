from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import tkinter as tk
from tkinter import font as tkfont, messagebox
from tkinter import ttk
from typing import Any

from application.providers import get_provider
from application.summary.models import SummaryGeneration
from application.ui.provider_settings import ProviderSettingsDialog
from application.ui.presentation import (
    build_timeline_rows,
    format_duration,
    format_summary,
    present_status,
)
from events.event_types import PresenceDetected, PresenceLost
from models.state import PresenceState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DashboardSnapshot:
    """Database-backed values fetched away from Tkinter's event thread."""

    state: Any
    stats: dict[str, Any]
    timeline_rows: tuple[Any, ...] | None


@dataclass(frozen=True)
class APIConnectionStatus:
    state: str
    provider_label: str
    detail: str = ""


def probe_api_connection(
    provider_settings: Any,
    configurable_llm_client: Any,
) -> APIConnectionStatus:
    selection = provider_settings.load()
    provider = get_provider(selection.provider_id)
    try:
        api_key = provider_settings.get_api_key(selection.provider_id)
    except Exception as exc:
        return APIConnectionStatus("error", provider.label, str(exc))
    if not api_key:
        return APIConnectionStatus("disconnected", provider.label)
    try:
        configurable_llm_client.validate(
            selection.provider_id,
            api_key,
            timeout_seconds=8.0,
        )
    except Exception as exc:
        return APIConnectionStatus("error", provider.label, str(exc))
    return APIConnectionStatus("connected", provider.label)


def load_dashboard_snapshot(
    api: Any,
    *,
    include_timeline: bool,
) -> DashboardSnapshot:
    """Read dashboard data without touching any Tkinter widgets."""

    timeline_rows = None
    if include_timeline:
        timeline_rows = tuple(build_timeline_rows(api.get_today_timeline()))
    return DashboardSnapshot(
        state=api.get_current_state(),
        stats=api.get_today_stats(),
        timeline_rows=timeline_rows,
    )


class DashboardApp:
    """A restrained desktop dashboard for status, timeline, and daily summary."""

    BACKGROUND = "#F5F5F3"
    PANEL = "#FFFFFF"
    TEXT = "#202020"
    MUTED = "#737373"
    BORDER = "#E3E3DF"

    def __init__(
        self,
        root: tk.Tk,
        api: Any,
        controller: Any,
        summary_service: Any,
        summary_store: Any,
        presence_adapter: Any | None = None,
        provider_settings: Any | None = None,
        configurable_llm_client: Any | None = None,
    ) -> None:
        self.root = root
        self.api = api
        self.controller = controller
        self.summary_service = summary_service
        self.summary_store = summary_store
        self.presence_adapter = presence_adapter
        self.provider_settings = provider_settings
        self.configurable_llm_client = configurable_llm_client
        self._summary_future: Future[SummaryGeneration] | None = None
        self._refresh_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="dashboard-refresh",
        )
        self._api_status_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="api-status",
        )
        self._refresh_future: Future[DashboardSnapshot] | None = None
        self._api_status_future: Future[APIConnectionStatus] | None = None
        self._timeline_refresh_requested = True
        self._timeline_refresh_ticks = 0
        self._timeline_signature: tuple[tuple[str, str, str], ...] | None = None
        self._displayed_state = PresenceState.IDLE
        self._presence_change_pending = False
        self._resetting = False
        self._closing = False

        self.status_var = tk.StringVar(value="● 空闲")
        self.sensor_var = tk.StringVar(value="传感器：等待连接")
        self.api_status_var = tk.StringVar(value="API：检查中")
        self.work_var = tk.StringVar(value="00:00:00")
        self.focus_var = tk.StringVar(value="00:00:00")
        self.break_var = tk.StringVar(value="0")
        self.summary_meta_var = tk.StringVar(value="尚未生成")
        self.ai_provider_var = tk.StringVar(value="AI：未连接")

        self._configure_window()
        self._configure_styles()
        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def start(self) -> None:
        self.controller.start()
        if self.presence_adapter is not None:
            self.presence_adapter.start()
        self._load_saved_summary()
        self._request_api_status_refresh()
        self._request_dashboard_refresh()

    def run(self) -> None:
        self.start()
        self.root.mainloop()

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            if self.presence_adapter is not None:
                self.presence_adapter.stop()
            self.controller.stop(stop_runtime=False)
            self.summary_service.close()
            self._refresh_executor.shutdown(wait=False, cancel_futures=True)
            self._api_status_executor.shutdown(
                wait=False,
                cancel_futures=True,
            )
            self.api.close()
        finally:
            self.root.destroy()

    def _configure_window(self) -> None:
        self.root.title("AI Desk")
        self.root.geometry("1040x700")
        self.root.minsize(860, 600)
        self.root.configure(background=self.BACKGROUND)

    def _configure_styles(self) -> None:
        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(family="Helvetica Neue", size=12)
        text_font = tkfont.nametofont("TkTextFont")
        text_font.configure(family="Helvetica Neue", size=12)

        style = ttk.Style(self.root)
        style.configure(
            "Dashboard.Treeview",
            background=self.PANEL,
            fieldbackground=self.PANEL,
            foreground=self.TEXT,
            borderwidth=0,
            rowheight=30,
            font=("Helvetica Neue", 11),
        )
        style.configure(
            "Dashboard.Treeview.Heading",
            background=self.PANEL,
            foreground=self.MUTED,
            borderwidth=0,
            font=("Helvetica Neue", 10),
        )
        style.map(
            "Dashboard.Treeview",
            background=[("selected", "#E9ECE8")],
            foreground=[("selected", self.TEXT)],
        )

    def _build_layout(self) -> None:
        shell = tk.Frame(self.root, bg=self.BACKGROUND)
        shell.pack(fill="both", expand=True, padx=28, pady=24)

        header = tk.Frame(shell, bg=self.BACKGROUND)
        header.pack(fill="x")
        tk.Label(
            header,
            text="AI Desk",
            bg=self.BACKGROUND,
            fg=self.TEXT,
            font=("Helvetica Neue", 24, "bold"),
        ).pack(side="left")
        self.status_label = tk.Label(
            header,
            textvariable=self.status_var,
            bg=self.BACKGROUND,
            fg=self.MUTED,
            font=("Helvetica Neue", 12, "bold"),
        )
        self.status_label.pack(side="right", pady=(8, 0))
        self.pause_button = ttk.Button(
            header,
            text="开始工作",
            command=self._toggle_pause,
        )
        self.pause_button.pack(side="right", padx=(0, 14), pady=(4, 0))
        self.clear_button = ttk.Button(
            header,
            text="清除全部",
            command=self._confirm_clear_all,
        )
        self.clear_button.pack(side="right", padx=(0, 14), pady=(4, 0))
        if (
            self.provider_settings is not None
            and self.configurable_llm_client is not None
        ):
            ttk.Button(
                header,
                text="AI 设置",
                command=self._open_provider_settings,
            ).pack(side="right", padx=(0, 14), pady=(4, 0))
            tk.Label(
                header,
                textvariable=self.ai_provider_var,
                bg=self.BACKGROUND,
                fg=self.MUTED,
                font=("Helvetica Neue", 10),
            ).pack(side="right", padx=(0, 12), pady=(8, 0))
            self._refresh_provider_label()
            self.api_status_label = tk.Label(
                header,
                textvariable=self.api_status_var,
                bg=self.BACKGROUND,
                fg=self.MUTED,
                font=("Helvetica Neue", 10, "bold"),
            )
            self.api_status_label.pack(
                side="right",
                padx=(0, 12),
                pady=(8, 0),
            )
        self.sensor_label = tk.Label(
            header,
            textvariable=self.sensor_var,
            bg=self.BACKGROUND,
            fg=self.MUTED,
            font=("Helvetica Neue", 10),
        )
        self.sensor_label.pack(side="right", padx=(0, 18), pady=(8, 0))

        stats = tk.Frame(shell, bg=self.BACKGROUND)
        stats.pack(fill="x", pady=(22, 18))
        self._stat_card(stats, "今日工作", self.work_var).pack(
            side="left",
            fill="x",
            expand=True,
        )
        self._stat_card(stats, "最长专注", self.focus_var).pack(
            side="left",
            fill="x",
            expand=True,
            padx=12,
        )
        self._stat_card(stats, "休息次数", self.break_var).pack(
            side="left",
            fill="x",
            expand=True,
        )

        content = tk.Frame(shell, bg=self.BACKGROUND)
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=3, uniform="content")
        content.grid_columnconfigure(1, weight=2, uniform="content")
        content.grid_rowconfigure(0, weight=1)

        timeline_panel = self._panel(content)
        timeline_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        summary_panel = self._panel(content)
        summary_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        self._build_timeline(timeline_panel)
        self._build_summary(summary_panel)

    def _stat_card(
        self,
        parent: tk.Widget,
        label: str,
        value: tk.StringVar,
    ) -> tk.Frame:
        card = tk.Frame(
            parent,
            bg=self.PANEL,
            highlightbackground=self.BORDER,
            highlightthickness=1,
            padx=18,
            pady=14,
        )
        tk.Label(
            card,
            text=label,
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Helvetica Neue", 10),
        ).pack(anchor="w")
        tk.Label(
            card,
            textvariable=value,
            bg=self.PANEL,
            fg=self.TEXT,
            font=("Helvetica Neue", 20, "bold"),
        ).pack(anchor="w", pady=(6, 0))
        return card

    def _panel(self, parent: tk.Widget) -> tk.Frame:
        return tk.Frame(
            parent,
            bg=self.PANEL,
            highlightbackground=self.BORDER,
            highlightthickness=1,
            padx=18,
            pady=16,
        )

    def _build_timeline(self, panel: tk.Frame) -> None:
        tk.Label(
            panel,
            text="时间线",
            bg=self.PANEL,
            fg=self.TEXT,
            font=("Helvetica Neue", 15, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        tree_frame = tk.Frame(panel, bg=self.PANEL)
        tree_frame.pack(fill="both", expand=True)
        self.timeline_tree = ttk.Treeview(
            tree_frame,
            columns=("time", "category", "detail"),
            show="headings",
            style="Dashboard.Treeview",
            selectmode="browse",
        )
        self.timeline_tree.heading("time", text="时间")
        self.timeline_tree.heading("category", text="类型")
        self.timeline_tree.heading("detail", text="内容")
        self.timeline_tree.column("time", width=58, stretch=False)
        self.timeline_tree.column("category", width=58, stretch=False)
        self.timeline_tree.column("detail", width=280, stretch=True)

        scrollbar = ttk.Scrollbar(
            tree_frame,
            orient="vertical",
            command=self.timeline_tree.yview,
        )
        self.timeline_tree.configure(yscrollcommand=scrollbar.set)
        self.timeline_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _build_summary(self, panel: tk.Frame) -> None:
        heading = tk.Frame(panel, bg=self.PANEL)
        heading.pack(fill="x", pady=(0, 12))
        tk.Label(
            heading,
            text="今日总结",
            bg=self.PANEL,
            fg=self.TEXT,
            font=("Helvetica Neue", 15, "bold"),
        ).pack(side="left")
        tk.Label(
            heading,
            textvariable=self.summary_meta_var,
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Helvetica Neue", 9),
        ).pack(side="right", pady=(4, 0))

        self.summary_text = tk.Text(
            panel,
            wrap="word",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            bg=self.PANEL,
            fg=self.TEXT,
            font=("Helvetica Neue", 11),
            spacing1=2,
            spacing3=4,
            padx=0,
            pady=0,
            cursor="arrow",
        )
        self.summary_text.pack(fill="both", expand=True)
        self.summary_text.insert(
            "1.0",
            "完成一段工作后，可以在这里生成今日总结。",
        )
        self.summary_text.configure(state="disabled")

        self.generate_button = ttk.Button(
            panel,
            text="生成今日总结",
            command=self._request_summary,
        )
        self.generate_button.pack(anchor="e", pady=(14, 0))

    def _request_dashboard_refresh(self) -> None:
        if self._closing or self._resetting:
            return

        updates = self.controller.get_updates()
        if any(
            event.name
            in {
                "SessionStarted",
                "SessionEnded",
                "BreakStarted",
                "BreakEnded",
            }
            for event in updates
        ):
            self._timeline_refresh_requested = True

        self._timeline_refresh_ticks += 1
        if self._timeline_refresh_ticks >= 5:
            self._timeline_refresh_requested = True
            self._timeline_refresh_ticks = 0

        include_timeline = self._timeline_refresh_requested
        self._timeline_refresh_requested = False
        self._refresh_future = self._refresh_executor.submit(
            load_dashboard_snapshot,
            self.api,
            include_timeline=include_timeline,
        )
        self.root.after(25, self._poll_dashboard_refresh)

    def _poll_dashboard_refresh(self) -> None:
        if self._closing or self._refresh_future is None:
            return
        if not self._refresh_future.done():
            self.root.after(25, self._poll_dashboard_refresh)
            return

        try:
            snapshot = self._refresh_future.result()
        except Exception:
            logger.exception("Dashboard refresh failed.")
        else:
            self._apply_dashboard_snapshot(snapshot)
        finally:
            self._refresh_future = None
            if not self._closing:
                self.root.after(1000, self._request_dashboard_refresh)

    def _apply_dashboard_snapshot(self, snapshot: DashboardSnapshot) -> None:
        self._displayed_state = snapshot.state
        self._presence_change_pending = False
        status = present_status(snapshot.state)
        self.status_var.set(f"● {status.label}")
        self.status_label.configure(fg=status.color)
        self._refresh_pause_button()
        self._refresh_sensor_status()
        self.work_var.set(
            format_duration(snapshot.stats.get("total_work_seconds", 0))
        )
        self.focus_var.set(
            format_duration(snapshot.stats.get("longest_focus_seconds", 0))
        )
        self.break_var.set(str(snapshot.stats.get("break_count", 0)))
        if snapshot.timeline_rows is not None:
            self._apply_timeline_rows(snapshot.timeline_rows)

    def _refresh_pause_button(self) -> None:
        if self._displayed_state == PresenceState.WORKING:
            text = "暂停"
            state = "normal"
        elif self._displayed_state in {PresenceState.IDLE, PresenceState.BREAK}:
            text = "继续工作"
            state = "normal"
        else:
            text = "今日已结束"
            state = "disabled"
        if self._presence_change_pending:
            text = "正在切换…"
            state = "disabled"
        self.pause_button.configure(text=text, state=state)

    def _toggle_pause(self) -> None:
        if self._presence_change_pending:
            return
        self._presence_change_pending = True
        self._refresh_pause_button()

        if self._displayed_state == PresenceState.WORKING:
            if self.presence_adapter is not None:
                self.presence_adapter.pause()
            else:
                self.api.post_event(PresenceLost())
        elif self._displayed_state in {PresenceState.IDLE, PresenceState.BREAK}:
            if self.presence_adapter is not None:
                self.presence_adapter.resume()
            else:
                self.api.post_event(PresenceDetected())
        else:
            self._presence_change_pending = False
            self._refresh_pause_button()

    def _refresh_sensor_status(self) -> None:
        if self.presence_adapter is None:
            self.sensor_var.set("传感器：未配置")
            self.sensor_label.configure(fg=self.MUTED)
            return

        sensor_status = self.presence_adapter.status
        if sensor_status.connected:
            self.sensor_var.set("传感器：已连接")
            self.sensor_label.configure(fg="#24734A")
        elif sensor_status.state == "error":
            self.sensor_var.set("传感器：重连中")
            self.sensor_label.configure(fg="#A16207")
        elif sensor_status.state == "waiting":
            self.sensor_var.set("传感器：等待连接")
            self.sensor_label.configure(fg=self.MUTED)
        else:
            self.sensor_var.set("传感器：未启动")
            self.sensor_label.configure(fg=self.MUTED)

    def _apply_timeline_rows(self, rows: tuple[Any, ...]) -> None:
        signature = tuple((row.time, row.category, row.detail) for row in rows)
        if signature == self._timeline_signature:
            return
        self._timeline_signature = signature
        existing = self.timeline_tree.get_children()
        if existing:
            self.timeline_tree.delete(*existing)
        for row in rows:
            self.timeline_tree.insert(
                "",
                "end",
                values=(row.time, row.category, row.detail),
            )
        children = self.timeline_tree.get_children()
        if children:
            self.timeline_tree.see(children[-1])

    def _load_saved_summary(self) -> None:
        try:
            generation = self.summary_store.load(
                datetime.now(timezone.utc).date()
            )
        except Exception:
            logger.exception("Saved summary could not be loaded.")
            return
        if generation is not None:
            self._show_summary(generation)

    def _request_summary(self) -> None:
        if self._summary_future and not self._summary_future.done():
            return
        self.generate_button.configure(state="disabled")
        self.summary_meta_var.set("正在生成…")
        self._summary_future = self.summary_service.generate_today_async()
        self.root.after(150, self._poll_summary)

    def _open_provider_settings(self) -> None:
        ProviderSettingsDialog(
            self.root,
            self.provider_settings,
            self.configurable_llm_client.validate,
            self._provider_settings_saved,
        )

    def _refresh_provider_label(self) -> None:
        selection = self.provider_settings.load()
        provider = get_provider(selection.provider_id)
        self.ai_provider_var.set(f"AI：{provider.label}")

    def _provider_settings_saved(self) -> None:
        self._refresh_provider_label()
        self.api_status_var.set("API：检查中")
        self.api_status_label.configure(fg=self.MUTED)
        self._request_api_status_refresh()

    def _request_api_status_refresh(self) -> None:
        if (
            self._closing
            or self.provider_settings is None
            or self.configurable_llm_client is None
        ):
            return
        if self._api_status_future and not self._api_status_future.done():
            return
        self._api_status_future = self._api_status_executor.submit(
            probe_api_connection,
            self.provider_settings,
            self.configurable_llm_client,
        )
        self.root.after(100, self._poll_api_status)

    def _poll_api_status(self) -> None:
        if self._closing or self._api_status_future is None:
            return
        if not self._api_status_future.done():
            self.root.after(100, self._poll_api_status)
            return

        status = self._api_status_future.result()
        self._api_status_future = None
        if status.state == "connected":
            self.api_status_var.set("API：已连接")
            self.api_status_label.configure(fg="#24734A")
        elif status.state == "disconnected":
            self.api_status_var.set("API：未连接")
            self.api_status_label.configure(fg=self.MUTED)
        else:
            self.api_status_var.set("API：连接失败")
            self.api_status_label.configure(fg="#B42318")
            if status.detail:
                logger.warning(
                    "%s API status check failed: %s",
                    status.provider_label,
                    status.detail,
                )
        if not self._closing:
            self.root.after(60_000, self._request_api_status_refresh)

    def _confirm_clear_all(self) -> None:
        if self._resetting:
            return
        if self._summary_future and not self._summary_future.done():
            messagebox.showwarning(
                "正在生成总结",
                "请等待今日总结生成完成后再清除数据。",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "清除全部数据",
            (
                "这会永久删除全部工作记录、休息记录、时间线、"
                "每日总结和事件日志，并从 0 重新开始。\n\n"
                "AI 设置和 API Key 会保留。是否继续？"
            ),
            icon="warning",
            parent=self.root,
        ):
            return
        self._clear_all()

    def _clear_all(self) -> None:
        self._resetting = True
        self.clear_button.configure(state="disabled", text="正在清除…")
        self._refresh_future = None
        try:
            if self.presence_adapter is not None:
                self.presence_adapter.stop()
            self.controller.stop(stop_runtime=False)
            self.api.clear_all_data()
            self.summary_store.clear()
            self.controller.start(start_runtime=False)
            if self.presence_adapter is not None:
                self.presence_adapter.reset_presence_state()
                self.presence_adapter.start()
        except Exception as exc:
            logger.exception("Could not clear all application data.")
            messagebox.showerror(
                "清除失败",
                f"无法清除全部数据：{exc}",
                parent=self.root,
            )
        else:
            self._displayed_state = PresenceState.IDLE
            self._presence_change_pending = False
            self.status_var.set("● 空闲")
            self.status_label.configure(fg=self.MUTED)
            self.work_var.set("00:00:00")
            self.focus_var.set("00:00:00")
            self.break_var.set("0")
            self._timeline_signature = None
            self._apply_timeline_rows(())
            self._show_empty_summary()
            self._timeline_refresh_requested = True
            self._timeline_refresh_ticks = 0
            self._refresh_pause_button()
        finally:
            self._resetting = False
            self.clear_button.configure(state="normal", text="清除全部")
            if not self._closing:
                self.root.after(50, self._request_dashboard_refresh)

    def _show_empty_summary(self) -> None:
        self.summary_meta_var.set("尚未生成")
        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", "end")
        self.summary_text.insert(
            "1.0",
            "完成一段工作后，可以在这里生成今日总结。",
        )
        self.summary_text.configure(state="disabled")

    def _poll_summary(self) -> None:
        if self._closing or self._summary_future is None:
            return
        if not self._summary_future.done():
            self.root.after(150, self._poll_summary)
            return

        try:
            generation = self._summary_future.result()
        except Exception:
            logger.exception("Daily summary generation failed.")
            self.summary_meta_var.set("生成失败")
        else:
            self._show_summary(generation)
        finally:
            self.generate_button.configure(state="normal")

    def _show_summary(self, generation: SummaryGeneration) -> None:
        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", "end")
        self.summary_text.insert("1.0", format_summary(generation))
        self.summary_text.configure(state="disabled")

        local_time = generation.generated_at.astimezone().strftime("%H:%M")
        if generation.source == "fallback":
            self.summary_meta_var.set(f"本地生成 · {local_time}")
        else:
            self.summary_meta_var.set(f"更新于 {local_time}")
