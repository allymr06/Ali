from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
from pathlib import Path


def _state_directory(override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    local = os.getenv("LOCALAPPDATA")
    if not local:
        raise RuntimeError("LOCALAPPDATA is unavailable.")
    return (Path(local) / "JARVIS").resolve()


def configure_state(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault(
        "JARVIS_CONVERSATION_DATABASE_PATH",
        str(directory / "jarvis_conversations.sqlite3"),
    )
    os.environ.setdefault(
        "JARVIS_MEMORY_DATABASE_PATH",
        str(directory / "jarvis_memory.sqlite3"),
    )
    os.environ.setdefault(
        "JARVIS_TASK_DATABASE_PATH",
        str(directory / "jarvis_tasks.sqlite3"),
    )
    os.environ.setdefault(
        "JARVIS_TASK_RUNTIME_DIRECTORY",
        str(directory / "tasks"),
    )
    os.environ.setdefault(
        "JARVIS_RESEARCH_CACHE_DATABASE_PATH",
        str(directory / "jarvis_research.sqlite3"),
    )


def smoke_test(output: Path, state_directory: Path) -> int:
    configure_state(state_directory)
    result: dict[str, object] = {
        "ok": False,
        "python": sys.version.split()[0],
        "frozen": bool(getattr(sys, "frozen", False)),
    }
    root = None
    controller = None
    try:
        import tkinter as tk

        from app.bootstrap import create_application
        from app.ui.controller import DesktopController
        from app.ui.desktop import DesktopWindow
        from app.ui.models import UIScreen

        from app.ui.nova.shell import WEB_ASSETS, resolve_web_root

        # Nova must be importable and its web assets bundled, otherwise
        # the frozen desktop would open an empty WebView2 window.
        nova_root = resolve_web_root()
        result["nova_assets"] = sorted(
            name for name in WEB_ASSETS if (nova_root / name).is_file()
        )

        application = create_application()
        report = __import__("asyncio").run(
            application.diagnostics.health_report()
        )
        root = tk.Tk()
        root.withdraw()
        controller = DesktopController(application)
        window = DesktopWindow(controller, root)
        for screen in UIScreen:
            window.render(screen)
        root.update_idletasks()
        result.update(
            {
                "ok": True,
                "screens": len(UIScreen),
                "health": report.status.value,
                "tools": len(application.tool_executor),
                "tcl": root.tk.call("info", "patchlevel"),
            }
        )
        window.close()
        root = None
        controller = None
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)[:500]
    finally:
        if controller is not None:
            controller.close()
        if root is not None:
            root.destroy()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
        )
    return 0 if result["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--state-dir")
    parser.add_argument(
        "--classic",
        action="store_true",
        help="open the classic Tkinter shell instead of Nova",
    )
    arguments = parser.parse_args(argv)
    state = _state_directory(arguments.state_dir)
    if arguments.smoke_test:
        if not arguments.output:
            raise SystemExit("--output is required with --smoke-test")
        return smoke_test(Path(arguments.output).resolve(), state)
    configure_state(state)

    # launch_desktop wires the APISettingsService so the packaged app reads
    # the Gemini key from Windows Credential Manager and the Settings screen
    # can accept one. A bare create_application() would boot an unconfigurable
    # runtime whose every provider call fails authentication.
    from app.ui.desktop import launch_desktop

    launch_desktop(classic=arguments.classic)
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
