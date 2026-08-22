from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, TYPE_CHECKING
import unicodedata

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
    Yalnizca dar kapsamli ve yuksek guvenli arac rotalarini secer.

    Belirsiz isteklerde otomatik secim yapilmaz ve normal model
    yonlendirmesi kullanilmaya devam eder.
    """

    _SYSTEM_INFO_TOOL = "get_windows_system_info"

    _INSTRUCTIONAL_MARKERS = (
        "nasil",
        "nedir",
        "ne demek",
    )

    _ACTION_STEMS = (
        "goster",
        "kontrol",
        "incele",
        "rapor",
        "soyle",
        "ver",
        "getir",
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
    def _system_info_intent(
        cls,
        text: str,
    ) -> bool:
        normalized = cls._normalize(text)
        tokens = normalized.split()

        if any(
            marker in normalized
            for marker in cls._INSTRUCTIONAL_MARKERS
        ):
            return False

        has_system_information = (
            "sistem" in tokens
            and any(
                token.startswith("bilgi")
                for token in tokens
            )
        )

        has_computer_specs = (
            any(
                token.startswith("bilgisayar")
                or token == "pc"
                for token in tokens
            )
            and any(
                token.startswith("ozellik")
                for token in tokens
            )
        )

        if not (
            has_system_information
            or has_computer_specs
        ):
            return False

        has_local_machine = (
            "bu bilgisayar" in normalized
            or "bu pc" in normalized
            or any(
                token.startswith("bilgisayarim")
                for token in tokens
            )
            or any(
                token.startswith("pcim")
                for token in tokens
            )
        )

        has_action = any(
            any(
                token.startswith(stem)
                for stem in cls._ACTION_STEMS
            )
            for token in tokens
        )

        return (
            has_local_machine
            or has_action
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

        if not self._system_info_intent(
            request.text
        ):
            return None

        tool_name = self._SYSTEM_INFO_TOOL

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

        parameters = schema.get(
            "parameters",
            {},
        )

        if not isinstance(parameters, dict):
            return None

        properties = parameters.get(
            "properties",
            {},
        )

        required = parameters.get(
            "required",
            [],
        )

        if properties not in ({}, None):
            return None

        if required not in ([], None):
            return None

        return DeterministicToolRoute(
            tool_name=tool_name,
            parameters={},
            reason=(
                "Acik bir yerel sistem bilgisi istegi, "
                "parametresiz ve salt okunur aracla eslesti."
            ),
        )
