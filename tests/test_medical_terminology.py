"""Anatomical terminology: alias resolution, in-text recognition, data sanity."""

from __future__ import annotations

import collections

import pytest

from app.medical.models import AnatomyStructure, Concept, Landmark
from app.medical.terminology import (
    AMBIGUOUS_ALIASES,
    TerminologyIndex,
    default_terminology,
    latin_variants,
    load_anatomy_data,
)


@pytest.fixture(scope="module")
def index() -> TerminologyIndex:
    """The shipped catalogue, built once because it is read-only here.

    Tests that mutate an index (adding concepts) build their own.
    """
    return default_terminology()


def hostile_structure() -> AnatomyStructure:
    """A structure whose abbreviations collide with everyday words.

    ``kas``/``alt`` are ordinary Turkish, ``os`` is too short to be a
    safe alias; none of them may make a plain sentence look medical.
    """
    return AnatomyStructure(
        structure_id="scapula",
        canonical="Scapula",
        kind="bone",
        region="upper_limb",
        turkish="Kürek kemiği",
        english="Shoulder blade",
        synonyms=["omoplat"],
        abbreviations=["KAS", "alt", "os"],
        landmarks=[
            Landmark("acromion", "Acromion", "Omuz çıkıntısı", "Clavicula ile eklemleşir"),
        ],
    )


# ---------------------------------------------------------------------------
# alias resolution and ranking
# ---------------------------------------------------------------------------


def test_latin_turkish_and_english_names_reach_the_same_entry(index: TerminologyIndex) -> None:
    for query in ("Scapula", "scapula", "kürek kemiği", "kurek kemigi", "shoulder blade", "omoplat"):
        hits = index.lookup(query, limit=3)
        assert hits, query
        assert hits[0].term_id == "scapula", query

    entry = index.get("scapula")
    assert entry is not None
    assert entry.canonical == "Scapula"  # official Latin is never translated away
    assert entry.kind == "bone" and entry.kind_label == "Kemik"
    assert entry.structure_id == "scapula" and entry.landmark_of is None
    assert entry.concept_id == "anatomy.scapula"
    payload = entry.to_dict()
    assert payload["kind_label"] == "Kemik"
    assert "Kürek kemiği" in payload["aliases"] and "scapulae" in payload["aliases"]

    assert index.lookup("") == []
    assert index.lookup("   ...   ") == []
    assert index.get("no.such.term") is None
    assert index.synonyms("no.such.term") == []


def test_lookup_ranks_exact_aliases_first_and_honours_the_limit(index: TerminologyIndex) -> None:
    ranked = [entry.term_id for entry in index.lookup("kurek kemigi", limit=5)]
    # Other bones share the token "kemigi" and may follow, but never lead.
    assert ranked[0] == "scapula" and ranked.count("scapula") == 1

    # A Turkish suffix on the query is stripped before ranking.
    assert index.lookup("scapulayi", limit=2)[0].term_id == "scapula"
    assert index.lookup("humerusun", limit=2)[0].term_id == "humerus"

    assert len(index.lookup("kurek kemigi", limit=2)) == 2
    # A zero or negative limit still yields one answer rather than nothing.
    assert len(index.lookup("kurek kemigi", limit=0)) == 1
    assert len(index.lookup("kurek kemigi", limit=-3)) == 1


def test_a_catalogue_term_answers_to_its_turkish_and_english_name(index: TerminologyIndex) -> None:
    """Most shipped terms are learnt by their Turkish name, not their Latin one."""
    assert index.lookup("Kenar", limit=3)[0].term_id == "term.margo"
    assert index.lookup("kenarı", limit=3)[0].term_id == "term.margo"
    assert index.lookup("Border / margin", limit=3)[0].term_id == "term.margo"
    assert index.lookup("Çıkıntı", limit=3)[0].term_id == "term.processus"
    assert index.lookup("Notch", limit=3)[0].term_id == "term.incisura"

    # The Turkish name is also recognised in running text beside the Latin one.
    matched = index.find_in_text("Scapulanin kenari kalindir")
    assert [match.entry.term_id for match in matched] == ["scapula", "term.margo"]

    # The guards still apply to translated names: "Kas" is the Turkish name of
    # Musculus and an everyday word, so it is displayed but never indexed.
    assert index.find_in_text("Bugün kas geliştirmek için spor salonuna gittim") == []
    assert "Kas" in index.synonyms("term.musculus")


