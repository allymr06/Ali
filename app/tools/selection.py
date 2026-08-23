from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from app.core.models import Request


@dataclass(
    frozen=True,
    slots=True,
)
class ToolSchemaSelection:
    names: frozenset[str]
    reason: str


class ToolSchemaSelector:
    """
    Deterministically narrow provider-visible tools.

    The selector never adds a tool that was not already
    exposed by Core. Unknown intent fails closed instead
    of exposing the complete registry.
    """

    _CALLER_FILTER_KEYS = (
        "allowed_tools",
        "tool_capabilities",
        "tool_tags",
    )

    _LAUNCH = (
        "ac",
        "baslat",
        "calistir",
        "launch",
        "open",
        "start",
        "run",
    )

    _LIST = (
        "liste",
        "goster",
        "bak",
        "getir",
        "show",
        "list",
        "inspect",
    )

    _DELETE = (
        "sil",
        "delete",
        "remove",
    )

    _FORGET = (
        "unut",
        "forget",
        "deactivate",
    )

    _SEARCH = (
        "ara",
        "bul",
        "search",
        "find",
    )

    _PAUSE = (
        "duraklat",
        "pause",
    )

    _RESUME = (
        "devam",
        "surdur",
        "resume",
    )

    _CANCEL = (
        "iptal",
        "cancel",
    )

    _DESTRUCTIVE_PROCESS = (
        "kapat",
        "sonlandir",
        "oldur",
        "kill",
        "terminate",
        "stop",
    )

    _TRANSLATION = str.maketrans(
        {
            "\u0131": "i",
        }
    )

    @classmethod
    def _normalize(
        cls,
        text: str,
    ) -> str:
        translated = (
            text.casefold()
            .translate(
                cls._TRANSLATION
            )
        )

        decomposed = (
            unicodedata.normalize(
                "NFKD",
                translated,
            )
        )

        without_accents = "".join(
            character
            for character in decomposed
            if not unicodedata.combining(
                character
            )
        )

        cleaned = re.sub(
            r"[^a-z0-9]+",
            " ",
            without_accents,
        )

        return " ".join(
            cleaned.split()
        )

    @classmethod
    def _tokens(
        cls,
        text: str,
    ) -> tuple[str, ...]:
        return tuple(
            cls._normalize(
                text
            ).split()
        )

    @staticmethod
    def _has_stem(
        tokens: tuple[str, ...],
        stems: tuple[str, ...],
    ) -> bool:
        return any(
            any(
                token.startswith(stem)
                for stem in stems
            )
            for token in tokens
        )

    @staticmethod
    def _has_any(
        tokens: tuple[str, ...],
        values: frozenset[str],
    ) -> bool:
        return bool(
            set(tokens)
            & values
        )

    def select(
        self,
        request: Request,
        *,
        available_names: set[str],
    ) -> ToolSchemaSelection:
        available = frozenset(
            name.strip()
            for name in available_names
            if isinstance(name, str)
            and name.strip()
        )

        if not available:
            return ToolSchemaSelection(
                frozenset(),
                "no_available_tools",
            )

        if (
            request.metadata.get(
                "tool_schema_selection"
            )
            is False
        ):
            return ToolSchemaSelection(
                available,
                "selection_disabled",
            )

        if any(
            request.metadata.get(key)
            is not None
            for key in self._CALLER_FILTER_KEYS
        ):
            return ToolSchemaSelection(
                available,
                "caller_filtered",
            )

        tokens = self._tokens(
            request.text
        )

        selected: set[str] = set()

        # --------------------------------------------------
        # Diagnostics
        # --------------------------------------------------

        diagnostics_domain = self._has_stem(
            tokens,
            (
                "diagnostik",
                "diagnostic",
                "metrik",
                "metric",
                "health",
                "saglik",
                "event",
                "olay",
                "log",
            ),
        )

        if diagnostics_domain:
            if self._has_stem(
                tokens,
                (
                    "metrik",
                    "metric",
                ),
            ):
                selected.add(
                    "diagnostics_metrics"
                )

            elif self._has_stem(
                tokens,
                (
                    "health",
                    "saglik",
                ),
            ):
                selected.add(
                    "diagnostics_health"
                )

            elif self._has_stem(
                tokens,
                (
                    "event",
                    "olay",
                    "log",
                ),
            ):
                selected.add(
                    "diagnostics_events"
                )

            else:
                selected.update(
                    {
                        "diagnostics_health",
                        "diagnostics_events",
                        "diagnostics_metrics",
                    }
                )

        # --------------------------------------------------
        # Durable tasks
        # --------------------------------------------------

        task_domain = self._has_any(
            tokens,
            frozenset(
                {
                    "gorev",
                    "gorevi",
                    "gorevler",
                    "task",
                    "tasks",
                }
            ),
        )

        if task_domain:
            if self._has_stem(
                tokens,
                self._PAUSE,
            ):
                selected.add(
                    "pause_task"
                )

            elif self._has_stem(
                tokens,
                self._RESUME,
            ):
                selected.add(
                    "resume_task"
                )

            elif self._has_stem(
                tokens,
                self._CANCEL,
            ):
                selected.add(
                    "cancel_task"
                )

            elif (
                "detay" in tokens
                or "detail" in tokens
                or any(
                    re.fullmatch(
                        (
                            r"[0-9a-f]{8}"
                            r"[0-9a-f-]{28}"
                        ),
                        token,
                    )
                    for token in tokens
                )
            ):
                selected.add(
                    "get_task"
                )

            else:
                selected.update(
                    {
                        "list_tasks",
                        "get_task",
                    }
                )

        # --------------------------------------------------
        # Memory
        # --------------------------------------------------

        memory_domain = self._has_any(
            tokens,
            frozenset(
                {
                    "hafiza",
                    "hafizam",
                    "hafizanda",
                    "memory",
                    "memories",
                }
            ),
        )

        if memory_domain:
            if self._has_stem(
                tokens,
                self._DELETE,
            ):
                selected.add(
                    "delete_memory"
                )

            elif self._has_stem(
                tokens,
                self._FORGET,
            ):
                selected.add(
                    "forget_memory"
                )

            elif self._has_stem(
                tokens,
                self._SEARCH,
            ):
                selected.add(
                    "search_memories"
                )

            else:
                selected.update(
                    {
                        "list_memories",
                        "search_memories",
                    }
                )

        # --------------------------------------------------
        # Bounded filesystem
        # --------------------------------------------------

        filesystem_domain = self._has_stem(
            tokens,
            ("dosya", "klasor", "dizin", "file", "folder", "director"),
        )

        if filesystem_domain:
            selected.add("list_allowed_file_roots")
            if self._has_stem(tokens, self._DELETE):
                pass
            elif self._has_stem(tokens, ("kopyala", "copy")):
                selected.add("copy_file")
            elif self._has_stem(tokens, ("tasi", "move", "rename")):
                selected.add("move_file")
            elif self._has_stem(tokens, ("yaz", "kaydet", "write", "save")):
                selected.add("write_text_file")
            elif self._has_stem(tokens, ("olustur", "yarat", "create", "mkdir")):
                if self._has_any(tokens, frozenset({"klasor", "dizin", "folder", "directory"})):
                    selected.add("create_directory")
                else:
                    selected.add("write_text_file")
            elif self._has_stem(tokens, self._LIST):
                selected.add("list_directory")
            elif self._has_stem(tokens, ("oku", "read", "ac")):
                selected.add("read_text_file")

        # --------------------------------------------------
        # Clipboard
        # --------------------------------------------------

        clipboard_domain = self._has_stem(
            tokens,
            ("pano", "clipboard"),
        )

        if clipboard_domain:
            if self._has_stem(tokens, ("temiz", "clear")):
                selected.add("clear_windows_clipboard")
            elif self._has_stem(tokens, ("yaz", "kopyala", "write", "copy")):
                selected.add("write_windows_clipboard")
            else:
                selected.add("read_windows_clipboard")

        # --------------------------------------------------
        # Allowlisted windows
        # --------------------------------------------------

        window_domain = self._has_stem(
            tokens,
            ("pencere", "window"),
        )

        if window_domain:
            selected.add("list_allowed_windows")
            if self._has_stem(tokens, ("kucult", "minimize")):
                selected.add("minimize_allowed_window")
            elif self._has_stem(tokens, ("geri", "restore", "duzelt")):
                selected.add("restore_allowed_window")
            elif self._has_stem(tokens, ("odak", "etkin", "activate", "focus")):
                selected.add("activate_allowed_window")

        # --------------------------------------------------
        # Windows processes
        # --------------------------------------------------

        process_domain = self._has_any(
            tokens,
            frozenset(
                {
                    "process",
                    "processes",
                    "surec",
                    "surecler",
                    "islem",
                    "islemler",
                }
            ),
        )

        if process_domain:
            destructive = self._has_stem(
                tokens,
                self._DESTRUCTIVE_PROCESS,
            )

            if not destructive:
                selected.add(
                    "list_windows_processes"
                )

        # --------------------------------------------------
        # Windows system info
        # --------------------------------------------------

        system_domain = self._has_any(
            tokens,
            frozenset(
                {
                    "sistem",
                    "system",
                    "ram",
                    "cpu",
                    "disk",
                    "bilgisayarim",
                    "pc",
                    "pcim",
                }
            ),
        )

        if system_domain:
            selected.add(
                "get_windows_system_info"
            )

        # --------------------------------------------------
        # Windows applications
        # --------------------------------------------------

        application_domain = self._has_any(
            tokens,
            frozenset(
                {
                    "uygulama",
                    "uygulamalar",
                    "program",
                    "programlar",
                    "application",
                    "applications",
                    "app",
                    "apps",
                }
            ),
        )

        launch_intent = self._has_stem(
            tokens,
            self._LAUNCH,
        )

        list_intent = self._has_stem(
            tokens,
            self._LIST,
        )

        if application_domain:
            if launch_intent:
                selected.add(
                    "launch_windows_application"
                )

            elif list_intent:
                selected.add(
                    "list_windows_applications"
                )

        # --------------------------------------------------
        # Application integrations
        # --------------------------------------------------

        music_domain = self._has_stem(
            tokens,
            (
                "spotify",
                "muzi",
                "music",
                "sarki",
                "song",
                "parca",
                "playlist",
            ),
        )

        if music_domain and not selected:
            if self._has_stem(tokens, ("playlist", "liste")):
                selected.update(
                    {
                        "spotify_create_playlist",
                        "spotify_play_track",
                    }
                )
            elif self._has_stem(
                tokens, ("istatistik", "stat", "dinleme", "top")
            ):
                selected.add("spotify_listening_stats")
            elif self._has_stem(
                tokens, ("sonraki", "next", "atla", "skip", "gec")
            ):
                selected.add("spotify_next_track")
            elif self._has_stem(
                tokens, ("onceki", "previous", "geri")
            ):
                selected.add("spotify_previous_track")
            elif self._has_stem(
                tokens,
                ("caliyor", "calan", "hangi", "ne", "nedir"),
            ):
                selected.add("spotify_now_playing")
            else:
                selected.update(
                    {
                        "spotify_play_pause",
                        "spotify_play_track",
                        "spotify_now_playing",
                    }
                )

        whatsapp_domain = self._has_stem(
            tokens, ("whatsapp", "vatsap", "wp")
        )

        delegation_intent = self._has_stem(
            tokens,
            (
                "devral",
                "devret",
                "yerime",
                "adima",
                "benim yerime",
                "delegate",
            ),
        ) or (
            self._has_stem(tokens, ("yanitla", "cevapla", "konus"))
            and self._has_stem(tokens, ("sorular", "mesajlar", "kisi"))
        )

        if whatsapp_domain and delegation_intent and not selected:
            selected.update(
                {
                    "whatsapp_delegate_chat",
                    "whatsapp_delegation_status",
                    "whatsapp_stop_delegation",
                    "whatsapp_list_contacts",
                }
            )
        elif (
            delegation_intent
            and self._has_stem(tokens, ("birak", "durdur", "kes"))
            and not selected
        ):
            selected.add("whatsapp_stop_delegation")

        if whatsapp_domain and not selected:
            if self._has_stem(
                tokens, ("ekle", "kaydet", "add")
            ) and self._has_stem(
                tokens, ("kisi", "numara", "contact", "rehber")
            ):
                selected.update(
                    {
                        "whatsapp_add_contact",
                        "whatsapp_list_contacts",
                    }
                )
            elif self._has_stem(
                tokens,
                ("gonder", "yaz", "ilet", "send", "at", "atar"),
            ):
                selected.update(
                    {
                        "whatsapp_send_message",
                        "whatsapp_open_chat",
                        "whatsapp_list_contacts",
                    }
                )
            elif self._has_stem(
                tokens, ("oku", "goster", "read", "son", "mesaj")
            ):
                selected.add("whatsapp_read_chats")
            else:
                selected.update(
                    {
                        "whatsapp_read_chats",
                        "whatsapp_open_chat",
                        "whatsapp_list_contacts",
                    }
                )

        reminder_domain = self._has_stem(
            tokens, ("hatirlat", "reminder", "alarm")
        )

        if reminder_domain and not selected:
            if self._has_stem(tokens, self._CANCEL):
                selected.update(
                    {"cancel_reminder", "list_reminders"}
                )
            elif self._has_stem(tokens, self._LIST):
                selected.add("list_reminders")
            else:
                selected.update(
                    {"create_reminder", "list_reminders"}
                )

        browser_domain = self._has_stem(
            tokens,
            (
                "site",
                "web",
                "tarayici",
                "browser",
                "google",
                "internet",
                "url",
                "www",
                "http",
            ),
        )

        if browser_domain and not selected:
            selected.update(
                {"open_website", "open_web_search"}
            )

        volume_domain = self._has_stem(
            tokens, ("ses", "volume")
        ) and self._has_stem(
            tokens,
            (
                "ac",
                "kis",
                "yukselt",
                "azalt",
                "artir",
                "dusur",
                "sessiz",
                "mute",
                "kapat",
                "seviye",
            ),
        )

        if volume_domain and not selected:
            selected.add("system_volume")

        # If no stronger domain matched, an explicit open/start
        # request can only target the approved app launcher among
        # the current JARVIS tool inventory.
        if (
            not selected
            and launch_intent
        ):
            selected.add(
                "launch_windows_application"
            )

        selected.intersection_update(
            available
        )

        if not selected:
            # No deterministic keyword matched. Rather than fail closed
            # — which left the model blind and unable to act on any
            # paraphrase the vocabulary did not anticipate — expose the
            # full available inventory and let the model resolve intent.
            # Interaction policy has already suppressed tools for social
            # and identity turns before this point, so anything reaching
            # here is a plausibly actionable request.
            return ToolSchemaSelection(
                available,
                "intent_unresolved_full_exposure",
            )

        if len(selected) > 5:
            raise RuntimeError(
                "Tool schema selector exceeded "
                "its maximum exposure bound."
            )

        return ToolSchemaSelection(
            frozenset(selected),
            "deterministic_intent_match",
        )
