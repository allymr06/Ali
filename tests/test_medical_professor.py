"""Professor import and style profiling: deterministic, and honest about keys.

Two promises are worth guarding here. Importing exam text is a pure text
transform: it may only report an answer key the text actually states, and
a question it could not fully parse is dropped with a note instead of
being half-saved. Profiling is evidence only: every feature carries its
observed/total, the confidence follows the sample size, and the directive
handed to generation repeats nothing that was not counted.
"""

from __future__ import annotations

from app.medical.models import (
    EvidenceSupport,
    Question,
    QuestionOption,
    QuestionOrigin,
)
from app.medical.professor import (
    FEATURE_DIRECTIVES,
    FEATURES,
    QuestionImportParser,
    StyleProfiler,
    confidence_for,
    imported_question,
)

# A messy but realistic paper: mixed numbering, options on their own lines
# and crammed onto one, a parenthesised set, an inline key, a stem with no
# options at all, and a trailing key table.
EXAM_TEXT = """ANATOMİ VİZE SORULARI

1. Aşağıdakilerden hangisi humerus'un proksimal ucunda bulunan yapılardan
biri değildir?
A) Caput humeri
B) Collum anatomicum
C) Tuberculum majus
D) Epicondylus medialis
E) Sulcus intertubercularis

2) 24 yaşında bir hasta düşme sonrası omuz ağrısı ile başvuruyor.
Muayenede kol abdüksiyonu kısıtlı. Hangi kas en olası etkilenmiştir?
a. Musculus deltoideus  b. Musculus biceps brachii  c. Musculus triceps brachii  d. Musculus pectoralis major  e. Musculus latissimus dorsi
Cevap: C

3- Articulatio genus ile ilgili aşağıdakilerden hangisi doğrudur?
(A) Ginglymus tipi bir eklemdir
(B) Sadece fleksiyon yapar
(C) Menisküs içermez
(D) Kapsülü yoktur

4. Şekilde okla işaretli yapı hangisidir?

Cevap anahtarı
1-A 2-D 3-A
"""

NO_KEY_TEXT = """1. Sternum'un parçaları aşağıdakilerden hangisidir?
A) Manubrium, corpus, processus xiphoideus
B) Caput, collum, corpus

2. Cavitas thoracis hangi yapıyla sınırlanır?
A) Diaphragma
B) Peritoneum
"""

# The word "cevap" turns up in ordinary exam prose: inside an option, and in a
# note next to the stem. Neither one states a key.
ANSWER_WORD_IN_PROSE_TEXT = """1. Kalp kapakları ile ilgili aşağıdakilerden hangisi yanlıştır?
A) Birinci ifade
B) İkinci ifade
C) Cevap A gibi görünen çeldirici
D) Dördüncü ifade

2. Sternum kaç parçadan oluşur?
A) İki
B) Üç
C) Dört
Cevap: B

3. Bu soruda kenara bir not düşülmüştür
Not: cevap D olarak duyurulmuştur
A) Birinci seçenek
B) İkinci seçenek
C) Üçüncü seçenek
D) Dördüncü seçenek
"""

# The trailing table names F for a question whose options stop at E.
TABLE_TYPO_TEXT = """1. Birinci soru kökü nedir?
A) bir
B) iki
C) üç
D) dört
E) beş

2. İkinci soru kökü nedir?
A) bir
B) iki
C) üç
D) dört
E) beş

3. Üçüncü soru kökü nedir?
A) bir
B) iki
C) üç
D) dört
E) beş

Cevap anahtarı
1-B 2-E 3-F
"""

NEUTRAL_STEM = "Dolasim sistemi dersinde islenen konulardan biri asagidakilerden secilebilir mi acaba"
CLINICAL_STEM = "Yetmis yasinda erkek hasta gogus agrisi sikayeti ile acil servise basvurdu ve muayenede sol kolda yayilim saptandi"
NEGATIVE_STEM = "Kalp kapaklari ile ilgili asagidakilerden hangisi kesinlikle dogru bir ifade degildir"
_TWIN = "Foramen ovale kafa tabaninda"
TWIN_OPTIONS = (f"{_TWIN} bulunur", f"{_TWIN} kapalidir", f"{_TWIN} genistir")