def test_a_three_letter_alias_never_prefix_matches_a_shorter_query() -> None:
    """The minimum length must guard both prefix directions, not just one."""
    small = TerminologyIndex(
        [
            AnatomyStructure(
                structure_id="m_tensor_fasciae_latae",
                canonical="Musculus tensor fasciae latae",
                kind="muscle",
                region="lower_limb",
                turkish="Fasya lata gerici kas",
                english="Tensor fasciae latae",
                abbreviations=["TFL"],
            )
        ]
    )
    assert small.lookup("tf") == []
    # The abbreviation itself, and any alias long enough to mean something,
    # still resolve.
    assert small.lookup("tfl")[0].term_id == "m_tensor_fasciae_latae"
    assert small.lookup("tensor")[0].term_id == "m_tensor_fasciae_latae"


def test_lookup_tries_every_suffix_stem_not_only_the_longest(index: TerminologyIndex) -> None:
    """Stripping the longest suffix chain eats the stem and buries the term.

    "processu" ends in "-su", which is a Turkish suffix of its own, so the
    greedy stem is "proces" and the term drops behind every landmark whose
    Latin name merely starts with the same letters. Only the one-letter
    strip yields "process" — the term's own English name.
    """
    ranked = [entry.term_id for entry in index.lookup("processu", limit=3)]
    assert ranked[0] == "term.processus"
    assert "radius.processus_styloideus_radii" in ranked

    # Latin stems survive their Turkish suffixes whichever chain matches.
    assert index.lookup("humerusun", limit=1)[0].term_id == "humerus"
    assert index.lookup("scapulayi", limit=1)[0].term_id == "scapula"


def test_an_ambiguous_latin_landmark_offers_both_owners(index: TerminologyIndex) -> None:
    # "Epicondylus medialis" belongs to both humerus and femur; lookup must
    # surface both candidates instead of silently picking one.
    owners = {entry.term_id for entry in index.lookup("epicondylus medialis", limit=6)}
    assert {"humerus.epicondylus_medialis", "femur.epicondylus_medialis_femoris"} <= owners


# ---------------------------------------------------------------------------
# recognition inside free text
# ---------------------------------------------------------------------------


def test_turkish_suffixes_resolve_but_a_longer_word_does_not(index: TerminologyIndex) -> None:
    assert [m.entry.term_id for m in index.find_in_text("Scapulayı anlat")] == ["scapula"]
    assert [m.entry.term_id for m in index.find_in_text("humerusun boynu nerede")] == ["humerus"]

    apostrophe = index.find_in_text("tuberculum majus humeri'yi göster")
    assert [m.entry.term_id for m in apostrophe] == ["humerus.tuberculum_majus", "humerus"]
    assert [m.alias for m in apostrophe] == ["tuberculum majus", "humeri"]

    # "Spina" is a catalogued term, but "spinal" is a different word: an
    # unknown trailing letter is not a Turkish suffix, so nothing matches.
    assert index.find_in_text("spinal kord yaralanması") == []
    # A term is only recognised at a word boundary.
    assert index.find_in_text("xscapula ve 3humerus") == []


def test_matches_are_longest_first_ordered_and_never_overlap(index: TerminologyIndex) -> None:
    text = "Scapula ve humerus arasinda articulatio humeri vardir"
    matches = index.find_in_text(text)
    assert [m.entry.term_id for m in matches] == ["scapula", "humerus", "articulatio_humeri"]
    assert [m.start for m in matches] == sorted(m.start for m in matches)
    for match in matches:
        assert text[match.start : match.end].casefold() == match.alias
    for left, right in zip(matches, matches[1:]):
        assert left.end <= right.start

    # The landmark wins over the bare terms "spina" and "scapula" inside it.
    landmarks = index.find_in_text("Spina scapulae ve fossa supraspinata")
    assert [m.entry.term_id for m in landmarks] == [
        "scapula.spina_scapulae",
        "scapula.fossa_supraspinata",
    ]

    assert index.find_in_text("") == []
    assert index.find_in_text("   \n  ") == []


