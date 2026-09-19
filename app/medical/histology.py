"""Histology practicals: identify a specimen from the student's own material.

A *specimen* is a rectangle on a page of an imported document — the figure
the lecture printed — kept with its link to that page. The crop is a fresh
rendering of the rectangle; the page image is never touched. A specimen's
answer (the tissue or structure) and its supporting features have a
recorded *basis*: the student confirmed them, or they come from a caption on
the page. A description the vision model wrote is kept as a description and
never becomes the answer on its own. Stain, magnification and region labels
stay unknown until someone records them; nothing here is invented, no
microscopy image is ever generated.

Only a specimen whose answer has an adequate basis is *eligible* for a scored
(timed) practical. The rest can still be studied, marked as what they are.
A session shows each specimen once, hides the answer until it is given, and
records the identification and the explanation as two separate outcomes.
Specimen identity is tracked so that seeing the same image again is not
mistaken for generalisation. Results feed mastery (for eligible specimens)
and the understanding workflow.
"""

from __future__ import annotations

import base64
import re
import unicodedata
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.core.time import utc_now
from app.medical.model import MedicalModelError
from app.medical.models import Question, new_id
from app.medical.prompts import PIPELINE_SYSTEM, histology_explanation_prompt
from app.medical.schemas import HISTOLOGY_EXPLANATION_SCHEMA

SPECIMEN_KIND = "histology_specimen"
SESSION_KIND = "histology_session"

BASIS_LABELS_TR: dict[str, str] = {
    "user_confirmed": "Öğrenci onayladı",
    "page_caption": "Sayfadaki başlık ya da etiket",
    "model_description": "Yalnız model betimlemesi (onay bekliyor)",
    "none": "Dayanak kaydedilmedi",
}
STATUS_LABELS_TR: dict[str, str] = {"eligible": "Puanlı sınava uygun", "study_only": "Yalnız çalışma", "unreadable": "Okunamıyor"}
STATUS_REASONS_TR: dict[str, str] = {
    "unreadable": "Okunamıyor olarak işaretlendi.",
    "no_label": "Adı kaydedilmedi.",
    "weak_basis": "Adın dayanağı yetersiz: öğrenci onayı ya da sayfadaki başlık gerekli.",
    "answer_visible": "Cevap görselin üzerinde yazıyor: kör sınava girmez. Yazıyı maskele ya da bölgeyi daralt.",
    "eligible": "",
}
# The grace a late answer gets after a timed item's clock runs out: the page
# sends the answer it had at the bell, and a slow bridge is not a slow student.
TIMED_GRACE_SECONDS = 5
QUALITY_LABELS_TR: dict[str, str] = {"specific": "Özgül", "partial": "Kısmen", "generic": "Genel", "wrong": "Yanlış", "unassessed": "Değerlendirilmedi"}
ELIGIBLE_BASES = frozenset({"user_confirmed", "page_caption"})
MAX_SPECIMENS_PER_SESSION = 10
DEFAULT_TIMED_SECONDS = 60
CROP_SCALE = 2.0
EMPTY_STATE_TR = "Henüz histoloji örneği yok. Kütüphane'de bir ders sayfasını aç, şekli çerçeve içine al ve adını kaydet; adı sen onaylayınca örnek puanlı sınava girebilir."


def _fold(text: str) -> str:
    lowered = str(text or "").replace("İ", "i").replace("I", "ı").lower()
    decomposed = unicodedata.normalize("NFKD", lowered)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9 ]+", " ", stripped.replace("ı", "i")).strip()


def identification_matches(answer: str, names: list[str]) -> bool:
    """A lenient examiner: case, diacritics and punctuation are ignored; a
    token of five letters or more may stand for the start of the word it
    abbreviates; naming a different structure is wrong."""
    given = _fold(answer)
    if not given:
        return False
    given_tokens = given.split()
    for name in names:
        wanted = _fold(name)
        if not wanted:
            continue
        if given == wanted:
            return True
        wanted_tokens = wanted.split()
        if len(given_tokens) == len(wanted_tokens) and all(
            token == target or (len(token) >= 5 and target.startswith(token)) for token, target in zip(given_tokens, wanted_tokens)
        ):
            return True
    return False


