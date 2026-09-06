"""Prompt assembly for the tutor and the structured pipelines.

Every instruction the tutor's model receives is assembled here, so the
academy's honesty guarantees — grounding, citations, "say nothing was
found" — only exist as long as these blocks reach the prompt. The tests
below call the builders directly with controlled arguments and pin the
distinctive wording of each block, so silently dropping one is a
failure rather than a green suite.
"""

from __future__ import annotations

import pytest

from app.medical.models import (
    DepthLevel,
    KnowledgePriority,
    KnowledgeSource,
    Subject,
)
from app.medical.prompts import (
    CITATION_RULES,
    DEPTH_GUIDANCE,
    FORMAT_RULES,
    HONESTY_RULES,
    LANGUAGE_RULES,
    LEVEL_RULES,
    MODE_GUIDANCE,
    NO_EVIDENCE_NOTE,
    PRIORITY_RULES,
    SAFETY_RULES,
    SOURCE_RULES,
    SPOKEN_RULES,
    SUBJECT_GUIDANCE,
    TUTOR_ROLE,
    comparison_prompt,
    concept_extraction_prompt,
    document_analysis_prompt,
    notes_prompt,
    page_visual_prompt,
    question_extraction_prompt,
    question_generation_prompt,
    style_annotation_prompt,
    tutor_system_prompt,
)

EVIDENCE = "[Kaynak 1] Anatomi ders notu, s. 12\nTuberculum majus humeri lateralde yer alir."
DOCUMENT_SOURCES = (KnowledgeSource.SELECTED_DOCUMENTS, KnowledgeSource.COURSE_MATERIAL)
STANDALONE_SOURCES = (KnowledgeSource.COURSE_AND_JARVIS, KnowledgeSource.STANDARD)
SUBJECTS = tuple(Subject)


def build(**overrides: object) -> str:
    """Build a tutor prompt from a neutral baseline plus the overrides under test."""
    arguments: dict[str, object] = {
        "subject": None,
        "topic_path": "",
        "intent": "medical.explain",
        "depth": DepthLevel.STANDARD,
        "knowledge_source": KnowledgeSource.STANDARD,
        "knowledge_priority": KnowledgePriority.BALANCED,
        "evidence_text": "",
        "curated_facts": "",
        "professor_directive": None,
        "mastery_hints": (),
        "spoken": False,
    }
    arguments.update(overrides)
    return tutor_system_prompt(**arguments)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# grounding
# ---------------------------------------------------------------------------


def test_the_four_grounding_modes_produce_four_different_prompts() -> None:
    prompts = {source: build(knowledge_source=source, evidence_text=EVIDENCE) for source in KnowledgeSource}

    assert len(set(prompts.values())) == 4
    for source, prompt in prompts.items():
        assert SOURCE_RULES[source] in prompt
        others = [rule for key, rule in SOURCE_RULES.items() if key is not source]
        assert not [rule for rule in others if rule in prompt]


def test_selected_documents_refuses_to_go_beyond_the_excerpts() -> None:
    prompt = build(knowledge_source=KnowledgeSource.SELECTED_DOCUMENTS, evidence_text=EVIDENCE)

    assert "answer ONLY from the course excerpts below" in prompt
    assert "Seçili ders notlarında bu bilgi yok" in prompt


def test_course_and_jarvis_keeps_lecture_and_textbook_distinguishable() -> None:
    prompt = build(knowledge_source=KnowledgeSource.COURSE_AND_JARVIS, evidence_text=EVIDENCE)

    assert "Keep the two distinguishable" in prompt
    assert "'Ders notu:'" in prompt
    assert "'Standart kaynaklar:'" in prompt


def test_course_material_labels_only_the_gap_filling_as_standard() -> None:
    prompt = build(knowledge_source=KnowledgeSource.COURSE_MATERIAL, evidence_text=EVIDENCE)

    assert "the course excerpts below are the primary source" in prompt
    assert "label such additions 'Standart kaynaklar:'" in prompt


def test_standard_grounding_lets_general_knowledge_lead() -> None:
    prompt = build(knowledge_source=KnowledgeSource.STANDARD, evidence_text=EVIDENCE)

    assert "standard first-year medical knowledge leads" in prompt