def test_dotted_abbreviations_resolve_the_way_lecture_notes_write_them(
    index: TerminologyIndex,
) -> None:
    """Notes say "n. radialis"; the stored alias folds to "n radialis"."""
    nerve = index.find_in_text("n. radialis yaralanmasi")
    assert [match.entry.term_id for match in nerve] == ["n_radialis"]
    assert nerve[0].alias == "n radialis"
    # The written dot belongs to the match, not to the text around it.
    assert (nerve[0].start, nerve[0].end) == (0, len("n. radialis"))

    muscle = index.find_in_text("m. deltoideus omuz eklemini hareket ettirir")
    assert [match.entry.term_id for match in muscle] == ["m_deltoideus", "articulatio_humeri"]

    # Every spelling of the abbreviation reaches the same entry, but a word
    # that merely starts with the letter does not.
    for text in ("m. deltoideus", "m.deltoideus", "m deltoideus", "m-deltoideus"):
        assert [match.alias for match in index.find_in_text(text)] == ["m deltoideus"], text
    assert index.find_in_text("mdeltoideus") == []
    assert [match.entry.term_id for match in index.find_in_text("deltoideus kasi")] == [
        "m_deltoideus"
    ]


def test_multiword_aliases_tolerate_hyphens_and_line_breaks(index: TerminologyIndex) -> None:
    for text in ("kürek-kemiği", "kurek\nkemigi", "kurek  kemigi"):
        assert [m.entry.term_id for m in index.find_in_text(text)] == ["scapula"], text


def test_latin_variants_are_recognised_in_compound_terms(index: TerminologyIndex) -> None:
    assert latin_variants("Scapula") == ["scapulae"]
    assert latin_variants("Humerus") == ["humeri"]
    assert latin_variants("Femur") == ["femoris"]
    assert latin_variants("Ligamentum") == ["ligamenti"]
    assert latin_variants("Os") == ["ossis"]
    # Only the head word counts, and short stems are left alone.
    assert latin_variants("Scapula dextra") == ["scapulae"]
    assert latin_variants("Caput humeri") == []
    assert latin_variants("Ala") == []
    assert latin_variants("Anus") == []
    assert latin_variants("") == []

    genitives = index.find_in_text("Angulus inferior scapulae ile collum femoris")
    assert [m.entry.term_id for m in genitives] == [
        "scapula.angulus_inferior",
        "scapula",
        "femur.collum_femoris",
    ]


# ---------------------------------------------------------------------------
# aliases that must never fire
# ---------------------------------------------------------------------------


def test_ambiguous_and_tiny_aliases_never_match_a_plain_sentence() -> None:
    small = TerminologyIndex([hostile_structure()])
    assert "kas" in AMBIGUOUS_ALIASES and "alt" in AMBIGUOUS_ALIASES

    assert small.find_in_text("Alt kattaki kas salonu bugün kapalı") == []
    assert small.lookup("alt") == []
    assert small.lookup("kas") == []
    assert small.lookup("os") == []
    # The real names of the same structure still resolve.
    assert [m.entry.term_id for m in small.find_in_text("Scapulayı çiz")] == ["scapula"]
    assert small.lookup("omoplat")[0].term_id == "scapula"
    # The entry keeps its aliases for display even though they are not indexed.
    assert "alt" in small.synonyms("scapula")


def test_everyday_turkish_stays_free_of_medical_matches(index: TerminologyIndex) -> None:
    for sentence in (
        "Yarın sabah dokuzda toplantı var, alt kattaki odada.",
        "Bugün hava çok güzel, dışarı çıkalım.",
    ):
        assert index.find_in_text(sentence) == [], sentence
        assert index.expand(sentence) == set()


# ---------------------------------------------------------------------------
# expansion, synonyms and explanations
# ---------------------------------------------------------------------------


def test_expand_returns_the_folded_synonyms_of_every_term_found(index: TerminologyIndex) -> None:
    assert index.expand("Scapulayı anlat") == {
        "scapula",
        "scapulae",
        "kurek kemigi",
        "shoulder blade",
        "omoplat",
    }
    # Two terms in one question contribute both synonym sets.
    combined = index.expand("Humerus ve scapula")
    assert {"humerus", "humeri", "kol kemigi"} <= combined
    assert {"scapula", "omoplat"} <= combined


def test_expand_offers_only_the_aliases_that_were_actually_indexed() -> None:
    """Search expansion must not smuggle back the aliases registration refused."""
    small = TerminologyIndex([hostile_structure()])
    expanded = small.expand("Scapulayı çiz")

    assert expanded == {"scapula", "scapulae", "kurek kemigi", "shoulder blade", "omoplat"}
    assert AMBIGUOUS_ALIASES & expanded == set()
    assert "os" not in expanded  # too short to be a safe search term
    # They are still on the entry, because display and search differ.
    assert {"KAS", "alt", "os"} <= set(small.synonyms("scapula"))


