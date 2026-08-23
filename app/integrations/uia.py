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

    def window_handle_for_process(self, executable: str) -> int:
        """First visible, titled top-level window of the process, else 0.

        Windows whose title changes constantly (Spotify shows the
        playing track) cannot be found by name, so this walks the HWND
        list and matches on the owning executable instead.
        """
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        target = executable.casefold()
        found: list[int] = []

        @ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
        )
        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.GetWindowTextLengthW(hwnd) <= 0:
                return True
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = kernel32.OpenProcess(0x1000, False, pid.value)
            if not process:
                return True
            try:
                size = ctypes.c_ulong(1024)
                buffer = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(
                    process, 0, buffer, ctypes.byref(size)
                ):
                    name = buffer.value.rsplit("\\", 1)[-1].casefold()
                    if name == target:
                        found.append(int(hwnd))
                        return False
            finally:
                kernel32.CloseHandle(process)
            return True

        user32.EnumWindows(callback, 0)
        return found[0] if found else 0

    def bring_handle_to_foreground(self, handle: int) -> bool:
        if not handle:
            return False
        user32 = ctypes.WinDLL("user32")
        user32.ShowWindow(handle, 9)  # SW_RESTORE
        user32.SetForegroundWindow(handle)
        time.sleep(1.0)
        return True

    def invoke_first_button_in_handle(
        self, handle: int, matcher: Any
    ) -> str | None:
        """Press the first button whose name satisfies the matcher.

        Returns the pressed button's accessible name, or None when no
        matching, invokable button exists in the window right now.
        """
        uia = self._uia()
        element = uia.ElementFromHandle(handle)
        buttons = element.FindAll(
            _TREE_SCOPE_DESCENDANTS,
            uia.CreatePropertyCondition(
                _CONTROL_TYPE_PROPERTY, _CONTROL_BUTTON
            ),
        )
        for index in range(buttons.Length):
            item = buttons.GetElement(index)
            name = (item.CurrentName or "").strip()
            if not name or not matcher(name):
                continue
            pattern = item.GetCurrentPattern(_INVOKE_PATTERN)
            if pattern is None:
                continue
            pattern.QueryInterface(
                self._module.IUIAutomationInvokePattern
            ).Invoke()
            return name
        return None

    def find_window(self, title: str):
        uia = self._uia()
        condition = uia.CreatePropertyCondition(
            _NAME_PROPERTY, title
        )
        element = uia.GetRootElement().FindFirst(
            _TREE_SCOPE_CHILDREN, condition
        )
        # comtypes hands back a falsy NULL pointer (not None) when
        # nothing matches; touching it raises ValueError, so normalize.
        return element if element else None

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

    @staticmethod
    def _click_screen_point(x: int, y: int) -> None:
        user32 = ctypes.WinDLL("user32")
        user32.SetCursorPos(x, y)
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP

    def click_item_by_name(
        self, title: str, name_contains: str
    ) -> str | None:
        """Open a named list row with a real click; returns its name.

        WhatsApp's chat rows expose no Invoke or SelectionItem pattern
        (LegacyIAccessible's default action is a no-op stub), so the
        only working activation is a genuine click at the row's
        on-screen centre after fronting the window.
        """
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
        needle = name_contains.strip().casefold()
        if not needle:
            return None
        for index in range(rows.Length):
            element = rows.GetElement(index)
            name = " ".join((element.CurrentName or "").split())
            if needle not in name.casefold():
                continue
            rect = element.CurrentBoundingRectangle
            if rect.right <= rect.left or rect.bottom <= rect.top:
                continue  # virtualized row that is not on screen
            self._click_screen_point(
                (rect.left + rect.right) // 2,
                (rect.top + rect.bottom) // 2,
            )
            time.sleep(1.2)
            return name
        return None

    def type_into_edit(
        self, title: str, name_contains: str, text: str
    ) -> bool:
        """Click a named edit control and type text into it.

        Keystrokes go to whatever is focused, so the target window is
        re-fronted first and control characters are stripped: an Enter
        would submit, and a stray dialog must not receive the text.
        """
        window = self.find_window(title)
        if window is None:
            return False
        handle = window.CurrentNativeWindowHandle
        user32 = ctypes.WinDLL("user32")
        if handle and user32.GetForegroundWindow() != handle:
            if not self.bring_to_foreground(title):
                return False
            window = self.find_window(title)
            if window is None:
                return False
        text = "".join(
            char if ord(char) >= 32 else " " for char in text
        )
        uia = self._uia()
        edits = window.FindAll(
            _TREE_SCOPE_DESCENDANTS,
            uia.CreatePropertyCondition(
                _CONTROL_TYPE_PROPERTY, _CONTROL_EDIT
            ),
        )
        needle = name_contains.casefold()
        for index in range(edits.Length):
            element = edits.GetElement(index)
            name = (element.CurrentName or "").casefold()
            if needle not in name:
                continue
            rect = element.CurrentBoundingRectangle
            if rect.right <= rect.left or rect.bottom <= rect.top:
                continue
            self._click_screen_point(
                (rect.left + rect.right) // 2,
                (rect.top + rect.bottom) // 2,
            )
            time.sleep(0.3)
            self._type_unicode(text)
            return True
        return False

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
