from __future__ import annotations

import sys
import tkinter as tk
from concurrent.futures import Future
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable

from app.ui.api_settings import APISettingsService, create_api_settings_service
from app.ui.controller import DesktopController
from app.ui.models import UIScreen, UITheme
from app.ui.theme import ThemeTokens, tokens

NAVIGATION = (
    (UIScreen.HOME, "01", "Overview"),
    (UIScreen.CHAT, "02", "Conversation"),
    (UIScreen.TASKS, "03", "Tasks"),
    (UIScreen.MEMORY, "04", "Memory"),
    (UIScreen.VOICE, "05", "Voice"),
    (UIScreen.VISION, "06", "Vision"),
    (UIScreen.RESEARCH, "07", "Research"),
    (UIScreen.TOOLS, "08", "Tools"),
    (UIScreen.INTEGRATIONS, "09", "Connections"),
    (UIScreen.DIAGNOSTICS, "10", "Diagnostics"),
    (UIScreen.SETTINGS, "11", "Settings"),
)

SHORTCUTS = (
    ("Enter", "Send the prompt from the command composer"),
    ("Shift + Enter", "Insert a new line in the command composer"),
    ("Ctrl + L", "Focus the command composer"),
    ("Ctrl + ,", "Open Settings"),
    ("Ctrl + Shift + T", "Toggle the light/dark grayscale theme"),
    ("Ctrl + Shift + N", "Collapse or expand navigation"),
    ("Alt + 1 ... 9", "Open navigation screens 1 through 9"),
    ("Alt + 0", "Open Diagnostics"),
    ("Alt + S", "Open Settings"),
    ("F1", "Show keyboard shortcuts"),
    ("Escape", "Return focus to the workspace"),
)


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
        self.scrollbar = tk.Scrollbar(
            self,
            orient="vertical",
            command=self.canvas.yview,
            relief="flat",
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
        self._busy_future: Future[Any] | None = None
        self._streaming_text: str | None = None
        self._streaming_label: tk.Label | None = None
        self._status_job: str | None = None
        self._nav_job: str | None = None
        self._pulse_frame = 0
        self._closing = False
        self._snapshot = self.controller.snapshot()
        self._configure_window()
        self._build_shell()
        self._bind_shortcuts()
        self.render(UIScreen.HOME)
        self._animate_status()
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
        self.root.geometry("1360x840")
        self.root.minsize(1040, 700)
        self.root.configure(bg=self._colors.background)
        self.root.option_add("*Font", ("Segoe UI", 10))

    def _build_shell(self) -> None:
        c = self._colors
        self._configure_ttk()
        topbar = tk.Frame(self.root, bg=c.background, height=68)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)
        brand = tk.Frame(topbar, bg=c.background)
        brand.pack(side="left", padx=(22, 16), pady=14)
        tk.Label(
            brand,
            text="J",
            bg=c.ink,
            fg=c.inverse,
            font=("Segoe UI", 14, "bold"),
            width=2,
            pady=4,
        ).pack(side="left")
        copy = tk.Frame(brand, bg=c.background)
        copy.pack(side="left", padx=10)
        tk.Label(
            copy,
            text="J.A.R.V.I.S.",
            bg=c.background,
            fg=c.ink,
            font=("Segoe UI Semibold", 11),
        ).pack(anchor="w")
        tk.Label(
            copy,
            text="BETA / LOCAL DESKTOP",
            bg=c.background,
            fg=c.muted,
            font=("Segoe UI", 7),
        ).pack(anchor="w")

        runtime = tk.Frame(topbar, bg=c.background)
        runtime.pack(side="right", padx=22, pady=15)
        self.status_dot = tk.Canvas(
            runtime,
            width=12,
            height=12,
            bg=c.background,
            highlightthickness=0,
        )
        self.status_dot.pack(side="left", padx=(0, 8))
        self._status_oval = self.status_dot.create_oval(
            2, 2, 10, 10, fill=c.muted, outline=""
        )
        self.status_label = tk.Label(
            runtime,
            text=self.controller.state.status,
            bg=c.background,
            fg=c.muted,
            font=("Segoe UI Semibold", 8),
        )
        self.status_label.pack(side="left", padx=(0, 18))
        self.provider_badge = self._badge(
            runtime, self._snapshot.provider.upper()
        )
        self.model_badge = self._badge(runtime, self._snapshot.model)
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
            padx=(30, 18),
            pady=(24, 132),
        )
        self.workspace = self.workspace_scroller.body
        self._build_composer()
        self._build_context()

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

    def _badge(self, parent: tk.Widget, text: str) -> tk.Label:
        label = tk.Label(
            parent,
            text=text,
            bg=self._colors.surface_alt,
            fg=self._colors.muted,
            font=("Segoe UI Semibold", 7),
            padx=9,
            pady=4,
        )
        label.pack(side="left", padx=3)
        return label

    def _build_navigation(self) -> None:
        c = self._colors
        self.nav = tk.Frame(self.shell, bg=c.surface, width=218)
        self.nav.pack(side="left", fill="y")
        self.nav.pack_propagate(False)
        tk.Label(
            self.nav,
            text="WORKSPACE",
            bg=c.surface,
            fg=c.faint,
            font=("Segoe UI Semibold", 7),
        ).pack(anchor="w", padx=18, pady=(20, 9))
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
                pady=9,
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
            "COLLAPSE",
            self._toggle_navigation,
            variant="ghost",
        )
        self.nav_collapse_button.pack(fill="x", padx=10, pady=12)

    def _build_composer(self) -> None:
        c = self._colors
        self.composer = tk.Frame(
            self.center,
            bg=c.surface,
            highlightbackground=c.line,
            highlightthickness=1,
            padx=12,
            pady=10,
        )
        self.composer.place(
            relx=0.5, rely=1.0, anchor="s", relwidth=0.90, y=-20
        )
        self.command = tk.Text(
            self.composer,
            height=3,
            wrap="word",
            relief="flat",
            borderwidth=0,
            bg=c.surface,
            fg=c.ink,
            insertbackground=c.ink,
            selectbackground=c.faint,
            padx=8,
            pady=6,
            font=("Segoe UI", 10),
        )
        self.command.pack(fill="x")
        self.command.bind("<Return>", self._on_composer_enter)
        self.command.bind("<Shift-Return>", self._on_composer_newline)
        bar = tk.Frame(self.composer, bg=c.surface)
        bar.pack(fill="x", pady=(5, 0))
        self._button(
            bar, "MIC", self._start_voice, variant="ghost"
        ).pack(side="left")
        self._button(
            bar, "VISION", self._start_vision, variant="ghost"
        ).pack(side="left", padx=4)
        tk.Label(
            bar,
            text="ENTER TO SEND  /  SHIFT + ENTER FOR NEW LINE",
            bg=c.surface,
            fg=c.faint,
            font=("Segoe UI", 7),
        ).pack(side="left", padx=10)
        self.send_button = self._button(
            bar, "SEND", self._submit_command, variant="primary"
        )
        self.send_button.pack(side="right")

    def _build_context(self) -> None:
        c = self._colors
        self.context = tk.Frame(self.shell, bg=c.surface, width=288)
        self.context.pack(side="right", fill="y")
        self.context.pack_propagate(False)
        tk.Label(
            self.context,
            text="LIVE CONTEXT",
            bg=c.surface,
            fg=c.faint,
            font=("Segoe UI Semibold", 7),
        ).pack(anchor="w", padx=18, pady=(21, 4))
        tk.Label(
            self.context,
            text="Runtime truth",
            bg=c.surface,
            fg=c.ink,
            font=("Segoe UI Semibold", 12),
        ).pack(anchor="w", padx=18)
        tabs = tk.Frame(self.context, bg=c.surface)
        tabs.pack(fill="x", padx=14, pady=14)
        for label in ("TASK", "MEMORY", "TOOLS"):
            self._button(
                tabs,
                label,
                lambda value=label: self._render_context(value),
                variant="chip",
            ).pack(side="left", padx=2)
        self.context_body = tk.Frame(self.context, bg=c.surface)
        self.context_body.pack(fill="both", expand=True, padx=18, pady=4)
        self._render_context("TASK")

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
            "JARVIS keyboard shortcuts",
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
            "primary": (c.ink, c.inverse, c.focus),
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
            fg=c.faint,
            font=("Segoe UI Semibold", 7),
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
        frame = tk.Frame(
            parent,
            bg=c.surface,
            highlightbackground=c.line,
            highlightthickness=1,
            padx=18,
            pady=16,
        )
        frame.pack(side=side, fill="both", expand=True, padx=6, pady=6)
        tk.Label(
            frame,
            text=title.upper(),
            bg=c.surface,
            fg=c.ink,
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
        row = tk.Frame(parent, bg=c.surface)
        row.pack(fill="x", pady=6)
        tk.Label(
            row,
            text=primary,
            bg=c.surface,
            fg=c.ink,
            anchor="w",
            justify="left",
            wraplength=560,
        ).pack(fill="x", anchor="w")
        if secondary:
            tk.Label(
                row,
                text=secondary,
                bg=c.surface,
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
            font=("Segoe UI Semibold", 7),
        ).pack(anchor="w", pady=(2, 0))

    def render(self, screen: UIScreen) -> None:
        self.controller.state.screen = screen
        self._snapshot = self.controller.snapshot()
        self._clear(self.workspace)
        self.workspace_scroller.reset()
        self.provider_badge.configure(text=self._snapshot.provider.upper())
        self.model_badge.configure(text=self._snapshot.model)
        for value, button in self._nav_buttons.items():
            selected = value is screen
            normal = self._colors.ink if selected else self._colors.surface
            button.configure(
                bg=normal,
                fg=self._colors.inverse if selected else self._colors.muted,
                font=(
                    "Segoe UI Semibold" if selected else "Segoe UI",
                    9,
                ),
            )
            self._bind_hover(
                button,
                self._colors.focus if selected else self._colors.hover,
                normal,
            )
        getattr(self, f"_render_{screen.value}")()

    def _render_home(self) -> None:
        self._heading(
            "LOCAL INTELLIGENCE",
            "Good to see you.",
            "A quiet view of the runtime, its capabilities, and current work.",
        )
        if self._snapshot.provider == "mock":
            notice = self._card(
                self.workspace,
                "AI provider not configured",
                subtitle="Mock mode cannot produce intelligent answers.",
            )
            self._line(
                notice,
                "Connect an API provider to begin a real conversation.",
                "The API key stays in Windows Credential Manager, never in project files.",
            )
            self._button(
                notice,
                "OPEN API SETTINGS",
                lambda: self.render(UIScreen.SETTINGS),
                variant="primary",
            ).pack(anchor="w", pady=(10, 0))
        metrics = self._card(self.workspace, "Runtime at a glance")
        metric_row = tk.Frame(metrics, bg=self._colors.surface)
        metric_row.pack(fill="x")
        self._metric(metric_row, str(self._snapshot.task_count), "Tasks")
        self._metric(
            metric_row, str(self._snapshot.memory_count), "Memories"
        )
        self._metric(
            metric_row, str(self._snapshot.enabled_tools), "Tools"
        )
        self._metric(
            metric_row, self._snapshot.provider.upper(), "Provider"
        )
        row = tk.Frame(self.workspace, bg=self._colors.background)
        row.pack(fill="both", expand=True)
        conversation = self._card(row, "Recent conversation", side="left")
        messages = self.controller.state.messages[-3:]
        if not messages:
            self._line(
                conversation,
                "No conversation yet",
                "Use the composer after connecting a provider.",
            )
        for message in messages:
            self._line(conversation, message.text, message.role.upper())
        capabilities = self._card(row, "Capabilities", side="left")
        for name, available in (
            ("Windows", self._snapshot.windows_available),
            ("Voice", self._snapshot.voice_available),
            ("Vision", self._snapshot.vision_available),
            ("Research", self._snapshot.research_available),
        ):
            self._line(
                capabilities,
                name,
                "AVAILABLE" if available else "NOT CONFIGURED",
            )

    def _render_chat(self) -> None:
        self._streaming_label = None

        self._heading(
            "CONVERSATION",
            "A focused dialogue.",
            "Messages move through the bounded Core pipeline and preserve context.",
        )
        card = self._card(self.workspace, "Conversation")
        if self._snapshot.provider == "mock":
            self._line(
                card,
                "AI provider not configured",
                "Open Settings to add an API key. Mock echoes are disabled here.",
            )
            self._button(
                card,
                "OPEN SETTINGS",
                lambda: self.render(UIScreen.SETTINGS),
                variant="primary",
            ).pack(anchor="w", pady=8)
            return
        for message in self.controller.state.messages:
            background = (
                self._colors.surface_alt
                if message.role == "user"
                else self._colors.surface
            )
            bubble = tk.Frame(card, bg=background, padx=12, pady=10)
            bubble.pack(fill="x", pady=4)
            tk.Label(
                bubble,
                text=message.role.upper(),
                bg=background,
                fg=self._colors.faint,
                font=("Segoe UI Semibold", 7),
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
            bubble = tk.Frame(
                card,
                bg=background,
                padx=12,
                pady=10,
            )
            bubble.pack(
                fill="x",
                pady=4,
            )

            tk.Label(
                bubble,
                text="ASSISTANT",
                bg=background,
                fg=self._colors.faint,
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
            self._line(card, "No messages", "Write below to begin.")

    def _render_tasks(self) -> None:
        self._heading(
            "DURABLE EXECUTION",
            "Missions and tasks.",
            "Persistent progress and recovery without visual noise.",
        )
        card = self._card(self.workspace, "Task queue")
        for task in self._snapshot.tasks:
            self._line(
                card,
                str(task["goal"]),
                f"{str(task['status']).upper()}  /  "
                f"{float(task['progress']) * 100:.0f}%  /  "
                f"{task.get('current_step') or 'No active step'}",
            )
        if not self._snapshot.tasks:
            self._line(
                card,
                "No durable tasks",
                "Tasks appear after Core creates them.",
            )

    def _render_memory(self) -> None:
        self._heading(
            "PROVENANCE-AWARE MEMORY",
            "What JARVIS remembers.",
            "Origin, confidence, and freshness stay visible.",
        )
        card = self._card(self.workspace, "Active memories")
        for memory in self._snapshot.memories:
            self._line(
                card,
                str(memory["content"]),
                f"{memory['source']}  /  {memory['freshness']}  /  "
                f"confidence {float(memory['confidence']):.2f}",
            )
        if not self._snapshot.memories:
            self._line(card, "No active memories")

    def _render_voice(self) -> None:
        self._heading(
            "VOICE",
            "Calm and interruptible.",
            "The microphone is used only after an explicit action.",
        )
        card = self._card(self.workspace, "Voice session")
        self._line(
            card,
            "READY" if self._snapshot.voice_available else "NOT CONFIGURED",
            "Current runtime state",
        )
        button = self._button(
            card, "START VOICE", self._start_voice, variant="primary"
        )
        button.configure(
            state="normal" if self._snapshot.voice_available else "disabled"
        )
        button.pack(anchor="w", pady=10)

    def _render_vision(self) -> None:
        self._heading(
            "VISION",
            "Consent before capture.",
            "Every screen capture requires one visible approval.",
        )
        card = self._card(self.workspace, "Capture policy")
        self._line(card, "Default", "Capture denied")
        self._line(card, "Retention", "Discard after analysis")
        self._line(card, "Taskbar", "Redacted before provider access")
        button = self._button(
            card,
            "REQUEST ONE CAPTURE",
            self._start_vision,
            variant="primary",
        )
        button.configure(
            state="normal" if self._snapshot.vision_available else "disabled"
        )
        button.pack(anchor="w", pady=10)

    def _render_research(self) -> None:
        self._heading(
            "RESEARCH",
            "Evidence before synthesis.",
            "Observed facts remain distinct from inference.",
        )
        card = self._card(self.workspace, "Research brief")
        self.research_entry = self._entry(card)
        self.research_entry.pack(fill="x", ipady=7, pady=5)
        button = self._button(
            card, "RESEARCH", self._start_research, variant="primary"
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
                "Research is not configured",
                "Configure a search provider before use.",
            )

    def _render_tools(self) -> None:
        self._heading(
            "CAPABILITY REGISTRY",
            "Tools and permissions.",
            "Availability never implies authorization.",
        )
        card = self._card(self.workspace, "Registered tools")
        for tool in self._snapshot.tools:
            self._line(
                card,
                str(tool["name"]),
                f"{str(tool['risk']).upper()}  /  "
                f"{'ENABLED' if tool['enabled'] else 'DISABLED'}  /  "
                f"{tool['source']}",
            )

    def _render_integrations(self) -> None:
        self._heading(
            "CONNECTIONS",
            "Services at a glance.",
            "Only live configuration is shown as connected.",
        )
        card = self._card(self.workspace, "Runtime connections")
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
            ("Voice", self._snapshot.voice_available),
            ("Vision", self._snapshot.vision_available),
            ("Web research", self._snapshot.research_available),
        ):
            self._line(
                card, name, "CONNECTED" if ready else "NOT CONFIGURED"
            )

    def _render_diagnostics(self) -> None:
        self._heading(
            "SYSTEM HEALTH",
            "Diagnostics without clutter.",
            "Operational truth from live runtime inspection.",
        )
        card = self._card(self.workspace, "Current health")
        self._line(card, "Core", "READY")
        self._line(card, "Provider", self._snapshot.provider)
        self._line(
            card,
            "Tool registry",
            f"{self._snapshot.enabled_tools}/{self._snapshot.tool_count} enabled",
        )
        self._line(card, "Durable tasks", str(self._snapshot.task_count))
        self._line(
            card,
            "Event ledger",
            (
                f"VALID  /  {self._snapshot.diagnostic_event_count} events"
                if self._snapshot.diagnostic_integrity_valid
                else "INTEGRITY FAILURE"
            ),
        )

    def _render_settings(self) -> None:
        self._heading(
            "SETTINGS",
            "Quiet controls, clear state.",
            "Secrets remain outside the project and source control.",
        )
        appearance = self._card(self.workspace, "Appearance")
        controls = tk.Frame(appearance, bg=self._colors.surface)
        controls.pack(fill="x")
        self._button(
            controls, "TOGGLE THEME", self._toggle_theme
        ).pack(side="left")
        self._button(
            controls,
            "REDUCE MOTION",
            self._toggle_motion,
            variant="ghost",
        ).pack(side="left", padx=5)
        self._button(
            controls, "KEYBOARD SHORTCUTS", self._show_shortcuts, variant="ghost"
        ).pack(side="left")
        self._line(
            appearance,
            "Motion",
            "REDUCED"
            if self.controller.state.reduced_motion
            else "SUBTLE",
        )

        api = self._card(
            self.workspace,
            "API connection",
            subtitle=(
                "The key is stored in Windows Credential Manager and is "
                "never shown again."
            ),
        )
        if self.api_settings is None:
            self._line(
                api,
                "API settings unavailable",
                "The credential service was not initialized.",
            )
            return
        state = self.api_settings.snapshot()
        self.api_provider = tk.StringVar(value=state.provider)
        self.api_model = tk.StringVar(value=state.model)
        self.api_key = tk.StringVar()
        self._field_label(api, "PROVIDER")
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
        self._field_label(api, "API KEY")
        self.api_key_entry = self._entry(
            api,
            textvariable=self.api_key,
            show="*",
        )
        self.api_key_entry.pack(
            fill="x",
            ipady=7,
        )

        if not state.credential_required:
            self.api_key_entry.configure(
                state="disabled"
            )
            self._line(
                api,
                "No API key required",
                (
                    "Ollama uses the local Ollama service."
                    if state.provider == "ollama"
                    else (
                        "This provider does not use "
                        "an API credential."
                    )
                ),
            )
        elif state.credential_configured:
            self._line(
                api,
                "Credential stored",
                "Leave the field empty to keep the existing key.",
            )
        else:
            self._line(
                api,
                "No credential stored",
                "Paste a key, test it, then save and activate.",
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
        actions = tk.Frame(api, bg=self._colors.surface)
        actions.pack(fill="x")
        self.api_test_button = self._button(
            actions, "TEST CONNECTION", self._start_api_test
        )
        self.api_test_button.pack(side="left")
        self._button(
            actions,
            "SAVE AND ACTIVATE",
            self._save_api_settings,
            variant="primary",
        ).pack(side="left", padx=6)
        self.api_delete_button = self._button(
            actions,
            "REMOVE KEY",
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
    ) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=textvariable,
            show=show,
            relief="flat",
            borderwidth=0,
            bg=self._colors.surface_alt,
            fg=self._colors.ink,
            insertbackground=self._colors.ink,
            selectbackground=self._colors.faint,
            highlightbackground=self._colors.line,
            highlightcolor=self._colors.focus,
            highlightthickness=1,
            font=("Segoe UI", 10),
        )

    def _field_label(self, parent: tk.Widget, text: str) -> None:
        tk.Label(
            parent,
            text=text,
            bg=self._colors.surface,
            fg=self._colors.faint,
            font=("Segoe UI Semibold", 7),
        ).pack(anchor="w", pady=(3, 5))

    def _on_api_provider_changed(self, _event: object | None = None) -> None:
        defaults = {
            "gemini": "gemini-3.7-flash",
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

        delete = getattr(
            self,
            "api_delete_button",
            None,
        )

        if delete is not None:
            delete.configure(
                state="disabled"
            )

    def _render_context(self, tab: str) -> None:
        self._clear(self.context_body)
        if tab == "TASK":
            for task in self._snapshot.tasks[:4]:
                self._line(
                    self.context_body,
                    str(task["goal"]),
                    str(task["status"]).upper(),
                )
            if not self._snapshot.tasks:
                self._line(
                    self.context_body, "No active task", "The queue is clear."
                )
        elif tab == "MEMORY":
            for memory in self._snapshot.memories[:6]:
                self._line(
                    self.context_body,
                    str(memory["content"]),
                    str(memory["freshness"]),
                )
            if not self._snapshot.memories:
                self._line(self.context_body, "No active memory")
        else:
            for tool in self._snapshot.tools[:9]:
                self._line(
                    self.context_body,
                    str(tool["name"]),
                    "ON" if tool["enabled"] else "OFF",
                )

    def _toggle_navigation(self) -> None:
        collapsed = not self.controller.state.nav_collapsed
        self.controller.state.nav_collapsed = collapsed
        target = 68 if collapsed else 218
        for screen, index, label in NAVIGATION:
            self._nav_buttons[screen].configure(
                text=index if collapsed else f"{index}    {label}",
                anchor="center" if collapsed else "w",
            )
        self.nav_collapse_button.configure(
            text="OPEN" if collapsed else "COLLAPSE"
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

    def _set_busy(self, busy: bool, status: str) -> None:
        self.status_label.configure(text=status)
        self.send_button.configure(state="disabled" if busy else "normal")

    def _submit_command(self) -> str:
        text = self.command.get("1.0", "end").strip()
        if not text or self._busy_future is not None:
            return "break"
        if self._snapshot.provider == "mock":
            self.render(UIScreen.SETTINGS)
            messagebox.showinfo(
                "Provider required",
                "Select and activate a real provider in Settings before starting a conversation.",
                parent=self.root,
            )
            return "break"
        self.command.delete("1.0", "end")

        self._streaming_text = ""
        self._streaming_label = None

        self._set_busy(
            True,
            "PROCESSING",
        )

        self._busy_future = self.controller.submit_background(
            self.controller.submit_command(
                text,
                stream_callback=self._on_stream_text,
            ),
            self._on_command_done,
        )
        return "break"

    def _on_stream_text(
        self,
        text: str,
    ) -> None:
        if self._closing:
            return

        self.root.after(
            0,
            lambda value=text: (
                self._apply_stream_text(
                    value
                )
            ),
        )

    def _apply_stream_text(
        self,
        text: str,
    ) -> None:
        if (
            self._busy_future is None
            or not isinstance(text, str)
            or not text
        ):
            return

        self._streaming_text = text

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
            label.configure(
                text=text
            )

            self.workspace_scroller.canvas.update_idletasks()
            self.workspace_scroller.canvas.yview_moveto(
                1.0
            )

    def _on_command_done(self, future: Future[Any]) -> None:
        self.root.after(0, lambda: self._finish_command(future))

    def _finish_command(self, future: Future[Any]) -> None:
        self._busy_future = None
        self._streaming_text = None
        self._streaming_label = None

        try:
            future.result()
        except Exception as exc:
            messagebox.showerror(
                "JARVIS request failed", str(exc), parent=self.root
            )
        self._set_busy(False, self.controller.state.status)
        self.render(UIScreen.CHAT)

    def _start_api_test(self) -> None:
        if self.api_settings is None or self._busy_future is not None:
            return
        self.api_status.configure(text="Testing the connection...")
        self.api_test_button.configure(state="disabled")
        self._set_busy(True, "TESTING CONNECTION")
        self._busy_future = self.controller.submit_background(
            self.api_settings.test_connection(
                self.api_provider.get(),
                self.api_model.get(),
                self.api_key.get(),
            ),
            self._on_api_test_done,
        )

    def _on_api_test_done(self, future: Future[Any]) -> None:
        self.root.after(0, lambda: self._finish_api_test(future))

    def _finish_api_test(self, future: Future[Any]) -> None:
        self._busy_future = None
        try:
            result = future.result()
            self.api_status.configure(
                text=result.message,
                fg=self._colors.ink if result.ok else self._colors.muted,
            )
        except Exception as exc:
            self.api_status.configure(
                text=f"Connection test failed: {exc}", fg=self._colors.muted
            )
        self.api_test_button.configure(state="normal")
        self._set_busy(False, "LOCAL CORE READY")

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
                text="Settings saved. The live runtime is now active.",
                fg=self._colors.ink,
            )
            self._set_busy(False, "LOCAL CORE READY")
        except Exception as exc:
            messagebox.showerror(
                "Could not save API settings", str(exc), parent=self.root
            )

    def _delete_api_key(self) -> None:
        if self.api_settings is None:
            return
        if not messagebox.askyesno(
            "Remove API key?",
            "This removes the JARVIS API credential from Windows Credential "
            "Manager and returns the desktop to mock mode.",
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
                text="Credential removed. Mock mode is active."
            )
        except Exception as exc:
            messagebox.showerror(
                "Could not remove API key", str(exc), parent=self.root
            )

    def _start_voice(self) -> None:
        if not self._snapshot.voice_available or self._busy_future is not None:
            messagebox.showinfo(
                "Voice", "Voice is not configured.", parent=self.root
            )
            return
        self._set_busy(True, "LISTENING")
        self._busy_future = self.controller.submit_background(
            self.controller.run_voice(),
            self._on_voice_done,
        )

    def _on_voice_done(
        self,
        future: Future[Any],
    ) -> None:
        self.root.after(
            0,
            lambda: self._finish_voice(
                future
            ),
        )

    def _finish_voice(
        self,
        future: Future[Any],
    ) -> None:
        self._busy_future = None

        try:
            future.result()
        except Exception as exc:
            messagebox.showerror(
                "JARVIS voice failed",
                str(exc),
                parent=self.root,
            )

        self._set_busy(
            False,
            "LOCAL CORE READY",
        )

        self.render(
            UIScreen.CHAT
        )

    def _start_vision(self) -> None:
        if not self._snapshot.vision_available or self._busy_future is not None:
            messagebox.showinfo(
                "Vision", "Vision is not configured.", parent=self.root
            )
            return
        purpose = (
            "Describe the current screen and answer the user's visible question."
        )
        approved = messagebox.askyesno(
            "Allow one screen capture?",
            "JARVIS will capture once, redact the taskbar, send the processed "
            "image to the configured provider, and discard it after analysis.",
            parent=self.root,
        )
        if not approved:
            return
        self._set_busy(True, "CAPTURING")
        self._busy_future = self.controller.submit_background(
            self.controller.run_vision(purpose), self._on_aux_done
        )

    def _start_research(self) -> None:
        query = self.research_entry.get().strip()
        if not query or self._busy_future is not None:
            return
        self._set_busy(True, "RESEARCHING")
        self._busy_future = self.controller.submit_background(
            self.controller.run_research(query), self._on_research_done
        )

    def _on_aux_done(self, future: Future[Any]) -> None:
        self.root.after(0, lambda: self._finish_aux(future))

    def _finish_aux(self, future: Future[Any]) -> None:
        self._busy_future = None
        try:
            messagebox.showinfo(
                "JARVIS", str(future.result()), parent=self.root
            )
        except Exception as exc:
            messagebox.showerror(
                "JARVIS operation failed", str(exc), parent=self.root
            )
        self._set_busy(False, "LOCAL CORE READY")

    def _on_research_done(self, future: Future[Any]) -> None:
        self.root.after(0, lambda: self._finish_research(future))

    def _finish_research(self, future: Future[Any]) -> None:
        self._busy_future = None
        self._clear(self.research_results)
        try:
            report = future.result()
            for claim in report.get("claims", []):
                self._line(
                    self.research_results,
                    claim.get("text", ""),
                    ", ".join(claim.get("citations", [])),
                )
            for uncertainty in report.get("uncertainties", []):
                self._line(
                    self.research_results, "UNCERTAINTY", uncertainty
                )
        except Exception as exc:
            self._line(self.research_results, "Research failed", str(exc))
        self._set_busy(False, "LOCAL CORE READY")

    def close(self) -> None:
        self._closing = True
        if self._status_job is not None:
            self.root.after_cancel(self._status_job)
        if self._nav_job is not None:
            self.root.after_cancel(self._nav_job)
        if self._busy_future is not None:
            self._busy_future.cancel()
        self.controller.close()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def launch_desktop() -> None:
    from app.bootstrap import create_application

    api_settings = create_api_settings_service()
    application = create_application(api_settings.build_runtime_settings())
    DesktopWindow(
        DesktopController(application), api_settings=api_settings
    ).run()