_OPTION_TEXTS = ("kalp", "akciger", "karaciger", "dalak", "bobrek")
_PREDICATES = {feature_id: predicate for feature_id, _, predicate in FEATURES}


def _question(question_id: str, stem: str, *, options: int = 4, texts: tuple[str, ...] | None = None, key: str | None = None, metadata: dict | None = None) -> Question:
    values = list(texts) if texts is not None else list(_OPTION_TEXTS[:options])
    return Question(
        question_id=question_id,
        subject="anatomy",
        stem=stem,
        options=[QuestionOption(key=letter, text=text) for letter, text in zip("ABCDEF", values)],
        correct_key=key,
        metadata=dict(metadata or {}),
    )


def _sample_of_ten() -> list[Question]:
    """Six clinical vignettes (two of them five-option and keyed), three negative, one plain."""
    questions = [
        _question(f"c{index}", CLINICAL_STEM, options=5 if index < 2 else 4, key="A" if index < 2 else None)
        for index in range(6)
    ]
    questions.extend(_question(f"n{index}", NEGATIVE_STEM) for index in range(3))
    questions.append(_question("z", NEUTRAL_STEM))
    return questions


def _feature(profile, feature_id: str):
    return next(item for item in profile.features if item.feature_id == feature_id)


# ---------------------------------------------------------------------------
# importing exam text
# ---------------------------------------------------------------------------


def test_messy_exam_text_is_parsed_into_stems_options_and_stated_keys() -> None:
    result = QuestionImportParser().parse(EXAM_TEXT)

    assert [question.number for question in result.questions] == ["1", "2", "3"]
    assert result.answer_key_found is True
    first, second, third = result.questions

    # A stem wrapped over two lines is rejoined; the title line is not a question.
    assert first.stem == "Aşağıdakilerden hangisi humerus'un proksimal ucunda bulunan yapılardan biri değildir?"
    assert [key for key, _ in first.options] == ["A", "B", "C", "D", "E"]
    assert first.options[0] == ("A", "Caput humeri")
    assert first.answer_key == "A"

    # Five lowercase options crammed onto one line still split into five.
    assert second.stem.startswith("24 yaşında bir hasta")
    assert second.options == [
        ("A", "Musculus deltoideus"),
        ("B", "Musculus biceps brachii"),
        ("C", "Musculus triceps brachii"),
        ("D", "Musculus pectoralis major"),
        ("E", "Musculus latissimus dorsi"),
    ]
    # An inline "Cevap: C" is the question's own statement and outranks the
    # trailing table, which claims D for the same number.
    assert second.answer_key == "C"

    assert third.options[0] == ("A", "Ginglymus tipi bir eklemdir")
    assert [key for key, _ in third.options] == ["A", "B", "C", "D"]
    assert third.answer_key == "A"

    # Question 4 has a stem but no options: reported by number, not half-saved.
    assert result.notes == ["Seçenekleri ayrıştırılamayan sorular: 4."]
    assert all(len(question.options) >= 2 for question in result.questions)


def test_the_parser_never_invents_an_answer_key() -> None:
    parser = QuestionImportParser()

    unkeyed = parser.parse(NO_KEY_TEXT)
    assert len(unkeyed.questions) == 2
    assert unkeyed.answer_key_found is False
    assert [question.answer_key for question in unkeyed.questions] == [None, None]
    assert unkeyed.notes == ["Metinde cevap anahtarı bulunamadı; sorular anahtarsız kaydedildi."]

    # "C" is not among the options, so it is discarded rather than snapped to
    # the nearest letter or resolved in favour of A or B.
    stray = parser.parse("1. Test sorusu nedir?\nA) bir\nB) iki\nCevap: C\n")
    assert [key for key, _ in stray.questions[0].options] == ["A", "B"]
    assert stray.questions[0].answer_key is None
    assert stray.answer_key_found is False

    for text in ("", "   \n\n", "Bunlar sadece düz ders notları, burada soru yok."):
        blank = parser.parse(text)
        assert blank.questions == [] and blank.answer_key_found is False and blank.notes == []


