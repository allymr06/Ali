"""Structured model calls for the academy's pipelines.

Every pipeline (document analysis, comparison, question generation,
question import, style annotation, page vision) goes through
``MedicalModelClient``: one bounded call to the provider gateway with a
JSON schema, strict parsing, validation, and a single repair attempt.
The client is provider-independent; the gateway routes the model.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

from app.core.models import Context, Request, RequestSource
from app.medical.schemas import coerce_strings, validate, wire_schema

JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
DEFAULT_TIMEOUT_SECONDS = 90.0


class MedicalModelError(RuntimeError):
    """The model could not produce a usable structured result."""

    def __init__(self, message: str, *, problems: list[str] | None = None, raw: str = "") -> None:
        super().__init__(message)
        self.problems = list(problems or [])
        self.raw = raw


def extract_json(text: str) -> Any:
    """Parse JSON from a model reply, tolerating fences and prose."""
    candidate = str(text or "").strip()
    if not candidate:
        raise ValueError("empty reply")
    candidate = JSON_FENCE.sub("", candidate).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        return json.loads(candidate[start : end + 1])
    start = candidate.find("[")
    end = candidate.rfind("]")
    if start >= 0 and end > start:
        return json.loads(candidate[start : end + 1])
    raise ValueError("no JSON object in reply")


class MedicalModelClient:
    def __init__(
        self,
        gateway: Any | None,
        *,
        model: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        diagnostics: Any | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._gateway = gateway
        self._model = (model or "").strip() or None
        self._timeout = float(timeout_seconds)
        self._diagnostics = diagnostics

    @property
    def available(self) -> bool:
        return self._gateway is not None

    @property
    def model(self) -> str | None:
        return self._model

    def _record(self, name: str, message: str, *, level: str = "info", **attributes: Any) -> None:
        if self._diagnostics is None:
            return
        try:
            from app.diagnostics.models import DiagnosticLevel

            self._diagnostics.record(
                "medical",
                name,
                message,
                level=DiagnosticLevel(level),
                attributes=attributes,
            )
        except Exception:
            pass

    async def _generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None,
        response_format: dict[str, Any] | None,
        images: list[dict[str, Any]] | None,
        task_type: str | None,
        name: str,
    ) -> str:
        if self._gateway is None:
            raise MedicalModelError("Model sağlayıcısı yapılandırılmamış.")
        metadata: dict[str, Any] = {
            "medical_pipeline": name,
            "tool_schema_selection": False,
        }
        if response_format is not None:
            metadata["structured_output"] = True
        if images:
            metadata["images"] = list(images)
            metadata["vision"] = True
        request = Request(prompt, source=RequestSource.SYSTEM, metadata=metadata)
        kwargs: dict[str, Any] = {
            "system_prompt": system_prompt,
            "task_type": task_type,
        }
        if self._model:
            kwargs["model"] = self._model
        if response_format is not None:
            kwargs["response_format"] = response_format
        try:
            response = await asyncio.wait_for(
                self._gateway.generate(request, Context(), **kwargs),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError as exc:
            raise MedicalModelError("Model yanıtı zaman aşımına uğradı.") from exc
        except MedicalModelError:
            raise
        except Exception as exc:
            raise MedicalModelError(f"Model çağrısı başarısız ({type(exc).__name__}).") from exc
        return str(getattr(response, "text", "") or "")

    async def structured(
        self,
        name: str,
        prompt: str,
        schema: dict[str, Any],
        *,
        system_prompt: str | None = None,
        images: list[dict[str, Any]] | None = None,
        task_type: str | None = "complex",
        max_attempts: int = 2,
    ) -> dict[str, Any]:
        """Ask for JSON matching ``schema``; repair once on a bad reply."""
        # The provider gets the structural schema only; every bound is still
        # enforced by ``validate`` below and shown to the model in the prompt.
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": name.replace(".", "_"), "schema": wire_schema(schema)},
        }
        instruction = (
            f"{prompt}\n\nReturn ONLY a JSON object that matches this JSON schema, "
            "with no prose before or after it:\n" + json.dumps(schema, ensure_ascii=False)
        )
        last_problems: list[str] = []
        raw = ""
        for attempt in range(1, max(1, max_attempts) + 1):
            raw = await self._generate(
                instruction if attempt == 1 else (
                    instruction
                    + "\n\nYour previous reply was rejected: "
                    + "; ".join(last_problems[:6])
                    + ". Reply again with valid JSON only."
                ),
                system_prompt=system_prompt,
                response_format=response_format,
                images=images,
                task_type=task_type,
                name=name,
            )
            try:
                data = extract_json(raw)
            except (ValueError, json.JSONDecodeError) as exc:
                last_problems = [f"not valid JSON ({exc})"]
                self._record("pipeline.invalid_json", f"{name}: reply was not JSON.", level="warning", attempt=attempt)
                continue
            if isinstance(data, dict):
                data = coerce_strings(data, schema)
            problems = validate(data, schema)
            if not problems:
                self._record("pipeline.completed", f"{name}: structured reply accepted.", attempt=attempt)
                return data if isinstance(data, dict) else {"value": data}
            last_problems = problems
            self._record(
                "pipeline.invalid_shape",
                f"{name}: reply did not match the schema.",
                level="warning",
                attempt=attempt,
                problems=len(problems),
            )
        # This message reaches the student: a failed background job is toasted and
        # published with str(exc), so it is Turkish like every other one here. The
        # pipeline name and the schema problems stay in the ledger, in English.
        raise MedicalModelError(
            "Model beklenen biçimde yanıt vermedi; tekrar dene.",
            problems=last_problems,
            raw=raw[:2000],
        )

    async def text(
        self,
        name: str,
        prompt: str,
        *,
        system_prompt: str | None = None,
        images: list[dict[str, Any]] | None = None,
        task_type: str | None = "standard",
    ) -> str:
        return await self._generate(
            prompt,
            system_prompt=system_prompt,
            response_format=None,
            images=images,
            task_type=task_type,
            name=name,
        )
