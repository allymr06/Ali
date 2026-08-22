from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, TYPE_CHECKING
import unicodedata
from uuid import UUID

from app.core.models import Request, RiskLevel

if TYPE_CHECKING:
    from app.tools.executor import ToolExecutor


@dataclass(frozen=True, slots=True)
class DeterministicToolRoute:
    """Model cikarimi olmadan secilen yuksek guvenli arac rotasi."""

    tool_name: str
    parameters: dict[str, Any]
    reason: str


class DeterministicToolRouter:
    """
    Yalnizca dar kapsamli, acik ve READ_ONLY arac rotalarini secer.

    Belirsiz veya eksik parametreli istekler normal model
    yonlendirmesinde kalir.
    """

    _INSTRUCTIONAL_MARKERS = (
        "nasil",
        "nedir",
        "ne demek",
    )

    _ACTION_STEMS = (
        "goster",
        "listele",
        "kontrol",
        "incele",
        "rapor",
        "soyle",
        "ver",
        "getir",
        "bak",
    )

    _TASK_FILTER_STEMS = (
        "tamamlanan",
        "tamamlanmis",
        "calisan",
        "bekleyen",
        "duraklatilmis",
        "basarisiz",
        "iptal",
    )

    _EVENT_FILTER_STEMS = (
        "hata",
        "uyari",
        "kritik",
        "bilesen",
    )

    _MEMORY_SEARCH_PATTERN = re.compile(
        (
            r"\b(?:"
            r"haf\u0131zada|"
            r"haf\u0131zanda|"
            r"haf\u0131zamda"
            r")\s+ara\b"
            r"\s*[:\-]?\s*"
            r"(?P<query>.+?)\s*$"
        ),
        re.IGNORECASE,
    )

    _TASK_ID_PATTERN = re.compile(
        (
            r"\b"
            r"[0-9a-fA-F]{8}-"
            r"[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{12}"
            r"\b"
        )
    )

    @staticmethod
    def _normalize(text: str) -> str:
        translated = text.casefold().translate(
            str.maketrans(
                {
                    "\u0131": "i",
                }
            )
        )

        decomposed = unicodedata.normalize(
            "NFKD",
            translated,
        )

        without_accents = "".join(
            character
            for character in decomposed
            if not unicodedata.combining(character)
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
    def _has_action(
        cls,
        tokens: list[str],
    ) -> bool:
        return any(
            any(
                token.startswith(stem)
                for stem in cls._ACTION_STEMS
            )
            for token in tokens
        )

    @staticmethod
    def _has_digit(
        tokens: list[str],
    ) -> bool:
        return any(
            token.isdigit()
            for token in tokens
        )

    @staticmethod
    def _has_stem(
        tokens: list[str],
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
    def _schema_for(
        tool_name: str,
        tool_schemas: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        for schema in tool_schemas:
            if not isinstance(schema, dict):
                continue

            function = schema.get("function")

            if not isinstance(function, dict):
                continue

            if function.get("name") == tool_name:
                return function

        return None

    @staticmethod
    def _schema_accepts_parameters(
        schema: dict[str, Any],
        parameters: dict[str, Any],
    ) -> bool:
        parameter_schema = schema.get(
            "parameters",
            {},
        )

        if not isinstance(parameter_schema, dict):
            return False

        properties = parameter_schema.get(
            "properties",
            {},
        )

        if properties is None:
            properties = {}

        if not isinstance(properties, dict):
            return False

        required = parameter_schema.get(
            "required",
            [],
        )

        if required is None:
            required = []

        if not isinstance(required, list):
            return False

        if not all(
            isinstance(name, str)
            for name in required
        ):
            return False

        if any(
            name not in properties
            for name in parameters
        ):
            return False

        if any(
            name not in parameters
            for name in required
        ):
            return False

        for name, value in parameters.items():
            property_schema = properties.get(
                name,
                {},
            )

            if not isinstance(
                property_schema,
                dict,
            ):
                return False

            expected = property_schema.get(
                "type"
            )

            if (
                expected == "string"
                and not isinstance(value, str)
            ):
                return False

            if (
                expected == "boolean"
                and not isinstance(value, bool)
            ):
                return False

            if (
                expected == "integer"
                and (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                )
            ):
                return False

        return True

    @classmethod
    def _candidate(
        cls,
        request: Request,
    ) -> tuple[
        str,
        dict[str, Any],
        str,
    ] | None:
        text = request.text
        normalized = cls._normalize(text)
        tokens = normalized.split()
        has_action = cls._has_action(tokens)

        # --------------------------------------------------
        # Memory search
        # Explicit syntax only:
        # "Hafizanda ara: JARVIS projesi"
        # --------------------------------------------------

        memory_match = (
            cls._MEMORY_SEARCH_PATTERN.search(
                text
            )
        )

        if memory_match is not None:
            query = (
                memory_match.group("query")
                .strip()
                .lstrip(":-")
                .strip()
            )

            if query:
                return (
                    "search_memories",
                    {
                        "query": query,
                    },
                    (
                        "Acik hafiza arama istegi "
                        "guvenli sorgu parametresiyle eslesti."
                    ),
                )

        # --------------------------------------------------
        # Single task inspection
        # Requires a real UUID from the user.
        # --------------------------------------------------

        task_match = (
            cls._TASK_ID_PATTERN.search(
                text
            )
        )

        has_task_subject = any(
            token.startswith("gorev")
            for token in tokens
        )

        has_task_detail = any(
            token.startswith("detay")
            for token in tokens
        )

        if (
            task_match is not None
            and has_task_subject
            and (
                has_action
                or has_task_detail
            )
        ):
            try:
                task_id = str(
                    UUID(
                        task_match.group(0)
                    )
                )
            except ValueError:
                pass
            else:
                return (
                    "get_task",
                    {
                        "task_id": task_id,
                    },
                    (
                        "Acik gorev UUID istegi "
                        "READ_ONLY gorev araciyla eslesti."
                    ),
                )

        # --------------------------------------------------
        # Windows system information
        # --------------------------------------------------

        instructional = any(
            marker in normalized
            for marker in cls._INSTRUCTIONAL_MARKERS
        )

        has_system_information = (
            "sistem" in tokens
            and any(
                token.startswith("bilgi")
                for token in tokens
            )
        )

        has_computer_specs = (
            any(
                token.startswith(
                    "bilgisayar"
                )
                or token == "pc"
                for token in tokens
            )
            and any(
                token.startswith(
                    "ozellik"
                )
                for token in tokens
            )
        )

        has_local_machine = (
            "bu bilgisayar" in normalized
            or "bu pc" in normalized
            or any(
                token.startswith(
                    "bilgisayarim"
                )
                for token in tokens
            )
            or any(
                token.startswith("pcim")
                for token in tokens
            )
        )

        if (
            not instructional
            and (
                has_system_information
                or has_computer_specs
            )
            and (
                has_local_machine
                or has_action
            )
        ):
            return (
                "get_windows_system_info",
                {},
                (
                    "Acik yerel sistem bilgisi istegi "
                    "READ_ONLY Windows araciyla eslesti."
                ),
            )

        # --------------------------------------------------
        # Applications approved for JARVIS launch
        # --------------------------------------------------

        has_application_subject = any(
            token.startswith("uygulama")
            or token.startswith("program")
            for token in tokens
        )

        has_launch_context = (
            "jarvis" in tokens
            or "onayli" in tokens
            or any(
                token.startswith("acabil")
                or token.startswith(
                    "baslatabil"
                )
                for token in tokens
            )
        )

        if (
            has_application_subject
            and has_launch_context
            and (
                has_action
                or any(
                    token.startswith("acabil")
                    or token.startswith(
                        "baslatabil"
                    )
                    for token in tokens
                )
            )
        ):
            return (
                "list_windows_applications",
                {},
                (
                    "JARVIS tarafindan acilabilen "
                    "Windows uygulamalari istendi."
                ),
            )

        # --------------------------------------------------
        # Windows process observation
        # --------------------------------------------------

        has_process_subject = any(
            token.startswith("islem")
            or token.startswith("surec")
            for token in tokens
        )

        has_process_context = (
            "windows" in tokens
            or any(
                token.startswith("calisan")
                for token in tokens
            )
            or any(
                token.startswith(
                    "bilgisayar"
                )
                for token in tokens
            )
        )

        if (
            has_process_subject
            and has_process_context
            and has_action
        ):
            return (
                "list_windows_processes",
                {},
                (
                    "Acik Windows surec listesi "
                    "READ_ONLY gozlem araciyla eslesti."
                ),
            )

        # --------------------------------------------------
        # Memory listing
        # --------------------------------------------------

        has_memory_subject = any(
            token.startswith("hafiza")
            for token in tokens
        )

        has_memory_collection = (
            any(
                token.startswith("kayit")
                for token in tokens
            )
            or any(
                token.startswith(
                    "hafizalar"
                )
                for token in tokens
            )
        )

        if (
            has_memory_subject
            and has_memory_collection
            and has_action
        ):
            if cls._has_digit(tokens):
                return None

            if (
                "pasif" in tokens
                or "aktif olmayan"
                in normalized
            ):
                return None

            parameters: dict[str, Any] = {}

            if "tum" in tokens:
                parameters[
                    "active_only"
                ] = False

            return (
                "list_memories",
                parameters,
                (
                    "Acik hafiza kaydi listeleme "
                    "istegi READ_ONLY aracla eslesti."
                ),
            )

        # --------------------------------------------------
        # Durable task listing
        # --------------------------------------------------

        has_task_collection = (
            any(
                token.startswith(
                    "gorevler"
                )
                or token.startswith(
                    "gorevleri"
                )
                for token in tokens
            )
            or (
                "tum" in tokens
                and has_task_subject
            )
        )

        if (
            has_task_collection
            and has_action
        ):
            if (
                cls._has_digit(tokens)
                or cls._has_stem(
                    tokens,
                    cls._TASK_FILTER_STEMS,
                )
            ):
                return None

            return (
                "list_tasks",
                {},
                (
                    "Acik gorev listesi istegi "
                    "READ_ONLY gorev araciyla eslesti."
                ),
            )

        # --------------------------------------------------
        # Diagnostics health
        # --------------------------------------------------

        has_health_subject = any(
            token.startswith("sagli")
            for token in tokens
        )

        has_diagnostics_context = (
            "jarvis" in tokens
            or any(
                token.startswith(
                    "diagnostik"
                )
                for token in tokens
            )
        )

        if (
            has_health_subject
            and has_diagnostics_context
            and has_action
        ):
            return (
                "diagnostics_health",
                {},
                (
                    "JARVIS saglik kontrolu "
                    "READ_ONLY diagnostik araciyla eslesti."
                ),
            )

        # --------------------------------------------------
        # Diagnostics events
        # --------------------------------------------------

        has_diagnostic_token = any(
            token.startswith("diagnostik")
            for token in tokens
        )

        has_event_subject = any(
            token.startswith("olay")
            or token.startswith("kayit")
            for token in tokens
        )

        if (
            has_diagnostic_token
            and has_event_subject
            and has_action
        ):
            if (
                cls._has_digit(tokens)
                or cls._has_stem(
                    tokens,
                    cls._EVENT_FILTER_STEMS,
                )
            ):
                return None

            return (
                "diagnostics_events",
                {},
                (
                    "Acik diagnostik olay listesi "
                    "READ_ONLY aracla eslesti."
                ),
            )

        # --------------------------------------------------
        # Diagnostics metrics
        # --------------------------------------------------

        has_metric_subject = any(
            token.startswith("metrik")
            for token in tokens
        )

        if (
            has_metric_subject
            and has_diagnostics_context
            and has_action
        ):
            return (
                "diagnostics_metrics",
                {},
                (
                    "JARVIS metrik istegi "
                    "READ_ONLY diagnostik araciyla eslesti."
                ),
            )

        return None

    def route(
        self,
        request: Request,
        *,
        provider_name: str,
        tool_executor: ToolExecutor,
        tool_schemas: list[dict[str, Any]],
    ) -> DeterministicToolRoute | None:
        if (
            provider_name.strip().casefold()
            != "ollama"
        ):
            return None

        if (
            request.metadata.get(
                "deterministic_tool_routing"
            )
            is False
        ):
            return None

        candidate = self._candidate(
            request
        )

        if candidate is None:
            return None

        (
            tool_name,
            parameters,
            reason,
        ) = candidate

        schema = self._schema_for(
            tool_name,
            tool_schemas,
        )

        if schema is None:
            return None

        try:
            registered = tool_executor.get(
                tool_name
            )
        except (KeyError, ValueError):
            return None

        if not registered.enabled:
            return None

        definition = registered.definition

        if (
            definition.risk_level
            is not RiskLevel.READ_ONLY
        ):
            return None

        if definition.requires_confirmation:
            return None

        if not self._schema_accepts_parameters(
            schema,
            parameters,
        ):
            return None

        return DeterministicToolRoute(
            tool_name=tool_name,
            parameters=parameters,
            reason=reason,
        )
