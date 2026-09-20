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
_CONTROL_DATA_GRID = 50028
_CONTROL_DATA_ITEM = 50029
_CONTROL_DOCUMENT = 50030
_INVOKE_PATTERN = 10000
_VALUE_PATTERN = 10002
_RANGE_VALUE_PATTERN = 10003
_TEXT_PATTERN = 10014
_TOGGLE_PATTERN = 10015
_CONTROL_CHECKBOX = 50002
_CONTROL_HYPERLINK = 50005
_CONTROL_MENU_ITEM = 50011
_CONTROL_SLIDER = 50015
_CONTROL_TEXT = 50020
_SW_SHOWNOACTIVATE = 4
# The transport bar of a media app lives in the window's bottom strip.
_BOTTOM_STRIP_HEIGHT = 120

# An outgoing WhatsApp bubble carries a "HH:MM <status>" button; incoming
# bubbles carry the time as plain text. This is how direction is read.
_MESSAGE_STATUS_WORDS = (
    "okundu", "teslim edildi", "gönderildi", "bekliyor", "iletildi",
    "read", "delivered", "sent", "pending",
)
# The header strip's first button opens the profile; the title is the
# button right after it. Group headers also carry a group-call button.
_PROFILE_BUTTON_NAMES = ("Profil detayları", "Profile details")
_GROUP_CALL_NAMES = ("Görüntülü grup araması", "Video group call")


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
        self,
        title: str,
        name_contains: str,
        text: str,
        *,
        per_char_seconds: float = 0.004,
    ) -> bool:
        """Click a named edit control and type text into it.

        Keystrokes go to whatever is focused, so the target window is
        re-fronted first and control characters are stripped: an Enter
        would submit, and a stray dialog must not receive the text.
        ``per_char_seconds`` above the default paces the keystrokes like
        a person typing, which is also what makes the other side see
        "yazıyor…" for a believable while.
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
            self._type_unicode(text, per_char_seconds=per_char_seconds)
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
    def _type_unicode(text: str, *, per_char_seconds: float = 0.004) -> None:
        """Send a string as Unicode keystrokes to the focused control.

        At the default pace this is a paste; at a human pace (tens of
        milliseconds per character) the delay is jittered and a short
        pause follows sentence punctuation, the way fingers actually move.
        """
        import random

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        human = per_char_seconds > 0.01

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
            if human:
                time.sleep(per_char_seconds * random.uniform(0.6, 1.5))
                if char in ".!?,":
                    time.sleep(random.uniform(0.15, 0.45))
            else:
                time.sleep(per_char_seconds)

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


    # ------------------------------------------------------------------
    # Focus-free reading (web-based WhatsApp Desktop, 2.26xx)
    #
    # The current WhatsApp Desktop is a Chromium page. Its chat rows are
    # DataItems under one DataGrid, its message list is the nearest
    # ancestor of any bubble that has DataItem children, and the text of
    # a bubble is only reachable through the document's TextPattern -
    # the words are not elements of their own. None of this needs the
    # window in front: reading works while another window is focused,
    # only a minimized window publishes nothing, so a minimized window
    # is restored without activation first.
    # ------------------------------------------------------------------

    def show_without_activating(self, title: str) -> bool:
        """Un-minimize the window without taking the user's focus."""
        window = self.find_window(title)
        if window is None:
            return False
        handle = window.CurrentNativeWindowHandle
        if not handle:
            return False
        user32 = ctypes.WinDLL("user32")
        if user32.IsIconic(handle):
            user32.ShowWindow(handle, _SW_SHOWNOACTIVATE)
            time.sleep(1.0)
        return True

    def _window_rect(self, window) -> tuple[int, int, int, int] | None:
        rect = window.CurrentBoundingRectangle
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)

    def read_chat_rows(self, title: str, *, limit: int) -> list[str] | None:
        """Accessible names of the chat-list rows, newest first.

        Returns None without a window, and an empty list when the list
        column is not on screen (a call or settings view, for example).
        """
        if not self.show_without_activating(title):
            return None
        window = self.find_window(title)
        if window is None:
            return None
        bounds = self._window_rect(window)
        if bounds is None:
            return []
        uia = self._uia()
        grid = window.FindFirst(
            _TREE_SCOPE_DESCENDANTS,
            uia.CreatePropertyCondition(_CONTROL_TYPE_PROPERTY, _CONTROL_DATA_GRID),
        )
        if not grid:
            return []
        grid_rect = grid.CurrentBoundingRectangle
        split = bounds[0] + (bounds[2] - bounds[0]) * 0.4
        if grid_rect.left > split:
            return []
        rows = grid.FindAll(
            _TREE_SCOPE_CHILDREN,
            uia.CreatePropertyCondition(_CONTROL_TYPE_PROPERTY, _CONTROL_DATA_ITEM),
        )
        names: list[str] = []
        for index in range(rows.Length):
            with _suppress(Exception):
                name = " ".join((rows.GetElement(index).CurrentName or "").split())
                if name:
                    names.append(name)
            if len(names) >= limit:
                break
        return names

    def _page(self, window):
        """The document's text pattern plus the window frame, or None."""
        bounds = self._window_rect(window)
        if bounds is None:
            return None
        uia = self._uia()
        document = window.FindFirst(
            _TREE_SCOPE_DESCENDANTS,
            uia.CreatePropertyCondition(_CONTROL_TYPE_PROPERTY, _CONTROL_DOCUMENT),
        )
        if not document:
            return None
        raw_pattern = document.GetCurrentPattern(_TEXT_PATTERN)
        if not raw_pattern:
            return None
        return raw_pattern.QueryInterface(self._module.IUIAutomationTextPattern), bounds

    def _main_area(self, pattern, bounds):
        """The element spanning both columns, reached by climbing from a
        text range in the conversation pane; None when nothing is there."""
        from ctypes import wintypes

        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        split = bounds[0] + width * 0.38
        point = wintypes.POINT(int(bounds[0] + width * 0.7), int(bounds[1] + height * 0.6))
        try:
            node = pattern.RangeFromPoint(point).GetEnclosingElement()
        except Exception:
            return None
        walker = self._uia().ControlViewWalker
        for _ in range(16):
            if not node:
                return None
            rect = node.CurrentBoundingRectangle
            if rect.left < split - 40 and (rect.right - rect.left) >= width * 0.5:
                return node
            node = walker.GetParentElement(node)
        return None

    def _right_side_children(self, main, bounds):
        width = bounds[2] - bounds[0]
        split = bounds[0] + width * 0.38
        walker = self._uia().ControlViewWalker
        child = walker.GetFirstChildElement(main)
        found = []
        for _ in range(16):
            if not child:
                break
            rect = child.CurrentBoundingRectangle
            if rect.left >= split - 40 and rect.right > rect.left:
                found.append(child)
            child = walker.GetNextSiblingElement(child)
        return found

    def chat_title(self, title: str) -> tuple[str, bool] | None:
        """(title, is_group) of the open chat from its header strip."""
        window = self.find_window(title)
        if window is None:
            return None
        page = self._page(window)
        if page is None:
            return None
        pattern, bounds = page
        main = self._main_area(pattern, bounds)
        if main is None:
            return None
        uia = self._uia()
        walker = uia.ControlViewWalker
        height = bounds[3] - bounds[1]
        for child in self._right_side_children(main, bounds):
            rect = child.CurrentBoundingRectangle
            if (rect.bottom - rect.top) > height * 0.3:
                continue  # the list or the whole column, not the header strip
            for name in _PROFILE_BUTTON_NAMES:
                profile = child.FindFirst(
                    _TREE_SCOPE_DESCENDANTS,
                    uia.CreatePropertyCondition(_NAME_PROPERTY, name),
                )
                if profile:
                    break
            else:
                continue
            sibling = walker.GetNextSiblingElement(profile)
            if not sibling:
                return None
            chat = " ".join((sibling.CurrentName or "").split())
            if not chat:
                return None
            group = any(
                child.FindFirst(
                    _TREE_SCOPE_DESCENDANTS,
                    uia.CreatePropertyCondition(_NAME_PROPERTY, name),
                )
                for name in _GROUP_CALL_NAMES
            )
            return chat, bool(group)
        return None

    def composer_name(self, title: str) -> str | None:
        """Accessible name of the message box, which names the open chat.

        Searched inside the conversation's own areas first (milliseconds);
        the whole-window scan is the slow fallback for an unknown layout.
        """
        window = self.find_window(title)
        if window is None:
            return None
        uia = self._uia()
        edit_condition = uia.CreatePropertyCondition(_CONTROL_TYPE_PROPERTY, _CONTROL_EDIT)
        scopes = []
        page = self._page(window)
        if page is not None:
            main = self._main_area(*page)
            if main is not None:
                scopes = self._right_side_children(main, page[1])
        for scope in scopes:
            edits = scope.FindAll(_TREE_SCOPE_DESCENDANTS, edit_condition)
            for index in range(edits.Length):
                with _suppress(Exception):
                    name = " ".join((edits.GetElement(index).CurrentName or "").split())
                    if "mesaj yaz" in name.casefold() or "type a message" in name.casefold():
                        return name
        edits = window.FindAll(_TREE_SCOPE_DESCENDANTS, edit_condition)
        for index in range(edits.Length):
            with _suppress(Exception):
                element = edits.GetElement(index)
                name = " ".join((element.CurrentName or "").split())
                lowered = name.casefold()
                if "mesaj yaz" in lowered or "type a message" in lowered:
                    rect = element.CurrentBoundingRectangle
                    if rect.right > rect.left:
                        return name
        return None

    def read_message_rows(self, title: str) -> list[tuple[str, bool]] | None:
        """The open conversation as (row text, has outgoing status) pairs.

        Row text is the bubble's own text run: an optional author label
        line ("Siz:" or "<Name>:"), the message lines, and the HH:MM
        line, in that order. Rows scrolled out of the virtualized list
        come back empty and are dropped. Returns None without a window
        and an empty list when no conversation is open.
        """
        if not self.show_without_activating(title):
            return None
        window = self.find_window(title)
        if window is None:
            return None
        page = self._page(window)
        if page is None:
            return []
        pattern, bounds = page
        uia = self._uia()
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        split = bounds[0] + width * 0.38
        from ctypes import wintypes

        point = wintypes.POINT(int(bounds[0] + width * 0.7), int(bounds[1] + height * 0.6))
        try:
            node = pattern.RangeFromPoint(point).GetEnclosingElement()
        except Exception:
            return []
        walker = uia.ControlViewWalker
        item_condition = uia.CreatePropertyCondition(_CONTROL_TYPE_PROPERTY, _CONTROL_DATA_ITEM)
        message_list = None
        for _ in range(14):
            if not node:
                break
            rect = node.CurrentBoundingRectangle
            children = node.FindAll(_TREE_SCOPE_CHILDREN, item_condition)
            if children.Length >= 1 and rect.left > split - 40:
                message_list = node
                break
            if rect.left < split - 40 and (rect.right - rect.left) >= width * 0.5:
                # The main area: the message list is inside its right column.
                child = walker.GetFirstChildElement(node)
                for _ in range(10):
                    if not child:
                        break
                    child_rect = child.CurrentBoundingRectangle
                    if child_rect.left >= split - 40 and (child_rect.right - child_rect.left) > 300:
                        inner = child.FindAll(_TREE_SCOPE_DESCENDANTS, item_condition)
                        message_list = child if inner.Length else None
                        break
                    child = walker.GetNextSiblingElement(child)
                break
            node = walker.GetParentElement(node)
        if message_list is None:
            return []
        rows = message_list.FindAll(_TREE_SCOPE_CHILDREN, item_condition)
        if rows.Length == 0:
            rows = message_list.FindAll(_TREE_SCOPE_DESCENDANTS, item_condition)
        button_condition = uia.CreatePropertyCondition(_CONTROL_TYPE_PROPERTY, _CONTROL_BUTTON)
        result: list[tuple[str, bool]] = []
        for index in range(rows.Length):
            with _suppress(Exception):
                row = rows.GetElement(index)
                text = pattern.RangeFromChild(row).GetText(-1) or ""
                if not text.strip():
                    continue
                outgoing = False
                buttons = row.FindAll(_TREE_SCOPE_DESCENDANTS, button_condition)
                for b in range(buttons.Length):
                    name = " ".join((buttons.GetElement(b).CurrentName or "").split()).casefold()
                    if name[:5].replace(".", ":").count(":") and any(
                        name.endswith(word) for word in _MESSAGE_STATUS_WORDS
                    ):
                        outgoing = True
                        break
                result.append((text, outgoing))
        return result


    # ------------------------------------------------------------------
    # Handle-based controls (Spotify's Chromium window)
    #
    # Spotify has no stable window title, so callers hold an HWND. The
    # window publishes its tree only after it has been fronted once, so
    # the play path fronts it; reads afterwards do not need focus.
    # ------------------------------------------------------------------

    def _bounds_of(self, element) -> tuple[int, int, int, int] | None:
        rect = element.CurrentBoundingRectangle
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)

    def _in_region(self, rect, window_rect, region: str) -> bool:
        if region == "bottom":
            return rect[1] > window_rect[3] - _BOTTOM_STRIP_HEIGHT
        if region == "sidebar":
            return rect[0] < window_rect[0] + 200 and rect[1] < window_rect[3]
        return True

    def _bar_element(self, handle: int):
        """The transport bar as an element: the widest short ancestor of
        the Play/Pause button in the bottom strip. Page content scrolled
        to the bottom sits in the same pixels but not in this subtree."""
        uia = self._uia()
        window = uia.ElementFromHandle(handle)
        window_rect = self._bounds_of(window)
        if window_rect is None:
            return None
        walker = uia.ControlViewWalker
        buttons = window.FindAll(
            _TREE_SCOPE_DESCENDANTS,
            uia.CreatePropertyCondition(_CONTROL_TYPE_PROPERTY, _CONTROL_BUTTON),
        )
        width = window_rect[2] - window_rect[0]
        for index in range(buttons.Length):
            with _suppress(Exception):
                element = buttons.GetElement(index)
                name = " ".join((element.CurrentName or "").split()).casefold()
                if name not in ("play", "pause"):
                    continue
                rect = self._bounds_of(element)
                if rect is None or not self._in_region(rect, window_rect, "bottom"):
                    continue
                node = element
                for _ in range(12):
                    parent = walker.GetParentElement(node)
                    if not parent:
                        break
                    parent_rect = self._bounds_of(parent)
                    if parent_rect is None:
                        break
                    if (parent_rect[2] - parent_rect[0]) >= width * 0.9:
                        return parent if (parent_rect[3] - parent_rect[1]) <= 200 else node
                    node = parent
                return node
        return None

    def _controls_in_handle(self, handle: int, control_types, *, region: str = "any"):
        """(name, rect, element) for every visible control of the types.

        ``region="bar"`` searches only the transport bar element; the
        other regions filter by position inside the whole window.
        """
        uia = self._uia()
        window = uia.ElementFromHandle(handle)
        window_rect = self._bounds_of(window)
        if window_rect is None:
            return []
        scope = window
        if region == "bar":
            scope = self._bar_element(handle)
            if scope is None:
                return []
            region = "any"
        found = []
        for control_type in control_types:
            elements = scope.FindAll(
                _TREE_SCOPE_DESCENDANTS,
                uia.CreatePropertyCondition(_CONTROL_TYPE_PROPERTY, control_type),
            )
            for index in range(elements.Length):
                with _suppress(Exception):
                    element = elements.GetElement(index)
                    rect = self._bounds_of(element)
                    if rect is None or not self._in_region(rect, window_rect, region):
                        continue
                    name = " ".join((element.CurrentName or "").split())
                    if name:
                        found.append((name, rect, element))
        return found

    def control_names_in_handle(self, handle: int, *, region: str = "any") -> list[str]:
        """Names of the visible buttons and checkboxes, in document order."""
        return [
            name for name, _rect, _element in self._controls_in_handle(
                handle, (_CONTROL_BUTTON, _CONTROL_CHECKBOX), region=region
            )
        ]

    def invoke_named_in_handle(self, handle: int, matcher, *, region: str = "any") -> str | None:
        """Press the first button/checkbox whose name satisfies the matcher."""
        for name, rect, element in self._controls_in_handle(
            handle, (_CONTROL_BUTTON, _CONTROL_CHECKBOX), region=region
        ):
            if not matcher(name):
                continue
            pattern = element.GetCurrentPattern(_INVOKE_PATTERN)
            if pattern:
                pattern.QueryInterface(self._module.IUIAutomationInvokePattern).Invoke()
            else:
                self._click_screen_point((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
            return name
        return None

    def toggle_state_in_handle(self, handle: int, name_contains: str, *, region: str = "any") -> int | None:
        """Current TogglePattern state (0 off, 1 on, 2 indeterminate) or None."""
        needle = name_contains.casefold()
        for name, _rect, element in self._controls_in_handle(
            handle, (_CONTROL_CHECKBOX, _CONTROL_BUTTON), region=region
        ):
            if needle not in name.casefold():
                continue
            pattern = element.GetCurrentPattern(_TOGGLE_PATTERN)
            if not pattern:
                return None
            return int(pattern.QueryInterface(self._module.IUIAutomationTogglePattern).CurrentToggleState)
        return None

    def toggle_in_handle(self, handle: int, name_contains: str, *, region: str = "any") -> int | None:
        """Toggle the control once and return its new state."""
        needle = name_contains.casefold()
        for name, _rect, element in self._controls_in_handle(
            handle, (_CONTROL_CHECKBOX, _CONTROL_BUTTON), region=region
        ):
            if needle not in name.casefold():
                continue
            pattern = element.GetCurrentPattern(_TOGGLE_PATTERN)
            if not pattern:
                return None
            toggle = pattern.QueryInterface(self._module.IUIAutomationTogglePattern)
            toggle.Toggle()
            time.sleep(0.3)
            return int(toggle.CurrentToggleState)
        return None

    def slider_in_handle(self, handle: int, name_contains: str) -> tuple[float, float, float] | None:
        """(value, minimum, maximum) of the named slider, or None."""
        needle = name_contains.casefold()
        for name, _rect, element in self._controls_in_handle(handle, (_CONTROL_SLIDER,)):
            if needle not in name.casefold():
                continue
            pattern = element.GetCurrentPattern(_RANGE_VALUE_PATTERN)
            if not pattern:
                return None
            value = pattern.QueryInterface(self._module.IUIAutomationRangeValuePattern)
            return (float(value.CurrentValue), float(value.CurrentMinimum), float(value.CurrentMaximum))
        return None

    def set_slider_in_handle(self, handle: int, name_contains: str, value: float) -> float | None:
        """Set the named slider and return what it reads back, or None."""
        needle = name_contains.casefold()
        for name, _rect, element in self._controls_in_handle(handle, (_CONTROL_SLIDER,)):
            if needle not in name.casefold():
                continue
            pattern = element.GetCurrentPattern(_RANGE_VALUE_PATTERN)
            if not pattern:
                return None
            range_value = pattern.QueryInterface(self._module.IUIAutomationRangeValuePattern)
            low, high = float(range_value.CurrentMinimum), float(range_value.CurrentMaximum)
            range_value.SetValue(max(low, min(high, float(value))))
            time.sleep(0.3)
            return float(range_value.CurrentValue)
        return None

    def double_click_named_in_handle(self, handle: int, matcher, *, region: str = "any") -> str | None:
        """Double-click the first matching button - how a row is played."""
        for name, rect, _element in self._controls_in_handle(handle, (_CONTROL_BUTTON,), region=region):
            if not matcher(name):
                continue
            x, y = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
            user32 = ctypes.WinDLL("user32")
            user32.SetCursorPos(x, y)
            for _ in range(2):
                user32.mouse_event(0x0002, 0, 0, 0, 0)
                user32.mouse_event(0x0004, 0, 0, 0, 0)
                time.sleep(0.08)
            return name
        return None

    def texts_in_handle(self, handle: int, *, region: str = "any") -> list[str]:
        """Visible text and link names, in document order."""
        return [
            name for name, _rect, _element in self._controls_in_handle(
                handle, (_CONTROL_TEXT, _CONTROL_HYPERLINK), region=region
            )
        ]

    def invoke_menu_item_in_handle(self, handle: int, name: str) -> bool:
        """Press the named item of the menu currently open in the window."""
        uia = self._uia()
        window = uia.ElementFromHandle(handle)
        item = window.FindFirst(
            _TREE_SCOPE_DESCENDANTS,
            uia.CreateAndCondition(
                uia.CreatePropertyCondition(_CONTROL_TYPE_PROPERTY, _CONTROL_MENU_ITEM),
                uia.CreatePropertyCondition(_NAME_PROPERTY, name),
            ),
        )
        if not item:
            return False
        pattern = item.GetCurrentPattern(_INVOKE_PATTERN)
        if pattern:
            pattern.QueryInterface(self._module.IUIAutomationInvokePattern).Invoke()
            return True
        rect = self._bounds_of(item)
        if rect is None:
            return False
        self._click_screen_point((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
        return True

    def window_name_in_handle(self, handle: int) -> str:
        """The window's accessible name (Spotify: 'Artist - Track' while playing)."""
        with _suppress(Exception):
            return " ".join((self._uia().ElementFromHandle(handle).CurrentName or "").split())
        return ""

    def press_escape(self) -> None:
        user32 = ctypes.WinDLL("user32")
        user32.keybd_event(0x1B, 0, 0, 0)
        user32.keybd_event(0x1B, 0, 2, 0)


class _suppress:
    def __init__(self, *exceptions):
        self.exceptions = exceptions

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        return kind is not None and issubclass(kind, self.exceptions)
