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

# Two past papers pasted into one import. Each one restarts at question 1 and
# closes with its own key table, and the second table's letters differ from
# the first's in every position.
TWO_PAPERS_TEXT = """2023 ANATOMİ VİZE SORULARI

1. Humerus'un cisim kırığında en sık hangi sinir zedelenir?
A) N. radialis
B) N. ulnaris
C) N. medianus
D) N. axillaris

2) Fossa cubiti'nin medial sınırını hangi kas yapar?
a. M. pronator teres  b. M. brachioradialis  c. M. biceps brachii  d. M. brachialis

3- Clavicula en sık hangi bölümünden kırılır?
(A) Orta 1/3
(B) Lateral 1/3
(C) Medial 1/3
(D) Sternal uç

Cevap anahtarı
1-A 2-A 3-A

2024 ANATOMİ VİZE SORULARI

1. Nervus facialis kafatasını hangi delikten terk eder?
A) Foramen ovale
B) Foramen stylomastoideum
C) Foramen rotundum
D) Foramen spinosum

2. Musculus deltoideus'u hangi sinir innerve eder?
A) N. radialis
B) N. musculocutaneus
C) N. axillaris
D) N. ulnaris

3. Arcus aortae'nin ilk dalı hangisidir?
A) A. subclavia sinistra
B) Truncus brachiocephalicus
C) A. carotis communis sinistra
D) A. coronaria dextra

CEVAP ANAHTARI (Anatomi Vize Sınavı 2024 - A Grubu)
1-B 2-C 3-B
"""

# Six five-option questions and a key row written at the bottom with no
# header at all — the shape a scanned paper's last page usually has.
HEADERLESS_KEY_TEXT = """1. Os frontale hangi kemikle sutura coronalis'i yapar?
A) Os parietale
B) Os occipitale
C) Os temporale
D) Os sphenoidale
E) Os zygomaticum

2. Diaphragma'yı hangi sinir innerve eder?
A) N. vagus
B) N. intercostalis
C) N. phrenicus
D) N. splanchnicus
E) N. thoracicus longus

3. Cor'un apex'i hangi tarafa bakar?
A) Sağ ve yukarı
B) Sol ve yukarı
C) Sol, aşağı ve öne
D) Sağ, aşağı ve arkaya
E) Tam ortada

4. Trachea kaç numaralı vertebra hizasında ikiye ayrılır?
A) T2
B) T4
C) T6
D) T8
E) T10

5. Humerus'un proksimal ucunda aşağıdakilerden hangisi bulunmaz?
A) Caput humeri
B) Collum chirurgicum
C) Tuberculum majus
D) Tuberculum minus
E) Epicondylus medialis

6. Vena cava inferior diaphragma'yı hangi hizada deler?
A) T8
B) T10
C) T12
D) L1
E) L2

1-A 2-C 3-C 4-B 5-E 6-A
"""

# A table that states question 1 twice, with two different letters.
CONTRADICTORY_TABLE_TEXT = """1. Birinci soru kökü nedir?
A) bir
B) iki
C) üç

2. İkinci soru kökü nedir?
A) bir
B) iki
C) üç

3. Üçüncü soru kökü nedir?
A) bir
B) iki
C) üç

Cevap anahtarı
1-A 2-B 3-C
1-C
"""

