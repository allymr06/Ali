from __future__ import annotations

import ctypes
import math
import sys
import tkinter as tk
from collections.abc import Coroutine
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, SimpleQueue
from tkinter import messagebox, ttk
from typing import Any, Callable

from app.config.provider_preferences import DEFAULT_GEMINI_MODEL
from app.ui.api_settings import APISettingsService, create_api_settings_service
from app.ui.controller import DesktopController
from app.ui.models import ChatMessage, UIScreen, UITheme
from app.ui.theme import ThemeTokens, tokens

DISPLAY_MODEL_NAME = "JARVIS 0.2"


@dataclass(frozen=True, slots=True)
class UIEvent:
    operation_id: int
    kind: str
    payload: object

NAVIGATION = (
    (UIScreen.HOME, "01", "Komuta Merkezi"),
    (UIScreen.CHAT, "02", "Sohbet"),
    (UIScreen.TASKS, "03", "Görevler"),
    (UIScreen.MEMORY, "04", "Hafıza Ağı"),
    (UIScreen.VOICE, "05", "Ses"),
    (UIScreen.VISION, "06", "Görüş"),
    (UIScreen.RESEARCH, "07", "Araştırma"),
    (UIScreen.TOOLS, "08", "Yetenekler"),
    (UIScreen.INTEGRATIONS, "09", "Güven ve Erişim"),
    (UIScreen.DIAGNOSTICS, "10", "Tanılama"),
    (UIScreen.SETTINGS, "11", "Ayarlar"),
)

SHORTCUTS = (
    ("Enter", "Komutu gönder"),
    ("Shift + Enter", "Yeni satır ekle"),
    ("Ctrl + L", "Komut alanına odaklan"),
    ("Ctrl + ,", "Ayarları aç"),
    ("Ctrl + Shift + T", "Açık/koyu temayı değiştir"),
    ("Ctrl + Shift + N", "Gezinmeyi daralt veya genişlet"),
    ("Alt + 1 ... 9", "1 ile 9 arasındaki ekranları aç"),
    ("Alt + 0", "Tanılamayı aç"),
    ("Alt + S", "Ayarları aç"),
    ("F1", "Klavye kısayollarını göster"),
    ("Escape", "Çalışma alanına dön"),
)

STATUS_TEXT = {
    "LOCAL CORE READY": "YEREL ÇEKİRDEK HAZIR",
    "PROCESSING": "İŞLENİYOR",
    "RESPONDING": "YANITLIYOR",
    "TESTING CONNECTION": "BAĞLANTI SINANIYOR",
    "LISTENING": "DİNLİYOR",
    "CAPTURING": "GÖRÜNTÜ ALINIYOR",
    "RESEARCHING": "ARAŞTIRIYOR",
}

TOKEN_TEXT = {
    "pending": "BEKLİYOR",
    "running": "ÇALIŞIYOR",
    "active": "AKTİF",
    "paused": "DURAKLATILDI",
    "completed": "TAMAMLANDI",
    "failed": "BAŞARISIZ",
    "blocked": "ENGELLENDİ",
    "cancelled": "İPTAL EDİLDİ",
    "fresh": "GÜNCEL",
    "stale": "ESKİ",
    "low": "DÜŞÜK",
    "medium": "ORTA",
    "high": "YÜKSEK",
    "critical": "KRİTİK",
}


def localize_token(value: object) -> str:
    text = str(value)
    return TOKEN_TEXT.get(text.strip().casefold(), text)


def enable_high_dpi_rendering() -> bool:
    """Ask Windows for crisp per-monitor rendering before Tk is created."""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return True
    except (AttributeError, OSError):
        pass
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        return shcore.SetProcessDpiAwareness(2) in {0, -2147024891}
    except (AttributeError, OSError):
        return False


def next_typewriter_text(current: str, target: str) -> str:
    """Advance rendered text smoothly toward the latest streamed value."""
    if not target:
        return current
    if not target.startswith(current):
        current = ""
    remaining = len(target) - len(current)
    step = max(1, min(10, (remaining + 17) // 18))
    return target[: len(current) + step]


class RoundedSurface(tk.Canvas):
    """Vector rounded container that stays crisp at every DPI scale."""

    def __init__(
        self,
        parent: tk.Widget,
        *,
        fill: str,
        outline: str,
        radius: int = 14,
        padx: int = 0,
        pady: int = 0,
    ) -> None:
        parent_bg = str(parent.cget("bg"))
        super().__init__(
            parent,
            bg=parent_bg,
            highlightthickness=0,
            borderwidth=0,
            height=1,
        )
        self._fill = fill
        self._outline = outline
        self._radius = radius
        self._padx = padx
        self._pady = pady
        self._geometry_job: str | None = None
        self._geometry_passes_remaining = 0
        self.content = tk.Frame(self, bg=fill)
        self._content_window = self.create_window(
            padx,
            pady,
            anchor="nw",
            window=self.content,
        )
        self.content.bind("<Configure>", self._fit_height)
        self.bind("<Configure>", self._redraw)
        self._schedule_geometry_sync()

    def _fit_height(self, _event: tk.Event[Any]) -> None:
        self._schedule_geometry_sync()

    def _schedule_geometry_sync(self, passes: int = 3) -> None:
        self._geometry_passes_remaining = max(
            self._geometry_passes_remaining,
            passes,
        )
        if self._geometry_job is None and self.winfo_exists():
            self._geometry_job = self.after_idle(self._sync_geometry)

    def _sync_geometry(self) -> None:
        self._geometry_job = None
        if not self.winfo_exists():
            return
        content_height = max(1, self.content.winfo_reqheight())
        target_height = content_height + self._pady * 2
        self.itemconfigure(self._content_window, height=content_height)
        if int(float(self.cget("height"))) != target_height:
            super().configure(height=target_height)

        ancestor = self.master
        while ancestor is not None:
            if isinstance(ancestor, RoundedSurface):
                ancestor._schedule_geometry_sync()
                break
            ancestor = getattr(ancestor, "master", None)

        if self._geometry_passes_remaining > 0:
            self._geometry_passes_remaining -= 1
            self._geometry_job = self.after(1, self._sync_geometry)

    def _redraw(self, event: tk.Event[Any]) -> None:
        width = max(2, event.width)
        height = max(2, event.height)
        self.delete("surface")
        radius = min(self._radius, width // 2, height // 2)
        points = (
            radius,
            1,
            width - radius,
            1,
            width - 1,
            radius,
            width - 1,
            height - radius,
            width - radius,
            height - 1,
            radius,
            height - 1,
            1,
            height - radius,
            1,
            radius,
        )
        self.create_polygon(
            points,
            smooth=True,
            splinesteps=18,
            fill=self._fill,
            outline=self._outline,
            width=1,
            tags=("surface",),
        )
        self.tag_lower("surface")
        self.itemconfigure(
            self._content_window,
            width=max(1, width - self._padx * 2),
        )
        self._schedule_geometry_sync()


class RoundedEntry(tk.Canvas):
    """Rounded vector-backed text entry with the standard Entry contract."""

    def __init__(
        self,
        parent: tk.Widget,
        *,
        colors: ThemeTokens,
        textvariable: tk.StringVar | None = None,
        show: str | None = None,
    ) -> None:
        super().__init__(
            parent,
            bg=str(parent.cget("bg")),
            height=32,
            highlightthickness=0,
            borderwidth=0,
        )
        self._colors = colors
        self.entry = tk.Entry(
            self,
            textvariable=textvariable,
            show=show,
            relief="flat",
            borderwidth=0,
            bg=colors.surface_alt,
            fg=colors.ink,
            insertbackground=colors.ink,
            selectbackground=colors.faint,
            font=("Segoe UI", 10),
        )
        self._entry_window = self.create_window(
            10,
            16,
            anchor="w",
            window=self.entry,
        )
        self.bind("<Configure>", self._draw_entry)

    def _draw_entry(self, event: tk.Event[Any]) -> None:
        self.delete("entry-surface")
        width = max(2, event.width)
        height = max(2, event.height)
        radius = min(11, height // 2)
        self.create_polygon(
            (
                radius,
                1,
                width - radius,
                1,
                width - 1,
                radius,
                width - 1,
                height - radius,
                width - radius,
                height - 1,
                radius,
                height - 1,
                1,
                height - radius,
                1,
                radius,
            ),
            smooth=True,
            splinesteps=16,
            fill=self._colors.surface_alt,
            outline=self._colors.line,
            tags=("entry-surface",),
        )
        self.tag_lower("entry-surface")
        self.itemconfigure(
            self._entry_window,
            width=max(1, width - 20),
        )

    def get(self) -> str:
        return self.entry.get()

    def cget(self, key: str) -> Any:
        if key in {"show", "state"}:
            return self.entry.cget(key)
        return super().cget(key)

    def configure(self, cnf: Any = None, **kwargs: Any) -> Any:
        entry_options = {
            key: kwargs.pop(key)
            for key in tuple(kwargs)
            if key in {"show", "state"}
        }
        if entry_options and hasattr(self, "entry"):
            self.entry.configure(**entry_options)
        return super().configure(cnf, **kwargs)

    config = configure


class ScrollableWorkspace(tk.Frame):
    """Borderless vertical workspace for screens with dense content."""

    def __init__(self, parent: tk.Widget, colors: ThemeTokens) -> None:
        super().__init__(parent, bg=colors.background)
        self.canvas = tk.Canvas(
            self,
            bg=colors.background,
            highlightthickness=0,
            borderwidth=0,
        )
        self.scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.canvas.yview,
            style="Jarvis.Vertical.TScrollbar",
        )
        self.body = tk.Frame(self.canvas, bg=colors.background)
        self._window = self.canvas.create_window(
            (0, 0), window=self.body, anchor="nw"
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.body.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            ),
        )
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(
                self._window, width=event.width
            ),
        )
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _event: tk.Event[Any]) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event: tk.Event[Any]) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event: tk.Event[Any]) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def reset(self) -> None:
        self.canvas.yview_moveto(0)


