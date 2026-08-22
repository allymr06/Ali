from __future__ import annotations

from ast import literal_eval as parse_literal
from dataclasses import replace
from typing import Any

from app.config.provider_preferences import (
    DEFAULT_OLLAMA_MODEL,
)
from app.config.settings import Settings
from app.providers.base import (
    ModelCapabilities,
)
from app.core.models import (
    Context,
    Request,
)
from app.providers.openai import (
    OpenAIProvider,
)


class OllamaProvider(OpenAIProvider):
    """
    Local Ollama adapter using its OpenAI-compatible API.

    The transport is intentionally shared with OpenAIProvider,
    while provider identity, configuration and capabilities
    remain independent.
    """

    _TOOL_FINALIZATION_PROMPT = (
        "Verified tool results are available. "
        "If another tool is required, call that tool. "
        "If a tool result contains CANONICAL_SYSTEM_REPORT, "
        "copy every line under CANONICAL_SYSTEM_REPORT exactly. "
        "Do not omit, rename, calculate, convert, infer, or modify "
        "any value or unit. Do not add facts that are not present."
    )

    _SYSTEM_INFO_FIELDS = (
        "release",
        "version",
        "logical_cpu_count",
        "memory_total_gib",
        "memory_available_gib",
        "disk_total_gib",
        "disk_free_gib",
    )

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        active = (
            settings
            or Settings.from_environment()
        )

        self._ollama_enabled = (
            active.ollama_enabled
            or (
                active.default_provider
                .strip()
                .casefold()
                == "ollama"
            )
        )

        ollama_settings = replace(
            active,
            api_key="ollama",
            api_base_url=(
                active.ollama_base_url
            ),
            openai_model=(
                active.ollama_model
                or DEFAULT_OLLAMA_MODEL
            ),
        )

        super().__init__(
            ollama_settings,
            client=client,
        )

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def provider_label(self) -> str:
        return "Ollama"

    @property
    def is_configured(self) -> bool:
        """
        Primary Ollama selection is usable immediately.

        As a fallback provider, Ollama participates only when
        explicitly enabled, preventing an offline local server
        from masking the original provider error.
        """
        return (
            self._ollama_enabled
            and self._client is not None
        )

    @property
    def capabilities(
        self,
    ) -> ModelCapabilities:
        # The current built-in default, llama3.2, is text-only.
        # Vision-capable Ollama models can receive their own
        # model profile later without overstating capability.
        return ModelCapabilities(
            text=True,
            streaming=True,
            structured_output=True,
            tool_calling=True,
            vision=False,
        )

    @staticmethod
    def _has_tool_result(
        request: Request,
        context: Context,
    ) -> bool:
        messages = context.values.get(
            "messages",
            [],
        )

        if not isinstance(messages, list):
            return False

        current_user_index: int | None = None

        for index in range(
            len(messages) - 1,
            -1,
            -1,
        ):
            message = messages[index]

            if (
                isinstance(message, dict)
                and message.get("role") == "user"
                and message.get("content") == request.text
            ):
                current_user_index = index
                break

        if current_user_index is None:
            return False

        return any(
            isinstance(message, dict)
            and message.get("role") == "tool"
            for message in messages[
                current_user_index + 1:
            ]
        )

    @classmethod
    def _tool_finalization_system_prompt(
        cls,
        system_prompt: str | None,
    ) -> str:
        existing = (
            system_prompt.strip()
            if system_prompt
            else ""
        )

        if existing:
            return (
                existing
                + "\n\n"
                + cls._TOOL_FINALIZATION_PROMPT
            )

        return cls._TOOL_FINALIZATION_PROMPT

    @staticmethod
    def _tool_names_by_call_id(
        messages: list[dict[str, Any]],
    ) -> dict[str, str]:
        names: dict[str, str] = {}

        for message in messages:
            if message.get("role") != "assistant":
                continue

            tool_calls = message.get("tool_calls")

            if not isinstance(tool_calls, list):
                continue

            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue

                tool_call_id = tool_call.get("id")
                function = tool_call.get("function")

                if (
                    not isinstance(tool_call_id, str)
                    or not isinstance(function, dict)
                ):
                    continue

                tool_name = function.get("name")

                if isinstance(tool_name, str):
                    names[tool_call_id] = tool_name

        return names

    @classmethod
    def _compact_system_info_content(
        cls,
        content: object,
    ) -> str | None:
        if not isinstance(content, str):
            return None

        try:
            parsed = parse_literal(content)
        except (ValueError, SyntaxError):
            return None

        if not isinstance(parsed, dict):
            return None

        if not all(
            key in parsed
            for key in cls._SYSTEM_INFO_FIELDS
        ):
            return None

        release = parsed["release"]
        version = parsed["version"]
        logical_cpus = parsed["logical_cpu_count"]
        memory_total = parsed["memory_total_gib"]
        memory_available = parsed["memory_available_gib"]
        disk_total = parsed["disk_total_gib"]
        disk_free = parsed["disk_free_gib"]

        return (
            "CANONICAL_SYSTEM_REPORT\n"
            f"Windows release: {release}\n"
            f"Windows version: {version}\n"
            f"Logical CPU count: {logical_cpus}\n"
            f"Total memory: {memory_total} GiB\n"
            f"Available memory: {memory_available} GiB\n"
            f"Total system-drive disk space: {disk_total} GiB\n"
            f"Free system-drive disk space: {disk_free} GiB"
        )

    @classmethod
    def _current_system_info_report(
        cls,
        request: Request,
        context: Context,
    ) -> str | None:
        messages = context.values.get(
            "messages",
            [],
        )

        if not isinstance(messages, list):
            return None

        current_user_index: int | None = None

        for index in range(
            len(messages) - 1,
            -1,
            -1,
        ):
            message = messages[index]

            if (
                isinstance(message, dict)
                and message.get("role") == "user"
                and message.get("content") == request.text
            ):
                current_user_index = index
                break

        if current_user_index is None:
            return None

        current_messages = [
            message
            for message in messages[
                current_user_index + 1:
            ]
            if isinstance(message, dict)
        ]

        tool_names = cls._tool_names_by_call_id(
            current_messages
        )

        tool_results: list[
            tuple[str, dict[str, Any]]
        ] = []

        for message in current_messages:
            if message.get("role") != "tool":
                continue

            tool_call_id = message.get(
                "tool_call_id"
            )

            if not isinstance(
                tool_call_id,
                str,
            ):
                return None

            tool_name = tool_names.get(
                tool_call_id
            )

            if not isinstance(
                tool_name,
                str,
            ):
                return None

            tool_results.append(
                (
                    tool_name,
                    message,
                )
            )

        if len(tool_results) != 1:
            return None

        tool_name, tool_message = (
            tool_results[0]
        )

        if (
            tool_name
            != "get_windows_system_info"
        ):
            return None

        compact = (
            cls._compact_system_info_content(
                tool_message.get("content")
            )
        )

        if compact is None:
            return None

        marker = (
            "CANONICAL_SYSTEM_REPORT\n"
        )

        if not compact.startswith(marker):
            return None

        return compact[len(marker):]

    def _messages(
        self,
        request: Request,
        context: Context,
        system_prompt: str | None,
    ) -> list[dict[str, Any]]:
        messages = super()._messages(
            request,
            context,
            system_prompt,
        )

        tool_names = self._tool_names_by_call_id(
            messages
        )

        for message in messages:
            if message.get("role") != "tool":
                continue

            tool_call_id = message.get(
                "tool_call_id"
            )

            if (
                not isinstance(tool_call_id, str)
                or tool_names.get(tool_call_id)
                != "get_windows_system_info"
            ):
                continue

            compact = (
                self._compact_system_info_content(
                    message.get("content")
                )
            )

            if compact is not None:
                message["content"] = compact

        return messages

    async def generate(
        self,
        request: Request,
        context: Context,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ):
        """
        Keep Ollama tool finalization concise and fact-preserving.

        Ollama remains responsible for deciding whether another tool
        is required. When one verified Windows system-info result is
        the complete current tool chain and Ollama finishes without
        another tool call, the user-facing text is rendered from that
        verified result instead of trusting model prose.
        """
        selected_system_prompt = system_prompt

        has_tool_result = self._has_tool_result(
            request,
            context,
        )

        system_report = (
            self._current_system_info_report(
                request,
                context,
            )
        )

        if has_tool_result:
            selected_system_prompt = (
                self._tool_finalization_system_prompt(
                    system_prompt
                )
            )

        response = await super().generate(
            request,
            context,
            model=model,
            system_prompt=selected_system_prompt,
            tools=tools,
            response_format=response_format,
        )

        if (
            system_report is not None
            and not response.tool_calls
            and response.finish_reason == "stop"
        ):
            response.text = system_report
            response.metadata[
                "deterministic_finalization"
            ] = "get_windows_system_info"

        return response
