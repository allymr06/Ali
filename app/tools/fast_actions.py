from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re

from app.core.models import Request


@dataclass(
    frozen=True,
    slots=True,
)
class FastActionRoute:
    tool_name: str
    parameters: dict[str, object]
    reason: str
    display_name: str


class ApprovedApplicationFastRouter:
    """
    Fail-closed router for explicit launches of
    applications already present in the trusted
    Windows application registry.

    This class never executes applications itself.
    """

    TOOL_NAME = (
        "launch_windows_application"
    )

    _VERBS = frozenset(
        {
            "ac",
            "baslat",
            "calistir",
        }
    )

    _POLITE = frozenset(
        {
            "jarvis",
            "lutfen",
            "hemen",
        }
    )

    _OBJECT_SUFFIXES = frozenset(
        {
            "i",
            "yi",
            "u",
            "yu",
        }
    )

    _TRANSLATION = str.maketrans(
        {
            "\u0131": "i",
            "\u015f": "s",
            "\u011f": "g",
            "\u00fc": "u",
            "\u00f6": "o",
            "\u00e7": "c",
        }
    )

    def __init__(
        self,
        applications: Mapping[
            str,
            tuple[str, str],
        ],
    ) -> None:
        aliases: dict[
            str,
            tuple[str, str],
        ] = {}

        ambiguous: set[str] = set()

        for raw_name, target in (
            applications.items()
        ):
            if (
                not isinstance(
                    raw_name,
                    str,
                )
                or not raw_name.strip()
            ):
                raise ValueError(
                    "Application alias "
                    "cannot be empty."
                )

            if (
                not isinstance(
                    target,
                    tuple,
                )
                or len(target) != 2
                or not all(
                    isinstance(item, str)
                    and item.strip()
                    for item in target
                )
            ):
                raise ValueError(
                    "Invalid application target."
                )

            normalized = (
                self._normalize(
                    raw_name
                )
            )

            canonical = (
                target[0].strip(),
                target[1].strip(),
            )

            if not normalized:
                continue

            if normalized in ambiguous:
                continue

            existing = aliases.get(
                normalized
            )

            if (
                existing is not None
                and existing != canonical
            ):
                aliases.pop(
                    normalized,
                    None,
                )

                ambiguous.add(
                    normalized
                )

                continue

            aliases[
                normalized
            ] = canonical

        self._aliases = aliases

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

        cleaned = re.sub(
            r"[^\w]+",
            " ",
            translated,
            flags=re.UNICODE,
        )

        cleaned = cleaned.replace(
            "_",
            " ",
        )

        return " ".join(
            cleaned.split()
        )

    @classmethod
    def _tokens(
        cls,
        text: str,
    ) -> list[str]:
        return (
            cls._normalize(
                text
            ).split()
        )

    def route(
        self,
        request: Request,
        *,
        available_tool_names: set[str],
    ) -> FastActionRoute | None:
        if (
            request.metadata.get(
                "provider"
            )
            is not None
            or request.metadata.get(
                "model"
            )
            is not None
            or request.metadata.get(
                "fast_action_routing"
            )
            is False
        ):
            return None

        if (
            self.TOOL_NAME
            not in available_tool_names
        ):
            return None

        tokens = [
            token
            for token
            in self._tokens(
                request.text
            )
            if token
            not in self._POLITE
        ]

        if len(tokens) < 2:
            return None

        if tokens[0] in self._VERBS:
            candidate_tokens = (
                tokens[1:]
            )

        elif (
            tokens[-1]
            in self._VERBS
        ):
            candidate_tokens = (
                tokens[:-1]
            )

        else:
            return None

        if not candidate_tokens:
            return None

        if any(
            token in self._VERBS
            for token
            in candidate_tokens
        ):
            return None

        candidates = [
            " ".join(
                candidate_tokens
            )
        ]

        if (
            len(candidate_tokens) > 1
            and candidate_tokens[-1]
            in self._OBJECT_SUFFIXES
        ):
            candidates.append(
                " ".join(
                    candidate_tokens[:-1]
                )
            )

        resolved = None

        for candidate in candidates:
            resolved = (
                self._aliases.get(
                    candidate
                )
            )

            if resolved is not None:
                break

        if resolved is None:
            return None

        application_id, display_name = (
            resolved
        )

        return FastActionRoute(
            tool_name=self.TOOL_NAME,
            parameters={
                "application": (
                    application_id
                ),
            },
            reason=(
                "explicit_approved_"
                "application_launch"
            ),
            display_name=display_name,
        )
