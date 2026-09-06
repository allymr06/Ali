"""JSON schemas for every model output that feeds software behaviour.

The model is asked for these shapes and the validator below checks them
before anything downstream trusts a field. The validator is small on
purpose (the subset of JSON Schema the academy uses) so it stays
dependency-free and predictable.
"""

from __future__ import annotations

from typing import Any


def _string(max_length: int = 4000) -> dict[str, Any]:
    return {"type": "string", "maxLength": max_length}


def _array(items: dict[str, Any], *, min_items: int = 0, max_items: int = 200) -> dict[str, Any]:
    return {"type": "array", "items": items, "minItems": min_items, "maxItems": max_items}


DOCUMENT_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": _string(200),
        "subject": {"type": "string", "enum": ["anatomy", "histology", "microbiology", "biochemistry", "biophysics", "physiology", "biology", "unknown"]},
        "summary": _string(2500),
        "topics": _array(
            {
                "type": "object",
                "properties": {
                    "title": _string(200),
                    "page_from": {"type": "integer", "minimum": 1},
                    "page_to": {"type": "integer", "minimum": 1},
                    "concepts": _array(_string(120), max_items=30),
                },
                "required": ["title", "page_from", "page_to"],
            },
            max_items=60,
        ),
        "key_terms": _array(_string(120), max_items=80),
        "high_yield": _array(_string(400), max_items=40),
        "uncertainties": _array(_string(300), max_items=20),
    },
    "required": ["title", "subject", "summary", "topics", "key_terms"],
}

PAGE_VISUAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "has_educational_figure": {"type": "boolean"},
        "figure_type": {"type": "string", "enum": ["anatomy_diagram", "histology_micrograph", "pathway", "table", "graph", "photo", "text_only", "other"]},
        "description": _string(2000),
        "labels": _array(_string(120), max_items=60),
        "structures": _array(_string(120), max_items=60),
        "educational_points": _array(_string(400), max_items=20),
        "legibility": {"type": "string", "enum": ["clear", "partial", "unreadable"]},
    },
    "required": ["has_educational_figure", "figure_type", "description", "labels", "legibility"],
}

COMPARISON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": _array(
            {
                "type": "object",
                "properties": {
                    "statement": _string(600),
                    "page": {"type": "integer", "minimum": 0},
                    "category": {"type": "string", "enum": ["consistent", "simplified", "incomplete", "potentially_misleading", "possibly_incorrect", "terminology_difference"]},
                    "explanation": _string(1200),
                    "standard_view": _string(1200),
                    "support": {"type": "string", "enum": ["high", "moderate", "limited"]},
                },
                "required": ["statement", "page", "category", "explanation"],
            },
            max_items=80,
        ),
        "overall": _string(1500),
    },
    "required": ["findings", "overall"],
}

QUESTION_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "stem": _string(1500),
        "options": _array(
            {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "enum": ["A", "B", "C", "D", "E", "F"]},
                    "text": _string(400),
                    "concept": _string(120),
                    "explanation": _string(500),
                },
                "required": ["key", "text"],
            },
            min_items=2,
            max_items=6,
        ),
        "correct_key": {"type": "string", "enum": ["A", "B", "C", "D", "E", "F"]},
        "explanation": _string(1500),
        "concept": _string(160),
        "topic": _string(160),
        "difficulty": {"type": "integer", "minimum": 1, "maximum": 5},
        "question_type": {"type": "string", "enum": ["single_best_answer", "true_false", "matching", "assertion_reason", "multi_statement"]},
        # The excerpt this item came from, named by the N of its "[Kaynak N]" label.
        # The page alone cannot identify it: two documents often share a page number.
        "source_index": {"type": "integer", "minimum": 0},
        "source_page": {"type": "integer", "minimum": 0},
        "source_quote": _string(400),
        # The N of the "[Şekil N]" figure the question is about; 0 for none.
        "figure_index": {"type": "integer", "minimum": 0},
        "trap": _string(400),
    },
    "required": ["stem", "options", "correct_key", "explanation", "concept"],
}

QUESTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"questions": _array(QUESTION_ITEM_SCHEMA, min_items=1, max_items=60)},
    "required": ["questions"],
}

QUESTION_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": _array(
            {
                "type": "object",
                "properties": {
                    "number": _string(20),
                    "stem": _string(2000),
                    "options": _array(
                        {"type": "object", "properties": {"key": _string(3), "text": _string(500)}, "required": ["key", "text"]},
                        max_items=8,
                    ),
                    "answer_key": {"type": ["string", "null"], "maxLength": 3},
                    "subject": _string(40),
                    "topic": _string(160),
                    "has_image": {"type": "boolean"},
                },
                "required": ["stem", "options"],
            },
            max_items=200,
        ),
        "answer_key_found": {"type": "boolean"},
        "notes": _string(800),
    },
    "required": ["questions", "answer_key_found"],
}

