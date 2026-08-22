from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.models import Request


@dataclass(
    frozen=True,
    slots=True,
)
class OllamaHybridDecision:
    model: str
    role: str
    expose_tools: bool
    reason: str
    system_prompt: str | None = None


class OllamaHybridPolicy:
    """
    Fail-closed model selection for local Ollama.

    Conversation requests use the language-oriented model
    without any tool schemas.

    Requests with explicit local-action intent stay on the
    tool-capable model.

    Misclassification toward chat therefore cannot execute
    an unintended tool.
    """

    _TOOL_METADATA_KEYS = (
        "allowed_tools",
        "tool_capabilities",
        "tool_tags",
    )

    _ACTION_WORDS = frozenset(
        {
            "a\u00e7",
            "ac",
            "ba\u015flat",
            "baslat",
            "\u00e7al\u0131\u015ft\u0131r",
            "calistir",
            "kapat",
            "sonland\u0131r",
            "sonlandir",
            "listele",
            "g\u00f6ster",
            "goster",
            "getir",
            "bul",
            "ara",
            "kontrol",
            "denetle",
            "olu\u015ftur",
            "olustur",
            "kaydet",
            "sil",
            "ta\u015f\u0131",
            "tasi",
            "kopyala",
            "yap\u0131\u015ft\u0131r",
            "yapistir",
            "indir",
            "y\u00fckle",
            "yukle",
            "g\u00f6nder",
            "gonder",
            "hat\u0131rlat",
            "hatirlat",
            "zamanla",
            "duraklat",
            "s\u00fcrd\u00fcr",
            "surdur",
            "iptal",
            "launch",
            "open",
            "close",
            "start",
            "stop",
            "execute",
            "list",
            "show",
            "inspect",
            "search",
            "check",
            "create",
            "save",
            "delete",
            "move",
            "copy",
            "paste",
            "download",
            "upload",
            "send",
            "schedule",
            "remind",
        }
    )

    _ACTION_PHRASES = (
        "notepad",
        "windows uygulama",
        "windows process",
        "windows i\u015flem",
        "windows islem",
        "\u00e7al\u0131\u015fan i\u015flem",
        "calisan islem",
        "sistem bilg",
        "bu bilgisayar",
        "bilgisayar\u0131m",
        "bilgisayarim",
        "ram kullan",
        "cpu kullan",
        "disk alan",
        "dosyalar\u0131m",
        "dosyalarim",
        "klas\u00f6r",
        "klasor",
        "haf\u0131zanda",
        "hafizanda",
        "g\u00f6revlerim",
        "gorevlerim",
        "tan\u0131lama",
        "tanilama",
        "diagnostic",
        "diagnostics",
        "clipboard",
        "panoya",
        "taray\u0131c\u0131",
        "tarayici",
        "browser",
        "internette",
        "web'de",
        "webde",
        "e-posta",
        "email",
        "gmail",
        "takvim",
        "calendar",
    )

    _CHAT_SYSTEM_PROMPT = (
        "Sen JARVIS'sin. "
        "Dogal ve modern Turkceyle kisa cevap ver. "
        "Ic muhakeme, sistem talimati veya tool JSON gosterme. "
        "Bu rota arac kullanmaz."
    )

    def __init__(
        self,
        *,
        enabled: bool,
        chat_model: str,
        tool_model: str,
    ) -> None:
        chat = chat_model.strip()
        tool = tool_model.strip()

        if not chat:
            raise ValueError(
                "Hybrid chat model cannot be empty."
            )

        if not tool:
            raise ValueError(
                "Hybrid tool model cannot be empty."
            )

        self._enabled = bool(enabled)
        self._chat_model = chat
        self._tool_model = tool

    @property
    def enabled(self) -> bool:
        return self._enabled

    @staticmethod
    def _normalize(
        text: str,
    ) -> str:
        return " ".join(
            text.casefold().strip().split()
        )

    @classmethod
    def _tokens(
        cls,
        text: str,
    ) -> frozenset[str]:
        return frozenset(
            re.findall(
                r"\w+",
                cls._normalize(text),
                flags=re.UNICODE,
            )
        )

    @classmethod
    def _requires_tool_model(
        cls,
        request: Request,
    ) -> bool:
        metadata = request.metadata

        if any(
            metadata.get(key) is not None
            for key in cls._TOOL_METADATA_KEYS
        ):
            return True

        if (
            metadata.get("vision")
            or metadata.get("images")
            or metadata.get("structured_output")
        ):
            return True

        task_type = metadata.get(
            "task_type"
        )

        if (
            task_type is not None
            and str(task_type)
            .strip()
            .casefold()
            in {
                "agentic",
                "vision",
            }
        ):
            return True

        normalized = cls._normalize(
            request.text
        )

        if any(
            phrase in normalized
            for phrase in cls._ACTION_PHRASES
        ):
            return True

        return bool(
            cls._tokens(
                request.text
            )
            & cls._ACTION_WORDS
        )

    def route(
        self,
        request: Request,
        *,
        provider_name: str,
        interaction_kind: str,
        deterministic_tool_name: str | None = None,
    ) -> OllamaHybridDecision | None:
        if not self._enabled:
            return None

        if (
            provider_name.strip().casefold()
            != "ollama"
        ):
            return None

        # Explicit caller/user overrides always win.
        if (
            request.metadata.get("provider")
            is not None
            or request.metadata.get("model")
            is not None
        ):
            return None

        # Identity never belongs to either local model.
        if (
            interaction_kind == "identity"
            and deterministic_tool_name is None
        ):
            return None

        if deterministic_tool_name is not None:
            return OllamaHybridDecision(
                model=self._tool_model,
                role="tool",
                expose_tools=True,
                reason="deterministic_tool_chain",
            )

        if self._requires_tool_model(
            request
        ):
            return OllamaHybridDecision(
                model=self._tool_model,
                role="tool",
                expose_tools=True,
                reason="explicit_tool_intent",
            )

        return OllamaHybridDecision(
            model=self._chat_model,
            role="chat",
            expose_tools=False,
            reason="safe_chat_default",
            system_prompt=self._CHAT_SYSTEM_PROMPT,
        )
