from __future__ import annotations

import json

from app.core.models import Request
from app.platform.windows.applications import (
    WindowsApplicationRegistry,
)
from app.tools.fast_actions import (
    ApprovedApplicationFastRouter,
)


def test_snapshot_loads_all_valid_entries(
    tmp_path,
) -> None:
    chrome = (
        tmp_path
        / "chrome.exe"
    )

    word = (
        tmp_path
        / "winword.exe"
    )

    chrome.write_bytes(
        b"test"
    )

    word.write_bytes(
        b"test"
    )

    snapshot = (
        tmp_path
        / "apps.json"
    )

    snapshot.write_text(
        json.dumps(
            [
                {
                    "name": (
                        "Google Chrome"
                    ),
                    "executable": str(
                        chrome
                    ),
                    "arguments": [],
                    "shortcut": (
                        "Chrome.lnk"
                    ),
                },
                {
                    "name": (
                        "Word 2013"
                    ),
                    "executable": str(
                        word
                    ),
                    "arguments": [
                        "--test",
                    ],
                    "shortcut": (
                        "Word.lnk"
                    ),
                },
            ]
        ),
        encoding="utf-8",
    )

    registry = (
        WindowsApplicationRegistry()
    )

    loaded = (
        registry.load_snapshot(
            snapshot
        )
    )

    assert loaded == 2
    assert len(
        registry.list()
    ) == 2

    chrome_app = (
        registry.resolve(
            "google chrome"
        )
    )

    word_app = (
        registry.resolve(
            "word 2013"
        )
    )

    assert (
        chrome_app.display_name
        == "Google Chrome"
    )

    assert (
        word_app.arguments
        == ("--test",)
    )


def test_snapshot_keeps_duplicate_names(
    tmp_path,
) -> None:
    first = (
        tmp_path
        / "first.exe"
    )

    second = (
        tmp_path
        / "second.exe"
    )

    first.write_bytes(
        b"first"
    )

    second.write_bytes(
        b"second"
    )

    snapshot = (
        tmp_path
        / "duplicate.json"
    )

    snapshot.write_text(
        json.dumps(
            [
                {
                    "name": "Same",
                    "executable": str(
                        first
                    ),
                    "arguments": [],
                    "shortcut": (
                        "A.lnk"
                    ),
                },
                {
                    "name": "Same",
                    "executable": str(
                        second
                    ),
                    "arguments": [],
                    "shortcut": (
                        "B.lnk"
                    ),
                },
            ]
        ),
        encoding="utf-8",
    )

    registry = (
        WindowsApplicationRegistry()
    )

    assert (
        registry.load_snapshot(
            snapshot
        )
        == 2
    )

    applications = (
        registry.list()
    )

    assert len(
        applications
    ) == 2

    assert (
        applications[0]
        .application_id
        != applications[1]
        .application_id
    )


def test_snapshot_skips_stale_executable(
    tmp_path,
) -> None:
    snapshot = (
        tmp_path
        / "stale.json"
    )

    snapshot.write_text(
        json.dumps(
            [
                {
                    "name": "Missing",
                    "executable": str(
                        tmp_path
                        / "missing.exe"
                    ),
                    "arguments": [],
                    "shortcut": (
                        "Missing.lnk"
                    ),
                }
            ]
        ),
        encoding="utf-8",
    )

    registry = (
        WindowsApplicationRegistry()
    )

    assert (
        registry.load_snapshot(
            snapshot
        )
        == 0
    )

    assert (
        registry.list()
        == ()
    )


def test_fast_router_supports_unicode_app_name():
    router = (
        ApprovedApplicationFastRouter(
            {
                (
                    "\u0423\u0434\u0430\u043b\u0435\u043d\u0438\u0435"
                ): (
                    "unicode-app",
                    (
                        "\u0423\u0434\u0430\u043b\u0435\u043d\u0438\u0435"
                    ),
                ),
            }
        )
    )

    route = router.route(
        Request(
            (
                "\u0423\u0434\u0430\u043b\u0435\u043d\u0438\u0435 "
                "a\u00e7"
            )
        ),
        available_tool_names={
            "launch_windows_application",
        },
    )

    assert route is not None

    assert (
        route.parameters[
            "application"
        ]
        == "unicode-app"
    )