NOTES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": _string(200),
        "markdown": _string(12000),
        "key_terms": _array(_string(120), max_items=60),
        "cited_pages": _array({"type": "integer", "minimum": 1}, max_items=200),
    },
    "required": ["title", "markdown"],
}

CONCEPT_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "concepts": _array(
            {
                "type": "object",
                "properties": {
                    "name": _string(160),
                    "latin": _string(160),
                    "subject": _string(40),
                    "topic": _string(160),
                    "pages": _array({"type": "integer", "minimum": 1}, max_items=50),
                },
                "required": ["name"],
            },
            max_items=120,
        )
    },
    "required": ["concepts"],
}

STYLE_ANNOTATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": _array(
            {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "minimum": 0},
                    "cognitive_level": {"type": "string", "enum": ["recall", "understanding", "application", "integration"]},
                    "distractor_style": {"type": "string", "enum": ["similar_terms", "neighboring_structures", "conceptual_opposites", "partial_truths", "unrelated", "mixed"]},
                    "topic": _string(160),
                    "subject": _string(40),
                },
                "required": ["index", "cognitive_level", "distractor_style"],
            },
            max_items=200,
        )
    },
    "required": ["questions"],
}


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

_TYPE_CHECKS = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "null": lambda value: value is None,
}


def validate(data: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Return a list of problems (empty when the data matches)."""
    problems: list[str] = []
    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_TYPE_CHECKS.get(item, lambda _v: True)(data) for item in types):
            return [f"{path}: expected {'/'.join(types)}"]
    enum = schema.get("enum")
    if enum is not None and data not in enum:
        problems.append(f"{path}: value not in {enum}")
    if isinstance(data, str):
        limit = schema.get("maxLength")
        if isinstance(limit, int) and len(data) > limit:
            problems.append(f"{path}: longer than {limit} characters")
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and data < minimum:
            problems.append(f"{path}: below minimum {minimum}")
        if maximum is not None and data > maximum:
            problems.append(f"{path}: above maximum {maximum}")
    if isinstance(data, dict):
        for key in schema.get("required", []):
            if key not in data:
                problems.append(f"{path}.{key}: missing")
        properties = schema.get("properties", {})
        for key, value in data.items():
            if key in properties:
                problems.extend(validate(value, properties[key], f"{path}.{key}"))
    if isinstance(data, list):
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(data) < min_items:
            problems.append(f"{path}: fewer than {min_items} items")
        if isinstance(max_items, int) and len(data) > max_items:
            problems.append(f"{path}: more than {max_items} items")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(data):
                problems.extend(validate(item, items, f"{path}[{index}]"))
    return problems


# Keywords the provider's structured-output endpoint may refuse. Gemini's
# OpenAI-compatible API answers 400 INVALID_ARGUMENT to a nested array
# carrying minItems/maxItems (measured live, 6 September 2026), and the
# other bounds are not guaranteed either. None of them is needed on the
# wire: ``validate`` enforces every bound locally, ``coerce_strings`` trims
# the strings, and the full schema is printed in the prompt so the model
# still sees the limits it is asked to respect.
WIRE_UNSUPPORTED_KEYWORDS = frozenset({"maxLength", "minLength", "minItems", "maxItems", "minimum", "maximum"})


def wire_schema(schema: Any) -> Any:
    """The schema as sent to the provider: structure, types, enums, required."""
    if isinstance(schema, dict):
        return {key: wire_schema(value) for key, value in schema.items() if key not in WIRE_UNSUPPORTED_KEYWORDS}
    if isinstance(schema, list):
        return [wire_schema(item) for item in schema]
    return schema


def coerce_strings(data: Any, schema: dict[str, Any]) -> Any:
    """Trim over-long strings instead of failing on a single long field."""
    if isinstance(data, str):
        limit = schema.get("maxLength")
        return data[:limit] if isinstance(limit, int) and len(data) > limit else data
    if isinstance(data, dict):
        properties = schema.get("properties", {})
        return {
            key: coerce_strings(value, properties[key]) if key in properties else value
            for key, value in data.items()
        }
    if isinstance(data, list):
        items = schema.get("items")
        if isinstance(items, dict):
            max_items = schema.get("maxItems")
            trimmed = data[:max_items] if isinstance(max_items, int) else data
            return [coerce_strings(item, items) for item in trimmed]
    return data
