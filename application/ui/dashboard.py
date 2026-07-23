from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
import logging
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Any

from application.summary.models import SummaryGeneration
from application.ui.provider_settings import ProviderSettingsDialog
from application.ui.presentation import (
    build_timeline_rows,
    format_duration,
    format_summary,
    present_status,
)

logger = logging.getLogger(__name__)


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
        activity_service: Any | None = None,
        privacy_policy: Any | None = None,
        privacy_store: Any | None = None,
        provider_settings: Any | None = None,
        provider_validator: Any | None = None,
    ) -> None:
        self.root = root
        self.api = api
        self.controller = controller
        self.summary_service = summary_service
        self.summary_store = summary_store
        self.activity_service = activity_service
        self.privacy_policy = privacy_policy
        self.privacy_store = privacy_store
        self.provider_settings = provider_settings
        self.provider_validator = provider_validator
        self._summary_future: Future[SummaryGeneration] | None = None
        self._activity_future: Future[dict[str, int]] | None = None
        self._activity_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="activity-dashboard",
        )
        self._closing = False

        self.status_var = tk.StringVar(value="● 空闲")
        self.work_var = tk.StringVar(value="00:00:00")
        self.focus_var = tk.StringVar(value="00:00:00")
        self.break_var = tk.StringVar(value="0")
        self.summary_meta_var = tk.StringVar(value="尚未生成")
        self.activity_var = tk.StringVar(value="活动分类：等待数据")
        self.pause_var = tk.StringVar(value="暂停采集")

        self._configure_window()
        self._configure_styles()
        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def start(self) -> None:
        self.controller.start()
        self._load_saved_summary()
        self._refresh_dashboard()
        self._refresh_timeline()
        self._refresh_activity()

    def run(self) -> None:
        self.start()
        self.root.mainloop()

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self.controller.stop(stop_runtime=False)
            self.summary_service.close()
            self._activity_executor.shutdown(wait=False, cancel_futures=True)
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
        if self.privacy_policy is not None:
            self.pause_button = ttk.Button(
                header,
                textvariable=self.pause_var,
                command=self._toggle_capture,
            )
            self.pause_button.pack(side="right", padx=(0, 14), pady=(4, 0))
            self._refresh_pause_label()
        if (
            self.provider_settings is not None
            and self.provider_validator is not None
        ):
            ttk.Button(
                header,
                text="AI 设置",
                command=self._open_provider_settings,
            ).pack(side="right", padx=(0, 8), pady=(4, 0))

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

        tk.Label(
            shell,
            textvariable=self.activity_var,
            bg=self.BACKGROUND,
            fg=self.MUTED,
            font=("Helvetica Neue", 10),
        ).pack(anchor="w", pady=(0, 12))

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

    def _refresh_dashboard(self) -> None:
        if self._closing:
            return
        try:
            state = self.api.get_current_state()
            status = present_status(state)
            self.status_var.set(f"● {status.label}")
            self.status_label.configure(fg=status.color)

            stats = self.api.get_today_stats()
            self.work_var.set(
                format_duration(stats.get("total_work_seconds", 0))
            )
            self.focus_var.set(
                format_duration(stats.get("longest_focus_seconds", 0))
            )
            self.break_var.set(str(stats.get("break_count", 0)))

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
                self._refresh_timeline_data()
        except Exception:
            logger.exception("Dashboard refresh failed.")
        self.root.after(1000, self._refresh_dashboard)

    def _refresh_timeline(self) -> None:
        if self._closing:
            return
        self._refresh_timeline_data()
        self.root.after(5000, self._refresh_timeline)

    def _refresh_activity(self) -> None:
        if self._closing:
            return
        if self.activity_service is not None and (
            self._activity_future is None or self._activity_future.done()
        ):
            self._activity_future = self._activity_executor.submit(
                self.activity_service.category_seconds_today
            )
            self.root.after(100, self._poll_activity)
        self.root.after(10000, self._refresh_activity)

    def _poll_activity(self) -> None:
        if self._closing or self._activity_future is None:
            return
        if not self._activity_future.done():
            self.root.after(100, self._poll_activity)
            return
        try:
            totals = self._activity_future.result()
        except Exception:
            logger.exception("Activity classification refresh failed.")
            self.activity_var.set("活动分类：暂时不可用")
            return
        labels = {
            "learning": "学习",
            "work": "工作",
            "entertainment": "娱乐",
            "unknown": "未知",
            "background_playback": "离座播放",
        }
        parts = [
            f"{labels[key]} {format_duration(seconds)}"
            for key, seconds in totals.items()
            if seconds > 0 and key in labels
        ]
        self.activity_var.set(
            "活动分类：" + (" · ".join(parts) if parts else "等待数据")
        )

    def _toggle_capture(self) -> None:
        if self.privacy_policy is None:
            return
        self.privacy_policy.paused = not self.privacy_policy.paused
        if self.privacy_store is not None:
            self.privacy_store.save(self.privacy_policy)
        self._refresh_pause_label()

    def _refresh_pause_label(self) -> None:
        if self.privacy_policy is None:
            return
        self.pause_var.set(
            "恢复采集" if self.privacy_policy.paused else "暂停采集"
        )

    def _open_provider_settings(self) -> None:
        ProviderSettingsDialog(
            self.root,
            self.provider_settings,
            self.provider_validator,
        )

    def _refresh_timeline_data(self) -> None:
        try:
            rows = build_timeline_rows(self.api.get_today_timeline())
        except Exception:
            logger.exception("Timeline refresh failed.")
            return

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
