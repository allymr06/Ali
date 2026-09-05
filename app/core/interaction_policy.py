from __future__ import annotations

import json
from dataclasses import dataclass

from datetime import datetime

from app.core.identity import Identity
from app.core.models import Request

_TURKISH_DAYS = (
    "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar",
)
_TURKISH_MONTHS = (
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
    "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)


def clock_answer(now: datetime | None = None) -> str:
    """The current local time and date, spoken the way JARVIS would."""
    moment = (now or datetime.now()).astimezone()
    return (
        f"Şu an saat {moment:%H:%M}; bugün {moment.day} "
        f"{_TURKISH_MONTHS[moment.month - 1]} {moment.year}, "
        f"{_TURKISH_DAYS[moment.weekday()]}."
    )


@dataclass(
    frozen=True,
    slots=True,
)
class InteractionDecision:
    kind: str
    expose_tools: bool
    system_prompt: str | None = None
    direct_response: str | None = None


class InteractionPolicy:
    """
    Separate simple conversation from action-capable requests.

    Identity facts are owned by JARVIS Core rather than by a
    language model. Simple social conversation can still use a
    provider, but without exposing action tools.
    """

    _IDENTITY_REQUESTS = frozenset(
        {
            "sen kimsin",
            "kimsin",
            "adin ne",
            "ad\u0131n ne",
            "senin adin ne",
            "senin ad\u0131n ne",
            "ismin ne",
            "senin ismin ne",
            "jarvis misin",
            "sen jarvis misin",
            "kimligin ne",
            "kimli\u011fin ne",
            "kendini tanit",
            "kendini tan\u0131t",
            "bana kendini tanit",
            "bana kendini tan\u0131t",
            "kendinden bahset",
            "genel olarak sen kimsin",
            "tam olarak sen kimsin",
            "sen tam olarak kimsin",
            "peki sen kimsin",
            "sen aslinda kimsin",
            "sen asl\u0131nda kimsin",
        }
    )

    _SOCIAL_REQUESTS = frozenset(
        {
            "merhaba",
            "selam",
            "hey",
            "nasilsin",
            "nas\u0131ls\u0131n",
            "bugun nasilsin",
            "bug\u00fcn nas\u0131ls\u0131n",
            "naber",
            "ne haber",
            "gunaydin",
            "g\u00fcnayd\u0131n",
            "iyi aksamlar",
            "iyi ak\u015famlar",
            "iyi geceler",
            "tesekkurler",
            "te\u015fekk\u00fcrler",
            "tesekkur ederim",
            "te\u015fekk\u00fcr ederim",
        }
    )

    _SOCIAL_MARKERS = (
        "neşelendir",
        "neselendir",
        "moralim",
        "moral ver",
        "kendimi kötü hissediyorum",
        "kendimi kotu hissediyorum",
        "sohbet edelim",
        "konuşalım",
        "konusalim",
        "espri yap",
        "beni güldür",
        "beni guldur",
    )

    _ACTION_MARKERS = (
        " aç",
        " ac",
        "kapat",
        "başlat",
        "baslat",
        "çalıştır",
        "calistir",
        "listele",
        "dosya",
        "klasör",
        "klasor",
        "masaüstü",
        "masaustu",
        "tarayıcı",
        "tarayici",
        "sistem bilg",
    )

    _IDENTITY_RESPONSES = (
        (
            "Ben {name}. Windows \u00fczerinde \u00e7al\u0131\u015fan "
            "ki\u015fisel yapay zek\u00e2 asistan\u0131n\u0131m."
        ),
        (
            "Ad\u0131m {name}. Bilgisayar\u0131ndaki i\u015flerde sana "
            "yard\u0131mc\u0131 olan ki\u015fisel yapay zek\u00e2 asistan\u0131n\u0131m."
        ),
        (
            "Ben {name}. Seninle konu\u015fabilen ve Windows'taki "
            "i\u015flerinde sana yard\u0131mc\u0131 olabilen yapay zek\u00e2 "
            "asistan\u0131n\u0131m."
        ),
        (
            "{name}. K\u0131saca, bilgisayar\u0131ndaki ki\u015fisel "
            "yapay zek\u00e2 asistan\u0131n\u0131m."
        ),
        (
            "Ben {name}. Sorular\u0131n\u0131 yan\u0131tlamak ve "
            "bilgisayar\u0131ndaki i\u015fleri halletmene yard\u0131mc\u0131 "
            "olmak i\u00e7in buraday\u0131m."
        ),
        (
            "Ad\u0131m {name}. Windows odakl\u0131 ki\u015fisel yapay "
            "zek\u00e2 asistan\u0131n\u0131m."
        ),
    )

    _SOCIAL_PROMPT = (
        "You are JARVIS. "
        "Reply in modern, natural Turkish when the user speaks Turkish. "
        "Keep the tone concise, calm, intelligent and conversational. "
        "Avoid archaic or Ottoman-style wording. "
        "Avoid broken translated Turkish and unnecessary foreign words. "
        "Do not invoke tools, describe tool syntax, or output tool-call JSON."
    )

    def __init__(
        self,
        identity: Identity | None = None,
    ) -> None:
        self._identity = (
            identity
            if identity is not None
            else Identity()
        )

    @staticmethod
    def _normalize(
        text: str,
    ) -> str:
        normalized = " ".join(
            text.casefold().strip().split()
        )

        return normalized.strip(
            " \t\r\n?!.,;:"
        )

    def _identity_response(
        self,
        request: Request,
    ) -> str:
        templates = self._IDENTITY_RESPONSES

        index = (
            request.request_id.int
            % len(templates)
        )

        return templates[index].format(
            name=self._identity.display_name
        )

    # A question about the clock is answered from the clock: a model
    # with sixty tool schemas in front of it was seen inventing the time
    # and the weekday even with the date in its system prompt.
    _CLOCK_MARKERS = (
        "saat kac",
        "saat kaç",
        "saati soyle",
        "saati söyle",
        "saat ne",
        "bugun gunlerden ne",
        "bugün günlerden ne",
        "bugun ne gun",
        "bugün ne gün",
        "hangi gundeyiz",
        "hangi gündeyiz",
        "bugunun tarihi",
        "bugünün tarihi",
        "tarih ne",
        "bugun ayin kaci",
        "bugün ayın kaçı",
        "what time is it",
        "what day is it",
        "what is the date",
    )
    _CLOCK_MAX_WORDS = 9

    @classmethod
    def is_clock_question(cls, normalized: str) -> bool:
        if len(normalized.split()) > cls._CLOCK_MAX_WORDS:
            return False
        return any(marker in normalized for marker in cls._CLOCK_MARKERS)

    def evaluate(
        self,
        request: Request,
    ) -> InteractionDecision:
        normalized = self._normalize(
            request.text
        )

        if self.is_clock_question(normalized):
            return InteractionDecision(
                kind="clock",
                expose_tools=False,
                system_prompt=None,
                direct_response=clock_answer(),
            )

        if normalized in self._IDENTITY_REQUESTS:
            return InteractionDecision(
                kind="identity",
                expose_tools=False,
                system_prompt=None,
                direct_response=(
                    self._identity_response(
                        request
                    )
                ),
            )

        is_social = normalized in self._SOCIAL_REQUESTS or (
            any(
                marker in normalized
                for marker in self._SOCIAL_MARKERS
            )
            and not any(
                marker in f" {normalized}"
                for marker in self._ACTION_MARKERS
            )
        )

        if is_social:
            return InteractionDecision(
                kind="social",
                expose_tools=False,
                system_prompt=self._SOCIAL_PROMPT,
                direct_response=None,
            )

        return InteractionDecision(
            kind="general",
            expose_tools=True,
            system_prompt=None,
            direct_response=None,
        )

    @staticmethod
    def _unwrap_code_fence(
        text: str,
    ) -> str:
        candidate = text.strip()

        if not (
            candidate.startswith("```")
            and candidate.endswith("```")
        ):
            return candidate

        lines = candidate.splitlines()

        if len(lines) < 3:
            return candidate

        return "\n".join(
            lines[1:-1]
        ).strip()

    @classmethod
    def plaintext_tool_name(
        cls,
        text: str,
        registered_tool_names,
    ) -> str | None:
        """
        Detect a complete raw JSON tool payload emitted as prose.

        Detection never converts prose into an executable tool call.
        """
        if not isinstance(
            text,
            str,
        ):
            return None

        candidate = cls._unwrap_code_fence(
            text
        )

        if not (
            candidate.startswith("{")
            and candidate.endswith("}")
        ):
            return None

        try:
            payload = json.loads(
                candidate
            )
        except (
            json.JSONDecodeError,
            TypeError,
        ):
            return None

        if not isinstance(
            payload,
            dict,
        ):
            return None

        name: str | None = None
        has_arguments = False

        direct_name = payload.get(
            "name"
        )

        if isinstance(
            direct_name,
            str,
        ):
            name = direct_name.strip()

            has_arguments = (
                "parameters" in payload
                or "arguments" in payload
            )

        function = payload.get(
            "function"
        )

        if isinstance(
            function,
            dict,
        ):
            function_name = function.get(
                "name"
            )

            if isinstance(
                function_name,
                str,
            ):
                name = function_name.strip()

                has_arguments = (
                    "arguments" in function
                    or "parameters" in function
                )

        if (
            not name
            or not has_arguments
        ):
            return None

        registered = {
            str(item).strip()
            for item in registered_tool_names
            if str(item).strip()
        }

        if name not in registered:
            return None

        return name

    def safe_fallback(
        self,
        decision: InteractionDecision,
    ) -> str:
        if decision.kind == "identity":
            return self._identity.describe()

        return (
            "Bu yan\u0131t ge\u00e7erli bir yap\u0131land\u0131r\u0131lm\u0131\u015f "
            "ara\u00e7 \u00e7a\u011fr\u0131s\u0131 de\u011fildi, bu nedenle "
            "g\u00fcvenlik i\u00e7in engellendi. L\u00fctfen iste\u011fini "
            "tekrar ifade et."
        )
