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
            return ToolSchemaSelection(
                frozenset(),
                "no_safe_tool_match",
            )

        if len(selected) > 3:
            raise RuntimeError(
                "Tool schema selector exceeded "
                "its maximum exposure bound."
            )

        return ToolSchemaSelection(
            frozenset(selected),
            "deterministic_intent_match",
        )