def test_explain_puts_latin_first_then_turkish_and_the_note(index: TerminologyIndex) -> None:
    assert index.explain(index.get("scapula")) == "Scapula — Kürek kemiği (İng. Shoulder blade)"
    # No English gloss when it would only repeat the Latin.
    assert index.explain(index.get("talus")) == "Talus — Aşık kemiği"

    landmark = index.explain(index.get("scapula.acromion"))
    assert landmark.startswith("Acromion — ")
    assert " · " in landmark and "articulatio acromioclavicularis" in landmark

    plane = index.get("term.planum_sagittale")
    assert plane.kind == "term" and plane.kind_label == "Düzlem"
    assert index.explain(plane).startswith("Planum sagittale — Sagittal düzlem")


# ---------------------------------------------------------------------------
# landmarks and concepts
# ---------------------------------------------------------------------------


def test_landmark_entries_point_back_at_the_bone_that_carries_them(index: TerminologyIndex) -> None:
    landmarks = index.entries(kind="landmark")
    assert len(landmarks) > 100
    for entry in landmarks:
        assert entry.landmark_of and entry.landmark_of == entry.structure_id
        assert index.get(entry.structure_id) is not None
        assert entry.term_id.startswith(entry.structure_id + ".")
        assert entry.concept_id == "anatomy." + entry.term_id
        assert entry.kind_label == "Kemik işareti"

    acromion = index.get("scapula.acromion")
    assert acromion.canonical == "Acromion" and acromion.landmark_of == "scapula"
    assert acromion.turkish and acromion.aliases == ["Acromion"]


def test_a_concept_already_covered_by_the_catalogue_is_not_indexed_twice() -> None:
    structure = hostile_structure()
    duplicate = Concept(concept_id="anatomy.scapula", subject="anatomy", name="Scapula")
    landmark_duplicate = Concept(
        concept_id="anatomy.scapula.acromion", subject="anatomy", name="Acromion"
    )
    fresh = Concept(
        concept_id="physiology.osmoz",
        subject="physiology",
        name="Osmoz",
        aliases=["osmosis", "ozmoz"],
    )
    small = TerminologyIndex([structure], concepts=[duplicate, landmark_duplicate, fresh])

    assert len(small) == 3  # bone + landmark + the one genuinely new concept
    assert {entry.term_id for entry in small.entries(kind="concept")} == {"physiology.osmoz"}
    assert small.get("scapula").kind == "bone"  # the structure entry survived

    osmosis = small.get("physiology.osmoz")
    assert osmosis.category == "physiology" and osmosis.concept_id == "physiology.osmoz"
    assert small.lookup("osmosis")[0].term_id == "physiology.osmoz"

    # Re-adding either duplicate later is still a no-op.
    small.add_concept(duplicate)
    small.add_concept(fresh)
    assert len(small) == 3


# ---------------------------------------------------------------------------
# the shipped data file
# ---------------------------------------------------------------------------


def test_anatomy_data_file_is_internally_consistent() -> None:
    structures, terms, source = load_anatomy_data()
    assert len(structures) >= 50 and len(terms) >= 50
    assert "Terminologia Anatomica" in source

    ids = [structure.structure_id for structure in structures]
    assert [item for item, count in collections.Counter(ids).items() if count > 1] == []
    known = set(ids)

    for structure in structures:
        assert structure.canonical and structure.turkish and structure.english
        assert structure.kind and structure.region
        if structure.parent_id:
            assert structure.parent_id in known, structure.structure_id
        for relation in structure.relations:
            assert relation["relation"] and relation["target"] in known, structure.structure_id
        landmark_ids = [landmark.landmark_id for landmark in structure.landmarks]
        assert len(set(landmark_ids)) == len(landmark_ids), structure.structure_id
        for landmark in structure.landmarks:
            assert landmark.latin and landmark.turkish

    for term in terms:
        assert str(term.get("canonical", "")).strip()
        assert str(term.get("turkish", "")).strip()


def test_every_shipped_entry_carries_a_turkish_kind_label(index: TerminologyIndex) -> None:
    for entry in index.entries():
        assert entry.kind_label, entry.term_id
        # An unlabelled kind would leak the raw English key into the UI.
        assert entry.kind_label != entry.kind, entry.term_id
        if entry.kind == "term":
            assert entry.category, entry.term_id
            assert entry.kind_label != "Terim", entry.term_id
