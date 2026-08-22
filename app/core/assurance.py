from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from app.core.models import ToolResult


class AssuranceLevel(StrEnum):
    TOOL_VERIFIED = "tool_verified"
    RESEARCH_SUPPORTED = "research_supported"
    UNVERIFIED = "unverified"


@dataclass(frozen=True, slots=True)
class AssuranceSummary:
    level: AssuranceLevel
    uncertainty: str | None = None


def summarize_assurance(
    tool_results: Sequence[ToolResult],
    *,
    outcome: str = "completed",
) -> AssuranceSummary:
    """Classify evidence without trusting a model's self-reported confidence."""
    if outcome != "completed":
        return AssuranceSummary(
            AssuranceLevel.UNVERIFIED,
            "İşlem doğrulanmış bir tamamlanma durumuna ulaşmadı.",
        )
    if not tool_results:
        return AssuranceSummary(AssuranceLevel.UNVERIFIED)

    if not all(result.succeeded and result.verified for result in tool_results):
        if any(not result.succeeded for result in tool_results):
            detail = "Bir veya daha fazla araç sonucu başarılı tamamlanmadı."
        else:
            detail = "Araç sonucu işlem sonrası doğrulanmadı."
        return AssuranceSummary(AssuranceLevel.UNVERIFIED, detail)

    research_results = [
        result for result in tool_results if result.tool_name == "research_web"
    ]
    if not research_results:
        return AssuranceSummary(AssuranceLevel.TOOL_VERIFIED)

    for result in research_results:
        data = result.data
        if not isinstance(data, Mapping):
            return AssuranceSummary(
                AssuranceLevel.UNVERIFIED,
                "Araştırma sonucu yapısal olarak doğrulanamadı.",
            )
        raw_sources = data.get("sources")
        raw_claims = data.get("claims")
        if not isinstance(raw_sources, list) or not raw_sources:
            return AssuranceSummary(
                AssuranceLevel.UNVERIFIED,
                "Araştırma kaynakları doğrulanamadı.",
            )
        if not isinstance(raw_claims, list) or not raw_claims:
            return AssuranceSummary(
                AssuranceLevel.UNVERIFIED,
                "Araştırma iddiaları atıflarla doğrulanamadı.",
            )
        source_ids = {
            str(source.get("id", "")).strip()
            for source in raw_sources
            if isinstance(source, Mapping) and str(source.get("id", "")).strip()
        }
        if len(source_ids) != len(raw_sources):
            return AssuranceSummary(
                AssuranceLevel.UNVERIFIED,
                "Araştırma kaynak kimlikleri doğrulanamadı.",
            )
        for claim in raw_claims:
            if not isinstance(claim, Mapping):
                return AssuranceSummary(
                    AssuranceLevel.UNVERIFIED,
                    "Araştırma iddiaları atıflarla doğrulanamadı.",
                )
            citations = claim.get("citations")
            if (
                not isinstance(citations, list)
                or not citations
                or any(
                    not isinstance(citation, str)
                    or citation not in source_ids
                    for citation in citations
                )
            ):
                return AssuranceSummary(
                    AssuranceLevel.UNVERIFIED,
                    "Araştırma iddiaları atıflarla doğrulanamadı.",
                )
        hosts = {
            str(urlsplit(str(source.get("url", ""))).hostname).casefold()
            for source in raw_sources
            if isinstance(source, Mapping)
            and urlsplit(str(source.get("url", ""))).hostname
        }
        if bool(data.get("stale")):
            return AssuranceSummary(
                AssuranceLevel.UNVERIFIED,
                "Araştırma hafızasındaki kanıtların güncelliği dolmuş olabilir.",
            )
        if len(hosts) < 2:
            return AssuranceSummary(
                AssuranceLevel.UNVERIFIED,
                "Araştırma kanıtı bağımsız iki kaynaktan doğrulanmadı.",
            )

    uncertainty_items: list[str] = []
    for result in research_results:
        raw_uncertainties = result.data.get("uncertainties")
        if not isinstance(raw_uncertainties, list):
            continue
        for item in raw_uncertainties:
            if (
                isinstance(item, str)
                and item.strip()
                and item.strip() not in uncertainty_items
            ):
                uncertainty_items.append(item.strip())
    uncertainty = "; ".join(uncertainty_items[:2]) or None
    return AssuranceSummary(AssuranceLevel.RESEARCH_SUPPORTED, uncertainty)
