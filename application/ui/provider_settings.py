from concurrent.futures import Future, ThreadPoolExecutor
import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser
from typing import Any, Callable

from application.providers import (
    MODEL_PROVIDERS,
    ProviderSelection,
    ProviderSettings,
    get_provider,
)


class ProviderSettingsDialog:
    """Product-facing model provider, model, and credential setup."""

    def __init__(
        self,
        parent: tk.Misc,
        settings: ProviderSettings,
        validator: Callable[[str, str], None],
        on_saved: Callable[[], None] | None = None,
    ) -> None:
        self.settings = settings
        self.validator = validator
        self.on_saved = on_saved
        self._future: Future[Any] | None = None
        self._key_future: Future[str | None] | None = None
        self._key_lookup_provider_id: str | None = None
        self._stored_keys: dict[str, str | None] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="provider-settings",
        )

        self.window = tk.Toplevel(parent)
        self.window.title("AI 设置")
        self.window.geometry("500x440")
        self.window.resizable(False, False)
        self.window.transient(parent)
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self._close)

        selection = settings.load()
        provider = get_provider(selection.provider_id)
        self.provider_var = tk.StringVar(value=provider.label)
        self.model_var = tk.StringVar()
        self.api_key_var = tk.StringVar()
        self.status_var = tk.StringVar(value="")
        self._selection_model_id = selection.model_id

        self._build()
        self._refresh_models()
        self._request_stored_key_state()
        self.window.after(20, self.window.focus_force)

    def _build(self) -> None:
        body = ttk.Frame(self.window, padding=28)
        body.pack(fill="both", expand=True)

        ttk.Label(
            body,
            text="连接 AI 服务",
            font=("Helvetica Neue", 20, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            body,
            text="选择服务商和模型。API Key 会保存在 macOS 钥匙串中。",
            foreground="#666666",
            wraplength=430,
        ).pack(anchor="w", pady=(6, 22))

        ttk.Label(body, text="服务商").pack(anchor="w")
        self.provider_box = ttk.Combobox(
            body,
            textvariable=self.provider_var,
            values=[provider.label for provider in MODEL_PROVIDERS],
            state="readonly",
        )
        self.provider_box.pack(fill="x", pady=(6, 16))
        self.provider_box.bind("<<ComboboxSelected>>", self._provider_changed)

        ttk.Label(body, text="模型").pack(anchor="w")
        self.model_box = ttk.Combobox(
            body,
            textvariable=self.model_var,
            state="readonly",
        )
        self.model_box.pack(fill="x", pady=(6, 16))

        key_heading = ttk.Frame(body)
        key_heading.pack(fill="x")
        ttk.Label(key_heading, text="API Key").pack(side="left")
        self.get_key_button = ttk.Button(
            key_heading,
            text="获取 API Key ↗",
            command=self._open_console,
        )
        self.get_key_button.pack(side="right")

        self.api_key_entry = ttk.Entry(
            body,
            textvariable=self.api_key_var,
            show="•",
        )
        self.api_key_entry.pack(fill="x", pady=(6, 7))
        ttk.Label(
            body,
            text="留空将继续使用已经保存的 Key。",
            foreground="#777777",
            font=("Helvetica Neue", 10),
        ).pack(anchor="w")

        self.status_label = ttk.Label(
            body,
            textvariable=self.status_var,
            foreground="#666666",
            wraplength=430,
        )
        self.status_label.pack(anchor="w", fill="x", pady=(18, 0))

        buttons = ttk.Frame(body)
        buttons.pack(side="bottom", fill="x")
        self.disconnect_button = ttk.Button(
            buttons,
            text="断开此服务商",
            command=self._disconnect,
        )
        self.disconnect_button.pack(side="left")
        ttk.Button(
            buttons,
            text="取消",
            command=self._close,
        ).pack(side="right")
        self.connect_button = ttk.Button(
            buttons,
            text="测试并保存",
            command=self._connect,
        )
        self.connect_button.pack(side="right", padx=(0, 10))
        self._refresh_disconnect_button()

    def _selected_provider(self):
        return next(
            provider
            for provider in MODEL_PROVIDERS
            if provider.label == self.provider_var.get()
        )

    def _provider_changed(self, _event: Any = None) -> None:
        self._selection_model_id = self._selected_provider().default_model
        self.api_key_var.set("")
        self._refresh_models()
        self._request_stored_key_state()

    def _refresh_models(self) -> None:
        provider = self._selected_provider()
        labels = [model.label for model in provider.models]
        self.model_box.configure(values=labels)
        selected = next(
            (
                model.label
                for model in provider.models
                if model.id == self._selection_model_id
            ),
            labels[0],
        )
        self.model_var.set(selected)
        self.api_key_entry.configure()

    def _request_stored_key_state(self) -> None:
        provider = self._selected_provider()
        cached_key = self._stored_keys.get(provider.id)
        if provider.id in self._stored_keys:
            self._show_stored_key_state(provider.id, cached_key)
            return
        self.status_var.set("正在检查钥匙串…")
        self.status_label.configure(foreground="#666666")
        self.disconnect_button.configure(state="disabled")
        self._key_lookup_provider_id = provider.id
        self._key_future = self._executor.submit(
            self.settings.get_api_key,
            provider.id,
        )
        self.window.after(50, self._poll_stored_key_state)

    def _poll_stored_key_state(self) -> None:
        if self._key_future is None or not self.window.winfo_exists():
            return
        if not self._key_future.done():
            self.window.after(50, self._poll_stored_key_state)
            return
        provider_id = self._key_lookup_provider_id
        try:
            key = self._key_future.result()
        except Exception as exc:
            key = None
            self.status_var.set(f"无法读取钥匙串：{exc}")
            self.status_label.configure(foreground="#B42318")
        finally:
            self._key_future = None
        if provider_id is None:
            return
        self._stored_keys[provider_id] = key
        if self._selected_provider().id == provider_id:
            self._show_stored_key_state(provider_id, key)

    def _show_stored_key_state(
        self,
        provider_id: str,
        stored_key: str | None,
    ) -> None:
        provider = get_provider(provider_id)
        if stored_key:
            self.status_var.set("已在钥匙串中保存此服务商的 API Key。")
        else:
            self.status_var.set(f"请输入 API Key（例如 {provider.key_hint}）。")
        self.status_label.configure(foreground="#666666")
        self._refresh_disconnect_button(bool(stored_key))

    def _refresh_disconnect_button(self, has_key: bool = False) -> None:
        self.disconnect_button.configure(
            state="normal" if has_key else "disabled"
        )

    def _selected_model_id(self) -> str:
        provider = self._selected_provider()
        return next(
            model.id
            for model in provider.models
            if model.label == self.model_var.get()
        )

    def _open_console(self) -> None:
        webbrowser.open(self._selected_provider().console_url)

    def _connect(self) -> None:
        if self._future and not self._future.done():
            return
        provider = self._selected_provider()
        key = self.api_key_var.get().strip()
        if not key:
            key = self._stored_keys.get(provider.id) or ""
        if not key:
            messagebox.showwarning(
                "缺少 API Key",
                "请先输入 API Key。",
                parent=self.window,
            )
            self.api_key_entry.focus_set()
            return

        selection = ProviderSelection(provider.id, self._selected_model_id())
        self.connect_button.configure(state="disabled")
        self.status_var.set("正在测试连接…")
        self.status_label.configure(foreground="#666666")
        self._future = self._executor.submit(
            self._validate_and_save,
            selection,
            key,
            bool(self.api_key_var.get().strip()),
        )
        self.window.after(100, self._poll_connection)

    def _disconnect(self) -> None:
        provider = self._selected_provider()
        if not messagebox.askyesno(
            "断开 AI 服务",
            f"从 macOS 钥匙串删除 {provider.label} 的 API Key？",
            parent=self.window,
        ):
            return
        self.settings.delete_api_key(provider.id)
        self._stored_keys[provider.id] = None
        self.api_key_var.set("")
        self._show_stored_key_state(provider.id, None)
        self.status_var.set(f"已断开 {provider.label}。")
        if self.on_saved:
            self.on_saved()

    def _validate_and_save(
        self,
        selection: ProviderSelection,
        key: str,
        should_save_key: bool,
    ) -> ProviderSelection:
        self.validator(selection.provider_id, key)
        if should_save_key:
            self.settings.save_api_key(selection.provider_id, key)
        self.settings.save(selection)
        return selection

    def _poll_connection(self) -> None:
        if self._future is None or not self.window.winfo_exists():
            return
        if not self._future.done():
            self.window.after(100, self._poll_connection)
            return
        try:
            selection = self._future.result()
        except Exception as exc:
            self.status_var.set(f"连接失败：{exc}")
            self.status_label.configure(foreground="#B42318")
            self.connect_button.configure(state="normal")
            return

        provider = get_provider(selection.provider_id)
        self._stored_keys[selection.provider_id] = (
            self.api_key_var.get().strip()
            or self._stored_keys.get(selection.provider_id)
        )
        self.status_var.set(f"已连接 {provider.label}，设置已保存。")
        self.status_label.configure(foreground="#24734A")
        self.connect_button.configure(state="normal")
        if self.on_saved:
            self.on_saved()
        self.window.after(650, self._close)

    def _close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        if self.window.winfo_exists():
            self.window.destroy()