def test_the_word_cevap_inside_exam_prose_is_not_read_as_a_key() -> None:
    result = QuestionImportParser().parse(ANSWER_WORD_IN_PROSE_TEXT)

    trap, stated, noted = result.questions

    # An option line is an option first, even when its text says "Cevap A":
    # it stays in the run, and it hands the question no key of its own.
    assert [key for key, _ in trap.options] == ["A", "B", "C", "D"]
    assert trap.options[2] == ("C", "Cevap A gibi görünen çeldirici")
    assert trap.answer_key is None

    # A line that says nothing but the answer is still read as one.
    assert stated.answer_key == "B"

    # A note that merely mentions a letter is prose, so it stays in the stem
    # instead of becoming a key the paper never stated.
    assert noted.stem == "Bu soruda kenara bir not düşülmüştür Not: cevap D olarak duyurulmuştur"
    assert noted.answer_key is None

    assert result.answer_key_found is True
    assert result.notes == []

    # Tightening the rule may not cost the ordinary ways a paper states a key.
    for line in ("Cevap: C", "cevap c", "Doğru cevap: C", "Yanıt: C", "Answer: C", "(Cevap: C)"):
        stated_only = QuestionImportParser().parse(f"1. Soru kökü nedir?\nA) bir\nB) iki\nC) üç\n{line}\n").questions[0]
        assert stated_only.answer_key == "C", line


def test_a_key_from_the_answer_table_must_name_an_option_that_exists() -> None:
    result = QuestionImportParser().parse(TABLE_TYPO_TEXT)

    first, second, third = result.questions
    assert [key for key, _ in third.options] == ["A", "B", "C", "D", "E"]

    # Table keys are checked exactly like inline ones: B and E name real
    # options, the mistyped F names nothing and is dropped rather than stored.
    assert (first.answer_key, second.answer_key) == ("B", "E")
    assert third.answer_key is None
    assert result.answer_key_found is True


def test_option_continuations_join_and_a_broken_letter_run_is_truncated() -> None:
    parser = QuestionImportParser()

    wrapped = parser.parse("1. Bir soru kökü burada\nyazıyor ve devam ediyor\nA) ilk seçenek\ndevamı burada\nB) ikinci\nC) üçüncü\n").questions[0]
    assert wrapped.stem == "Bir soru kökü burada yazıyor ve devam ediyor"
    assert wrapped.options[0] == ("A", "ilk seçenek devamı burada")

    # A, B, D: the run stops at the gap instead of silently renumbering D to C.
    gapped = parser.parse("1. Soru kökü nedir\nA) bir\nB) iki\nD) dört\n").questions[0]
    assert [key for key, _ in gapped.options] == ["A", "B"]


def test_imported_question_is_tagged_as_an_exam_import_and_keeps_the_professor() -> None:
    parsed = QuestionImportParser().parse("5. Şekilde okla işaretli yapı hangisidir?\nA) Fossa ovalis\nB) Sulcus terminalis\nC) Crista terminalis\n").questions[0]
    assert parsed.has_image is True

    question = imported_question(parsed, subject="anatomy", professor_id="prof-42", topic_id="topic-7", document_id="doc-9")

    assert question.origin == QuestionOrigin.IMPORTED_EXAM == "imported_exam"
    assert question.professor_id == "prof-42"
    assert question.subject == "anatomy" and question.topic_id == "topic-7"
    assert question.stem == parsed.stem
    assert [(option.key, option.text) for option in question.options] == parsed.options
    assert question.correct_key is None and question.has_answer_key is False
    assert question.tags == ["imported"]
    assert question.metadata == {"number": "5", "has_image": True, "source_document_id": "doc-9"}
    assert question.question_id.startswith("q-")
    assert imported_question(parsed, subject="anatomy", professor_id=None).professor_id is None