def test_unknown_grounding_mode_falls_back_instead_of_dropping_the_rule() -> None:
    prompt = build(knowledge_source="telepathy", evidence_text=EVIDENCE)

    assert SOURCE_RULES[KnowledgeSource.COURSE_AND_JARVIS] in prompt


# ---------------------------------------------------------------------------
# evidence, citations and the missing-evidence guarantee
# ---------------------------------------------------------------------------


def test_evidence_brings_the_citation_rule_and_the_excerpts_verbatim() -> None:
    prompt = build(knowledge_source=KnowledgeSource.COURSE_MATERIAL, evidence_text=EVIDENCE)

    assert CITATION_RULES in prompt
    assert "cite it inline as (Kaynak N, s. PAGE)" in prompt
    assert "Never cite a page that is not in the excerpts" in prompt
    assert "Course excerpts:\n" + EVIDENCE in prompt
    assert NO_EVIDENCE_NOTE not in prompt


@pytest.mark.parametrize("source", DOCUMENT_SOURCES)
def test_missing_evidence_tells_a_document_scoped_tutor_to_report_the_gap(source: str) -> None:
    prompt = build(knowledge_source=source, evidence_text="")

    assert NO_EVIDENCE_NOTE in prompt
    assert "nothing relevant was found in the selected documents" in prompt
    assert "Course excerpts:" not in prompt
    assert CITATION_RULES not in prompt


@pytest.mark.parametrize("source", STANDALONE_SOURCES)
def test_missing_evidence_adds_no_note_when_the_tutor_may_answer_from_its_own_knowledge(source: str) -> None:
    prompt = build(knowledge_source=source, evidence_text="")

    assert NO_EVIDENCE_NOTE not in prompt
    assert not [rule for rule in SOURCE_RULES.values() if rule in prompt]


def test_citation_rule_is_absent_without_evidence_so_no_page_can_be_cited() -> None:
    prompt = build(knowledge_source=KnowledgeSource.SELECTED_DOCUMENTS, evidence_text="")

    assert "(Kaynak N, s. PAGE)" not in prompt


# ---------------------------------------------------------------------------
# exam priority
# ---------------------------------------------------------------------------


def test_the_four_exam_priorities_produce_four_different_prompts() -> None:
    prompts = {
        priority: build(knowledge_priority=priority, evidence_text=EVIDENCE) for priority in KnowledgePriority
    }

    assert len(set(prompts.values())) == 4
    for priority, prompt in prompts.items():
        assert PRIORITY_RULES[priority] in prompt
        others = [rule for key, rule in PRIORITY_RULES.items() if key is not priority]
        assert not [rule for rule in others if rule in prompt]


def test_strict_lecture_priority_forbids_going_beyond_the_material() -> None:
    prompt = build(knowledge_priority=KnowledgePriority.STRICT_LECTURE, evidence_text=EVIDENCE)

    assert "do not go beyond it" in prompt


def test_unknown_exam_priority_falls_back_to_balanced() -> None:
    prompt = build(knowledge_priority="whatever", evidence_text=EVIDENCE)

    assert PRIORITY_RULES[KnowledgePriority.BALANCED] in prompt


# ---------------------------------------------------------------------------
# depth and mode
# ---------------------------------------------------------------------------


def test_every_depth_level_contributes_its_own_guidance() -> None:
    prompts = {depth: build(depth=depth) for depth in DepthLevel}

    assert len(set(prompts.values())) == len(DepthLevel)
    for depth, prompt in prompts.items():
        assert DEPTH_GUIDANCE[depth] in prompt
        assert f"depth={depth}" in prompt
        others = [block for key, block in DEPTH_GUIDANCE.items() if key is not depth]
        assert not [block for block in others if block in prompt]


def test_unknown_depth_falls_back_to_standard_rather_than_an_empty_block() -> None:
    prompt = build(depth="quantum")

    assert DEPTH_GUIDANCE[DepthLevel.STANDARD] in prompt
    assert "depth=quantum" in prompt


def test_every_mode_contributes_its_own_guidance() -> None:
    prompts = {intent: build(intent=intent) for intent in MODE_GUIDANCE}

    assert len(set(prompts.values())) == len(MODE_GUIDANCE)
    for intent, prompt in prompts.items():
        assert MODE_GUIDANCE[intent] in prompt


