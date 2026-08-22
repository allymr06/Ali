from __future__ import annotations

from app.core.assurance import AssuranceLevel, summarize_assurance
from app.core.models import ToolExecutionStatus, ToolResult


def result(name: str, *, verified: bool = True, data=None) -> ToolResult:
    return ToolResult(
        ToolExecutionStatus.SUCCESS,
        name,
        verified=verified,
        data=data,
    )


def test_model_only_response_is_not_claimed_as_verified() -> None:
    summary = summarize_assurance([])

    assert summary.level is AssuranceLevel.UNVERIFIED
    assert summary.uncertainty is None


def test_verified_non_research_tools_are_labeled_deterministically() -> None:
    summary = summarize_assurance([result("get_windows_system_info")])

    assert summary.level is AssuranceLevel.TOOL_VERIFIED


def test_incomplete_outcome_never_keeps_tool_assurance() -> None:
    summary = summarize_assurance(
        [result("get_windows_system_info")],
        outcome="cancelled",
    )

    assert summary.level is AssuranceLevel.UNVERIFIED
    assert "tamamlanma" in (summary.uncertainty or "")


def test_unverified_or_failed_tool_never_gets_assurance_label() -> None:
    unverified = summarize_assurance([result("tool", verified=False)])
    failed = summarize_assurance(
        [ToolResult(ToolExecutionStatus.FAILED, "tool", verified=True)]
    )

    assert unverified.level is AssuranceLevel.UNVERIFIED
    assert "doğrulanmadı" in (unverified.uncertainty or "")
    assert failed.level is AssuranceLevel.UNVERIFIED


def test_research_requires_two_fresh_independent_sources() -> None:
    supported = summarize_assurance(
        [
            result(
                "research_web",
                data={
                    "sources": [
                        {"id": "S1", "url": "https://one.example/a"},
                        {"id": "S2", "url": "https://two.example/b"},
                    ],
                    "stale": False,
                    "claims": [{"text": "supported", "citations": ["S1", "S2"]}],
                    "uncertainties": [],
                },
            )
        ]
    )
    single = summarize_assurance(
        [
            result(
                "research_web",
                data={
                    "sources": [
                        {"id": "S1", "url": "https://one.example/a"}
                    ],
                    "claims": [{"text": "single", "citations": ["S1"]}],
                },
            )
        ]
    )
    stale = summarize_assurance(
        [
            result(
                "research_web",
                data={
                    "sources": [
                        {"id": "S1", "url": "https://one.example/a"},
                        {"id": "S2", "url": "https://two.example/b"},
                    ],
                    "stale": True,
                    "claims": [{"text": "stale", "citations": ["S1", "S2"]}],
                },
            )
        ]
    )

    assert supported.level is AssuranceLevel.RESEARCH_SUPPORTED
    assert single.level is AssuranceLevel.UNVERIFIED
    assert stale.level is AssuranceLevel.UNVERIFIED
    assert "güncelliği" in (stale.uncertainty or "")


def test_research_claims_must_cite_known_sources() -> None:
    base_data = {
        "sources": [
            {"id": "S1", "url": "https://one.example/a"},
            {"id": "S2", "url": "https://two.example/b"},
        ],
        "stale": False,
        "uncertainties": [],
    }

    missing = summarize_assurance(
        [
            result(
                "research_web",
                data={**base_data, "claims": [{"text": "unsupported"}]},
            )
        ]
    )
    unknown = summarize_assurance(
        [
            result(
                "research_web",
                data={
                    **base_data,
                    "claims": [
                        {"text": "unsupported", "citations": ["S3"]}
                    ],
                },
            )
        ]
    )

    assert missing.level is AssuranceLevel.UNVERIFIED
    assert unknown.level is AssuranceLevel.UNVERIFIED
    assert "atıf" in (missing.uncertainty or "")


def test_supported_research_preserves_bounded_uncertainty_summary() -> None:
    summary = summarize_assurance(
        [
            result(
                "research_web",
                data={
                    "sources": [
                        {"id": "S1", "url": "https://one.example/a"},
                        {"id": "S2", "url": "https://two.example/b"},
                    ],
                    "claims": [
                        {"text": "supported", "citations": ["S1", "S2"]}
                    ],
                    "stale": False,
                    "uncertainties": ["Birinci not", "İkinci not", "Üçüncü not"],
                },
            )
        ]
    )

    assert summary.level is AssuranceLevel.RESEARCH_SUPPORTED
    assert summary.uncertainty == "Birinci not; İkinci not"