# ---------------------------------------------------------------------------
# style features
# ---------------------------------------------------------------------------


def test_each_style_feature_fires_only_on_a_question_that_has_the_trait() -> None:
    plain = _question("plain", NEUTRAL_STEM)
    cases: list[tuple[str, Question]] = [
        ("negative_stem", _question("a", NEGATIVE_STEM)),
        ("which_true", _question("b", "Sinir hucresi ile ilgili asagidakilerden hangisi dogrudur")),
        ("clinical_vignette", _question("c", CLINICAL_STEM)),
        ("multi_statement", _question("d", "Kalp kapaklari icin I. mitral II. trikuspit III. aortik onermelerinden hangileri sol kalptedir")),
        ("multi_statement", _question("e", "Verilen onermelerden hangileri gecerlidir", texts=("Yalniz I", "I ve II", "II ve III", "I, II ve III"))),
        ("matching", _question("f", "Verilen kaslari kokenleri ile eslestiriniz asagidaki secenekleri kullanarak")),
        ("latin_terminology", _question("g", "Musculus biceps brachii tendonu nereye tutunur", texts=("Tuberositas radii", "Processus coracoideus", "Olecranon ulnae", "Condylus lateralis"))),
        ("numeric_fact", _question("h", "Eriskin bir insanda toplam 206 kemik bulunur ifadesi hangi bolumde gecerlidir")),
        ("definition", _question("i", "Kemik iligi uretiminin bozulmasina ne denir")),
        ("structure_recognition", _question("j", "Isaret edilen olusum asagidakilerden secilir", metadata={"has_image": True})),
        ("mechanism", _question("k", "Insulin salinimi kan sekerini nasil dusurur")),
        ("exception", _question("l", "Karaciger fonksiyonlari sunlardir haric olarak verilen secenek hangisidir")),
        ("long_stem", _question("m", " ".join(["kelime"] * 30))),
        ("short_stem", _question("n", "Aort nereden cikar")),
        ("similar_distractors", _question("o", NEUTRAL_STEM, texts=TWIN_OPTIONS)),
        ("five_options", _question("p", NEUTRAL_STEM, options=5)),
    ]

    for feature_id, positive in cases:
        predicate = _PREDICATES[feature_id]
        assert predicate(positive) is True, f"{feature_id} missed a question that has the trait"
        assert predicate(plain) is False, f"{feature_id} fired on a question without the trait"

    # The plain question is the shared negative: it must trip nothing at all.
    assert [feature_id for feature_id, _, predicate in FEATURES if predicate(plain)] == []
    # Two near-identical options are not yet a pattern; three are.
    assert _PREDICATES["similar_distractors"](_question("q", NEUTRAL_STEM, texts=TWIN_OPTIONS[:2])) is False


# ---------------------------------------------------------------------------
# profiles, confidence and directives
# ---------------------------------------------------------------------------


def test_confidence_follows_the_sample_size_at_its_boundaries() -> None:
    assert confidence_for(0) == EvidenceSupport.NONE
    assert confidence_for(-3) == EvidenceSupport.NONE
    assert confidence_for(1) == EvidenceSupport.LIMITED
    assert confidence_for(9) == EvidenceSupport.LIMITED
    assert confidence_for(10) == EvidenceSupport.MODERATE
    assert confidence_for(29) == EvidenceSupport.MODERATE
    assert confidence_for(30) == EvidenceSupport.HIGH