def test_unknown_mode_falls_back_to_the_general_task() -> None:
    prompt = build(intent="medical.astrology")

    assert MODE_GUIDANCE["medical.general"] in prompt


def test_why_wrong_mode_walks_through_every_option() -> None:
    prompt = build(intent="medical.why_wrong")

    assert "why the chosen option is wrong" in prompt
    assert "why the other options are wrong" in prompt


# ---------------------------------------------------------------------------
# format, spoken replies and the always-on rules
# ---------------------------------------------------------------------------


def test_spoken_swaps_the_format_rules_for_the_spoken_rules() -> None:
    written = build(spoken=False)
    spoken = build(spoken=True)

    assert FORMAT_RULES in written
    assert SPOKEN_RULES not in written
    assert SPOKEN_RULES in spoken
    assert FORMAT_RULES not in spoken


def test_spoken_rules_forbid_markdown_and_cap_the_reply_at_three_sentences() -> None:
    spoken = build(spoken=True)

    assert "Markdown" not in spoken
    assert "at most three short natural sentences" in spoken
    assert "no lists, tables" in spoken


@pytest.mark.parametrize("spoken", [False, True])
@pytest.mark.parametrize("source", list(KnowledgeSource))
@pytest.mark.parametrize("depth", list(DepthLevel))
def test_language_honesty_and_safety_rules_survive_every_configuration(
    spoken: bool, source: str, depth: str
) -> None:
    prompt = build(spoken=spoken, knowledge_source=source, depth=depth, evidence_text=EVIDENCE)

    assert TUTOR_ROLE in prompt
    assert LEVEL_RULES in prompt
    assert LANGUAGE_RULES in prompt
    assert HONESTY_RULES in prompt
    assert SAFETY_RULES in prompt


def test_honesty_rules_forbid_invented_percentages() -> None:
    prompt = build()

    assert "Do not report invented confidence percentages" in prompt
    assert "'yüksek destek', 'sınırlı kanıt'" in prompt


def test_honesty_rules_forbid_fabricating_citations_and_pages() -> None:
    prompt = build()

    assert "Never fabricate citations, page numbers" in prompt


def test_language_rules_keep_latin_terms_in_latin() -> None:
    prompt = build()

    assert "Terminologia Anatomica" in prompt
    assert "'Latin term — Türkçe açıklama'" in prompt
    assert "Never invent Latin words or forms" in prompt


def test_safety_rules_stay_a_boundary_and_not_a_blanket_disclaimer() -> None:
    prompt = build()

    assert "need no disclaimer at all" in prompt
    assert "cannot replace a clinical evaluation" in prompt


def test_the_role_opens_the_prompt_and_the_boundary_closes_it() -> None:
    prompt = build(evidence_text=EVIDENCE, knowledge_source=KnowledgeSource.COURSE_MATERIAL)

    assert prompt.startswith(TUTOR_ROLE)
    assert prompt.endswith(SAFETY_RULES)
    # An empty block would show up as a third newline between two blocks.
    assert "\n\n\n" not in prompt


# ---------------------------------------------------------------------------
# subject guidance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subject", SUBJECTS)
def test_subject_guidance_appears_only_for_the_matching_subject(subject: str) -> None:
    prompt = build(subject=subject)

    assert SUBJECT_GUIDANCE[subject] in prompt
    others = [block for key, block in SUBJECT_GUIDANCE.items() if key != subject]
    assert len(others) == 6
    assert not [block for block in others if block in prompt]


def test_all_seven_subjects_are_covered_by_guidance() -> None:
    assert set(SUBJECT_GUIDANCE) == {str(subject) for subject in SUBJECTS}


def test_anatomy_guidance_carries_the_structure_templates() -> None:
    prompt = build(subject=Subject.ANATOMY)

    assert "origo, insertio, innervatio" in prompt


def test_unknown_or_missing_subject_adds_no_guidance_block() -> None:
    unknown = build(subject="astrology")
    missing = build(subject=None)

    for prompt in (unknown, missing):
        assert not [block for block in SUBJECT_GUIDANCE.values() if block in prompt]


def test_session_line_names_the_subject_topic_and_depth() -> None:
    prompt = build(subject=Subject.HISTOLOGY, topic_path="Histoloji › Epitel", depth=DepthLevel.EXAM)

    assert "Study session: subject=Histoloji, topic=Histoloji › Epitel, depth=exam" in prompt


