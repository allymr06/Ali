"""Flashcards with spaced repetition, built only from what the student already has.

A card's two sides always come from recorded material: a curated anatomy
fact, a terminology entry, a question answered wrong (its own key and
explanation), a histology specimen's crop, or a label the lecture page
itself prints (image occlusion, read from the PDF's text layer). Nothing is
written by a model; every card names its source, and a rebuild finds the
same card ids instead of duplicating them.

A grade is the student's own word, so reviews schedule repetition and count
as study time — they are never measurement. Concept mastery, findings and
exam analyses do not move when a card is answered.

The scheduler is a small, deterministic SM-2 variant, documented next to
the numbers it uses. Days are the unit; there is no random fuzz, so the
same history always gives the same plan.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from app.core.time import utc_now
from app.medical.models import new_id

CARD_KIND = "flashcard"
REVIEW_KIND = "flashcard_review"
SETTINGS_KIND = "flashcard_settings"

GRADES = ("again", "hard", "good", "easy")
GRADE_LABELS_TR: dict[str, str] = {"again": "Tekrar", "hard": "Zor", "good": "İyi", "easy": "Kolay"}
SOURCE_LABELS_TR: dict[str, str] = {
    "anatomy_fact": "Ders kartı (anatomi)",
    "terminology": "Terim sözlüğü",
    "question": "Yanlış yapılan soru",
    "histology": "Histoloji örneği",
    "occlusion": "Ders şekli (etiket kapatma)",
}
STATE_LABELS_TR: dict[str, str] = {"new": "Yeni", "learning": "Öğreniliyor", "review": "Tekrarda", "suspended": "Askıda"}

# The scheduler's numbers. Ease starts where SM-2 starts and never leaves
# [1.3, 3.0]; a lapse costs 0.2 ease and sends the card back to one day;
# "hard" grows the interval a fifth and costs 0.15 ease; "easy" adds a 1.3×
# bonus and 0.15 ease. Intervals are whole days in [1, 365].
START_EASE = 2.5
MIN_EASE, MAX_EASE = 1.3, 3.0
LAPSE_EASE_PENALTY = 0.2
HARD_EASE_PENALTY = 0.15
EASY_EASE_BONUS = 0.15
HARD_FACTOR = 1.2
EASY_BONUS = 1.3
FIRST_GOOD_DAYS = 1
FIRST_EASY_DAYS = 3
MAX_INTERVAL_DAYS = 365
DEFAULT_NEW_PER_DAY = 15
QUEUE_LIMIT = 60

# Fields short enough to sit on the back of a card, per structure kind.
FACT_CARD_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "muscle": (("origin", "origosu"), ("insertion", "insertio'su"), ("innervation", "innervasyonu"), ("action", "işlevi"), ("arterial_supply", "arteri")),
    "nerve": (("origin", "kökeni"), ("course", "seyri"), ("motor", "motor innervasyonu"), ("sensory", "duyusal alanı"), ("foramen", "kafatası çıkışı")),
    "artery": (("origin", "kökeni"), ("branches", "dalları"), ("supply", "beslediği alan")),
    "vein": (("origin", "başlangıcı"), ("drains_into", "döküldüğü yer"), ("tributaries", "katılan venleri")),
    "bone": (("articulations", "eklemleri"), ("parts", "bölümleri")),
    "joint": (("joint_type", "eklem tipi"), ("ligaments", "bağları"), ("movements", "hareketleri")),
}
MAX_BACK_CHARS = 420
MAX_BACK_ITEMS = 5


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def _back_text(value: Any) -> str | None:
    """A fact as a card back, or None when it would not fit on one."""
    if isinstance(value, list):
        items = [_clean(item) for item in value if _clean(item)]
        if not items or len(items) > MAX_BACK_ITEMS:
            return None
        text = "\n".join(f"• {item}" for item in items)
    else:
        text = _clean(value)
    if not text or len(text) > MAX_BACK_CHARS:
        return None
    return text


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"fc-{digest}"


class Scheduler:
    """The state moved by one grade; pure so tests can walk tables through it."""

    @staticmethod
    def apply(card: dict[str, Any], grade: str, now: datetime) -> dict[str, Any]:
        if grade not in GRADES:
            raise ValueError("Not 'again', 'hard', 'good' ya da 'easy' olmalı.")
        ease = float(card.get("ease") or START_EASE)
        interval = float(card.get("interval_days") or 0.0)
        state = str(card.get("state") or "new")
        reps = int(card.get("reps") or 0)
        lapses = int(card.get("lapses") or 0)
        if state in ("new", "learning"):
            if grade == "again":
                state, interval = "learning", 0.0  # again today
            elif grade == "hard":
                state, interval = "learning", 0.0 if reps == 0 else FIRST_GOOD_DAYS
            elif grade == "good":
                state, interval = "review", FIRST_GOOD_DAYS
            else:
                state, interval = "review", FIRST_EASY_DAYS
                ease = min(MAX_EASE, ease + EASY_EASE_BONUS)
        else:
            if grade == "again":
                lapses += 1
                ease = max(MIN_EASE, ease - LAPSE_EASE_PENALTY)
                state, interval = "learning", 0.0
            elif grade == "hard":
                ease = max(MIN_EASE, ease - HARD_EASE_PENALTY)
                interval = max(interval + 1, interval * HARD_FACTOR)
            elif grade == "good":
                interval = max(interval + 1, interval * ease)
            else:
                ease = min(MAX_EASE, ease + EASY_EASE_BONUS)
                interval = max(interval + 1, interval * ease * EASY_BONUS)
        # Half-up, not banker's: 2.5 days schedules 3, not 2.
        interval = min(MAX_INTERVAL_DAYS, int(interval + 0.5))
        due = now if interval == 0 else (now + timedelta(days=interval)).replace(hour=4, minute=0, second=0, microsecond=0)
        return {
            **card,
            "state": state,
            "state_label": STATE_LABELS_TR[state],
            "ease": round(ease, 2),
            "interval_days": int(interval),
            "reps": reps + 1,
            "lapses": lapses,
            "due_at": due.isoformat(),
            "last_grade": grade,
            "last_reviewed_at": now.isoformat(),
        }

    @classmethod
    def preview(cls, card: dict[str, Any], now: datetime) -> dict[str, str]:
        """What each grade would schedule, as the label under the button."""
        labels: dict[str, str] = {}
        for grade in GRADES:
            days = int(cls.apply(dict(card), grade, now)["interval_days"])
            labels[grade] = "bugün" if days == 0 else (f"{days} gün" if days < 31 else f"{round(days / 30.44, 1)} ay")
        return labels


class FlashcardDeck:
    def __init__(self, store: Any, anatomy: Any, terminology: Any, curriculum: Any, histology: Any, pipeline: Any, *, planner: Any = None, clock: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._anatomy = anatomy
        self._terminology = terminology
        self._curriculum = curriculum
        self._histology = histology
        self._pipeline = pipeline
        self._planner = planner
        self._clock = clock or utc_now

    # ------------------------------------------------------------------
    # storage
    # ------------------------------------------------------------------

    def get(self, card_id: str) -> dict[str, Any] | None:
        return self._store.get_record(CARD_KIND, card_id)

    def all(self, *, limit: int = 5000) -> list[dict[str, Any]]:
        return self._store.list_records(CARD_KIND, limit=limit, newest_first=False)

    def _save(self, card: dict[str, Any]) -> dict[str, Any]:
        card["source_label"] = SOURCE_LABELS_TR.get(card.get("source", ""), card.get("source", ""))
        card["state_label"] = STATE_LABELS_TR.get(card.get("state", "new"), card.get("state", ""))
        self._store.save_record(CARD_KIND, card["card_id"], card, subject_key=card.get("topic_id") or card.get("source"))
        return card

    def _create(self, card_id: str, *, source: str, front: str, back: str, provenance: str, topic_id: str | None = None, extra: dict[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
        existing = self.get(card_id)
        if existing is not None:
            return existing, False
        now = self._clock()
        card = {
            "card_id": card_id,
            "source": source,
            "front": front,
            "back": back,
            "provenance": provenance,
            "topic_id": topic_id,
            "state": "new",
            "ease": START_EASE,
            "interval_days": 0,
            "reps": 0,
            "lapses": 0,
            "due_at": now.isoformat(),
            "last_grade": None,
            "last_reviewed_at": None,
            "created_at": now.isoformat(),
            **(extra or {}),
        }
        return self._save(card), True

    def settings(self) -> dict[str, Any]:
        record = self._store.get_record(SETTINGS_KIND, "settings") or {}
        return {"new_per_day": max(0, min(100, int(record.get("new_per_day") or DEFAULT_NEW_PER_DAY)))}

    def update_settings(self, fields: dict[str, Any]) -> dict[str, Any]:
        current = self.settings()
        if "new_per_day" in fields:
            current["new_per_day"] = max(0, min(100, int(fields["new_per_day"])))
        self._store.save_record(SETTINGS_KIND, "settings", current)
        return current

    # ------------------------------------------------------------------
    # builders: every card from something the student already has
    # ------------------------------------------------------------------

    def build_topic(self, topic_id: str) -> dict[str, Any]:
        """Fact and terminology cards for one curriculum topic."""
        if not self._curriculum.exists(topic_id):
            raise ValueError("Konu bulunamadı.")
        added, existing = [], 0
        structures = [
            structure
            for structure in self._anatomy.all()
            if structure.topic_id
            and self._curriculum.is_within(structure.topic_id, topic_id)
            # Atlas registrations mirror curated lessons; cards from both
            # would ask the same fact twice under two names.
            and not structure.structure_id.startswith("za_")
        ]
        structure_ids = {structure.structure_id for structure in structures}
        for structure in structures:
            for field, question in FACT_CARD_FIELDS.get(structure.kind, ()):
                back = _back_text(structure.facts.get(field))
                if back is None:
                    continue
                card, created = self._create(
                    _stable_id("anat", structure.structure_id, field),
                    source="anatomy_fact",
                    front=f"{structure.canonical} — {question}?",
                    back=back,
                    provenance=f"Ders kartı: {structure.canonical} · {field}",
                    topic_id=structure.topic_id,
                    extra={"structure_id": structure.structure_id, "field": field},
                )
                if created:
                    added.append(card)
                else:
                    existing += 1
        for entry in self._terminology.entries():
            owner = entry.structure_id or entry.landmark_of
            if owner not in structure_ids:
                continue
            back = _back_text(entry.turkish + (f"\n{entry.note}" if entry.note else ""))
            if back is None or not entry.turkish:
                continue
            card, created = self._create(
                _stable_id("term", entry.term_id),
                source="terminology",
                front=f"{entry.canonical} — Türkçesi?",
                back=back,
                provenance=f"Terim sözlüğü: {entry.term_id}",
                topic_id=topic_id,
                extra={"term_id": entry.term_id},
            )
            if created:
                added.append(card)
            else:
                existing += 1
        return {"added": len(added), "existing": existing, "topic_id": topic_id, "topic_label": self._curriculum.breadcrumb(topic_id)}

    def add_wrong_questions(self, *, scoring: Callable[[Any], dict[str, Any]] | None = None) -> dict[str, Any]:
        """One card per question the student answered wrong in a scored paper.

        The back is the question's own key and explanation, exactly as the
        bank stores them; an unscored or invalidated question makes no card,
        because its key was never held to be worth learning.
        """
        added, existing, skipped_unscored = 0, 0, 0
        wrong_ids: list[str] = []
        for attempt in self._store.list_attempts(limit=2000):
            for question_id, entry in attempt.answers.items():
                if entry.answer_key and entry.correct is False and question_id not in wrong_ids:
                    wrong_ids.append(question_id)
        for question_id in wrong_ids:
            question = self._store.get_question(question_id)
            if question is None or not question.correct_key:
                continue
            if scoring is not None and not scoring(question).get("scored", False):
                skipped_unscored += 1
                continue
            correct = question.option(question.correct_key)
            back = f"{question.correct_key}) {correct.text if correct else ''}"
            explanation = _clean(question.explanation)
            if explanation:
                back += f"\n{explanation[:MAX_BACK_CHARS]}"
            options = "\n".join(f"{option.key}) {option.text}" for option in question.options)
            card, created = self._create(
                _stable_id("q", question_id),
                source="question",
                front=f"{question.stem}\n{options}",
                back=back,
                provenance=f"Soru bankası: {question_id} (yanlış cevaplanmıştı)",
                topic_id=question.topic_id,
                extra={"question_id": question_id},
            )
            added += 1 if created else 0
            existing += 0 if created else 1
        return {"added": added, "existing": existing, "skipped_unscored": skipped_unscored, "wrong_seen": len(wrong_ids)}

    def add_histology(self) -> dict[str, Any]:
        """A card per labelled specimen: the crop in front, the recorded name behind."""
        added, existing = 0, 0
        for specimen in self._histology.specimens():
            if not specimen.get("label") or specimen.get("status") == "unreadable":
                continue
            back = specimen["label"]
            if specimen.get("latin"):
                back += f"\n({specimen['latin']})"
            features = [item for item in (specimen.get("features") or []) if item][:MAX_BACK_ITEMS]
            if features:
                back += "\n" + "\n".join(f"• {item}" for item in features)
            card, created = self._create(
                _stable_id("histo", specimen["specimen_id"]),
                source="histology",
                front="Bu doku ya da yapı nedir?",
                back=back[:MAX_BACK_CHARS * 2],
                provenance=f"Histoloji örneği · {specimen.get('basis_label', '')}",
                topic_id=specimen.get("topic_id"),
                extra={"specimen_id": specimen["specimen_id"], "image": True},
            )
            added += 1 if created else 0
            existing += 0 if created else 1
        return {"added": added, "existing": existing}

    # ------------------------------------------------------------------
    # image occlusion from a lecture figure's own labels
    # ------------------------------------------------------------------

    def occlusion_candidates(self, document_id: str, page_number: int, region: dict[str, float] | None = None) -> dict[str, Any]:
        """The labels the page itself prints inside the region, with their boxes.

        Everything comes from the PDF's text layer; a scanned page has none
        and is refused with that reason instead of guessed labels.
        """
        document = self._store.get_document(document_id)
        if document is None:
            raise ValueError("Belge bulunamadı.")
        rect = self._region(region)
        text = self._pipeline.text_in_region(document_id, page_number, (rect["x"], rect["y"], rect["w"], rect["h"]))
        if text is None:
            return {"candidates": [], "reason": "Bu belge türünden bölge metni okunamıyor (yalnız PDF)."}
        phrases: list[str] = []
        for line in text.replace("\r", "\n").split("\n"):
            phrase = _clean(line)
            # A label is a short printed word or phrase; a sentence is prose.
            if 3 <= len(phrase) <= 40 and not phrase.endswith((".", ":", ";")) and phrase not in phrases:
                phrases.append(phrase)
        candidates: list[dict[str, Any]] = []
        for phrase in phrases[:60]:
            boxes = [
                box
                for box in self._pipeline.text_boxes(document_id, page_number, phrase)
                if rect["x"] - 0.001 <= box[0] and box[0] + box[2] <= rect["x"] + rect["w"] + 0.001
                and rect["y"] - 0.001 <= box[1] and box[1] + box[3] <= rect["y"] + rect["h"] + 0.001
            ]
            if 1 <= len(boxes) <= 4:
                candidates.append({"label": phrase, "boxes": [[round(v, 4) for v in box] for box in boxes]})
            if len(candidates) >= 40:
                break
        reason = "" if candidates else "Bu bölgede metin katmanında etiket bulunamadı; taranmış bir şekil olabilir. Etiketleri kendin yazarak histoloji örneği gibi kaydedebilirsin."
        return {"candidates": candidates, "reason": reason, "region": rect}

    def add_occlusion(self, document_id: str, page_number: int, region: dict[str, float] | None, labels: list[str]) -> dict[str, Any]:
        """One card per chosen label: the figure with every print of that label
        covered in front, the label text behind. Refused when the page does
        not print the label — a mask nobody can check is a guess."""
        found = self.occlusion_candidates(document_id, page_number, region)
        by_label = {item["label"]: item for item in found["candidates"]}
        document = self._store.get_document(document_id)
        added, existing, missing = 0, 0, []
        for label in labels:
            wanted = _clean(label)
            candidate = by_label.get(wanted)
            if candidate is None:
                missing.append(wanted)
                continue
            card, created = self._create(
                _stable_id("occ", document_id, str(page_number), wanted),
                source="occlusion",
                front=f"Kapatılan etiket nedir? ({len(candidate['boxes'])} yerde)",
                back=wanted,
                provenance=f"{document.title} · s. {page_number} (sayfanın kendi etiketi)",
                topic_id=next((item for item in document.topic_ids if self._curriculum.exists(item)), None),
                extra={"document_id": document_id, "page_number": int(page_number), "region": found["region"], "boxes": candidate["boxes"], "image": True},
            )
            added += 1 if created else 0
            existing += 0 if created else 1
        return {"added": added, "existing": existing, "missing": missing}

    @staticmethod
    def _region(region: dict[str, float] | None) -> dict[str, float]:
        if region is None:
            return {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        try:
            rect = {key: float(region[key]) for key in ("x", "y", "w", "h")}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Bölge x, y, w, h (sayfa oranı) olarak verilmeli.") from exc
        if rect["w"] <= 0 or rect["h"] <= 0 or rect["x"] < 0 or rect["y"] < 0 or rect["x"] + rect["w"] > 1.0001 or rect["y"] + rect["h"] > 1.0001:
            raise ValueError("Bölge sayfanın içinde olmalı.")
        return {key: round(value, 4) for key, value in rect.items()}

    # ------------------------------------------------------------------
    # the front image, when a card has one
    # ------------------------------------------------------------------

    def front_image(self, card_id: str) -> bytes:
        card = self.get(card_id)
        if card is None:
            raise ValueError("Kart bulunamadı.")
        if card["source"] == "histology":
            # The masked rendering: a name printed on the picture stays covered.
            return self._histology.crop(card["specimen_id"], masked=True)
        if card["source"] == "occlusion":
            key = f"card:{card_id}"
            cached = self._store.get_media(key)
            if cached is not None:
                return cached
            region = card["region"]
            masks = []
            pad = 0.004
            for x, y, w, h in card["boxes"]:
                masks.append((
                    max(0.0, (x - region["x"]) / region["w"] - pad),
                    max(0.0, (y - region["y"]) / region["h"] - pad),
                    min(1.0, w / region["w"] + 2 * pad),
                    min(1.0, h / region["h"] + 2 * pad),
                ))
            png = self._pipeline.render_region(card["document_id"], card["page_number"], (region["x"], region["y"], region["w"], region["h"]), masks=masks)
            self._store.put_media(key, "flashcard_front", png)
            return png
        raise ValueError("Bu kartın görseli yok.")

    # ------------------------------------------------------------------
    # the day's queue and one answer
    # ------------------------------------------------------------------

    def _reviews_today(self, now: datetime) -> list[dict[str, Any]]:
        today = now.date().isoformat()
        return [item for item in self._store.list_records(REVIEW_KIND, limit=500) if str(item.get("at", "")).startswith(today)]

    def queue(self, *, limit: int = QUEUE_LIMIT) -> dict[str, Any]:
        now = self._clock()
        cards = [card for card in self.all() if card.get("state") != "suspended"]
        due = sorted(
            (card for card in cards if card.get("state") != "new" and str(card.get("due_at", "")) <= now.isoformat()),
            key=lambda card: str(card.get("due_at", "")),
        )
        new_seen_today = sum(1 for item in self._reviews_today(now) if item.get("was_new"))
        new_budget = max(0, self.settings()["new_per_day"] - new_seen_today)
        fresh = [card for card in cards if card.get("state") == "new"][:new_budget]
        chosen = (due + fresh)[: max(1, int(limit))]
        return {
            "cards": [self.payload(card, previews=True) for card in chosen],
            "due": len(due),
            "new_available": len([card for card in cards if card.get("state") == "new"]),
            "new_budget": new_budget,
            "total": len(cards),
            "suspended": sum(1 for card in self.all() if card.get("state") == "suspended"),
            "reviewed_today": len(self._reviews_today(now)),
            "settings": self.settings(),
        }

    def answer(self, card_id: str, grade: str, *, submission_id: str | None = None) -> dict[str, Any]:
        card = self.get(card_id)
        if card is None:
            raise ValueError("Kart bulunamadı.")
        key = _clean(submission_id)
        if key:
            for review in self._store.list_records(REVIEW_KIND, subject_key=card_id, limit=50):
                if review.get("submission_id") == key:
                    return {"card": self.payload(self.get(card_id) or card), "review": review, "repeated": True}
        now = self._clock()
        was_new = card.get("state") == "new"
        updated = self._save(Scheduler.apply(card, grade, now))
        review = {
            "review_id": new_id("fcr"),
            "card_id": card_id,
            "grade": grade,
            "grade_label": GRADE_LABELS_TR[grade],
            "was_new": was_new,
            "interval_days": updated["interval_days"],
            "at": now.isoformat(),
            "submission_id": key or None,
        }
        self._store.save_record(REVIEW_KIND, review["review_id"], review, subject_key=card_id)
        if self._planner is not None and len(self._reviews_today(now)) % 20 == 0:
            # Twenty answers is a study block worth a line in the plan's log;
            # per-card logging would drown it.
            try:
                self._planner.log_study(topic_id=card.get("topic_id"), activity="review", minutes=5)
            except Exception:
                pass
        return {"card": self.payload(updated, previews=False), "review": review, "repeated": False}

    def suspend(self, card_id: str, suspended: bool = True) -> dict[str, Any]:
        card = self.get(card_id)
        if card is None:
            raise ValueError("Kart bulunamadı.")
        if suspended:
            card["state_before_suspend"] = card.get("state", "new")
            card["state"] = "suspended"
        elif card.get("state") == "suspended":
            card["state"] = card.pop("state_before_suspend", "review" if card.get("reps") else "new")
        return {"card": self.payload(self._save(card))}

    def delete(self, card_id: str) -> bool:
        delete_media = getattr(self._store, "delete_media", None)
        if callable(delete_media):
            try:
                delete_media(f"card:{card_id}")
            except Exception:
                pass
        return self._store.delete_record(CARD_KIND, card_id)

    def forecast(self, *, days: int = 7) -> list[dict[str, Any]]:
        """Due counts per day: today includes the backlog, later days only their own."""
        now = self._clock()
        cards = [card for card in self.all() if card.get("state") in ("learning", "review")]
        counts: list[dict[str, Any]] = []
        for offset in range(max(1, int(days))):
            day = (now + timedelta(days=offset)).date()
            start = datetime.combine(day, datetime.min.time(), tzinfo=now.tzinfo).isoformat()
            end = datetime.combine(day, datetime.max.time(), tzinfo=now.tzinfo).isoformat()
            if offset == 0:
                due = sum(1 for card in cards if str(card.get("due_at", "")) <= end)
            else:
                due = sum(1 for card in cards if start <= str(card.get("due_at", "")) <= end)
            counts.append({"date": day.isoformat(), "due": due})
        return counts

    def payload(self, card: dict[str, Any], *, previews: bool = False) -> dict[str, Any]:
        body = {key: card.get(key) for key in ("card_id", "source", "source_label", "front", "back", "provenance", "topic_id", "state", "state_label", "ease", "interval_days", "reps", "lapses", "due_at", "last_grade", "created_at")}
        body["has_image"] = bool(card.get("image"))
        body["topic_label"] = self._curriculum.breadcrumb(card["topic_id"]) if card.get("topic_id") and self._curriculum.exists(card["topic_id"]) else ""
        if previews:
            body["previews"] = Scheduler.preview(card, self._clock())
            body["preview_labels"] = GRADE_LABELS_TR
        return body

    def overview(self) -> dict[str, Any]:
        cards = self.all()
        by_source: dict[str, int] = {}
        for card in cards:
            by_source[card.get("source", "")] = by_source.get(card.get("source", ""), 0) + 1
        queue = self.queue(limit=1)
        return {
            "total": len(cards),
            "by_source": [{"source": source, "label": SOURCE_LABELS_TR.get(source, source), "count": count} for source, count in sorted(by_source.items())],
            "due": queue["due"],
            "new_available": queue["new_available"],
            "new_budget": queue["new_budget"],
            "reviewed_today": queue["reviewed_today"],
            "suspended": queue["suspended"],
            "forecast": self.forecast(),
            "settings": self.settings(),
            "empty_state": "" if cards else "Henüz kart yok. Bir konudan kart üret, yanlışlarını ekle ya da bir ders şeklinin etiketlerini kapat.",
        }


__all__ = ["CARD_KIND", "DEFAULT_NEW_PER_DAY", "FACT_CARD_FIELDS", "FlashcardDeck", "GRADES", "GRADE_LABELS_TR", "REVIEW_KIND", "Scheduler", "SOURCE_LABELS_TR"]