def test_profile_counts_what_it_saw_and_ignores_empty_stems() -> None:
    profile = StyleProfiler().profile("  Prof. Dr. Ayşe Yılmaz  ", _sample_of_ten(), subject="anatomy", profile_id="prof-1")

    assert profile.profile_id == "prof-1"
    assert profile.name == "Prof. Dr. Ayşe Yılmaz"
    assert profile.sample_size == 10 and len(profile.question_ids) == 10
    assert profile.confidence == EvidenceSupport.MODERATE
    assert profile.average_options == 4.2
    assert profile.average_stem_words == 14.5
    assert profile.answer_distribution == {"A": 2}

    clinical = _feature(profile, "clinical_vignette")
    assert (clinical.observed, clinical.total, clinical.level) == (6, 10, "high")
    negative = _feature(profile, "negative_stem")
    assert (negative.observed, negative.total, negative.level) == (3, 10, "moderate")
    assert (_feature(profile, "matching").observed, _feature(profile, "matching").level) == (0, "low")

    # A stem that is blank or whitespace carries no evidence, so it is not
    # counted and its id never enters the profile; a nameless one gets a label.
    thin = StyleProfiler().profile("   ", [_question("keep", NEUTRAL_STEM), _question("blank", "   "), _question("empty", "")])
    assert thin.name == "Hoca"
    assert thin.sample_size == 1 and thin.question_ids == ["keep"]


def test_ratio_levels_move_at_ten_thirty_and_sixty_percent() -> None:
    for observed, expected in ((1, "low"), (3, "moderate"), (6, "high"), (7, "very_high")):
        questions = [_question(f"c{index}", CLINICAL_STEM) for index in range(observed)]
        questions += [_question(f"z{index}", NEUTRAL_STEM) for index in range(10 - observed)]
        feature = _feature(StyleProfiler().profile("Hoca", questions), "clinical_vignette")
        assert (feature.observed, feature.ratio, feature.level) == (observed, observed / 10, expected)


def test_basis_states_the_sample_size_and_warns_while_it_is_small() -> None:
    small = StyleProfiler().profile("Hoca", [_question(f"q{index}", CLINICAL_STEM) for index in range(3)])
    assert small.confidence == EvidenceSupport.LIMITED
    assert "3 soruya dayanıyor" in small.basis and "güven sınırlı" in small.basis
    assert "Örneklem küçük" in small.basis
    # None of the three carried a key, and the basis says so out loud.
    assert "3 sorunun cevap anahtarı yok" in small.basis

    large = StyleProfiler().profile("Hoca", _sample_of_ten())
    assert "10 soruya dayanıyor" in large.basis
    assert "Örneklem küçük" not in large.basis
    assert "8 sorunun cevap anahtarı yok" in large.basis

    empty = StyleProfiler().profile("Hoca", [])
    assert empty.sample_size == 0 and empty.confidence == EvidenceSupport.NONE
    assert empty.basis == "Henüz soru yüklenmedi; profil boş."
    assert empty.average_options == 0.0 and empty.average_stem_words == 0.0
    assert all(feature.total == 0 and feature.observed == 0 for feature in empty.features)


def test_directive_repeats_only_ratios_that_were_actually_counted() -> None:
    profile = StyleProfiler().profile("Prof. Dr. Ayşe Yılmaz", _sample_of_ten(), subject="anatomy")
    directive = StyleProfiler.directive(profile)

    assert directive is not None
    assert directive.startswith(
        "Professor style profile 'Prof. Dr. Ayşe Yılmaz' (based on 10 real exam questions; "
        "confidence moderate). Imitate the STYLE, never the content:"
    )
    # 6/10 is a strong tendency and is quoted with its own numbers.
    assert "- In about 60% of questions (6/10): open with a short clinical vignette" in directive
    # 3/10 is only occasional and is worded as such.
    assert "- Occasionally (3/10): use negative stems" in directive
    # 2/10 was seen twice: too thin to state, so it is left out entirely.
    assert "use five options" not in directive
    # Nothing that was never counted may be asked for, whatever its wording.
    for feature in profile.features:
        if feature.observed == 0:
            assert FEATURE_DIRECTIVES[feature.feature_id] not in directive
    assert "- Average option count observed: 4.2; average stem length 14.5 words." in directive
    assert "sample is small" not in directive
    # Header, the two observed tendencies, the averages, and nothing else.
    assert len(directive.splitlines()) == 4