def test_session_line_reports_an_unchosen_subject_and_omits_an_empty_topic() -> None:
    prompt = build(subject=None, topic_path="")

    assert "Study session: subject=not chosen, depth=standard" in prompt
    assert "topic=" not in prompt


def test_session_line_echoes_a_subject_without_a_turkish_label() -> None:
    prompt = build(subject="astrology")

    assert "subject=astrology" in prompt


# ---------------------------------------------------------------------------
# optional blocks
# ---------------------------------------------------------------------------


def test_professor_directive_is_included_when_given_and_absent_when_not() -> None:
    directive = "Professor style: prefers table-based muscle questions with neighbouring-structure traps."

    assert directive in build(professor_directive=directive)
    assert directive not in build(professor_directive=None)
    assert "Professor style" not in build(professor_directive="")


def test_mastery_hints_are_included_when_given_and_absent_when_not() -> None:
    with_hints = build(mastery_hints=["Trochlea/capitulum karışıyor", "Foramen listesi zayıf"])

    assert "Learning history" in with_hints
    assert "Trochlea/capitulum karışıyor; Foramen listesi zayıf" in with_hints
    assert "never to lecture the student about it" in with_hints
    assert "Learning history" not in build(mastery_hints=())


def test_mastery_hints_drop_empty_entries_and_cap_the_list() -> None:
    only_empty = build(mastery_hints=["", "   ".strip(), None and ""])
    many = build(mastery_hints=[f"hint-{index}" for index in range(8)])

    assert "Learning history" not in only_empty
    assert "hint-4" in many
    assert "hint-5" not in many


def test_curated_facts_are_marked_as_preferred_over_recall() -> None:
    facts = "Tuberculum majus humeri — lateral tüberkül."
    prompt = build(curated_facts=facts)

    assert "Verified reference facts" in prompt
    assert facts in prompt
    assert "prefer these over" in prompt
    assert "Verified reference facts" not in build(curated_facts="")


def test_quiz_note_is_included_only_when_given() -> None:
    note = "Quiz in progress: question 2 of 5 is open."

    assert note in build(quiz_note=note)
    assert "Quiz in progress" not in build()


# ---------------------------------------------------------------------------
# pipeline prompts
# ---------------------------------------------------------------------------


def test_document_analysis_prompt_carries_the_title_and_every_page() -> None:
    pages = [(3, "Epitel dokusu tek katlidir."), (7, "Bez epiteli salgi yapar.")]

    prompt = document_analysis_prompt("Histoloji Ders 2", pages)

    assert "Document title: Histoloji Ders 2" in prompt
    for page_number, text in pages:
        assert f"--- Page {page_number} ---" in prompt
        assert text in prompt
    assert "page_from/page_to" in prompt


def test_document_analysis_prompt_asks_for_uncertainties_rather_than_confident_gaps() -> None:
    prompt = document_analysis_prompt("Ders", [(1, "metin")])

    assert "anything unclear or possibly inconsistent" in prompt


def test_page_visual_prompt_names_the_page_and_the_document() -> None:
    prompt = page_visual_prompt("Anatomi Ders 1", 12, "Humerus proksimal ucu")

    assert "page 12 of the lecture material 'Anatomi Ders 1'" in prompt
    assert "Humerus proksimal ucu" in prompt
    assert "instead of guessing" in prompt


def test_page_visual_prompt_marks_an_empty_page_instead_of_inventing_text() -> None:
    prompt = page_visual_prompt("Anatomi", 4, "")

    assert "(no text)" in prompt


def test_page_visual_prompt_truncates_a_long_page_without_losing_the_page_number() -> None:
    prompt = page_visual_prompt("Anatomi", 9, "x" * 2000)

    assert "page 9 of" in prompt
    assert "x" * 1500 in prompt
    assert "x" * 1501 not in prompt


def test_comparison_prompt_uses_the_turkish_subject_label_and_keeps_the_evidence() -> None:
    prompt = comparison_prompt(Subject.BIOCHEMISTRY, EVIDENCE)

    assert "Subject: Biyokimya." in prompt
    assert f"Lecture excerpts:\n{EVIDENCE}" in prompt
    assert "only pages shown in the excerpts" in prompt


