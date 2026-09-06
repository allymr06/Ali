"""The subject hierarchy, loaded from data rather than hardcoded.

``curriculum.json`` nests subjects and topics; here they become flat
``Topic`` records addressed by dotted ids such as
``anatomy.musculoskeletal.upper_limb.shoulder_girdle``. The page, the
tutor and the question bank all navigate through this catalogue.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.medical.models import SUBJECT_LABELS_TR, Subject, Topic
from app.medical.text import content_tokens, fold, stem

DATA_DIRECTORY = Path(__file__).resolve().parent / "data"
CURRICULUM_FILE = DATA_DIRECTORY / "curriculum.json"


class Curriculum:
    """Flat, queryable view of the nested curriculum data."""

    def __init__(self, path: Path | None = None) -> None:
        source = path or CURRICULUM_FILE
        raw = json.loads(source.read_text(encoding="utf-8"))
        self._topics: dict[str, Topic] = {}
        self._children: dict[str, list[str]] = {}
        self._roots: list[str] = []
        # Search vocabulary, computed once: a request must not re-tokenise
        # the whole curriculum.
        self._tokens: dict[str, set[str]] = {}
        self._stems: dict[str, set[str]] = {}
        self._phrases: dict[str, tuple[str, ...]] = {}
        self._words: dict[str, tuple[str, ...]] = {}
        for order, subject in enumerate(raw.get("subjects", [])):
            subject_id = str(subject["id"])
            root = Topic(
                topic_id=subject_id,
                subject=subject_id,
                title_tr=str(subject.get("title_tr") or SUBJECT_LABELS_TR.get(subject_id, subject_id)),
                title_en=str(subject.get("title_en") or subject_id),
                parent_id=None,
                keywords=[str(item) for item in subject.get("keywords", [])],
                order=order,
            )
            self._add(root)
            self._roots.append(subject_id)
            self._load_children(subject_id, subject_id, subject.get("topics", []))

    def _add(self, topic: Topic) -> None:
        if topic.topic_id in self._topics:
            raise ValueError(f"Duplicate topic id: {topic.topic_id}")
        self._topics[topic.topic_id] = topic
        haystack = " ".join(
            [topic.title_tr, topic.title_en, *topic.keywords, topic.topic_id.split(".")[-1].replace("_", " ")]
        )
        topic_tokens = set(content_tokens(haystack))
        self._tokens[topic.topic_id] = topic_tokens
        self._stems[topic.topic_id] = {stem(token) for token in topic_tokens}
        folded_keywords = [fold(keyword) for keyword in topic.keywords]
        self._phrases[topic.topic_id] = tuple(item for item in folded_keywords if " " in item)
        self._words[topic.topic_id] = tuple(item for item in folded_keywords if " " not in item and len(item) >= 3)
        self._children.setdefault(topic.topic_id, [])
        if topic.parent_id is not None:
            self._children.setdefault(topic.parent_id, []).append(topic.topic_id)

    def _load_children(self, subject: str, parent_id: str, nodes: list[dict[str, Any]]) -> None:
        for order, node in enumerate(nodes):
            topic_id = f"{parent_id}.{node['id']}"
            topic = Topic(
                topic_id=topic_id,
                subject=subject,
                title_tr=str(node.get("title_tr") or node["id"]),
                title_en=str(node.get("title_en") or node["id"]),
                parent_id=parent_id,
                keywords=[str(item) for item in node.get("keywords", [])],
                order=order,
            )
            self._add(topic)
            self._load_children(subject, topic_id, node.get("topics", []))

    # ------------------------------------------------------------------
    # lookup
    # ------------------------------------------------------------------

    def subjects(self) -> list[Topic]:
        return [self._topics[item] for item in self._roots]

    def subject_ids(self) -> list[str]:
        return list(self._roots)

    def get(self, topic_id: str | None) -> Topic | None:
        if not topic_id:
            return None
        return self._topics.get(str(topic_id).strip())

    def exists(self, topic_id: str | None) -> bool:
        return self.get(topic_id) is not None

    def children(self, topic_id: str) -> list[Topic]:
        return [self._topics[item] for item in self._children.get(topic_id, [])]

    def descendants(self, topic_id: str) -> list[Topic]:
        found: list[Topic] = []
        stack = list(self._children.get(topic_id, []))
        while stack:
            current = stack.pop(0)
            topic = self._topics[current]
            found.append(topic)
            stack.extend(self._children.get(current, []))
        return found

    def path(self, topic_id: str) -> list[Topic]:
        chain: list[Topic] = []
        current = self.get(topic_id)
        while current is not None:
            chain.append(current)
            current = self.get(current.parent_id)
        chain.reverse()
        return chain

    def breadcrumb(self, topic_id: str | None) -> str:
        return " › ".join(topic.title_tr for topic in self.path(topic_id or ""))

    def subject_of(self, topic_id: str | None) -> str | None:
        topic = self.get(topic_id)
        return topic.subject if topic else None

    def all_topics(self) -> list[Topic]:
        return list(self._topics.values())

    def is_within(self, topic_id: str, ancestor_id: str) -> bool:
        return topic_id == ancestor_id or topic_id.startswith(ancestor_id + ".")

    # ------------------------------------------------------------------
    # search and resolution
    # ------------------------------------------------------------------

    def search(self, text: str, *, subject: str | None = None, limit: int = 8) -> list[Topic]:
        """Rank topics by overlap between the text and their titles/keywords.

        A whole token or a multi-word keyword must match; prefix stems
        alone only refine the ranking, so "şıklı" never lands on
        "siklin".
        """
        query_tokens = set(content_tokens(text))
        query_stems = {stem(token) for token in query_tokens}
        if not query_tokens:
            return []
        scored: list[tuple[float, Topic]] = []
        folded_text = f" {fold(text)} "
        for topic in self._topics.values():
            if subject and topic.subject != subject:
                continue
            topic_tokens = self._tokens[topic.topic_id]
            exact = query_tokens & topic_tokens
            stems_only = (query_stems & self._stems[topic.topic_id]) - {stem(token) for token in exact}
            phrase_hits = sum(1 for phrase in self._phrases[topic.topic_id] if f" {phrase} " in folded_text)
            if not exact and not phrase_hits:
                continue
            score = len(exact) + 0.35 * len(stems_only) + 1.5 * phrase_hits
            score += 0.05 * topic.topic_id.count(".")
            scored.append((score, topic))
        scored.sort(key=lambda item: (-item[0], item[1].topic_id))
        # A caller asking for nothing gets nothing; a negative limit is
        # treated the same rather than slicing from the end.
        return [topic for _score, topic in scored[: max(0, limit)]]

    def resolve_subject(self, text: str) -> str | None:
        """The subject named in the text, or the one whose topics' keywords
        appear in it (a subject name always outranks topic keywords)."""
        return self.resolve_subject_detail(text)[0]

    def resolve_subject_detail(self, text: str) -> tuple[str | None, str]:
        """``(subject, basis)`` where basis is ``name`` when the subject
        itself is mentioned, ``keyword`` when only topic vocabulary is.

        The caller reads ``name`` as the user's own choice of subject, so
        only the subject's title or one of its keywords met as written may
        report it: a token that merely begins with a keyword is a hint,
        never a choice.
        """
        folded = fold(text)
        tokens = set(content_tokens(text))

        def keyword_hits(topic_id: str) -> tuple[int, int]:
            """``(written, partial)`` hits: keywords present as written, and
            keywords a longer token only starts with."""
            written = 0
            partial = 0
            for phrase in self._phrases[topic_id]:
                if phrase in folded:
                    written += 2
            for word in self._words[topic_id]:
                if word in tokens:
                    written += 1
                elif len(word) >= 5 and any(token.startswith(word) for token in tokens):
                    partial += 1
            return written, partial

        scores: dict[str, int] = {}
        for root_id in self._roots:
            root = self._topics[root_id]
            written, partial = keyword_hits(root_id)
            if fold(root.title_tr) in tokens or fold(root.title_en) in tokens or written:
                return root_id, "name"
            # A partial hit still points at the subject, but it must not stop
            # the walk: a subject named further down outranks it.
            if partial:
                scores[root_id] = scores.get(root_id, 0) + partial
        for topic in self._topics.values():
            if topic.parent_id is None:
                continue
            written, partial = keyword_hits(topic.topic_id)
            if written or partial:
                scores[topic.subject] = scores.get(topic.subject, 0) + written + partial
        if not scores:
            return None, "none"
        ranked = sorted(scores.items(), key=lambda item: (-item[1], self._roots.index(item[0])))
        return ranked[0][0], "keyword"

    def tree(self) -> list[dict[str, Any]]:
        def node(topic_id: str) -> dict[str, Any]:
            topic = self._topics[topic_id]
            return {
                "topic_id": topic.topic_id,
                "subject": topic.subject,
                "title": topic.title_tr,
                "title_en": topic.title_en,
                "keywords": list(topic.keywords),
                "children": [node(child) for child in self._children.get(topic_id, [])],
            }

        return [node(root) for root in self._roots]


def valid_subject(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    for subject in Subject:
        if text == subject.value:
            return subject.value
    for subject_id, label in SUBJECT_LABELS_TR.items():
        if text and fold(text) == fold(label):
            return str(subject_id)
    return None


_default: Curriculum | None = None


def default_curriculum() -> Curriculum:
    global _default
    if _default is None:
        _default = Curriculum()
    return _default