# One booklet, two papers, a single key table at the very end: the numbers
# 1-3 each belong to two different questions.
SHARED_TABLE_TEXT = """1. Birinci soru kökü nedir?
A) bir
B) iki
C) üç

2. İkinci soru kökü nedir?
A) bir
B) iki
C) üç

3. Üçüncü soru kökü nedir?
A) bir
B) iki
C) üç

1. Dördüncü soru kökü nedir?
A) bir
B) iki
C) üç

2. Beşinci soru kökü nedir?
A) bir
B) iki
C) üç

3. Altıncı soru kökü nedir?
A) bir
B) iki
C) üç

Cevap anahtarı
1-A 2-B 3-C
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


def test_each_paper_in_one_file_keeps_its_own_answer_table() -> None:
    result = QuestionImportParser().parse(TWO_PAPERS_TEXT)

    # Both papers survive the import: a table ends the paper above it, it does
    # not end the document.
    assert [question.number for question in result.questions] == ["1", "2", "3", "1", "2", "3"]

    # Every paper restarts at question 1, so the second table's letters must
    # never reach the first paper's questions. The 2023 paper says A, A, A.
    assert [question.answer_key for question in result.questions] == ["A", "A", "A", "B", "C", "B"]
    humerus = result.questions[0]
    assert humerus.stem == "Humerus'un cisim kırığında en sık hangi sinir zedelenir?"
    assert humerus.options[0] == ("A", "N. radialis")

    # The 2024 table carries a title, which makes its header line 50
    # characters long: a header is recognised by the rows under it, not by
    # how short it is.
    assert result.questions[3].stem == "Nervus facialis kafatasını hangi delikten terk eder?"
    assert result.questions[3].options[1] == ("B", "Foramen stylomastoideum")

    assert result.answer_key_found is True
    assert result.notes == []


def test_a_key_row_written_without_a_header_costs_no_question_and_no_option() -> None:
    result = QuestionImportParser().parse(HEADERLESS_KEY_TEXT)

    # Only the row itself is cut away. The last question is still there and
    # the one before it still has all five options the professor wrote.
    assert [question.number for question in result.questions] == ["1", "2", "3", "4", "5", "6"]
    assert [question.answer_key for question in result.questions] == ["A", "C", "C", "B", "E", "A"]
    assert all(len(question.options) == 5 for question in result.questions)
    assert result.questions[4].options[4] == ("E", "Epicondylus medialis")
    assert result.questions[5].stem == "Vena cava inferior diaphragma'yı hangi hizada deler?"
    assert result.notes == []


def test_a_table_that_states_one_number_twice_hands_out_no_key_for_it() -> None:
    result = QuestionImportParser().parse(CONTRADICTORY_TABLE_TEXT)

    first, second, third = result.questions
    # Which of "1-A" and "1-C" is the typo cannot be known, so question 1 is
    # stored without a key instead of taking whichever row came last.
    assert first.answer_key is None
    assert (second.answer_key, third.answer_key) == ("B", "C")
    assert result.notes == ["Cevap anahtarı şu soru numaralarına güvenle bağlanamadı: 1. Bu sorular anahtarsız kaydedildi."]


def test_one_table_over_restarted_numbering_refuses_to_pick_a_question() -> None:
    result = QuestionImportParser().parse(SHARED_TABLE_TEXT)

    # Six questions numbered 1-3 twice under a single table: each row names
    # two questions, so it names neither of them.
    assert [question.number for question in result.questions] == ["1", "2", "3", "1", "2", "3"]
    assert [question.answer_key for question in result.questions] == [None] * 6
    assert result.answer_key_found is False
    # The honest note says the key could not be attached, not that the text
    # had no key in it at all.
    assert result.notes == ["Cevap anahtarı şu soru numaralarına güvenle bağlanamadı: 1, 2, 3. Bu sorular anahtarsız kaydedildi."]


def test_a_sentence_about_which_option_is_not_the_answer_states_no_key() -> None:
    parser = QuestionImportParser()
    options = "A) Os frontale\nB) Os sphenoidale\nC) Os temporale\nD) Os occipitale\nE) Os ethmoidale\n"

    # A margin note that excludes an option, or ordinary prose that merely
    # begins with "cevap", is not a key — and it stays in the stem so the
    # student can read what the paper actually said.
    for note in ("Cevap D değildir.", "Cevap E olamaz", "Cevap A değil", "Cevap belirtiniz", "Cevap iptal edilmiştir"):
        parsed = parser.parse(f"1. Foramen ovale hangi kemikte bulunur? {note}\n{options}").questions[0]
        assert parsed.answer_key is None, note
        assert parsed.stem.endswith(note), note

    # The same note on its own line, which is where PDF text extraction
    # usually drops it, is joined into the stem and is no more of a key there.
    own_line = parser.parse(f"1. Foramen ovale hangi kemikte bulunur?\nCevap A değildir\n{options}").questions[0]
    assert own_line.answer_key is None
    assert own_line.stem == "Foramen ovale hangi kemikte bulunur? Cevap A değildir"

    # A key really stated at the end of the stem line is still read, and the
    # statement is taken out of the stem so it is not shown while practising.
    for tail, expected in (("Cevap: B", "B"), ("cevap b", "B"), ("Doğru cevap: B", "B"), ("(Yanıt: B)", "B"), ("Answer: B.", "B")):
        stated = parser.parse(f"1. Foramen ovale hangi kemikte bulunur? {tail}\n{options}").questions[0]
        assert (stated.answer_key, stated.stem) == (expected, "Foramen ovale hangi kemikte bulunur?"), tail


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


# ---------------------------------------------------------------------------
# who wrote it: mentions on title pages, in file names, in headings
# ---------------------------------------------------------------------------


def test_a_title_line_is_read_in_every_spelling_and_role_lines_are_not() -> None:
    from app.medical.professor import academic_title_and_name, professor_key

    assert academic_title_and_name("Prof. Dr. RABET GÖZİL") == ("Prof. Dr.", "Rabet Gözil")
    assert academic_title_and_name("PROF.DR.RABET GÖZİL") == ("Prof. Dr.", "Rabet Gözil")
    assert academic_title_and_name("Dr.Ragıba Zağyapan") == ("Dr.", "Ragıba Zağyapan")
    assert academic_title_and_name("YRD.DOÇ.DR. ÇAĞLA ZÜBEYDE KÖPRÜ") == ("Yrd. Doç. Dr.", "Çağla Zübeyde Köprü")
    assert academic_title_and_name("Doktor Öğretim Üyesi Pınar ŞAHİN") == ("Dr. Öğr. Üyesi", "Pınar Şahin")
    assert academic_title_and_name("Dr. Öğr. Üyesi Müge Öçal-Demirtaş") == ("Dr. Öğr. Üyesi", "Müge Öçal-Demirtaş")
    assert academic_title_and_name("• Dr. Öğr. Üyesi Çağla Zübeyde KÖPRÜ (Anabilim Dalı Başkanı)") == ("Dr. Öğr. Üyesi", "Çağla Zübeyde Köprü")
    assert academic_title_and_name("PROF. DR. A. KÜRKÇÜOĞLU") == ("Prof. Dr.", "A. Kürkçüoğlu")
    assert academic_title_and_name("Prof. Dr. Ayla Kürkçüoğlu soruları") == ("Prof. Dr.", "Ayla Kürkçüoğlu")
    assert academic_title_and_name("Prof. Dr. Recep AKDUR'un sınav soruları") == ("Prof. Dr.", "Recep Akdur")
    # Not people: a heading that starts like a title, a job, a structure.
    assert academic_title_and_name("Öğrenim Hedefleri") is None
    assert academic_title_and_name("Halk Sağlığı Uzmanı, Epidemiyolog") is None
    assert academic_title_and_name("A. profunda femoris") is None
    assert academic_title_and_name("2025-2026 Eğitim Öğretim Yılı") is None
    assert academic_title_and_name("Görkem Cengiz") is None, "no title, no mention"
    assert professor_key("PROF.DR.RABET GÖZİL") == professor_key("Prof. Dr. Rabet Gözil") == "rabet gozil"
    assert professor_key("Dr. Görkem CENGİZ") == "gorkem cengiz"


def test_the_same_person_is_recognised_across_initials_but_not_across_people() -> None:
    from app.medical.professor import professor_key, same_person

    assert same_person(professor_key("A. KÜRKÇÜOĞLU"), professor_key("Ayla Kürkçüoğlu"))
    assert same_person(professor_key("Ayşe G. Canseven Kurşun"), professor_key("Ayşe Gülnihal Canseven Kurşun"))
    assert not same_person(professor_key("Noyan Can Akdur"), professor_key("Recep Akdur"))
    assert not same_person("hasan", "hasan ozan"), "a lone first name matches nobody"
    assert not same_person("", "ayla kurkcuoglu")


def test_mentions_come_from_the_opening_pages_and_a_split_name_is_joined() -> None:
    from types import SimpleNamespace

    from app.medical.professor import professor_from_title, professor_mentions

    pages = [
        SimpleNamespace(page_number=1, text="KAS KLİNİĞİ\nDr. Hasan\nOZAN\nFascia profunda’nın verdiği uzantılar,"),
        SimpleNamespace(page_number=2, text="Prof. Dr. Ayla KÜRKÇÜOĞLU\nÖğrenim Hedefleri"),
        SimpleNamespace(page_number=9, text="Doç. Dr. Gamze GÜVEN"),
    ]
    mentions = professor_mentions(pages)
    assert [mention.name for mention in mentions] == ["Dr. Hasan Ozan", "Prof. Dr. Ayla Kürkçüoğlu"], "page 9 is past the title pages"
    assert mentions[0].complete and mentions[0].page_number == 1 and mentions[0].source == "page"
    lone = professor_mentions([SimpleNamespace(page_number=1, text="Dr. Hasan\nFascia profunda ekstremitelerde")])
    assert lone[0].name == "Dr. Hasan" and lone[0].complete is False

    assert professor_from_title("Mikrobiyoloji Ülker Çuhacı 2 - Mantarların Yapısı").name == "Ülker Çuhacı"
    assert professor_from_title("Halk Sağlığı Şeyma Kara 1 - ÇOCUK VE SAĞLIK").key == "seyma kara"
    assert professor_from_title("Mikrobiyoloji Görkem Cengiz 1 - Parazitoloji TR").key == "gorkem cengiz"
    assert professor_from_title("Anatomi 1 - Terminoloji") is None
    assert professor_from_title("Biyokimya 10 (Lipidler)") is None
    assert professor_from_title("Fizyoloji 2 - Vücut sıvıları") is None


def test_a_compiled_question_file_is_cut_at_the_headings_that_name_a_lecturer() -> None:
    from app.medical.professor import QuestionImportParser, split_by_professor

    text = (
        "2024 Anatomi vize soruları\n"
        "Prof. Dr. Ayla Kürkçüoğlu soruları\n"
        "1. Scapula'nın lateral açısında hangi yapı bulunur?\nA) Cavitas glenoidalis\nB) Spina scapulae\n"
        "2. Dr. Hasan Ozan'ın anlattığı kompartman sendromu belirtisi hangisidir?\nA) Pain\nB) Pallor\n"
        "Doç. Dr. Gamze Güven\n"
        "1. Kanıta dayalı tıp nedir?\nA) x\nB) y\n"
    )
    sections = split_by_professor(text)
    assert [mention.name if mention else None for mention, _ in sections] == [None, "Prof. Dr. Ayla Kürkçüoğlu", "Doç. Dr. Gamze Güven"]
    parsed = [QuestionImportParser().parse(body).questions for _mention, body in sections]
    assert [len(items) for items in parsed] == [0, 2, 1], "a name inside a question stem does not cut the section"


def test_only_a_real_rank_names_a_person_and_a_garbled_surname_still_merges() -> None:
    from app.medical.professor import academic_title_and_name, garbled_name, looks_like_question_paper, professor_key, same_person

    # Sentences that open with a bare abbreviation are prose, not people.
    assert academic_title_and_name("Arş. Geliştirme Çalışmaları") is None
    assert academic_title_and_name("Öğr. Elemanları İçin") is None
    assert academic_title_and_name("Yrd. Üreme Teknikleri") is None
    assert academic_title_and_name("Uzm. Hekimlik İlk Kez Görüldü") is None
    # The pairs that only mean a rank still do.
    assert academic_title_and_name("Arş. Gör. Pınar Tarıkahya") == ("Arş. Gör.", "Pınar Tarıkahya")
    assert academic_title_and_name("Öğr. Gör. Cansu ÖZTÜRK") == ("Öğr. Gör.", "Cansu Öztürk")
    assert academic_title_and_name("Uzm. Dr. Yavuzalp SOLAK") == ("Uzm. Dr.", "Yavuzalp Solak")
    assert academic_title_and_name("Yrd. Doç. Dr. M. Esin Ocaktan") == ("Yrd. Doç. Dr.", "M. Esin Ocaktan")

    assert garbled_name("Ayla Kürkçüo Lu Ğ") and not garbled_name("Ayla Kürkçüoğlu") and not garbled_name("M. Esin Ocaktan")
    assert same_person(professor_key("Ayla Kürkçüo lu ğ"), professor_key("Ayla Kürkçüoğlu"))
    assert not same_person(professor_key("Ayla Yılmaz"), professor_key("Ayla Kürkçüoğlu"))

    paper = "1. Soru?\nA) x\nB) y\n" * 6
    lecture = "Uzun bir ders notu satırı.\n" * 40 + paper
    assert looks_like_question_paper(paper, 6) and not looks_like_question_paper(lecture, 6)
    assert not looks_like_question_paper(paper, 3), "fewer than five questions is a lecture with review items"


def test_a_name_in_prose_is_read_only_as_far_as_the_sentence_lets_it() -> None:
    from app.medical.professor import looks_like_cover, mentions_in_text

    cover = "Şekil (other): Bir sunum kapak slaytı. Başlıkta 'Bakteri Metabolizması' yazıyor, altında Prof. Dr. Özgül Kısa adı bulunuyor."
    assert [mention.name for mention in mentions_in_text(cover)] == ["Prof. Dr. Özgül Kısa"], "the sentence after the name is not part of it"
    assert mentions_in_text(cover)[0].source == "visual"
    assert [mention.name for mention in mentions_in_text("Kapakta 'ALT EKSTREMİTE DAMARLARI' başlığı ve PROF. DR. A. KÜRKÇÜOĞLU imzası var.")] == ["Prof. Dr. A. Kürkçüoğlu"]
    assert [mention.name for mention in mentions_in_text("Sunumu hazırlayan: Dr. Öğr. Üyesi Müge Öçal-Demirtaş, Tıbbi Biyoloji Anabilim Dalı.")] == ["Dr. Öğr. Üyesi Müge Öçal-Demirtaş"]
    # Prose that only opens with an abbreviation, and a lone first name, name nobody.
    assert mentions_in_text("Öğrenim hedefleri listelenmiş; araştırma yapmanın temel ilkeleri anlatılıyor.") == []
    assert mentions_in_text("Etiketler: dr, scapula, acromion") == []
    assert mentions_in_text("Sayfada Dr. Hasan yazıyor") == []

    # A portrait inside a lecture names a person too, so only a cover slide counts.
    assert looks_like_cover(cover) and looks_like_cover("Sunum başlığı ve bölüm adı")
    assert not looks_like_cover("Görselde Dr. Refik Saydam'ın portresi ve sağlık örgütlenmesi şeması var.")
    assert not looks_like_cover("")



# ---------------------------------------------------------------------------
# an exam system's export: the owner of every question, the key as a suffix
# ---------------------------------------------------------------------------

EXAM_EXPORT = """KOMITE 5