def test_directive_stays_safe_for_thin_and_featureless_profiles() -> None:
    profiler = StyleProfiler()

    assert profiler.directive(profiler.profile("Hoca", [])) is None

    small = profiler.directive(profiler.profile("Hoca", [_question(f"q{index}", CLINICAL_STEM) for index in range(3)]))
    assert small is not None
    assert "confidence limited" in small
    assert "- In about 100% of questions (3/3): open with a short clinical vignette" in small
    assert "- The sample is small; treat these as loose tendencies, keep questions varied." in small

    # A sample large enough to trust with nothing distinctive in it: the
    # directive says so rather than inventing a pattern to imitate.
    featureless = profiler.profile("Hoca", [Question(question_id=f"b{index}", subject="anatomy", stem=NEUTRAL_STEM, options=[], correct_key=None) for index in range(10)])
    plain_directive = profiler.directive(featureless)
    assert plain_directive is not None
    assert plain_directive.endswith("- No strong pattern stands out; write varied first-year exam questions of the same subject.")
    assert "Average option count" not in plain_directive


def test_a_thin_featureless_profile_still_gets_guidance_under_its_caveat() -> None:
    profiler = StyleProfiler()
    blank_options = [Question(question_id=f"b{index}", subject="anatomy", stem=NEUTRAL_STEM, options=[], correct_key=None) for index in range(3)]
    thin = profiler.profile("Hoca", blank_options)
    directive = profiler.directive(thin)

    assert thin.confidence == EvidenceSupport.LIMITED
    assert directive is not None
    assert "- The sample is small; treat these as loose tendencies, keep questions varied." in directive
    # The caveat warns, it does not guide: without the fallback the directive
    # would hand generation a header and a warning and nothing to write from.
    assert "- No strong pattern stands out; write varied first-year exam questions of the same subject." in directive
    assert len(directive.splitlines()) == 3


def test_to_dict_exposes_the_evidence_behind_every_feature() -> None:
    questions = [_question(f"c{index}", CLINICAL_STEM, key="B") for index in range(2)] + [_question("z", NEUTRAL_STEM)]
    profile = StyleProfiler().profile("Hoca", questions, subject="anatomy", notes="ilk vize")
    payload = StyleProfiler.to_dict(profile)

    assert set(payload) == {
        "profile_id", "name", "subject", "subject_label", "sample_size", "question_ids", "features",
        "average_options", "average_stem_words", "answer_distribution", "confidence",
        "confidence_label", "basis", "updated_at", "notes",
    }
    assert payload["subject_label"] == "Anatomi"
    assert payload["confidence"] == EvidenceSupport.LIMITED
    assert payload["confidence_label"] == "Sınırlı"
    assert payload["sample_size"] == 3 and payload["question_ids"] == ["c0", "c1", "z"]
    assert payload["answer_distribution"] == {"B": 2}
    assert payload["updated_at"] == profile.updated_at.isoformat()
    assert payload["notes"] == "ilk vize"

    assert len(payload["features"]) == len(FEATURES)
    clinical = next(item for item in payload["features"] if item["feature_id"] == "clinical_vignette")
    assert clinical == {
        "feature_id": "clinical_vignette",
        "label": "Klinik senaryo",
        "observed": 2,
        "total": 3,
        "ratio": 0.667,
        "level": "very_high",
        "level_label": "Çok yüksek",
    }
    assert next(item for item in payload["features"] if item["feature_id"] == "matching")["level_label"] == "Düşük"

    # An unset subject degrades to a label instead of an empty string.
    assert StyleProfiler.to_dict(StyleProfiler().profile("Hoca", []))["subject_label"] == "Belirsiz"