def test_comparison_prompt_falls_back_to_a_generic_subject() -> None:
    prompt = comparison_prompt(None, EVIDENCE)

    assert "Subject: tıp." in prompt


def test_question_generation_prompt_keeps_every_caller_constraint() -> None:
    prompt = question_generation_prompt(
        count=7,
        option_count=5,
        difficulty=4,
        subject=Subject.PHYSIOLOGY,
        topic_path="Fizyoloji › Kalp",
        question_type="single_best",
        evidence_text=EVIDENCE,
        knowledge_priority=KnowledgePriority.STRICT_LECTURE,
        style_directive="Professor style: two-step reasoning stems.",
        avoid_stems=["Eski soru govdesi"],
        concept_hints=["Kalp döngüsü"],
        curated_facts="Sistol ventrikul kasilmasidir.",
        weak_concepts=["Diyastol"],
    )

    assert "Write 7 NEW multiple-choice questions" in prompt
    assert "exactly 5 per question, keys A, B, C, D, E" in prompt
    assert "Difficulty 4/5 — medium-hard: applied reasoning" in prompt
    assert "Subject: Fizyoloji. Topic: Fizyoloji › Kalp." in prompt
    assert "Question type: single_best." in prompt
    assert PRIORITY_RULES[KnowledgePriority.STRICT_LECTURE] in prompt
    assert "Lecture excerpts:\n" + EVIDENCE in prompt
    assert "Sistol ventrikul kasilmasidir." in prompt
    assert "Spread the questions across these concepts: Kalp döngüsü" in prompt
    assert "The student is weak on: Diyastol" in prompt
    assert "Professor style: two-step reasoning stems." in prompt
    assert "Eski soru govdesi" in prompt


def test_question_generation_prompt_omits_the_blocks_it_was_not_given() -> None:
    prompt = question_generation_prompt(
        count=3,
        option_count=4,
        difficulty=2,
        subject=None,
        topic_path="",
        question_type="single_best",
        evidence_text="",
        knowledge_priority=KnowledgePriority.BALANCED,
        style_directive=None,
        avoid_stems=[],
        concept_hints=[""],
        curated_facts="",
        weak_concepts=[],
    )

    assert "Subject: mixed. Topic: as the material indicates." in prompt
    assert "keys A, B, C, D" in prompt
    assert "Difficulty 2/5 — easy-medium" in prompt
    assert "Lecture excerpts:" not in prompt
    assert "Verified reference facts" not in prompt
    assert "Spread the questions across" not in prompt
    assert "The student is weak on" not in prompt
    assert "Do NOT reuse" not in prompt
    assert not [rule for rule in PRIORITY_RULES.values() if rule in prompt]


def test_question_generation_prompt_states_its_quality_rules() -> None:
    prompt = question_generation_prompt(
        count=1,
        option_count=4,
        difficulty=3,
        subject=Subject.ANATOMY,
        topic_path="",
        question_type="single_best",
        evidence_text="",
        knowledge_priority=KnowledgePriority.BALANCED,
        style_directive=None,
        avoid_stems=[],
        concept_hints=[],
        curated_facts="",
        weak_concepts=[],
    )

    assert "no 'hepsi'/'hiçbiri'" in prompt
    assert "the stem must not contain the answer" in prompt
    assert "exactly one defensible correct answer" in prompt
    assert "All stems, options and explanations in Turkish" in prompt


def test_question_generation_prompt_falls_back_for_an_off_scale_difficulty() -> None:
    prompt = question_generation_prompt(
        count=1,
        option_count=4,
        difficulty=9,
        subject=Subject.ANATOMY,
        topic_path="",
        question_type="single_best",
        evidence_text="",
        knowledge_priority=KnowledgePriority.BALANCED,
        style_directive=None,
        avoid_stems=[],
        concept_hints=[],
        curated_facts="",
        weak_concepts=[],
    )

    assert "Difficulty 9/5 — medium: understanding" in prompt