1. soru:
"Eklem tipi art. sellaris'tir. Discus articularis'i vardır." Aşağıdaki eklemlerden hangisi bu özelliklere sahiptir?
Soru Sahibi : RABET GÖZİL
Anabilimdalı : Anatomi

A) Art. sternoclavicularis-Doğru Seçenek
B) Art. acromioclavicularis-Öğrencinin işaretlediği
C) Skapulotorakal eklem
D) Artt. costochondrales
E) Artt. costotransversaria

 2. soru:
Sulcus arteriae vertebralis aşağıdaki kemiklerin hangisinde bulunur?
Soru Sahibi : HAKKI YEŞİLYURT
Anabilimdalı : Anatomi

A) Os occipitale-Öğrencinin işaretlediği
B) Os temporale
C) Os sphenoidale
D) Atlas-Doğru Seçenek
E)  Axis

 3. soru:
Aşağıdaki yapılardan hangisi sadece servikal vertebralarda bulunur?
Soru Sahibi : HAKKI YEŞİLYURT
Anabilimdalı : Anatomi

A) Foramen vertebrale
B) Foramen transversarium-Doğru Seçenek-Öğrencinin işaretlediği
C) Processus transversus
D) Incisura vertebralis inferior
E) Processus articularis superior
KOMITE 1