class HistologyBank:
    def __init__(self, store: Any, pipeline: Any, learning: Any, understanding: Any | None, concepts: Any, model: Any | None = None, *, clock: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._pipeline = pipeline
        self._learning = learning
        self._understanding = understanding
        self._concepts = concepts
        self._model = model
        self._clock = clock or utc_now

    # ------------------------------------------------------------------
    # specimens
    # ------------------------------------------------------------------

    @staticmethod
    def _region(value: Any) -> dict[str, float]:
        try:
            region = {key: float(value[key]) for key in ("x", "y", "w", "h")}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Bölge x, y, w, h (sayfa oranı) olarak verilmeli.") from exc
        if region["w"] <= 0 or region["h"] <= 0 or region["x"] < 0 or region["y"] < 0 or region["x"] + region["w"] > 1.0001 or region["y"] + region["h"] > 1.0001:
            raise ValueError("Bölge sayfanın içinde olmalı.")
        return {key: round(number, 4) for key, number in region.items()}

    def _status(self, specimen: dict[str, Any]) -> str:
        return self._status_and_reason(specimen)[0]

    @staticmethod
    def _status_and_reason(specimen: dict[str, Any]) -> tuple[str, str]:
        if specimen.get("unreadable"):
            return "unreadable", "unreadable"
        if not specimen.get("label"):
            return "study_only", "no_label"
        if specimen.get("basis") not in ELIGIBLE_BASES:
            return "study_only", "weak_basis"
        # A name printed inside the crop is on the picture: a blind test of
        # it would test reading. A mask over that text puts it back.
        if specimen.get("answer_visible") and not specimen.get("masks"):
            return "study_only", "answer_visible"
        return "eligible", "eligible"

    def _concept_for(self, label: str, latin: str = "") -> str:
        """The concept a specimen's answer is learning evidence for.

        Only an exact name — the concept's own name or one of its recorded
        aliases, folded — links the two. "Tek katlı kübik epitel" shares three
        of four words with "Tek katlı yassı epitel"; a nearest-match lookup
        would file the cuboid answer under the squamous concept, so nearness
        is no link at all. An unmatched answer gets its own stable id.
        """
        names = [_fold(name) for name in (label, latin) if name and _fold(name)]
        if self._concepts is not None and names:
            for concept in self._concepts.by_subject("histology"):
                known = {_fold(concept.name)} | {_fold(alias) for alias in concept.aliases}
                known.discard("")
                if any(name in known for name in names):
                    return concept.concept_id
        slug = re.sub(r"[^a-z0-9]+", "_", _fold(label)).strip("_") or "unknown"
        return f"histology.specimen.{slug}"

    def _answer_visible(self, specimen: dict[str, Any]) -> bool | None:
        """Is the specimen's own name printed inside its rectangle? None when the page cannot say."""
        names = [name for name in (specimen.get("label"), specimen.get("latin")) if name]
        if not names:
            return False
        region = specimen.get("region") or {}
        try:
            text = self._pipeline.text_in_region(specimen["document_id"], int(specimen["page_number"]), (region["x"], region["y"], region["w"], region["h"]))
        except Exception:
            text = None
        if text is None:
            return None
        folded = " " + " ".join(_fold(text).split()) + " "
        return any(f" {_fold(name)} " in folded for name in names if _fold(name))

    def _refresh_visibility(self, specimen: dict[str, Any]) -> None:
        specimen["answer_visible"] = self._answer_visible(specimen)

    def hide_printed_answer(self, specimen_id: str) -> dict[str, Any]:
        """Mask the specimen's own name where it is printed inside the crop.

        The rectangles come from the page's text layer, never from guessing;
        when the name is printed nowhere the page can locate, nothing is
        masked and the specimen stays study-only until the region is redrawn.
        """
        specimen = self._require(specimen_id)
        region = specimen.get("region") or {}
        added = 0
        # The page may print the name with or without its diacritics, or split
        # over a line; the whole name is tried first, then its longer words.
        needles: list[str] = []
        for name in (specimen.get("label"), specimen.get("latin")):
            if not name:
                continue
            for form in (name, _fold(name)):
                if form and form not in needles:
                    needles.append(form)
        words = [word for needle in list(needles) for word in needle.split() if len(word) >= 4]
        for x, y, w, h in self._search_boxes(specimen, needles) or self._search_boxes(specimen, words):
            if True:
                # Page fractions → crop fractions, kept only where the two overlap.
                left, top = max(x, region["x"]), max(y, region["y"])
                right, bottom = min(x + w, region["x"] + region["w"]), min(y + h, region["y"] + region["h"])
                if right <= left or bottom <= top:
                    continue
                pad = 0.01
                points = [[max(0.0, (left - region["x"]) / region["w"] - pad), max(0.0, (top - region["y"]) / region["h"] - pad)], [min(1.0, (right - region["x"]) / region["w"] + pad), min(1.0, (bottom - region["y"]) / region["h"] + pad)]]
                self.add_mask(specimen_id, kind="rect", points=points, label="ad gizlendi")
                added += 1
        specimen = self._require(specimen_id)
        specimen["hidden_answer_masks"] = added
        self._forget_crop(specimen_id)
        return self._save(specimen, f"Görseldeki ad {added} maskeyle gizlendi." if added else "Görselde gizlenecek ad bulunamadı; bölgeyi daralt.")

    def _search_boxes(self, specimen: dict[str, Any], needles: list[str]) -> list[tuple[float, float, float, float]]:
        boxes: list[tuple[float, float, float, float]] = []
        seen: set[tuple[float, float, float, float]] = set()
        for needle in needles:
            for box in self._pipeline.text_boxes(specimen["document_id"], int(specimen["page_number"]), needle):
                rounded = tuple(round(value, 4) for value in box)
                if rounded not in seen:
                    seen.add(rounded)
                    boxes.append(box)
        return boxes

    def add_specimen(
        self,
        document_id: str,
        page_number: int,
        region: Any,
        *,
        label: str = "",
        latin: str = "",
        stain: str | None = None,
        magnification: str | None = None,
        features: list[str] | None = None,
        basis: str = "none",
        notes: str = "",
        confusable_with: list[str] | None = None,
    ) -> dict[str, Any]:
        document = self._store.get_document(document_id)
        if document is None:
            raise ValueError("Belge bulunamadı.")
        page = self._store.get_page(document_id, int(page_number))
        if page is None:
            raise ValueError("Sayfa bulunamadı.")
        rect = self._region(region)
        if basis not in BASIS_LABELS_TR:
            raise ValueError("Dayanak türü bilinmiyor.")
        name = " ".join(str(label or "").split())[:120]
        if name and basis == "none":
            raise ValueError("Bir ad kaydediliyorsa dayanağı da kaydedilmeli (öğrenci onayı ya da sayfadaki başlık).")
        now = self._clock().isoformat()
        specimen = {
            "specimen_id": new_id("hs"),
            "document_id": document_id,
            "document_title": document.title,
            "page_number": int(page_number),
            "region": rect,
            "source_hash": document.sha256,
            "label": name,
            "latin": " ".join(str(latin or "").split())[:120],
            "stain": (" ".join(str(stain).split())[:60] if stain else None),
            "magnification": (" ".join(str(magnification).split())[:40] if magnification else None),
            "features": [" ".join(str(item).split())[:200] for item in (features or []) if str(item).strip()][:12],
            "basis": basis if name else "none",
            "basis_label": BASIS_LABELS_TR[basis if name else "none"],
            "caption_excerpt": " ".join(page.text.split())[:300],
            "model_description": " ".join(page.visual_summary.split())[:600],
            "model_labels": list(page.visual_labels)[:20],
            "notes": " ".join(str(notes or "").split())[:400],
            "confusable_with": [item for item in (confusable_with or []) if item],
            "masks": [],
            "unreadable": False,
            "topic_id": next((item for item in document.topic_ids if item.startswith("histology")), None) or "histology",
            "exposures": 0,
            "last_shown_at": None,
            "created_at": now,
            "history": [{"at": now, "note": "Örnek eklendi."}],
        }
        self._refresh_visibility(specimen)
        specimen["concept_id"] = self._concept_for(specimen["label"], specimen["latin"]) if specimen["label"] else None
        specimen["status"], reason = self._status_and_reason(specimen)
        specimen["status_label"] = STATUS_LABELS_TR[specimen["status"]]
        specimen["status_reason"] = STATUS_REASONS_TR.get(reason, "")
        self._store.save_record(SPECIMEN_KIND, specimen["specimen_id"], specimen, subject_key=document_id)
        return specimen

    def specimen(self, specimen_id: str) -> dict[str, Any] | None:
        return self._store.get_record(SPECIMEN_KIND, specimen_id)

    def specimens(self, *, document_id: str | None = None, eligible_only: bool = False) -> list[dict[str, Any]]:
        # Oldest first: the order the student added them in is the order they expect.
        items = self._store.list_records(SPECIMEN_KIND, subject_key=document_id, limit=1000, newest_first=False)
        # A specimen saved before the page was read for a printed answer is
        # read now, once, so an old specimen cannot slip into a blind test
        # with its name on the picture.
        for index, item in enumerate(items):
            if "answer_visible" not in item and item.get("label"):
                self._refresh_visibility(item)
                items[index] = self._save(item, "Görselde ad denetimi yapıldı.")
        if eligible_only:
            items = [item for item in items if item.get("status") == "eligible"]
        return items

    def _save(self, specimen: dict[str, Any], note: str = "") -> dict[str, Any]:
        specimen["concept_id"] = self._concept_for(specimen.get("label", ""), specimen.get("latin", "")) if specimen.get("label") else None
        specimen["status"], reason = self._status_and_reason(specimen)
        specimen["status_label"] = STATUS_LABELS_TR[specimen["status"]]
        specimen["status_reason"] = STATUS_REASONS_TR.get(reason, "")
        specimen["basis_label"] = BASIS_LABELS_TR.get(specimen.get("basis", "none"), specimen.get("basis", ""))
        if note:
            specimen.setdefault("history", []).append({"at": self._clock().isoformat(), "note": note})
        self._store.save_record(SPECIMEN_KIND, specimen["specimen_id"], specimen, subject_key=specimen["document_id"])
        return specimen

    def _require(self, specimen_id: str) -> dict[str, Any]:
        specimen = self.specimen(specimen_id)
        if specimen is None:
            raise ValueError("Örnek bulunamadı.")
        return specimen

    def confirm_label(self, specimen_id: str, label: str, *, latin: str = "", features: list[str] | None = None, stain: str | None = None, magnification: str | None = None) -> dict[str, Any]:
        """The student's word on the answer: this is what makes a specimen eligible."""
        specimen = self._require(specimen_id)
        name = " ".join(str(label or "").split())[:120]
        if not name:
            raise ValueError("Ad boş olamaz.")
        specimen["label"] = name
        if latin:
            specimen["latin"] = " ".join(str(latin).split())[:120]
        if features is not None:
            specimen["features"] = [" ".join(str(item).split())[:200] for item in features if str(item).strip()][:12]
        if stain is not None:
            specimen["stain"] = " ".join(str(stain).split())[:60] or None
        if magnification is not None:
            specimen["magnification"] = " ".join(str(magnification).split())[:40] or None
        specimen["basis"] = "user_confirmed"
        self._refresh_visibility(specimen)
        return self._save(specimen, f"Ad onaylandı: {name}.")

    def update_specimen(self, specimen_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        specimen = self._require(specimen_id)
        notes: list[str] = []
        if "basis" in fields:
            basis = str(fields["basis"])
            if basis not in BASIS_LABELS_TR:
                raise ValueError("Dayanak türü bilinmiyor.")
            specimen["basis"] = basis
            notes.append(f"dayanak: {BASIS_LABELS_TR[basis]}")
        for key, limit in (("label", 120), ("latin", 120), ("notes", 400)):
            if key in fields:
                specimen[key] = " ".join(str(fields[key] or "").split())[:limit]
                notes.append(key)
        for key in ("stain", "magnification"):
            if key in fields:
                value = " ".join(str(fields[key] or "").split())
                specimen[key] = value[:60] or None
                notes.append(key)
        if "features" in fields:
            specimen["features"] = [" ".join(str(item).split())[:200] for item in (fields["features"] or []) if str(item).strip()][:12]
            notes.append("özellikler")
        if "confusable_with" in fields:
            specimen["confusable_with"] = [item for item in (fields["confusable_with"] or []) if item and item != specimen_id]
            notes.append("karıştırılanlar")
        if "unreadable" in fields:
            specimen["unreadable"] = bool(fields["unreadable"])
            notes.append("okunamıyor" if specimen["unreadable"] else "okunuyor")
        if specimen.get("label") and specimen.get("basis") == "none":
            raise ValueError("Adı olan bir örneğin dayanağı 'none' olamaz.")
        if "label" in fields or "latin" in fields:
            self._refresh_visibility(specimen)
        return self._save(specimen, "Güncellendi: " + ", ".join(notes) + "." if notes else "")

    def delete_specimen(self, specimen_id: str) -> bool:
        for scale in (CROP_SCALE,):
            self._store.delete_media(f"crop:{specimen_id}:{scale}")
        return self._store.delete_record(SPECIMEN_KIND, specimen_id)

    # ------------------------------------------------------------------
    # the image: a crop, and masks kept apart from it
    # ------------------------------------------------------------------

    def crop(self, specimen_id: str, *, scale: float = CROP_SCALE, masked: bool = False) -> bytes:
        """The specimen's image: the plain crop, or the crop with its masks painted on.

        The plain crop is the page as printed and is never altered; the masked
        one is what a practical shows when the answer is printed on the
        picture. Both are renderings of the page, cached apart.
        """
        specimen = self._require(specimen_id)
        key = f"crop:{specimen_id}:{scale}" + (":masked" if masked else "")
        cached = self._store.get_media(key)
        if cached is not None:
            return cached
        region = specimen["region"]
        rects: list[tuple[float, float, float, float]] = []
        for mask in specimen.get("masks", []) if masked else []:
            points = mask.get("points") or []
            if mask.get("kind") == "rect" and len(points) == 2:
                (x0, y0), (x1, y1) = points
                rects.append((min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))
            elif points:
                xs, ys = [p[0] for p in points], [p[1] for p in points]
                rects.append((min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))
        png = self._pipeline.render_region(specimen["document_id"], specimen["page_number"], (region["x"], region["y"], region["w"], region["h"]), scale=scale, masks=rects)
        self._store.put_media(key, "histology_crop", png)
        return png

    def _forget_crop(self, specimen_id: str) -> None:
        """A mask changed: the masked rendering is stale, the plain one is not."""
        delete = getattr(self._store, "delete_media", None)
        if callable(delete):
            try:
                delete(f"crop:{specimen_id}:{CROP_SCALE}:masked")
            except Exception:
                pass

    def crop_data_url(self, specimen_id: str, *, masked: bool = False) -> str:
        return "data:image/png;base64," + base64.b64encode(self.crop(specimen_id, masked=masked)).decode("ascii")

    def add_mask(self, specimen_id: str, *, kind: str = "rect", points: list[list[float]] | None = None, label: str = "") -> dict[str, Any]:
        """An annotation over the crop, stored beside it; the crop and the page stay untouched."""
        specimen = self._require(specimen_id)
        if kind not in ("rect", "polygon"):
            raise ValueError("Maske türü 'rect' ya da 'polygon' olmalı.")
        coordinates = [[round(float(x), 4), round(float(y), 4)] for x, y in (points or [])]
        if kind == "rect" and len(coordinates) != 2:
            raise ValueError("Dikdörtgen maske iki köşe ister.")
        if kind == "polygon" and len(coordinates) < 3:
            raise ValueError("Çokgen maske en az üç nokta ister.")
        if any(not (0 <= x <= 1 and 0 <= y <= 1) for x, y in coordinates):
            raise ValueError("Maske noktaları kırpma alanının içinde (0-1) olmalı.")
        mask = {"mask_id": new_id("mask"), "kind": kind, "points": coordinates, "label": " ".join(str(label or "").split())[:80], "created_at": self._clock().isoformat()}
        specimen.setdefault("masks", []).append(mask)
        self._forget_crop(specimen_id)
        self._save(specimen, "Maske eklendi.")
        return mask

    def remove_mask(self, specimen_id: str, mask_id: str) -> dict[str, Any]:
        specimen = self._require(specimen_id)
        specimen["masks"] = [item for item in specimen.get("masks", []) if item.get("mask_id") != mask_id]
        self._forget_crop(specimen_id)
        return self._save(specimen, "Maske kaldırıldı.")

    def source(self, specimen_id: str) -> dict[str, Any]:
        specimen = self._require(specimen_id)
        document = self._store.get_document(specimen["document_id"])
        return {
            "document_id": specimen["document_id"],
            "title": document.title if document is not None else specimen.get("document_title", ""),
            "page_number": specimen["page_number"],
            "region": specimen["region"],
            "available": document is not None,
            "changed": document is not None and document.sha256 != specimen.get("source_hash"),
        }

    def compare(self, specimen_id: str) -> dict[str, Any]:
        """Specimens this one is commonly confused with, with only their recorded features."""
        specimen = self._require(specimen_id)
        others = [item for item in self.specimens() if item["specimen_id"] != specimen_id]
        explicit = [item for item in others if item["specimen_id"] in specimen.get("confusable_with", []) or specimen_id in item.get("confusable_with", [])]
        same_topic = [item for item in others if item not in explicit and item.get("topic_id") == specimen.get("topic_id") and item.get("label") and item.get("label") != specimen.get("label")]
        return {
            "specimen": self.payload(specimen, reveal=True),
            "confusable": [self.payload(item, reveal=True) for item in explicit[:6]],
            "same_topic": [self.payload(item, reveal=True) for item in same_topic[:6]],
            "note": "Karşılaştırma yalnız kaydedilmiş özelliklere dayanır; eksik alanlar bilinmiyor demektir." if explicit or same_topic else "Karşılaştırılacak başka örnek yok; bir örnek daha ekleyince ayrım burada görünür.",
        }

    def payload(self, specimen: dict[str, Any], *, reveal: bool) -> dict[str, Any]:
        """What the page may show: during a test the answer, the features and the labels stay hidden.

        Hidden means hidden everywhere the page could print it: the name, the
        Latin, the features, the page caption, the model's description, and
        the document title and page number too (a lecture called "Epitel
        Doku" names the tissue family). Only the crop and the stain remain.
        """
        shared = {key: specimen.get(key) for key in ("specimen_id", "document_id", "page_number", "region", "status", "status_label", "status_reason", "basis", "basis_label", "exposures", "topic_id", "created_at", "masks", "answer_visible", "concept_id")}
        shared["source_changed"] = self.source(specimen["specimen_id"])["changed"]
        if not reveal:
            hidden = {**shared, "document_title": "", "page_number": None, "document_id": None, "concept_id": None, "masked": True, "stain": specimen.get("stain") if specimen.get("basis") == "user_confirmed" else None}
            return hidden
        return {**shared, "document_title": specimen.get("document_title"), "masked": False, **{key: specimen.get(key) for key in ("label", "latin", "stain", "magnification", "features", "notes", "caption_excerpt", "model_description", "model_labels", "confusable_with", "history")}}

    def concept_label(self, concept_id: str) -> str:
        """The name of the specimen a ``histology.specimen.*`` concept stands for, or ""."""
        if not concept_id.startswith("histology.specimen."):
            return ""
        for specimen in self.specimens():
            if specimen.get("concept_id") == concept_id and specimen.get("label"):
                return str(specimen["label"])
        return ""

    def under_test(self) -> set[str]:
        """Specimens an open timed session is still asking about: their answers are off every screen."""
        hidden: set[str] = set()
        for session in self._store.list_records(SESSION_KIND, subject_key="timed", limit=50):
            if session.get("status") != "open":
                continue
            for item in session.get("items", []):
                if item.get("answer") is None:
                    hidden.add(str(item.get("specimen_id")))
        return hidden

    # ------------------------------------------------------------------
    # sessions
    # ------------------------------------------------------------------

    def start_session(self, *, mode: str = "study", seconds: int = DEFAULT_TIMED_SECONDS, count: int = MAX_SPECIMENS_PER_SESSION, document_id: str | None = None, specimen_ids: list[str] | None = None) -> dict[str, Any] | None:
        if mode not in ("study", "timed"):
            raise ValueError("Oturum türü 'study' ya da 'timed' olmalı.")
        pool = self.specimens(document_id=document_id)
        if specimen_ids:
            wanted = set(specimen_ids)
            pool = [item for item in pool if item["specimen_id"] in wanted]
        if mode == "timed":
            pool = [item for item in pool if item.get("status") == "eligible"]
        else:
            pool = [item for item in pool if item.get("status") != "unreadable"]
        if not pool:
            return None
        # Least-seen first, and never the same image twice in one session.
        pool.sort(key=lambda item: (int(item.get("exposures", 0)), item.get("created_at", "")))
        chosen = pool[: max(1, min(int(count), MAX_SPECIMENS_PER_SESSION))]
        session = {
            "session_id": new_id("hsx"),
            "mode": mode,
            "seconds": max(15, min(int(seconds), 300)) if mode == "timed" else 0,
            "status": "open",
            "started_at": self._clock().isoformat(),
            "items": [{"index": index, "specimen_id": item["specimen_id"], "scored": item.get("status") == "eligible", "shown_at": None, "answer": None} for index, item in enumerate(chosen)],
            "results": None,
        }
        self._store.save_record(SESSION_KIND, session["session_id"], session, subject_key=mode)
        return session

    def session(self, session_id: str) -> dict[str, Any] | None:
        return self._store.get_record(SESSION_KIND, session_id)

    def session_payload(self, session: dict[str, Any]) -> dict[str, Any]:
        items = []
        for item in session["items"]:
            specimen = self.specimen(item["specimen_id"])
            if specimen is None:
                continue
            items.append({**item, "specimen": self.payload(specimen, reveal=item.get("answer") is not None)})
        return {**session, "items": items}

    def show(self, session_id: str, index: int) -> dict[str, Any]:
        """Mark a specimen shown; its exposure count is what keeps repeats honest."""
        session = self.session(session_id)
        if session is None:
            raise ValueError("Oturum bulunamadı.")
        item = next((entry for entry in session["items"] if entry["index"] == int(index)), None)
        if item is None:
            raise ValueError("Bu sırada örnek yok.")
        if item["shown_at"] is None:
            item["shown_at"] = self._clock().isoformat()
            specimen = self.specimen(item["specimen_id"])
            if specimen is not None:
                specimen["exposures"] = int(specimen.get("exposures", 0)) + 1
                specimen["last_shown_at"] = item["shown_at"]
                self._save(specimen)
            self._store.save_record(SESSION_KIND, session_id, session, subject_key=session["mode"])
        return self.session_payload(session)

    async def answer(self, session_id: str, specimen_id: str, text: str, *, confidence: str | None = None, explanation: str = "", submission_id: str | None = None, timed_out: bool = False) -> dict[str, Any]:
        session = self.session(session_id)
        if session is None:
            raise ValueError("Oturum bulunamadı.")
        item = next((entry for entry in session["items"] if entry["specimen_id"] == specimen_id), None)
        if item is None:
            raise ValueError("Bu örnek oturumda yok.")
        if item.get("answer") is not None:
            return self.session_payload(session)
        specimen = self._require(specimen_id)
        given = " ".join(str(text or "").split())[:120]
        if session.get("mode") == "timed" and not timed_out and item.get("shown_at"):
            # The bell is the session's, not the page's: an answer that
            # arrives after the item's time (plus a short grace for the
            # bridge) is recorded as it was typed, credited as late.
            try:
                shown = datetime.fromisoformat(str(item["shown_at"]))
                elapsed = (self._clock() - shown).total_seconds()
            except (TypeError, ValueError):
                elapsed = 0.0
            if elapsed > int(session.get("seconds") or 0) + TIMED_GRACE_SECONDS:
                timed_out = True
        names = [specimen.get("label", ""), specimen.get("latin", "")]
        correct = (not timed_out) and identification_matches(given, [name for name in names if name])
        quality, features_named, note, assessor = "unassessed", [], "", "none"
        if explanation.strip() and item.get("scored") and self._model is not None and self._model.available:
            try:
                data = await self._model.structured("histology_explanation", histology_explanation_prompt(specimen_label=specimen.get("label", ""), supported_features=specimen.get("features", []), student_explanation=explanation[:800]), HISTOLOGY_EXPLANATION_SCHEMA, system_prompt=PIPELINE_SYSTEM, max_attempts=1)
                quality = str(data.get("quality") or "unassessed")
                features_named = [str(feature)[:120] for feature in data.get("features_named", [])][:10]
                note = " ".join(str(data.get("note") or "").split())[:300]
                assessor = f"model:{getattr(self._model, 'model', '') or 'unknown'}"
            except MedicalModelError as exc:
                note = f"Açıklama değerlendirilemedi: {exc}"
        elif explanation.strip() and item.get("scored"):
            note = "Model kapalı: açıklama kaydedildi, değerlendirilmedi."
        event_id = None
        if item.get("scored"):
            concept_id = specimen.get("concept_id") or self._concept_for(specimen.get("label", ""), specimen.get("latin", ""))
            question = Question(question_id=f"histology-{specimen_id}", subject="histology", stem=f"Histoloji örneği: {specimen.get('label', '')}", options=[], correct_key=None, topic_id=specimen.get("topic_id"), concept_ids=[concept_id])
            self._learning.record(question, bool(correct))
            if self._understanding is not None:
                event = self._understanding.record_event(question, correct=correct, answer_key=given or None, confidence=confidence, reasoning=explanation, source="histology", submission_id=submission_id)
                event_id = event.get("event_id")
        item["answer"] = {
            "given": given,
            "correct": bool(correct),
            "timed_out": bool(timed_out),
            "confidence": confidence,
            "explanation": " ".join(explanation.split())[:800],
            "explanation_quality": quality,
            "explanation_quality_label": QUALITY_LABELS_TR.get(quality, quality),
            "features_named": features_named,
            "explanation_note": note,
            "assessor": assessor,
            "event_id": event_id,
            "at": self._clock().isoformat(),
        }
        if all(entry.get("answer") is not None for entry in session["items"]):
            self._finish(session)
        self._store.save_record(SESSION_KIND, session_id, session, subject_key=session["mode"])
        return self.session_payload(session)

    def _finish(self, session: dict[str, Any]) -> None:
        answered = [entry for entry in session["items"] if entry.get("answer") is not None]
        scored = [entry for entry in answered if entry.get("scored")]
        seen_before = [entry for entry in answered if int((self.specimen(entry["specimen_id"]) or {}).get("exposures", 1)) > 1]
        qualities = [entry["answer"]["explanation_quality"] for entry in scored if entry["answer"]["explanation_quality"] != "unassessed"]
        session["status"] = "closed"
        session["finished_at"] = self._clock().isoformat()
        session["results"] = {
            "shown": len(answered),
            "scored": len(scored),
            "identified": sum(1 for entry in scored if entry["answer"]["correct"]),
            "identification_accuracy": round(sum(1 for entry in scored if entry["answer"]["correct"]) / len(scored), 3) if scored else None,
            "explanations_assessed": len(qualities),
            "explanation_quality": {quality: qualities.count(quality) for quality in QUALITY_LABELS_TR if quality in qualities},
            "study_only": len(answered) - len(scored),
            "repeated_specimens": len(seen_before),
            "note": ("Bazı örnekler daha önce görülmüştü; aynı görüntüyü tanımak yeni örnekleri tanımayı kanıtlamaz." if seen_before else "") + (" Puanlanmayan örnekler yalnız çalışma içindi." if len(answered) > len(scored) else ""),
        }

    def finish_session(self, session_id: str) -> dict[str, Any]:
        session = self.session(session_id)
        if session is None:
            raise ValueError("Oturum bulunamadı.")
        if session.get("status") != "closed":
            self._finish(session)
            self._store.save_record(SESSION_KIND, session_id, session, subject_key=session["mode"])
        return self.session_payload(session)

    def sessions(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self._store.list_records(SESSION_KIND, limit=limit)

    def overview(self) -> dict[str, Any]:
        items = self.specimens()
        counts = {status: sum(1 for item in items if item.get("status") == status) for status in STATUS_LABELS_TR}
        hidden = self.under_test()
        return {
            "specimens": [self.payload(item, reveal=item["specimen_id"] not in hidden) for item in items[:200]],
            "under_test": sorted(hidden),
            "counts": counts,
            "total": len(items),
            "empty_state": EMPTY_STATE_TR if not items else "",
            "sessions": self.sessions(limit=10),
            "bases": [{"key": key, "label": label} for key, label in BASIS_LABELS_TR.items()],
        }