def test_question_generation_prompt_caps_the_avoid_list_it_forwards() -> None:
    """The cap is a real ceiling: stems past it never reach the model."""
    stems = [f"Soru govdesi {index:03d}" for index in range(50)]

    prompt = question_generation_prompt(
        count=1,
        option_count=4,
        difficulty=3,
        subject=Subject.ANATOMY,
        topic_path="",
        question_type="single_best",
        evidence_text="",
        knowledge_priority=KnowledgePriority.BALANCED,
        style_directive=None,
        avoid_stems=stems,
        concept_hints=[],
        curated_facts="",
        weak_concepts=[],
    )

    assert "Do NOT reuse, paraphrase or lightly modify" in prompt
    assert "Soru govdesi 039" in prompt
    assert "Soru govdesi 040" not in prompt


def test_question_extraction_prompt_carries_the_text_and_forbids_guessing_the_key() -> None:
    text = "1. Humerus hangi kemiktir?\nA) Kol B) Bacak\nCevap: A"

    prompt = question_extraction_prompt(text)

    assert text in prompt
    assert "Never guess an answer key" in prompt
    assert "use null when it is not present" in prompt
    assert "Keep the original language." in prompt


def test_question_extraction_prompt_truncates_a_huge_source_text() -> None:
    prompt = question_extraction_prompt("y" * 25000)

    assert "y" * 20000 in prompt
    assert "y" * 20001 not in prompt


@pytest.mark.parametrize(
    ("mode", "marker"),
    [
        ("medical.short_notes", "concise exam notes"),
        ("medical.high_yield", "only the highest-yield facts"),
        ("medical.rapid_review", "ultra-compact one-page revision sheet"),
        ("medical.summarize", "a structured lecture summary"),
        ("medical.unknown_mode", "concise structured study notes"),
    ],
)
def test_notes_prompt_uses_the_style_of_its_mode(mode: str, marker: str) -> None:
    prompt = notes_prompt(
        mode=mode,
        subject=Subject.MICROBIOLOGY,
        topic_path="Mikrobiyoloji › Bakteriler",
        evidence_text=EVIDENCE,
        depth=DepthLevel.EXAM,
        curated_facts="",
    )

    assert marker in prompt
    assert "Subject: Mikrobiyoloji. Topic: Mikrobiyoloji › Bakteriler. Depth: exam." in prompt
    assert f"Material:\n{EVIDENCE}" in prompt
    assert "using only pages that appear below" in prompt


def test_notes_prompt_says_so_when_there_is_no_material() -> None:
    prompt = notes_prompt(
        mode="medical.short_notes",
        subject=None,
        topic_path="",
        evidence_text="",
        depth=DepthLevel.STANDARD,
        curated_facts="Kural: Gram pozitif duvar kalindir.",
    )

    assert "Subject: mixed. Topic: as the material indicates. Depth: standard." in prompt
    assert "No lecture material is available" in prompt
    assert "say so in the first line" in prompt
    assert "Material:" not in prompt
    assert "Verified reference facts:\nKural: Gram pozitif duvar kalindir." in prompt


def test_style_annotation_prompt_lists_every_item_with_its_options() -> None:
    items = [
        (0, "Humerus hangi kemiktir?", ["Kol kemigi", "Bacak kemigi"]),
        (1, "Epitel dokusu nedir?", ["Ortu dokusu", "Bag dokusu"]),
    ]

    prompt = style_annotation_prompt(items)

    for index, stem, options in items:
        assert f"[{index}] {stem}" in prompt
        for option in options:
            assert f"    - {option}" in prompt
    assert "recall / understanding / application / integration" in prompt
    assert "similar_terms" in prompt


def test_concept_extraction_prompt_carries_the_title_and_page_numbers() -> None:
    pages = [(2, "Mitoz evreleri"), (5, "Mayoz evreleri")]

    prompt = concept_extraction_prompt("Biyoloji Ders 3", pages)

    assert "Document: Biyoloji Ders 3" in prompt
    for page_number, text in pages:
        assert f"--- Page {page_number} ---" in prompt
        assert text in prompt
    assert "pages where it is discussed" in prompt


def test_concept_extraction_prompt_truncates_long_pages_but_keeps_every_page_header() -> None:
    pages = [(1, "a" * 3000), (2, "b" * 10)]

    prompt = concept_extraction_prompt("Ders", pages)

    assert "--- Page 1 ---" in prompt
    assert "--- Page 2 ---" in prompt
    assert "a" * 2500 in prompt
    assert "a" * 2501 not in prompt