29. soru:
Organik Kimyada Alkan yapıların genel formülü aşağıdakilerden hangisidir?
Soru Sahibi : CUMHUR BİLGİ
Anabilimdalı : Tıbbi Biyokimya

A) CnH2n+2-Doğru Seçenek-
B) CnH2n-2-Doğru Seçenek
C) CnH2n+1
D) CnH2n-1
E) CnH2n
"""


def test_an_exam_export_yields_the_owner_department_committee_key_and_the_students_mark() -> None:
    from app.medical.professor import QuestionImportParser

    result = QuestionImportParser().parse(EXAM_EXPORT)
    questions = {question.number: question for question in result.questions}
    assert set(questions) == {"1", "2", "3", "29"}
    first = questions["1"]
    assert first.stem.startswith('"Eklem tipi') and "soru:" not in first.stem, "the numbering word is not the stem"
    assert first.owner == "RABET GÖZİL" and first.department == "Anatomi" and first.committee == "5"
    assert first.answer_key == "A" and first.student_marked == "B"
    assert [text for _key, text in first.options] == ["Art. sternoclavicularis", "Art. acromioclavicularis", "Skapulotorakal eklem", "Artt. costochondrales", "Artt. costotransversaria"], "the suffixes are not part of the option"
    assert questions["2"].answer_key == "D" and questions["2"].student_marked == "A"
    # Both suffixes on one option: the key and the mark are the same letter.
    assert questions["3"].answer_key == "B" and questions["3"].student_marked == "B"
    assert questions["3"].committee == "5", "a committee heading holds until the next one"
    # After the next heading the committee changes; two options marked correct name no key.
    assert questions["29"].committee == "1" and questions["29"].department == "Tıbbi Biyokimya"
    assert questions["29"].answer_key is None and questions["29"].warnings
    assert questions["29"].options[0][1] == "CnH2n+2"
    assert result.answer_key_found is True
    # A plain paper without the export lines still parses as before.
    plain = QuestionImportParser().parse("1. Scapula nedir?\nA) Kemik\nB) Kas\nCevap: A\n").questions[0]
    assert plain.owner is None and plain.committee is None and plain.answer_key == "A"


def test_mention_from_an_owner_line() -> None:
    from app.medical.professor import mention_from_owner, professor_key, same_person

    mention = mention_from_owner("RABET GÖZİL")
    assert mention is not None and mention.name == "Rabet Gözil" and mention.source == "question"
    assert same_person(mention.key, professor_key("Prof. Dr. Rabet Gözil"))
    assert mention_from_owner("HASAN") is None and mention_from_owner("") is None


def test_in_an_export_a_numbered_list_inside_a_stem_is_not_a_new_question() -> None:
    from app.medical.professor import QuestionImportParser

    text = (
        "KOMITE 1\n\n12. soru:\nÖnceki soru?\nSoru Sahibi : DİLEK YONAR\nAnabilimdalı : Biyofizik\n\nA) p-Doğru Seçenek\nB) q\n\n"
        "13. soru:\nBu durum aşağıdaki iki liste nasıl eşleştirilebilir?\n1. Bazı öğünlerden sonra sindirim sorunları yaşayan hasta\n"
        "2. Acılı, baharatlı gıda alımı\n3. Acısız ve baharatsız beslenme\nSoru Sahibi : DİLEK YONAR\nAnabilimdalı : Biyofizik\n\n"
        "A) 1-a, 2-b, 3-c-Doğru Seçenek\nB) 1-b, 2-a, 3-c\nC) 1-c, 2-b, 3-a\n\n"
        "14. soru:\nİkinci soru?\nSoru Sahibi : DİLEK YONAR\nAnabilimdalı : Biyofizik\n\nA) x-Doğru Seçenek\nB) y\n"
    )
    result = QuestionImportParser().parse(text)
    assert [question.number for question in result.questions] == ["12", "13", "14"]
    matching = result.questions[1]
    assert "1. Bazı öğünlerden" in matching.stem and "3. Acısız" in matching.stem
    assert matching.answer_key == "A" and len(matching.options) == 3
    assert result.notes == []
    # Fewer than three "N. soru:" starts is not an export, and plain numbering still splits.
    plain = QuestionImportParser().parse("1. Birinci?\nA) a\nB) b\n2. İkinci?\nA) c\nB) d\n")
    assert [question.number for question in plain.questions] == ["1", "2"]


def test_in_an_export_a_stem_opening_with_an_abbreviation_is_not_option_a() -> None:
    from app.medical.professor import QuestionImportParser

    text = (
        "KOMİTE 6\n\n7. soru:\nA. subclavia ve brachial plexus'un trunkuslarının yer aldığı üçgen aşağıdakilerden hangisidir?\n"
        "Soru Sahibi : RABET GÖZİL\nAnabilimdalı : Anatomi\n\nA) Trigonum occipitale\nB) Trigonum supraclaviculare-Doğru Seçenek-Öğrencinin işaretlediği\n"
        "C) Trigonum musculare\nD) Trigonum caroticum\nE) Trigonum suboccipitale\n\n"
        "8. soru:\nİkinci?\nSoru Sahibi : RABET GÖZİL\nAnabilimdalı : Anatomi\n\nA) a\nB) b-Doğru Seçenek\n\n"
        "9. soru:\nÜçüncü?\nSoru Sahibi : RABET GÖZİL\nAnabilimdalı : Anatomi\n\nA) a-Doğru Seçenek\nB) b\n"
    )
    result = QuestionImportParser().parse(text)
    first = result.questions[0]
    assert first.number == "7" and first.stem.startswith("A. subclavia ve brachial plexus")
    assert [key for key, _ in first.options] == ["A", "B", "C", "D", "E"] and first.answer_key == "B" and first.committee == "6"
    assert len(result.questions) == 3 and result.notes == []