class DesktopWindow:
    """Minimal monochrome desktop shell for the live JARVIS runtime."""

    def __init__(
        self,
        controller: DesktopController,
        root: tk.Tk | None = None,
        api_settings: APISettingsService | None = None,
    ) -> None:
        self.controller = controller
        self.root = root or tk.Tk()
        self.api_settings = api_settings
        self._colors = tokens(self.controller.state.theme)
        self._nav_buttons: dict[UIScreen, tk.Button] = {}
        self._context_buttons: dict[str, tk.Button] = {}
        self._busy_future: Future[Any] | None = None
        self._streaming_text: str | None = None
        self._stream_target_text = ""
        self._streaming_label: tk.Label | None = None
        self._typewriter_job: str | None = None
        self._typing_frame = 0
        self._command_complete_pending = False
        self._pending_user_text: str | None = None
        self._status_job: str | None = None
        self._nav_job: str | None = None
        self._orb_job: str | None = None
        self._orb_phase = 0
        self._home_orb: tk.Canvas | None = None
        self._pulse_frame = 0
        self._closing = False
        self._ui_events: SimpleQueue[UIEvent] = SimpleQueue()
        self._ui_poll_job: str | None = None
        self._operation_sequence = 0
        self._active_operation_id: int | None = None
        self._command_operation_id: int | None = None
        self._voice_operation_id: int | None = None
        self._voice_future: Future[Any] | None = None
        self._voice_stop_future: Future[Any] | None = None
        self._voice_stop_requested = False
        self._api_test_feedback: tuple[str, bool] | None = None
        self._research_report: dict[str, object] | None = None
        self._research_error: str | None = None
        self._snapshot = self.controller.snapshot()
        self._configure_window()
        self._build_shell()
        self._bind_shortcuts()
        self.render(UIScreen.HOME)
        self._animate_status()
        self._schedule_ui_event_pump()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _configure_window(self) -> None:
        self.root.title("J.A.R.V.I.S. Beta")
        bundle_root = Path(
            getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])
        )
        for candidate in (
            bundle_root / "assets" / "branding" / "jarvis.ico",
            bundle_root / "assets" / "jarvis.ico",
        ):
            if not candidate.exists():
                continue
            try:
                self.root.iconbitmap(default=str(candidate))
                break
            except (AttributeError, tk.TclError):
                continue
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        window_width = min(1600, max(1040, screen_width - 120))
        window_height = min(900, max(700, screen_height - 100))
        x = max(0, (screen_width - window_width) // 2)
        y = max(0, (screen_height - window_height) // 2)
        self.root.geometry(
            f"{window_width}x{window_height}+{x}+{y}"
        )
        self.root.minsize(1040, 700)
        self.root.configure(bg=self._colors.background)
        self.root.option_add("*Font", ("Segoe UI", 10))
        if screen_width >= 1920 and screen_height >= 1080:
            try:
                self.root.state("zoomed")
            except tk.TclError:
                pass

    def _build_shell(self) -> None:
        c = self._colors
        self._configure_ttk()
        topbar = tk.Frame(self.root, bg=c.surface, height=48)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)
        brand = tk.Frame(
            topbar,
            bg=c.surface,
            width=230,
            highlightbackground=c.line,
            highlightthickness=1,
        )
        brand.pack(side="left", fill="y")
        brand.pack_propagate(False)
        tk.Label(
            brand,
            text="J",
            bg=c.surface_alt,
            fg=c.accent,
            font=("Segoe UI", 13, "bold"),
            width=3,
            pady=5,
            highlightbackground=c.faint,
            highlightthickness=1,
        ).pack(side="left", padx=(13, 10), pady=8)
        copy = tk.Frame(brand, bg=c.surface)
        copy.pack(side="left", pady=7)
        tk.Label(
            copy,
            text="JARVIS",
            bg=c.surface,
            fg=c.ink,
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w")
        tk.Label(
            copy,
            text="KİŞİSEL İŞLETİM KATMANI",
            bg=c.surface,
            fg=c.muted,
            font=("Segoe UI", 8),
        ).pack(anchor="w")

        runtime = tk.Frame(topbar, bg=c.surface)
        runtime.pack(side="left", fill="both", expand=True, padx=18)
        self.status_dot = tk.Canvas(
            runtime,
            width=14,
            height=48,
            bg=c.surface,
            highlightthickness=0,
        )
        self.status_dot.pack(side="left", padx=(0, 6))
        self._status_oval = self.status_dot.create_oval(
            3, 20, 10, 27, fill=c.accent_strong, outline=""
        )
        self.status_label = tk.Label(
            runtime,
            text=STATUS_TEXT.get(
                self.controller.state.status,
                self.controller.state.status,
            ),
            bg=c.surface,
            fg=c.muted,
            font=("Segoe UI Semibold", 8),
        )
        self.status_label.pack(side="left", padx=(0, 16))
        self.provider_badge = self._status_field(
            runtime, "ÇEKİRDEK", self._snapshot.provider.upper()
        )
        self.model_badge = self._status_field(
            runtime, "MODEL", DISPLAY_MODEL_NAME
        )
        self._status_field(runtime, "GÜVEN", "SEVİYE 03")
        self.voice_status_badge = self._status_field(
            runtime,
            "MİKROFON",
            (
                self.controller.state.voice_status
                if self.controller.state.voice_active
                else "PASİF"
                if self._snapshot.voice_available
                else "ÇEVRİMDIŞI"
            ),
        )
        self._status_field(
            runtime,
            "GÖRÜŞ",
            "ONAYLI" if self._snapshot.vision_available else "KİLİTLİ",
        )
        tk.Frame(self.root, bg=c.line, height=1).pack(fill="x")

        self.shell = tk.Frame(self.root, bg=c.background)
        self.shell.pack(fill="both", expand=True)
        self._build_navigation()
        self.center = tk.Frame(self.shell, bg=c.background)
        self.center.pack(side="left", fill="both", expand=True)
        self.workspace_scroller = ScrollableWorkspace(self.center, c)
        self.workspace_scroller.pack(
            fill="both",
            expand=True,
            padx=(26, 18),
            pady=(22, 142),
        )
        self.workspace = self.workspace_scroller.body
        self._build_composer()
        self._build_context()

    def _status_field(
        self,
        parent: tk.Widget,
        label: str,
        value: str,
    ) -> tk.Label:
        block = tk.Frame(parent, bg=self._colors.surface)
        block.pack(side="left", padx=(0, 16), pady=8)
        tk.Label(
            block,
            text=label,
            bg=self._colors.surface,
            fg=self._colors.faint,
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w")
        value_label = tk.Label(
            block,
            text=value,
            bg=self._colors.surface,
            fg=self._colors.ink,
            font=("Segoe UI Semibold", 8),
        )
        value_label.pack(anchor="w")
        return value_label

    def _configure_ttk(self) -> None:
        c = self._colors
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Jarvis.TCombobox",
            fieldbackground=c.surface_alt,
            background=c.surface_alt,
            foreground=c.ink,
            arrowcolor=c.ink,
            bordercolor=c.line,
            lightcolor=c.line,
            darkcolor=c.line,
            padding=8,
        )
        style.map(
            "Jarvis.TCombobox",
            fieldbackground=[("readonly", c.surface_alt)],
            foreground=[("readonly", c.ink)],
        )
        style.configure(
            "Jarvis.Vertical.TScrollbar",
            background=c.surface_alt,
            troughcolor=c.background,
            bordercolor=c.background,
            lightcolor=c.surface_alt,
            darkcolor=c.surface_alt,
            arrowcolor=c.muted,
            relief="flat",
            borderwidth=0,
            arrowsize=0,
            width=7,
        )
        style.layout(
            "Jarvis.Vertical.TScrollbar",
            [
                (
                    "Vertical.Scrollbar.trough",
                    {
                        "sticky": "ns",
                        "children": [
                            (
                                "Vertical.Scrollbar.thumb",
                                {"expand": "1", "sticky": "nswe"},
                            )
                        ],
                    },
                )
            ],
        )
        style.map(
            "Jarvis.Vertical.TScrollbar",
            background=[("active", c.faint)],
        )

    def _badge(self, parent: tk.Widget, text: str) -> tk.Label:
        label = tk.Label(
            parent,
            text=text,
            bg=self._colors.surface_alt,
            fg=self._colors.muted,
            font=("Segoe UI Semibold", 8),
            padx=9,
            pady=4,
            highlightbackground=self._colors.line,
            highlightthickness=1,
        )
        label.pack(side="left", padx=3)
        return label

    def _build_navigation(self) -> None:
        c = self._colors
        self.nav = tk.Frame(
            self.shell,
            bg=c.surface,
            width=230,
            highlightbackground=c.line,
            highlightthickness=1,
        )
        self.nav.pack(side="left", fill="y")
        self.nav.pack_propagate(False)
        tk.Label(
            self.nav,
            text="GÖREV KONTROLÜ",
            bg=c.surface,
            fg=c.accent_strong,
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w", padx=18, pady=(18, 10))
        for screen, index, label in NAVIGATION:
            button = tk.Button(
                self.nav,
                text=f"{index}    {label}",
                anchor="w",
                relief="flat",
                borderwidth=0,
                bg=c.surface,
                fg=c.muted,
                activebackground=c.hover,
                activeforeground=c.ink,
                padx=17,
                pady=10,
                font=("Segoe UI", 9),
                cursor="hand2",
                command=lambda value=screen: self.render(value),
            )
            button.pack(fill="x", padx=8, pady=1)
            self._bind_hover(button, c.hover, c.surface)
            self._nav_buttons[screen] = button
        tk.Frame(self.nav, bg=c.surface).pack(fill="both", expand=True)
        self.nav_collapse_button = self._button(
            self.nav,
            "DARALT",
            self._toggle_navigation,
            variant="ghost",
        )
        self.nav_collapse_button.pack(fill="x", padx=10, pady=12)

    def _build_composer(self) -> None:
        c = self._colors
        self.composer_host = RoundedSurface(
            self.center,
            fill=c.surface,
            outline=c.faint,
            radius=18,
            padx=10,
            pady=8,
        )
        self.composer_host.place(
            relx=0.5, rely=1.0, anchor="s", relwidth=0.93, y=-18
        )
        self.composer = self.composer_host.content
        self.command = tk.Text(
            self.composer,
            height=2,
            wrap="word",
            relief="flat",
            borderwidth=0,
            bg=c.surface,
            fg=c.ink,
            insertbackground=c.ink,
            selectbackground=c.faint,
            padx=9,
            pady=8,
            font=("Segoe UI", 10),
        )
        self.command.pack(fill="x")
        self.command.bind("<Return>", self._on_composer_enter)
        self.command.bind("<Shift-Return>", self._on_composer_newline)
        bar = tk.Frame(self.composer, bg=c.surface)
        bar.pack(fill="x", pady=(5, 0))
        self.composer_voice_button = self._button(
            bar,
            (
                "SESİ DURDUR"
                if self.controller.state.voice_active
                else "MİKROFON"
            ),
            self._start_voice,
            variant="ghost",
        )
        self.composer_voice_button.pack(side="left")
        self._button(
            bar, "GÖRÜŞ", self._start_vision, variant="ghost"
        ).pack(side="left", padx=4)
        voice_bars = tk.Canvas(
            bar,
            width=18,
            height=18,
            bg=c.surface,
            highlightthickness=0,
        )
        voice_bars.pack(side="left", padx=(6, 2))
        for x, height in ((4, 6), (9, 12), (14, 8)):
            voice_bars.create_line(
                x,
                15,
                x,
                15 - height,
                fill=c.accent_strong,
                width=2,
            )
        self.composer_voice_state_label = tk.Label(
            bar,
            text=(
                self.controller.state.voice_status
                if self.controller.state.voice_active
                else "BEKLİYOR"
            ),
            bg=c.surface,
            fg=c.faint,
            font=("Segoe UI", 8),
        )
        self.composer_voice_state_label.pack(side="left", padx=(2, 10))
        self._badge(bar, DISPLAY_MODEL_NAME)
        self._badge(
            bar,
            f"{self._snapshot.enabled_tools} YETENEK",
        )
        self.send_button = self._button(
            bar, "GÖNDER", self._submit_command, variant="primary"
        )
        self.send_button.pack(side="right")

    def _build_context(self) -> None:
        c = self._colors
        self.context = tk.Frame(
            self.shell,
            bg=c.surface,
            width=330,
            highlightbackground=c.line,
            highlightthickness=1,
        )
        self.context.pack(side="right", fill="y")
        self.context.pack_propagate(False)
        tk.Label(
            self.context,
            text="GÖREV TELEMETRİSİ",
            bg=c.surface,
            fg=c.accent_strong,
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w", padx=18, pady=(21, 4))
        tk.Label(
            self.context,
            text="Çalışma durumu  ·  CANLI",
            bg=c.surface,
            fg=c.ink,
            font=("Segoe UI Semibold", 12),
        ).pack(anchor="w", padx=18)
        tabs = tk.Frame(self.context, bg=c.surface)
        tabs.pack(fill="x", padx=14, pady=14)
        for label in ("YÜRÜTME", "HAFIZA", "ARAÇLAR"):
            button = self._button(
                tabs,
                label,
                lambda value=label: self._render_context(value),
                variant="chip",
            )
            button.pack(side="left", padx=2)
            self._context_buttons[label] = button
        self.context_body = tk.Frame(self.context, bg=c.surface)
        self.context_body.pack(fill="both", expand=True, padx=18, pady=4)
        self._render_context("YÜRÜTME")

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-l>", self._focus_composer)
        self.root.bind("<Control-comma>", self._open_settings)
        self.root.bind("<Control-Shift-T>", self._shortcut_theme)
        self.root.bind("<Control-Shift-N>", self._shortcut_navigation)
        for index, (screen, _number, _label) in enumerate(
            NAVIGATION[:9], start=1
        ):
            self.root.bind(
                f"<Alt-Key-{index}>",
                lambda _event, value=screen: self.render(value),
            )
        self.root.bind(
            "<Alt-Key-0>",
            lambda _event: self.render(UIScreen.DIAGNOSTICS),
        )
        self.root.bind(
            "<Alt-s>", lambda _event: self.render(UIScreen.SETTINGS)
        )
        self.root.bind("<F1>", self._show_shortcuts)
        self.root.bind("<Escape>", self._focus_workspace)

    def _on_composer_enter(self, _event: tk.Event[Any]) -> str:
        return self._submit_command()

    @staticmethod
    def _on_composer_newline(_event: tk.Event[Any]) -> None:
        return None

    def _focus_composer(self, _event: tk.Event[Any] | None = None) -> str:
        self.command.focus_set()
        return "break"

    def _open_settings(self, _event: tk.Event[Any] | None = None) -> str:
        self.render(UIScreen.SETTINGS)
        return "break"

    def _shortcut_theme(self, _event: tk.Event[Any]) -> str:
        self._toggle_theme()
        return "break"

    def _shortcut_navigation(self, _event: tk.Event[Any]) -> str:
        self._toggle_navigation()
        return "break"

    def _show_shortcuts(self, _event: tk.Event[Any] | None = None) -> str:
        messagebox.showinfo(
            "JARVIS klavye kısayolları",
            "\n".join(f"{key}  —  {description}" for key, description in SHORTCUTS),
            parent=self.root,
        )
        return "break"

    def _focus_workspace(self, _event: tk.Event[Any]) -> str:
        self.workspace.focus_set()
        return "break"

    def _button(
        self,
        parent: tk.Widget,
        text: str,
        command: Callable[[], Any],
        *,
        variant: str = "secondary",
    ) -> tk.Button:
        c = self._colors
        background, foreground, hover = {
            "primary": (c.accent, c.inverse, c.accent_strong),
            "secondary": (c.surface_alt, c.ink, c.hover),
            "ghost": (c.surface, c.muted, c.hover),
            "chip": (c.surface_alt, c.muted, c.hover),
        }[variant]
        button = tk.Button(
            parent,
            text=text,
            command=command,
            relief="flat",
            borderwidth=0,
            bg=background,
            fg=foreground,
            activebackground=hover,
            activeforeground=c.inverse if variant == "primary" else c.ink,
            padx=12,
            pady=7,
            font=("Segoe UI Semibold", 8),
            cursor="hand2",
        )
        self._bind_hover(button, hover, background)
        return button

    @staticmethod
    def _bind_hover(widget: tk.Widget, hover: str, normal: str) -> None:
        widget.bind("<Enter>", lambda _event: widget.configure(bg=hover))
        widget.bind("<Leave>", lambda _event: widget.configure(bg=normal))

    @staticmethod
    def _clear(frame: tk.Widget) -> None:
        for child in frame.winfo_children():
            child.destroy()

    def _heading(self, eyebrow: str, title: str, subtitle: str) -> None:
        c = self._colors
        tk.Label(
            self.workspace,
            text=eyebrow.upper(),
            bg=c.background,
            fg=c.accent_strong,
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w")
        tk.Label(
            self.workspace,
            text=title,
            bg=c.background,
            fg=c.ink,
            font=("Segoe UI Semibold", 24),
        ).pack(anchor="w", pady=(5, 2))
        tk.Label(
            self.workspace,
            text=subtitle,
            bg=c.background,
            fg=c.muted,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(0, 20))

    def _card(
        self,
        parent: tk.Widget,
        title: str,
        *,
        side: str = "top",
        subtitle: str = "",
    ) -> tk.Frame:
        c = self._colors
        surface = RoundedSurface(
            parent,
            fill=c.surface,
            outline=c.line,
            radius=16,
            padx=18,
            pady=16,
        )
        surface.pack(side=side, fill="both", expand=True, padx=6, pady=6)
        frame = surface.content
        setattr(frame, "_rounded_host", surface)
        tk.Frame(
            frame,
            bg=c.accent_strong,
            width=42,
            height=1,
        ).place(x=0, y=0)
        tk.Label(
            frame,
            text=title.upper(),
            bg=c.surface,
            fg=c.muted,
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w")
        if subtitle:
            tk.Label(
                frame,
                text=subtitle,
                bg=c.surface,
                fg=c.faint,
                font=("Segoe UI", 8),
            ).pack(anchor="w", pady=(3, 11))
        else:
            tk.Frame(frame, bg=c.surface, height=10).pack()
        return frame

    def _line(self, parent: tk.Widget, primary: str, secondary: str = "") -> None:
        c = self._colors
        surface = RoundedSurface(
            parent,
            fill=c.surface_alt,
            outline=c.line,
            radius=10,
            padx=10,
            pady=8,
        )
        surface.pack(fill="x", pady=2)
        row = surface.content
        tk.Label(
            row,
            text=primary,
            bg=c.surface_alt,
            fg=c.ink,
            anchor="w",
            justify="left",
            wraplength=560,
        ).pack(fill="x", anchor="w")
        if secondary:
            tk.Label(
                row,
                text=secondary,
                bg=c.surface_alt,
                fg=c.muted,
                font=("Segoe UI", 8),
                anchor="w",
                justify="left",
                wraplength=560,
            ).pack(fill="x", anchor="w", pady=(2, 0))

    def _metric(self, parent: tk.Widget, value: str, label: str) -> None:
        c = self._colors
        block = tk.Frame(parent, bg=c.surface)
        block.pack(side="left", fill="both", expand=True, padx=4)
        tk.Label(
            block,
            text=value,
            bg=c.surface,
            fg=c.ink,
            font=("Segoe UI Semibold", 22),
        ).pack(anchor="w")
        tk.Label(
            block,
            text=label.upper(),
            bg=c.surface,
            fg=c.faint,
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w", pady=(2, 0))

    def _chip(
        self,
        parent: tk.Widget,
        text: str,
        *,
        live: bool = False,
        warning: bool = False,
    ) -> tk.Label:
        color = (
            self._colors.warning
            if warning
            else self._colors.accent if live else self._colors.muted
        )
        chip = tk.Label(
            parent,
            text=text,
            bg=self._colors.surface_alt,
            fg=color,
            font=("Segoe UI Semibold", 8),
            padx=8,
            pady=4,
            highlightbackground=color if live or warning else self._colors.line,
            highlightthickness=1,
        )
        chip.pack(side="left", padx=(0, 6))
        return chip

    def _build_core_visual(self, parent: tk.Widget) -> None:
        c = self._colors
        canvas = tk.Canvas(
            parent,
            width=290,
            height=290,
            bg=c.surface,
            highlightthickness=0,
            cursor="hand2",
            takefocus=True,
        )
        canvas.pack(side="right", padx=(8, 2), pady=2)
        center = 145
        canvas.create_oval(
            18,
            18,
            272,
            272,
            outline=c.line,
            dash=(2, 6),
        )
        canvas.create_oval(
            39,
            39,
            251,
            251,
            outline=c.faint,
        )
        canvas.create_oval(
            61,
            61,
            229,
            229,
            outline=c.line,
            dash=(6, 5),
        )
        canvas.create_oval(
            73,
            73,
            217,
            217,
            fill=c.surface_alt,
            outline=c.accent,
            width=1,
            tags=("core",),
        )
        for x, y in ((62, 56), (267, 146), (82, 242)):
            canvas.create_oval(
                x - 4,
                y - 4,
                x + 4,
                y + 4,
                fill=c.accent,
                outline=c.accent_strong,
            )
        canvas.create_text(
            center,
            center - 5,
            text="JARVIS",
            fill=c.ink,
            font=("Segoe UI Semibold", 12),
        )
        canvas.create_text(
            center,
            center + 15,
            text="KONUŞMAK İÇİN TIKLA",
            fill=c.muted,
            font=("Segoe UI", 8),
        )
        canvas.bind("<Button-1>", lambda _event: self._start_voice())
        canvas.bind("<Return>", lambda _event: self._start_voice())
        canvas.bind(
            "<Enter>",
            lambda _event: canvas.itemconfigure(
                "core", outline=c.accent_strong, width=2
            ),
        )
        canvas.bind(
            "<Leave>",
            lambda _event: canvas.itemconfigure(
                "core", outline=c.accent, width=1
            ),
        )
        self._home_orb = canvas
        self._animate_orb()

    def _stop_orb_animation(self) -> None:
        if self._orb_job is not None:
            self.root.after_cancel(self._orb_job)
            self._orb_job = None
        self._home_orb = None

    def _animate_orb(self) -> None:
        canvas = self._home_orb
        if (
            self._closing
            or canvas is None
            or not canvas.winfo_exists()
            or self.controller.state.screen is not UIScreen.HOME
        ):
            self._orb_job = None
            return
        if self.controller.state.reduced_motion:
            canvas.coords("core", 73, 73, 217, 217)
            self._orb_job = None
            return
        pulse = 2.5 * math.sin(self._orb_phase / 18)
        canvas.coords(
            "core",
            73 - pulse,
            73 - pulse,
            217 + pulse,
            217 + pulse,
        )
        self._orb_phase += 1
        self._orb_job = self.root.after(16, self._animate_orb)

    def render(self, screen: UIScreen) -> None:
        self._stop_orb_animation()
        self.controller.state.screen = screen
        self._snapshot = self.controller.snapshot()
        self._clear(self.workspace)
        self.workspace_scroller.reset()
        self.provider_badge.configure(text=self._snapshot.provider.upper())
        self.model_badge.configure(text=DISPLAY_MODEL_NAME)
        for value, button in self._nav_buttons.items():
            selected = value is screen
            index, label = next(
                (item[1], item[2])
                for item in NAVIGATION
                if item[0] is value
            )
            collapsed = self.controller.state.nav_collapsed
            normal = self._colors.hover if selected else self._colors.surface
            button.configure(
                bg=normal,
                fg=self._colors.accent if selected else self._colors.muted,
                text=(
                    index
                    if collapsed
                    else f"{'▎ ' if selected else ''}{index}    {label}"
                ),
                font=(
                    "Segoe UI Semibold" if selected else "Segoe UI",
                    9,
                ),
            )
            self._bind_hover(
                button,
                self._colors.surface_alt if selected else self._colors.hover,
                normal,
            )
        getattr(self, f"_render_{screen.value}")()

    def _render_home(self) -> None:
        self._heading(
            "GÖREV KOMUTASI",
            "Komuta Merkezi",
            "Canlı çalışma durumu, aktif hedefler ve sınırlı yetenekler.",
        )
        active_task = self._snapshot.tasks[0] if self._snapshot.tasks else None
        objective = (
            str(active_task.get("goal", "Aktif görev"))
            if active_task
            else "Yeni hedefin için hazırım."
        )
        task_status = (
            str(active_task.get("status", "active")).upper()
            if active_task
            else "BEKLİYOR"
        )
        core = self._card(
            self.workspace,
            "AKTİF HEDEF" if active_task else "YÜRÜTME ÇEKİRDEĞİ",
        )
        mission = tk.Frame(core, bg=self._colors.surface)
        mission.pack(side="left", fill="both", expand=True, padx=(0, 12))
        tk.Label(
            mission,
            text=(
                "GÖREV SÜRÜYOR"
                if active_task
                else "ÇEKİRDEK SİSTEMLER NORMAL"
            ),
            bg=self._colors.surface,
            fg=self._colors.accent_strong,
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w", pady=(14, 7))
        tk.Label(
            mission,
            text=objective,
            bg=self._colors.surface,
            fg=self._colors.ink,
            justify="left",
            wraplength=530,
            font=("Segoe UI Semibold", 25),
        ).pack(anchor="w")
        tk.Label(
            mission,
            text=(
                "JARVIS görev ilerlerken kanıtları, izinleri ve bağlamı "
                "korur."
                if active_task
                else "Başlamak için aşağıya bir hedef yaz veya Sohbet'i aç."
            ),
            bg=self._colors.surface,
            fg=self._colors.muted,
            justify="left",
            wraplength=530,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(10, 12))
        facts = tk.Frame(mission, bg=self._colors.surface)
        facts.pack(anchor="w")
        self._chip(facts, task_status, live=bool(active_task))
        self._chip(facts, f"{self._snapshot.enabled_tools} YETENEK")
        self._chip(facts, f"{self._snapshot.memory_count} HAFIZA")
        actions = tk.Frame(mission, bg=self._colors.surface)
        actions.pack(anchor="w", pady=(18, 0))
        self._button(
            actions,
            "SOHBETİ AÇ",
            lambda: self.render(UIScreen.CHAT),
            variant="primary",
        ).pack(side="left")
        self._button(
            actions,
            "GÖREVLERİ GÖR",
            lambda: self.render(UIScreen.TASKS),
            variant="secondary",
        ).pack(side="left", padx=7)
        self._build_core_visual(core)

        if self._snapshot.provider == "mock":
            notice = self._card(
                self.workspace,
                "Yapay zekâ sağlayıcısı ayarlanmamış",
                subtitle="Deneme modu akıllı yanıt üretemez.",
            )
            self._line(
                notice,
                "Gerçek bir sohbet için API sağlayıcısı bağla.",
                "API anahtarı proje dosyalarına değil Windows Kimlik Bilgisi Yöneticisi'ne kaydedilir.",
            )
            self._button(
                notice,
                "API AYARLARINI AÇ",
                lambda: self.render(UIScreen.SETTINGS),
                variant="primary",
            ).pack(anchor="w", pady=(10, 0))
        metrics = self._card(self.workspace, "Çalışma durumuna genel bakış")
        metric_row = tk.Frame(metrics, bg=self._colors.surface)
        metric_row.pack(fill="x")
        self._metric(metric_row, str(self._snapshot.task_count), "Görevler")
        self._metric(
            metric_row, str(self._snapshot.memory_count), "Hafızalar"
        )
        self._metric(
            metric_row, str(self._snapshot.enabled_tools), "Yetenekler"
        )
        self._metric(
            metric_row, self._snapshot.provider.upper(), "Sağlayıcı"
        )
        row = tk.Frame(self.workspace, bg=self._colors.background)
        row.pack(fill="both", expand=True)
        conversation = self._card(row, "Son sohbet", side="left")
        messages = self.controller.state.messages[-3:]
        if not messages:
            self._line(
                conversation,
                "Henüz sohbet yok",
                "Sağlayıcıyı bağladıktan sonra komut alanını kullan.",
            )
        for message in messages:
            self._line(conversation, message.text, message.role.upper())
        capabilities = self._card(row, "Yetenekler", side="left")
        for name, available in (
            ("Windows", self._snapshot.windows_available),
            ("Ses", self._snapshot.voice_available),
            ("Görüş", self._snapshot.vision_available),
            ("Araştırma", self._snapshot.research_available),
        ):
            self._line(
                capabilities,
                name,
                "KULLANILABİLİR" if available else "AYARLANMAMIŞ",
            )

    def _render_chat(self) -> None:
        self._streaming_label = None

        self._heading(
            "İNSAN ARAYÜZÜ",
            "Sohbet",
            "Hafıza, kanıt ve sınırlı izinlerle desteklenen iletişim.",
        )
        layout = tk.Frame(self.workspace, bg=self._colors.background)
        layout.pack(fill="both", expand=True)
        thread = self._card(layout, "Geçerli sohbet", side="left")
        thread_host = getattr(thread, "_rounded_host")
        thread_host.pack_configure(fill="y", expand=False)
        thread_host.configure(width=230)
        self._line(
            thread,
            "Canlı sohbet",
            f"{len(self.controller.state.messages)} mesaj",
        )
        self._line(
            thread,
            DISPLAY_MODEL_NAME,
            f"{self._snapshot.provider.upper()} çalışma zamanı",
        )
        self._line(
            thread,
            "Bağlam korunuyor",
            f"{self._snapshot.memory_count} kalıcı hafıza",
        )
        card = self._card(layout, "Canlı sohbet", side="left")
        if self._snapshot.provider == "mock":
            self._line(
                card,
                "Yapay zekâ sağlayıcısı ayarlanmamış",
                "API anahtarı eklemek için Ayarlar'ı aç. Deneme tekrarları devre dışı.",
            )
            self._button(
                card,
                "AYARLARI AÇ",
                lambda: self.render(UIScreen.SETTINGS),
                variant="primary",
            ).pack(anchor="w", pady=8)
            return
        messages = list(self.controller.state.messages)
        if (
            self._pending_user_text
            and not any(
                message.role == "user"
                and message.text == self._pending_user_text
                for message in messages[-2:]
            )
        ):
            messages.append(
                ChatMessage("user", self._pending_user_text)
            )
        if (
            self._streaming_text is not None
            and self._busy_future is not None
            and messages
            and messages[-1].role == "assistant"
        ):
            messages = messages[:-1]

        for message in messages:
            background = (
                self._colors.surface_alt
                if message.role in {"user", "system"}
                else self._colors.surface
            )
            role_color = (
                self._colors.warning
                if message.role == "system"
                else self._colors.accent_strong
                if message.role == "assistant"
                else self._colors.muted
            )
            bubble_surface = RoundedSurface(
                card,
                fill=background,
                outline=self._colors.line,
                radius=12,
                padx=12,
                pady=10,
            )
            bubble_surface.pack(fill="x", pady=4)
            bubble = bubble_surface.content
            tk.Label(
                bubble,
                text={
                    "user": "SEN",
                    "assistant": "JARVIS",
                    "system": "SİSTEM",
                }[message.role],
                bg=background,
                fg=role_color,
                font=("Segoe UI Semibold", 8),
            ).pack(anchor="w")
            tk.Label(
                bubble,
                text=message.text,
                bg=background,
                fg=self._colors.ink,
                justify="left",
                wraplength=720,
            ).pack(anchor="w", pady=(4, 0))
        if self._streaming_text is not None:
            background = self._colors.surface
            bubble_surface = RoundedSurface(
                card,
                fill=background,
                outline=self._colors.line,
                radius=12,
                padx=12,
                pady=10,
            )
            bubble_surface.pack(
                fill="x",
                pady=4,
            )
            bubble = bubble_surface.content

            tk.Label(
                bubble,
                text="JARVIS",
                bg=background,
                fg=self._colors.accent_strong,
                font=(
                    "Segoe UI Semibold",
                    7,
                ),
            ).pack(
                anchor="w"
            )

            self._streaming_label = tk.Label(
                bubble,
                text=(
                    self._streaming_text
                    or "..."
                ),
                bg=background,
                fg=self._colors.ink,
                justify="left",
                wraplength=720,
            )

            self._streaming_label.pack(
                anchor="w",
                pady=(4, 0),
            )

        if (
            not self.controller.state.messages
            and self._streaming_text is None
        ):
            self._line(card, "Henüz mesaj yok", "Başlamak için aşağıya yaz.")

    def _render_tasks(self) -> None:
        self._heading(
            "KALICI YÜRÜTME",
            "Görevler",
            "Görsel karmaşa olmadan kalıcı ilerleme ve kurtarma.",
        )
        card = self._card(self.workspace, "Görev kuyruğu")
        for task in self._snapshot.tasks:
            self._line(
                card,
                str(task["goal"]),
                f"{localize_token(task['status'])}  /  "
                f"{float(task['progress']) * 100:.0f}%  /  "
                f"{task.get('current_step') or 'Aktif adım yok'}",
            )
        if not self._snapshot.tasks:
            self._line(
                card,
                "Kalıcı görev yok",
                "Çekirdek görev oluşturduğunda burada görünür.",
            )

    def _render_memory(self) -> None:
        self._heading(
            "KAYNAK BİLİNÇLİ HAFIZA",
            "JARVIS'in hatırladıkları",
            "Kaynak, güven ve güncellik her zaman görünür kalır.",
        )
        card = self._card(self.workspace, "Aktif hafızalar")
        for memory in self._snapshot.memories:
            self._line(
                card,
                str(memory["content"]),
                f"{memory['source']}  /  "
                f"{localize_token(memory['freshness'])}  /  "
                f"güven {float(memory['confidence']):.2f}",
            )
        if not self._snapshot.memories:
            self._line(card, "Aktif hafıza yok")

    def _render_voice(self) -> None:
        self._heading(
            "SES",
            "Sakin ve kesilebilir iletişim",
            "Mikrofon yalnızca açık bir işlemden sonra kullanılır.",
        )
        card = self._card(self.workspace, "Ses oturumu")
        self._line(
            card,
            (
                self.controller.state.voice_status
                if self.controller.state.voice_active
                else "HAZIR"
                if self._snapshot.voice_available
                else "AYARLANMAMIŞ"
            ),
            "Metin sohbetinden bağımsız ses çalışma durumu",
        )
        self.voice_action_button = self._button(
            card,
            (
                "SESLİ İLETİŞİMİ DURDUR"
                if self.controller.state.voice_active
                else "SESLİ İLETİŞİMİ BAŞLAT"
            ),
            self._start_voice,
            variant=(
                "ghost" if self.controller.state.voice_active else "primary"
            ),
        )
        self.voice_action_button.configure(
            state="normal" if self._snapshot.voice_available else "disabled"
        )
        self.voice_action_button.pack(anchor="w", pady=10)

        history = self._card(self.workspace, "Ses oturumu geçmişi")
        messages = self.controller.state.voice_messages[-10:]
        if not messages:
            self._line(
                history,
                "Henüz sesli konuşma yok",
                "Ses transkriptleri normal sohbet geçmişine karışmaz.",
            )
        for message in messages:
            self._line(
                history,
                message.text,
                {
                    "user": "SEN",
                    "assistant": "JARVIS",
                    "system": "SİSTEM",
                }[message.role],
            )

    def _render_vision(self) -> None:
        self._heading(
            "GÖRÜŞ",
            "Görüntüden önce onay",
            "Her ekran görüntüsü için görünür bir onay gerekir.",
        )
        card = self._card(self.workspace, "Görüntü alma ilkesi")
        self._line(card, "Varsayılan", "Görüntü alma kapalı")
        self._line(card, "Saklama", "Analizden sonra silinir")
        self._line(card, "Görev çubuğu", "Sağlayıcı erişiminden önce gizlenir")
        button = self._button(
            card,
            "TEK GÖRÜNTÜ İSTE",
            self._start_vision,
            variant="primary",
        )
        button.configure(
            state="normal" if self._snapshot.vision_available else "disabled"
        )
        button.pack(anchor="w", pady=10)

    def _render_research(self) -> None:
        self._heading(
            "ARAŞTIRMA",
            "Sentezden önce kanıt",
            "Gözlenen gerçekler çıkarımlardan ayrı tutulur.",
        )
        card = self._card(self.workspace, "Araştırma özeti")
        self.research_entry = self._entry(card)
        self.research_entry.pack(fill="x", ipady=7, pady=5)
        button = self._button(
            card, "ARAŞTIR", self._start_research, variant="primary"
        )
        button.configure(
            state="normal"
            if self._snapshot.research_available
            else "disabled"
        )
        button.pack(anchor="e", pady=7)
        self.research_results = tk.Frame(card, bg=self._colors.surface)
        self.research_results.pack(fill="both", expand=True)
        if not self._snapshot.research_available:
            self._line(
                self.research_results,
                "Araştırma ayarlanmamış",
                "Kullanmadan önce bir arama sağlayıcısı ayarla.",
            )
        elif self._research_error:
            self._line(
                self.research_results,
                "Araştırma başarısız",
                self._research_error,
            )
        elif self._research_report is not None:
            for claim in self._research_report.get("claims", []):
                self._line(
                    self.research_results,
                    claim.get("text", ""),
                    ", ".join(claim.get("citations", [])),
                )
            for uncertainty in self._research_report.get(
                "uncertainties",
                [],
            ):
                self._line(
                    self.research_results,
                    "BELİRSİZLİK",
                    uncertainty,
                )

    def _render_tools(self) -> None:
        self._heading(
            "YETENEK KAYDI",
            "Araçlar ve izinler",
            "Kullanılabilirlik hiçbir zaman yetki anlamına gelmez.",
        )
        card = self._card(self.workspace, "Kayıtlı araçlar")
        for tool in self._snapshot.tools:
            self._line(
                card,
                str(tool["name"]),
                f"{localize_token(tool['risk'])}  /  "
                f"{'AÇIK' if tool['enabled'] else 'KAPALI'}  /  "
                f"{tool['source']}",
            )

    def _render_integrations(self) -> None:
        self._heading(
            "BAĞLANTILAR",
            "Hizmetlere genel bakış",
            "Yalnızca canlı yapılandırmalar bağlı olarak gösterilir.",
        )
        card = self._card(self.workspace, "Çalışma zamanı bağlantıları")
        for name, ready in (
            ("Windows", self._snapshot.windows_available),
            ("OpenAI", bool(self.controller.application.settings.api_key)),
            (
                "Gemini",
                bool(self.controller.application.settings.gemini_api_key),
            ),
            (
                "Ollama",
                bool(
                    self.controller.application.settings.ollama_enabled
                    or (
                        self.controller.application.settings
                        .default_provider
                        .strip()
                        .casefold()
                        == "ollama"
                    )
                ),
            ),
            ("Ses", self._snapshot.voice_available),
            ("Görüş", self._snapshot.vision_available),
            ("Web araştırması", self._snapshot.research_available),
        ):
            self._line(
                card, name, "BAĞLI" if ready else "AYARLANMAMIŞ"
            )

    def _render_diagnostics(self) -> None:
        self._heading(
            "SİSTEM SAĞLIĞI",
            "Karmaşasız tanılama",
            "Canlı çalışma zamanı incelemesinden alınan gerçek durum.",
        )
        card = self._card(self.workspace, "Geçerli sağlık")
        self._line(card, "Çekirdek", "HAZIR")
        self._line(card, "Sağlayıcı", self._snapshot.provider)
        self._line(
            card,
            "Araç kaydı",
            f"{self._snapshot.enabled_tools}/{self._snapshot.tool_count} açık",
        )
        self._line(card, "Kalıcı görevler", str(self._snapshot.task_count))
        self._line(
            card,
            "Olay defteri",
            (
                f"GEÇERLİ  /  {self._snapshot.diagnostic_event_count} olay"
                if self._snapshot.diagnostic_integrity_valid
                else "BÜTÜNLÜK HATASI"
            ),
        )

    def _render_settings(self) -> None:
        self._heading(
            "AYARLAR",
            "Sade kontroller, açık durum",
            "Gizli bilgiler proje ve kaynak kontrolü dışında kalır.",
        )
        appearance = self._card(self.workspace, "Görünüm")
        controls = tk.Frame(appearance, bg=self._colors.surface)
        controls.pack(fill="x")
        self._button(
            controls, "TEMAYI DEĞİŞTİR", self._toggle_theme
        ).pack(side="left")
        self._button(
            controls,
            "HAREKETİ AZALT",
            self._toggle_motion,
            variant="ghost",
        ).pack(side="left", padx=5)
        self._button(
            controls, "KLAVYE KISAYOLLARI", self._show_shortcuts, variant="ghost"
        ).pack(side="left")
        self._line(
            appearance,
            "Hareket",
            "AZALTILMIŞ"
            if self.controller.state.reduced_motion
            else "YUMUŞAK",
        )

        api = self._card(
            self.workspace,
            "Yapay zekâ sağlayıcısı ve API anahtarı",
            subtitle=(
                "Gemini veya OpenAI anahtarını buraya yapıştır. Anahtar proje "
                "dosyalarına değil Windows Kimlik Bilgisi Yöneticisi'ne kaydedilir."
            ),
        )
        if self.api_settings is None:
            self._line(
                api,
                "API ayarları kullanılamıyor",
                "Kimlik bilgisi hizmeti başlatılmadı.",
            )
            return
        state = self.api_settings.snapshot()
        self.api_provider = tk.StringVar(value=state.provider)
        self.api_model = tk.StringVar(value=state.model)
        self.api_key = tk.StringVar()
        self._field_label(api, "SAĞLAYICI")
        provider_input = ttk.Combobox(
            api,
            textvariable=self.api_provider,
            values=("gemini", "openai", "ollama", "mock"),
            state="readonly",
            style="Jarvis.TCombobox",
        )
        provider_input.pack(fill="x", pady=(0, 10))
        provider_input.bind("<<ComboboxSelected>>", self._on_api_provider_changed)
        self._field_label(api, "MODEL")
        self._entry(api, textvariable=self.api_model).pack(
            fill="x", ipady=7, pady=(0, 10)
        )
        self._field_label(api, "API ANAHTARI")
        key_row = tk.Frame(api, bg=self._colors.surface)
        key_row.pack(fill="x")
        self.api_key_entry = self._entry(
            key_row,
            textvariable=self.api_key,
            show="*",
        )
        self.api_key_entry.pack(
            side="left",
            fill="x",
            expand=True,
            ipady=7,
        )
        self.api_key_visibility_button = self._button(
            key_row,
            "GÖSTER",
            self._toggle_api_key_visibility,
            variant="ghost",
        )
        self.api_key_visibility_button.pack(side="right", padx=(6, 0))

        if not state.credential_required:
            self.api_key_entry.configure(
                state="disabled"
            )
            self.api_key_visibility_button.configure(state="disabled")
            self._line(
                api,
                "API anahtarı gerekmiyor",
                (
                    "Ollama yerel Ollama hizmetini kullanır."
                    if state.provider == "ollama"
                    else (
                        "Bu sağlayıcı API kimlik bilgisi kullanmaz."
                    )
                ),
            )
        elif state.credential_configured:
            self._line(
                api,
                "Anahtar güvenle saklanıyor",
                "Mevcut anahtarı korumak için alanı boş bırak.",
            )
        else:
            self._line(
                api,
                "Kayıtlı anahtar yok",
                "Anahtarı yapıştır, sına, ardından kaydedip etkinleştir.",
            )
        self.api_status = tk.Label(
            api,
            text="",
            bg=self._colors.surface,
            fg=self._colors.muted,
            justify="left",
            wraplength=680,
        )
        self.api_status.pack(fill="x", pady=(4, 8))
        if self._api_test_feedback is not None:
            feedback, ok = self._api_test_feedback
            self.api_status.configure(
                text=feedback,
                fg=self._colors.ink if ok else self._colors.muted,
            )
        actions = tk.Frame(api, bg=self._colors.surface)
        actions.pack(fill="x")
        self.api_test_button = self._button(
            actions, "BAĞLANTIYI SINA", self._start_api_test
        )
        self.api_test_button.pack(side="left")
        self._button(
            actions,
            "KAYDET VE ETKİNLEŞTİR",
            self._save_api_settings,
            variant="primary",
        ).pack(side="left", padx=6)
        self.api_delete_button = self._button(
            actions,
            "ANAHTARI SİL",
            self._delete_api_key,
            variant="ghost",
        )
        self.api_delete_button.configure(
            state=(
                "normal"
                if (
                    state.credential_required
                    and state.credential_configured
                )
                else "disabled"
            )
        )
        self.api_delete_button.pack(
            side="right"
        )

    def _entry(
        self,
        parent: tk.Widget,
        *,
        textvariable: tk.StringVar | None = None,
        show: str | None = None,
    ) -> RoundedEntry:
        return RoundedEntry(
            parent,
            colors=self._colors,
            textvariable=textvariable,
            show=show,
        )

    def _field_label(self, parent: tk.Widget, text: str) -> None:
        tk.Label(
            parent,
            text=text,
            bg=self._colors.surface,
            fg=self._colors.faint,
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w", pady=(3, 5))

    def _on_api_provider_changed(self, _event: object | None = None) -> None:
        defaults = {
            "gemini": DEFAULT_GEMINI_MODEL,
            "openai": "gpt-4o-mini",
            "ollama": "llama3.2:latest",
            "mock": "mock-model",
        }

        provider = (
            self.api_provider.get()
        )

        self.api_model.set(
            defaults[provider]
        )
        self.api_key.set("")

        required = (
            self.api_settings is not None
            and self.api_settings.requires_credential(
                provider
            )
        )

        entry = getattr(
            self,
            "api_key_entry",
            None,
        )

        if entry is not None:
            entry.configure(
                state=(
                    "normal"
                    if required
                    else "disabled"
                )
            )

        visibility = getattr(
            self,
            "api_key_visibility_button",
            None,
        )
        if visibility is not None and entry is not None:
            visibility.configure(
                state="normal" if required else "disabled",
                text="GÖSTER",
            )
            entry.configure(show="*")

        delete = getattr(
            self,
            "api_delete_button",
            None,
        )

        if delete is not None:
            delete.configure(
                state="disabled"
            )

    def _toggle_api_key_visibility(self) -> None:
        visible = self.api_key_entry.cget("show") == ""
        self.api_key_entry.configure(show="*" if visible else "")
        self.api_key_visibility_button.configure(
            text="GÖSTER" if visible else "GİZLE"
        )

    def _render_context(self, tab: str) -> None:
        self._clear(self.context_body)
        for label, button in self._context_buttons.items():
            selected = label == tab
            normal = (
                self._colors.hover
                if selected
                else self._colors.surface_alt
            )
            button.configure(
                bg=normal,
                fg=(
                    self._colors.accent
                    if selected
                    else self._colors.muted
                ),
            )
            self._bind_hover(button, self._colors.hover, normal)
        if tab == "YÜRÜTME":
            for task in self._snapshot.tasks[:4]:
                self._line(
                    self.context_body,
                    str(task["goal"]),
                    localize_token(task["status"]),
                )
            if not self._snapshot.tasks:
                self._line(
                    self.context_body, "Aktif görev yok", "Kuyruk boş."
                )
        elif tab == "HAFIZA":
            for memory in self._snapshot.memories[:6]:
                self._line(
                    self.context_body,
                    str(memory["content"]),
                    localize_token(memory["freshness"]),
                )
            if not self._snapshot.memories:
                self._line(self.context_body, "Aktif hafıza yok")
        else:
            for tool in self._snapshot.tools[:9]:
                self._line(
                    self.context_body,
                    str(tool["name"]),
                    "AÇIK" if tool["enabled"] else "KAPALI",
                )

    def _toggle_navigation(self) -> None:
        collapsed = not self.controller.state.nav_collapsed
        self.controller.state.nav_collapsed = collapsed
        target = 64 if collapsed else 230
        for screen, index, label in NAVIGATION:
            self._nav_buttons[screen].configure(
                text=index if collapsed else f"{index}    {label}",
                anchor="center" if collapsed else "w",
            )
        self.nav_collapse_button.configure(
            text="AÇ" if collapsed else "DARALT"
        )
        self._animate_nav_width(target)

    def _animate_nav_width(self, target: int) -> None:
        if self._nav_job is not None:
            self.root.after_cancel(self._nav_job)
            self._nav_job = None
        if self.controller.state.reduced_motion:
            self.nav.configure(width=target)
            return
        current = int(self.nav.cget("width"))
        if current == target:
            self._nav_job = None
            return
        delta = max(8, abs(target - current) // 4)
        value = (
            min(target, current + delta)
            if target > current
            else max(target, current - delta)
        )
        self.nav.configure(width=value)
        self._nav_job = self.root.after(
            14, lambda: self._animate_nav_width(target)
        )

    def _toggle_theme(self) -> None:
        self.controller.state.theme = (
            UITheme.LIGHT
            if self.controller.state.theme is UITheme.DARK
            else UITheme.DARK
        )
        if self._status_job is not None:
            self.root.after_cancel(self._status_job)
            self._status_job = None
        for child in self.root.winfo_children():
            child.destroy()
        self._colors = tokens(self.controller.state.theme)
        self.root.configure(bg=self._colors.background)
        self._nav_buttons.clear()
        self._context_buttons.clear()
        self._build_shell()
        self.render(self.controller.state.screen)
        self._animate_status()

    def _toggle_motion(self) -> None:
        self.controller.state.reduced_motion = (
            not self.controller.state.reduced_motion
        )
        self.render(UIScreen.SETTINGS)

    def _animate_status(self) -> None:
        if self._closing:
            return
        colors = (
            self._colors.faint,
            self._colors.muted,
            self._colors.ink,
            self._colors.muted,
        )
        color = (
            colors[0]
            if self.controller.state.reduced_motion
            else colors[self._pulse_frame % len(colors)]
        )
        self.status_dot.itemconfigure(self._status_oval, fill=color)
        self._pulse_frame += 1
        self._status_job = self.root.after(520, self._animate_status)

    def _schedule_ui_event_pump(self) -> None:
        if self._closing or self._ui_poll_job is not None:
            return
        self._ui_poll_job = self.root.after(16, self._drain_ui_events)

    def _queue_ui_event(
        self,
        operation_id: int,
        kind: str,
        payload: object,
    ) -> None:
        self._ui_events.put(UIEvent(operation_id, kind, payload))

    def _drain_ui_events(self) -> None:
        self._ui_poll_job = None
        if self._closing:
            return
        while True:
            try:
                event = self._ui_events.get_nowait()
            except Empty:
                break
            voice_event = event.kind.startswith("voice_")
            expected_operation = (
                self._voice_operation_id
                if voice_event
                else self._active_operation_id
            )
            if event.operation_id != expected_operation:
                continue
            try:
                if event.kind == "command_stream":
                    self._apply_stream_text(
                        event.operation_id,
                        event.payload,
                    )
                elif event.kind == "command_done":
                    self._finish_command(
                        event.operation_id,
                        event.payload,
                    )
                elif event.kind == "api_done":
                    self._finish_api_test(
                        event.operation_id,
                        event.payload,
                    )
                elif event.kind == "voice_done":
                    self._finish_voice(
                        event.operation_id,
                        event.payload,
                    )
                elif event.kind == "voice_message":
                    self._apply_voice_message(
                        event.operation_id,
                        event.payload,
                    )
                elif event.kind == "voice_stop_done":
                    self._finish_voice_stop(
                        event.operation_id,
                        event.payload,
                    )
                elif event.kind == "aux_done":
                    self._finish_aux(
                        event.operation_id,
                        event.payload,
                    )
                elif event.kind == "research_done":
                    self._finish_research(
                        event.operation_id,
                        event.payload,
                    )
                else:
                    raise RuntimeError(f"Unknown UI event: {event.kind}")
            except Exception as exc:
                if voice_event:
                    self._recover_voice_operation(event.operation_id, exc)
                else:
                    self._recover_operation(event.operation_id, exc)
        self._schedule_ui_event_pump()

    def _begin_operation(self, status: str) -> int | None:
        if self._active_operation_id is not None:
            return None
        self._operation_sequence += 1
        operation_id = self._operation_sequence
        self._active_operation_id = operation_id
        self.controller.state.busy = True
        self.controller.state.status = status
        self._set_busy(True, status)
        return operation_id

    def _launch_operation(
        self,
        operation_id: int,
        operation: Coroutine[Any, Any, Any],
        done_event: str,
    ) -> bool:
        try:
            future = self.controller.submit_background(
                operation,
                lambda result, value=operation_id, kind=done_event: (
                    self._queue_ui_event(value, kind, result)
                ),
            )
        except Exception as exc:
            operation.close()
            self._recover_operation(operation_id, exc)
            return False
        self._busy_future = future
        return True

    def _complete_operation(
        self,
        operation_id: int,
        status: str = "LOCAL CORE READY",
    ) -> bool:
        if operation_id != self._active_operation_id:
            return False
        self._busy_future = None
        self._active_operation_id = None
        self.controller.state.busy = False
        if self.controller.state.voice_active:
            status = self.controller.state.voice_status
        self.controller.state.status = status
        self._set_busy(False, status)
        return True

    def _begin_voice_operation(self) -> int | None:
        if self._voice_operation_id is not None:
            return None
        self._operation_sequence += 1
        operation_id = self._operation_sequence
        self._voice_operation_id = operation_id
        self._voice_stop_requested = False
        self.controller.state.voice_active = True
        self.controller.state.voice_status = "DİNLİYOR"
        if self._active_operation_id is None:
            self.controller.state.status = "LISTENING"
            self._set_busy(False, "LISTENING")
        self._update_voice_controls()
        return operation_id

    def _launch_voice_operation(
        self,
        operation_id: int,
        operation: Coroutine[Any, Any, Any],
    ) -> bool:
        try:
            future = self.controller.submit_background(
                operation,
                lambda result, current=operation_id: self._queue_ui_event(
                    current,
                    "voice_done",
                    result,
                ),
            )
        except Exception as exc:
            operation.close()
            self._recover_voice_operation(operation_id, exc)
            return False
        self._voice_future = future
        return True

    def _complete_voice_operation(self, operation_id: int) -> bool:
        if operation_id != self._voice_operation_id:
            return False
        self._voice_future = None
        self._voice_stop_future = None
        self._voice_operation_id = None
        self._voice_stop_requested = False
        self.controller.state.voice_active = False
        self.controller.state.voice_status = "IDLE"
        if self._active_operation_id is None:
            self.controller.state.status = "LOCAL CORE READY"
            self._set_busy(False, "LOCAL CORE READY")
        self._update_voice_controls()
        return True

    def _update_voice_controls(self) -> None:
        active = self.controller.state.voice_active
        status = self.controller.state.voice_status if active else "BEKLİYOR"
        try:
            badge = getattr(self, "voice_status_badge", None)
            if badge is not None and badge.winfo_exists():
                badge.configure(
                    text=(
                        self.controller.state.voice_status
                        if active
                        else "PASİF"
                        if self._snapshot.voice_available
                        else "ÇEVRİMDIŞI"
                    )
                )
            composer_button = getattr(
                self,
                "composer_voice_button",
                None,
            )
            if composer_button is not None and composer_button.winfo_exists():
                composer_button.configure(
                    text="SESİ DURDUR" if active else "MİKROFON"
                )
            state_label = getattr(
                self,
                "composer_voice_state_label",
                None,
            )
            if state_label is not None and state_label.winfo_exists():
                state_label.configure(text=status)
            action_button = getattr(self, "voice_action_button", None)
            if action_button is not None and action_button.winfo_exists():
                action_button.configure(
                    text=(
                        "SESLİ İLETİŞİMİ DURDUR"
                        if active
                        else "SESLİ İLETİŞİMİ BAŞLAT"
                    )
                )
        except tk.TclError:
            return

    def _recover_voice_operation(
        self,
        operation_id: int,
        error: Exception,
    ) -> None:
        if operation_id != self._voice_operation_id:
            return
        stopped = self._voice_stop_requested
        if self._voice_future is not None and not self._voice_future.done():
            self._voice_future.cancel()
        if not stopped:
            self.controller.state.voice_messages.append(
                ChatMessage(
                    "system",
                    "Ses oturumu güvenli biçimde durduruldu. "
                    "Metin sohbeti kullanılabilir.",
                )
            )
        self._complete_voice_operation(operation_id)
        if self._closing:
            return
        if self.controller.state.screen is UIScreen.VOICE:
            self.render(UIScreen.VOICE)
        if not stopped:
            try:
                messagebox.showerror(
                    "JARVIS sesli iletişimi başarısız",
                    str(error),
                    parent=self.root,
                )
            except tk.TclError:
                return

    def _recover_operation(self, operation_id: int, error: Exception) -> None:
        if operation_id != self._active_operation_id:
            return
        if self._busy_future is not None and not self._busy_future.done():
            self._busy_future.cancel()
        command_failed = operation_id == self._command_operation_id
        if command_failed:
            if self._typewriter_job is not None:
                self.root.after_cancel(self._typewriter_job)
                self._typewriter_job = None
            self._streaming_text = None
            self._stream_target_text = ""
            self._streaming_label = None
            self._command_complete_pending = False
            self._pending_user_text = None
            self._command_operation_id = None
            self.controller.state.messages.append(
                ChatMessage(
                    "system",
                    "İstek tamamlanamadı. JARVIS oturumu korundu; "
                    "tekrar deneyebilirsin.",
                )
            )
        self._complete_operation(operation_id)
        if self._closing:
            return
        try:
            if command_failed:
                self.render(UIScreen.CHAT)
            messagebox.showerror(
                "JARVIS işlemi başarısız",
                str(error),
                parent=self.root,
            )
        except tk.TclError:
            return

    def _set_busy(self, busy: bool, status: str) -> None:
        try:
            if self.status_label.winfo_exists():
                self.status_label.configure(
                    text=STATUS_TEXT.get(status, status)
                )
            if self.send_button.winfo_exists():
                self.send_button.configure(
                    state="disabled" if busy else "normal"
                )
        except tk.TclError:
            return

    def _submit_command(self) -> str:
        text = self.command.get("1.0", "end").strip()
        if not text or self._active_operation_id is not None:
            return "break"
        if self._snapshot.provider == "mock":
            self.render(UIScreen.SETTINGS)
            messagebox.showinfo(
                "Sağlayıcı gerekli",
                "Sohbete başlamadan önce Ayarlar'dan gerçek bir sağlayıcı seçip etkinleştir.",
                parent=self.root,
            )
            return "break"
        self.command.delete("1.0", "end")

        self._streaming_text = ""
        self._stream_target_text = ""
        self._streaming_label = None
        self._typing_frame = 0
        self._command_complete_pending = False
        self._pending_user_text = None

        operation_id = self._begin_operation("PROCESSING")
        if operation_id is None:
            return "break"
        self._command_operation_id = operation_id
        self.controller.state.messages.append(ChatMessage("user", text))

        launched = self._launch_operation(
            operation_id,
            self.controller.submit_command(
                text,
                stream_callback=(
                    lambda value, current=operation_id: self._queue_ui_event(
                        current,
                        "command_stream",
                        value,
                    )
                ),
                manage_state=False,
            ),
            "command_done",
        )
        if not launched:
            return "break"
        self.render(UIScreen.CHAT)
        self._schedule_typewriter(180)
        return "break"

    def _on_stream_text(
        self,
        operation_id: int,
        text: str,
    ) -> None:
        self._queue_ui_event(operation_id, "command_stream", text)

    def _apply_stream_text(
        self,
        operation_id: int,
        text: object,
    ) -> None:
        if (
            operation_id != self._active_operation_id
            or not isinstance(text, str)
            or not text
        ):
            return

        self._stream_target_text = text

        label = self._streaming_label

        if (
            self.controller.state.screen
            is not UIScreen.CHAT
            or label is None
            or not label.winfo_exists()
        ):
            self.render(
                UIScreen.CHAT
            )

            label = (
                self._streaming_label
            )

        if (
            label is not None
            and label.winfo_exists()
        ):
            self._schedule_typewriter(0)

    def _schedule_typewriter(self, delay: int = 16) -> None:
        if self._closing or self._typewriter_job is not None:
            return
        self._typewriter_job = self.root.after(
            delay,
            self._animate_stream_text,
        )

    def _animate_stream_text(self) -> None:
        self._typewriter_job = None
        if self._busy_future is None or self._streaming_text is None:
            return

        label = self._streaming_label
        if label is None or not label.winfo_exists():
            self.render(UIScreen.CHAT)
            label = self._streaming_label

        target = self._stream_target_text
        if target:
            if self.controller.state.reduced_motion:
                rendered = target
            else:
                rendered = next_typewriter_text(
                    self._streaming_text,
                    target,
                )
            self._streaming_text = rendered
            if label is not None and label.winfo_exists():
                label.configure(text=rendered)
            if rendered != target:
                self._schedule_typewriter()
            elif self._command_complete_pending:
                self._finalize_command()
                return
        else:
            if (
                label is not None
                and label.winfo_exists()
                and not self.controller.state.reduced_motion
            ):
                label.configure(text="." * (1 + self._typing_frame % 3))
                self._typing_frame += 1
            self._schedule_typewriter(180)

        self.workspace_scroller.canvas.update_idletasks()
        self.workspace_scroller.canvas.yview_moveto(1.0)

    def _on_command_done(
        self,
        operation_id: int,
        future: Future[Any],
    ) -> None:
        self._queue_ui_event(operation_id, "command_done", future)

    def _finish_command(
        self,
        operation_id: int,
        payload: object,
    ) -> None:
        if operation_id != self._active_operation_id:
            return
        if not isinstance(payload, Future):
            raise TypeError("Command completion payload must be a Future.")
        try:
            message = payload.result()
        except Exception as exc:
            self.controller.state.messages.append(
                ChatMessage(
                    "system",
                    "İstek tamamlanamadı. JARVIS oturumu korundu; "
                    "tekrar deneyebilirsin.",
                )
            )
            self._finalize_command(operation_id)
            messagebox.showerror(
                "JARVIS isteği başarısız",
                str(exc),
                parent=self.root,
            )
            return

        self._pending_user_text = None
        if isinstance(message, ChatMessage):
            self.controller.state.messages.append(message)
        self.controller.state.status = "LOCAL CORE READY"

        if (
            not self._stream_target_text
            and getattr(message, "role", None) == "assistant"
            and isinstance(getattr(message, "text", None), str)
        ):
            self._stream_target_text = message.text

        if (
            self._stream_target_text
            and self._streaming_text != self._stream_target_text
            and not self.controller.state.reduced_motion
        ):
            self._command_complete_pending = True
            self._set_busy(True, "RESPONDING")
            self._schedule_typewriter(0)
            return

        self._finalize_command(operation_id)

    def _finalize_command(self, operation_id: int | None = None) -> None:
        operation_id = operation_id or self._command_operation_id
        if operation_id is None or operation_id != self._active_operation_id:
            return
        if self._typewriter_job is not None:
            self.root.after_cancel(self._typewriter_job)
            self._typewriter_job = None
        self._streaming_text = None
        self._stream_target_text = ""
        self._streaming_label = None
        self._command_complete_pending = False
        self._pending_user_text = None
        self._command_operation_id = None
        self._complete_operation(operation_id)
        self.render(UIScreen.CHAT)
        self.root.after(0, self._scroll_chat_to_bottom)

    def _scroll_chat_to_bottom(self) -> None:
        if self.controller.state.screen is not UIScreen.CHAT:
            return
        self.workspace_scroller.canvas.update_idletasks()
        self.workspace_scroller.canvas.yview_moveto(1.0)

    def _start_api_test(self) -> None:
        if self.api_settings is None or self._active_operation_id is not None:
            return
        self.api_status.configure(text="Bağlantı sınanıyor...")
        self.api_test_button.configure(state="disabled")
        operation_id = self._begin_operation("TESTING CONNECTION")
        if operation_id is None:
            return
        self._launch_operation(
            operation_id,
            self.api_settings.test_connection(
                self.api_provider.get(),
                self.api_model.get(),
                self.api_key.get(),
            ),
            "api_done",
        )

    def _on_api_test_done(
        self,
        operation_id: int,
        future: Future[Any],
    ) -> None:
        self._queue_ui_event(operation_id, "api_done", future)

    def _finish_api_test(
        self,
        operation_id: int,
        payload: object,
    ) -> None:
        if not isinstance(payload, Future):
            raise TypeError("API completion payload must be a Future.")
        try:
            result = payload.result()
            self._api_test_feedback = (result.message, bool(result.ok))
        except Exception as exc:
            self._api_test_feedback = (
                f"Bağlantı sınaması başarısız: {exc}",
                False,
            )
        finally:
            self._complete_operation(operation_id)

        if self.controller.state.screen is UIScreen.SETTINGS:
            status = getattr(self, "api_status", None)
            button = getattr(self, "api_test_button", None)
            if status is not None and status.winfo_exists():
                feedback, ok = self._api_test_feedback
                status.configure(
                    text=feedback,
                    fg=self._colors.ink if ok else self._colors.muted,
                )
            if button is not None and button.winfo_exists():
                button.configure(state="normal")

    def _save_api_settings(self) -> None:
        if self.api_settings is None or self._busy_future is not None:
            return
        try:
            self.api_settings.save(
                self.api_provider.get(),
                self.api_model.get(),
                self.api_key.get(),
            )
            from app.bootstrap import create_application

            application = create_application(
                self.api_settings.build_runtime_settings()
            )
            self.controller.replace_application(application)
            self.api_key.set("")
            self._snapshot = self.controller.snapshot()
            self.render(UIScreen.SETTINGS)
            self.api_status.configure(
                text="Ayarlar kaydedildi. Canlı çalışma zamanı etkin.",
                fg=self._colors.ink,
            )
            self._set_busy(False, "LOCAL CORE READY")
        except Exception as exc:
            messagebox.showerror(
                "API ayarları kaydedilemedi", str(exc), parent=self.root
            )

    def _delete_api_key(self) -> None:
        if self.api_settings is None:
            return
        if not messagebox.askyesno(
            "API anahtarı silinsin mi?",
            "Bu işlem JARVIS API anahtarını Windows Kimlik Bilgisi "
            "Yöneticisi'nden siler ve uygulamayı deneme moduna döndürür.",
            parent=self.root,
        ):
            return
        try:
            self.api_settings.delete_api_key()
            from app.bootstrap import create_application

            application = create_application(
                self.api_settings.build_runtime_settings()
            )
            self.controller.replace_application(application)
            self._snapshot = self.controller.snapshot()
            self.render(UIScreen.SETTINGS)
            self.api_status.configure(
                text="Anahtar silindi. Deneme modu etkin."
            )
        except Exception as exc:
            messagebox.showerror(
                "API anahtarı silinemedi", str(exc), parent=self.root
            )

    def _start_voice(self) -> None:
        if self._voice_operation_id is not None:
            self._stop_voice()
            return
        if not self._snapshot.voice_available:
            messagebox.showinfo(
                "Ses", "Sesli iletişim ayarlanmamış.", parent=self.root
            )
            return
        operation_id = self._begin_voice_operation()
        if operation_id is None:
            return
        self._launch_voice_operation(
            operation_id,
            self.controller.run_voice(
                message_callback=(
                    lambda message, current=operation_id: (
                        self._queue_ui_event(
                            current,
                            "voice_message",
                            message,
                        )
                    )
                ),
                manage_state=False,
            ),
        )

    def _stop_voice(self) -> None:
        operation_id = self._voice_operation_id
        if operation_id is None or self._voice_stop_requested:
            return
        self._voice_stop_requested = True
        self.controller.state.voice_status = "DURDURULUYOR"
        self._update_voice_controls()
        operation = self.controller.interrupt_voice()
        try:
            self._voice_stop_future = self.controller.submit_background(
                operation,
                lambda result, current=operation_id: self._queue_ui_event(
                    current,
                    "voice_stop_done",
                    result,
                ),
            )
        except Exception:
            operation.close()
            if self._voice_future is not None:
                self._voice_future.cancel()

    def _finish_voice_stop(
        self,
        operation_id: int,
        payload: object,
    ) -> None:
        if operation_id != self._voice_operation_id:
            return
        if not isinstance(payload, Future):
            raise TypeError("Voice stop payload must be a Future.")
        try:
            interrupted = bool(payload.result())
        except Exception:
            interrupted = False
        self._voice_stop_future = None
        if not interrupted and self._voice_future is not None:
            self._voice_future.cancel()

    def _apply_voice_message(
        self,
        operation_id: int,
        payload: object,
    ) -> None:
        if operation_id != self._voice_operation_id:
            return
        if not isinstance(payload, ChatMessage):
            raise TypeError("Voice message payload must be a ChatMessage.")
        self.controller.state.voice_messages.append(payload)
        self.controller.state.voice_status = "DİNLİYOR"
        self._update_voice_controls()
        if self.controller.state.screen is UIScreen.VOICE:
            self.render(UIScreen.VOICE)

    def _on_voice_done(
        self,
        operation_id: int,
        future: Future[Any],
    ) -> None:
        self._queue_ui_event(operation_id, "voice_done", future)

    def _finish_voice(
        self,
        operation_id: int,
        payload: object,
    ) -> None:
        if not isinstance(payload, Future):
            raise TypeError("Voice completion payload must be a Future.")
        try:
            payload.result()
        except CancelledError:
            if not self._voice_stop_requested:
                self.controller.state.voice_messages.append(
                    ChatMessage(
                        "system",
                        "Ses oturumu beklenmedik biçimde kesildi.",
                    )
                )
        except Exception as exc:
            self.controller.state.voice_messages.append(
                ChatMessage(
                    "system",
                    f"Ses oturumu durdu: {type(exc).__name__}.",
                )
            )
            if not self._voice_stop_requested:
                messagebox.showerror(
                    "JARVIS sesli iletişimi başarısız",
                    str(exc),
                    parent=self.root,
                )
        finally:
            self._complete_voice_operation(operation_id)
        if self.controller.state.screen is UIScreen.VOICE:
            self.render(UIScreen.VOICE)

    def _start_vision(self) -> None:
        if (
            not self._snapshot.vision_available
            or self._active_operation_id is not None
        ):
            messagebox.showinfo(
                "Görüş", "Görsel analiz ayarlanmamış.", parent=self.root
            )
            return
        purpose = (
            "Geçerli ekranı açıkla ve kullanıcının görünür sorusunu yanıtla."
        )
        approved = messagebox.askyesno(
            "Tek ekran görüntüsüne izin verilsin mi?",
            "JARVIS bir kez görüntü alır, görev çubuğunu gizler, işlenmiş "
            "görseli ayarlı sağlayıcıya yollar ve analizden sonra siler.",
            parent=self.root,
        )
        if not approved:
            return
        operation_id = self._begin_operation("CAPTURING")
        if operation_id is None:
            return
        self._launch_operation(
            operation_id,
            self.controller.run_vision(purpose),
            "aux_done",
        )

    def _start_research(self) -> None:
        query = self.research_entry.get().strip()
        if not query or self._active_operation_id is not None:
            return
        operation_id = self._begin_operation("RESEARCHING")
        if operation_id is None:
            return
        self._research_report = None
        self._research_error = None
        self._launch_operation(
            operation_id,
            self.controller.run_research(query),
            "research_done",
        )

    def _on_aux_done(
        self,
        operation_id: int,
        future: Future[Any],
    ) -> None:
        self._queue_ui_event(operation_id, "aux_done", future)

    def _finish_aux(self, operation_id: int, payload: object) -> None:
        if not isinstance(payload, Future):
            raise TypeError("Auxiliary completion payload must be a Future.")
        try:
            messagebox.showinfo(
                "JARVIS", str(payload.result()), parent=self.root
            )
        except Exception as exc:
            messagebox.showerror(
                "JARVIS işlemi başarısız", str(exc), parent=self.root
            )
        finally:
            self._complete_operation(operation_id)

    def _on_research_done(
        self,
        operation_id: int,
        future: Future[Any],
    ) -> None:
        self._queue_ui_event(operation_id, "research_done", future)

    def _finish_research(
        self,
        operation_id: int,
        payload: object,
    ) -> None:
        if not isinstance(payload, Future):
            raise TypeError("Research completion payload must be a Future.")
        try:
            self._research_report = payload.result()
            self._research_error = None
        except Exception as exc:
            self._research_report = None
            self._research_error = str(exc)
        finally:
            self._complete_operation(operation_id)
        if self.controller.state.screen is UIScreen.RESEARCH:
            self.render(UIScreen.RESEARCH)

    def close(self) -> None:
        self._closing = True
        self._stop_orb_animation()
        if self._ui_poll_job is not None:
            self.root.after_cancel(self._ui_poll_job)
            self._ui_poll_job = None
        if self._status_job is not None:
            self.root.after_cancel(self._status_job)
        if self._nav_job is not None:
            self.root.after_cancel(self._nav_job)
        if self._typewriter_job is not None:
            self.root.after_cancel(self._typewriter_job)
        if self._busy_future is not None:
            self._busy_future.cancel()
        if self._voice_stop_future is not None:
            self._voice_stop_future.cancel()
        if self._voice_future is not None:
            self._voice_future.cancel()
        self._busy_future = None
        self._voice_stop_future = None
        self._voice_future = None
        self._active_operation_id = None
        self._command_operation_id = None
        self._voice_operation_id = None
        self.controller.close()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def launch_desktop() -> None:
    from app.bootstrap import create_application

    enable_high_dpi_rendering()
    api_settings = create_api_settings_service()
    application = create_application(api_settings.build_runtime_settings())
    DesktopWindow(
        DesktopController(application), api_settings=api_settings
    ).run()
