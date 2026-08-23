"""UI Automation (UIA3) client for reading and driving app content.

The managed UIA2 bridge cannot see WinUI3/Chromium content (WhatsApp
exposes 8 skeleton elements through it, ~27k through UIA3), so this
module talks to the native COM automation API via comtypes. All calls
are synchronous and must run off the event loop; callers wrap them in
asyncio.to_thread.
"""

from __future__ import annotations

import ctypes
import sys
import time
from typing import Any

_UIA_CLSID = "{FF48DBA4-60EF-4201-AA87-54103EEF594E}"
_NAME_PROPERTY = 30005
_CONTROL_TYPE_PROPERTY = 30003
_TREE_SCOPE_CHILDREN = 2
_TREE_SCOPE_DESCENDANTS = 4
_CONTROL_BUTTON = 50000
_CONTROL_EDIT = 50004
_CONTROL_DATA_ITEM = 50029
_INVOKE_PATTERN = 10000


class UiaClient:
    """Thin, bounded wrapper over IUIAutomation for one window family."""

    def __init__(self) -> None:
        self._automation: Any = None
        self._module: Any = None

    def _uia(self):
        if self._automation is None:
            if sys.platform != "win32":
                raise RuntimeError("UIA is only available on Windows.")
            import comtypes
            from comtypes.client import CreateObject, GetModule

            with _suppress(Exception):
                comtypes.CoInitialize()
            self._module = GetModule("UIAutomationCore.dll")
            self._automation = CreateObject(
                comtypes.GUID(_UIA_CLSID),
                interface=self._module.IUIAutomation,
            )
        return self._automation

    def find_window(self, title: str):
        uia = self._uia()
        condition = uia.CreatePropertyCondition(
            _NAME_PROPERTY, title
        )
        return uia.GetRootElement().FindFirst(
            _TREE_SCOPE_CHILDREN, condition
        )

    def window_exists(self, title: str) -> bool:
        try:
            return self.find_window(title) is not None
        except Exception:
            return False

    def bring_to_foreground(self, title: str) -> bool:
        window = self.find_window(title)
        if window is None:
            return False
        handle = window.CurrentNativeWindowHandle
        if not handle:
            return False
        user32 = ctypes.WinDLL("user32")
        user32.ShowWindow(handle, 9)  # SW_RESTORE
        user32.SetForegroundWindow(handle)
        time.sleep(1.8)  # let Chromium publish its accessibility tree
        return True

    def read_items(
        self,
        title: str,
        *,
        limit: int,
        minimum_length: int = 6,
    ) -> list[str] | None:
        """Read named data rows (chat entries) from the window."""
        if not self.bring_to_foreground(title):
            return None
        window = self.find_window(title)
        if window is None:
            return None
        uia = self._uia()
        rows = window.FindAll(
            _TREE_SCOPE_DESCENDANTS,
            uia.CreatePropertyCondition(
                _CONTROL_TYPE_PROPERTY, _CONTROL_DATA_ITEM
            ),
        )
        names: list[str] = []
        seen: set[str] = set()
        for index in range(rows.Length):
            name = rows.GetElement(index).CurrentName or ""
            cleaned = " ".join(name.split())
            if len(cleaned) < minimum_length or cleaned in seen:
                continue
            seen.add(cleaned)
            names.append(cleaned)
            if len(names) >= limit:
                break
        return names

    def read_conversation(
        self,
        title: str,
        *,
        limit: int,
    ) -> list[str] | None:
        """Read message rows from the conversation pane.

        WhatsApp publishes both the chat list and the open conversation
        as DataItem rows carrying a HH:MM timestamp in their accessible
        name. Conversation rows are the ones the chat-list reader does
        not also return, so the caller can subtract them; here we return
        the timestamped rows in document order, most recent last.
        """
        import re

        if not self.bring_to_foreground(title):
            return None
        window = self.find_window(title)
        if window is None:
            return None
        uia = self._uia()
        rows = window.FindAll(
            _TREE_SCOPE_DESCENDANTS,
            uia.CreatePropertyCondition(
                _CONTROL_TYPE_PROPERTY, _CONTROL_DATA_ITEM
            ),
        )
        time_pattern = re.compile(r"\b\d{1,2}:\d{2}\b")
        messages: list[str] = []
        seen: set[str] = set()
        for index in range(rows.Length):
            name = rows.GetElement(index).CurrentName or ""
            cleaned = " ".join(name.split())
            if (
                len(cleaned) < 3
                or cleaned in seen
                or not time_pattern.search(cleaned)
                or "kaybolacak" in cleaned  # disappearing-msg banner
                or "okunmamış mesaj" in cleaned  # chat-list badge
            ):
                continue
            seen.add(cleaned)
            messages.append(cleaned)
        return messages[-limit:] if limit else messages

    def set_edit_text_and_send(
        self,
        title: str,
        text: str,
        button_names: tuple[str, ...],
    ) -> bool:
        """Type into the focused message box via SendInput, then send.

        The deep link already placed the caret in the message box; this
        types the reply as real keystrokes (so WhatsApp's composer state
        updates) and presses the verified send button.
        """
        if not self.bring_to_foreground(title):
            return False
        self._type_unicode(text)
        time.sleep(0.4)
        return self.invoke_button(title, button_names)

    @staticmethod
    def _type_unicode(text: str) -> None:
        """Send a string as Unicode keystrokes to the focused control."""
        user32 = ctypes.WinDLL("user32", use_last_error=True)

        class _KeyInput(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class _Input(ctypes.Structure):
            class _U(ctypes.Union):
                _fields_ = [("ki", _KeyInput)]

            _anonymous_ = ("u",)
            _fields_ = [("type", ctypes.c_ulong), ("u", _U)]

        KEYEVENTF_UNICODE = 0x0004
        KEYEVENTF_KEYUP = 0x0002
        for char in text:
            for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                event = _Input(
                    type=1,
                    ki=_KeyInput(
                        wVk=0,
                        wScan=ord(char),
                        dwFlags=flags,
                        time=0,
                        dwExtraInfo=None,
                    ),
                )
                user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event))
            time.sleep(0.004)

    def invoke_button(
        self, title: str, button_names: tuple[str, ...]
    ) -> bool:
        """Find and press a named button; True only when invoked."""
        window = self.find_window(title)
        if window is None:
            return False
        uia = self._uia()
        buttons = window.FindAll(
            _TREE_SCOPE_DESCENDANTS,
            uia.CreatePropertyCondition(
                _CONTROL_TYPE_PROPERTY, _CONTROL_BUTTON
            ),
        )
        wanted = {name.casefold() for name in button_names}
        for index in range(buttons.Length):
            element = buttons.GetElement(index)
            name = (element.CurrentName or "").strip().casefold()
            if name in wanted:
                pattern = element.GetCurrentPattern(_INVOKE_PATTERN)
                if pattern is None:
                    return False
                pattern.QueryInterface(
                    self._module.IUIAutomationInvokePattern
                ).Invoke()
                return True
        return False

    def find_edit_value(self, title: str, name_contains: str) -> bool:
        """Whether an Edit control whose name contains the text exists."""
        window = self.find_window(title)
        if window is None:
            return False
        uia = self._uia()
        edits = window.FindAll(
            _TREE_SCOPE_DESCENDANTS,
            uia.CreatePropertyCondition(
                _CONTROL_TYPE_PROPERTY, _CONTROL_EDIT
            ),
        )
        needle = name_contains.casefold()
        for index in range(edits.Length):
            name = (edits.GetElement(index).CurrentName or "").casefold()
            if needle in name:
                return True
        return False


class _suppress:
    def __init__(self, *exceptions):
        self.exceptions = exceptions

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        return kind is not None and issubclass(kind, self.exceptions)
