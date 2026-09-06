"""Medical Academy text utilities: folding, chunking, page ranges.

Everything here is deterministic and provider-free, so these tests are
plain function calls with no fakes: the module promises that matching is
accent- and case-blind, that a chunk's character offsets point back at
the exact span it came from, and that a heading becomes a chunk's label
instead of being swallowed into the prose.
"""

from __future__ import annotations

from app.medical.text import (
    STOPWORDS,
    TextSpan,
    chunk_text,
    clean_lines,
    content_tokens,
    excerpt,
    fold,
    has_stem,
    is_heading,
    jaccard,
    latin_density,
    looks_latin,
    ngrams,
    normalize,
    parse_page_range,
    sentences,
    similarity,
    stem,
    stems,
    tokens,
)


def _squashed(text: str) -> str:
    """Collapse every whitespace run, the way a chunk joins its lines."""
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# folding and tokens
# ---------------------------------------------------------------------------


def test_folding_erases_turkish_case_and_accents() -> None:
    assert fold("İSTANBUL Işık") == "istanbul isik"
    assert fold("ÇĞÖŞÜ çğöşü") == "cgosu cgosu"
    # The dotless/dotted i pair is the whole point: both collapse to "i".
    assert fold("İĞİNE") == fold("igine") == "igine"
    assert fold("") == "" and fold(None) == ""
    assert normalize("  Musculus  biceps,  brachii!  ") == "musculus biceps brachii"
    assert normalize("---") == ""
    assert tokens("Kalp (cor) — 2 boşluk") == ["kalp", "cor", "2", "bosluk"]


def test_content_tokens_drop_stopwords_and_short_noise() -> None:
    assert content_tokens("Bana kalp ve akciğer hakkında bilgi ver") == [
        "kalp",
        "akciger",
        "bilgi",
    ]
    # Single characters never survive the default minimum length.
    assert content_tokens("a b 3 kalp") == ["kalp"]
    assert content_tokens("os coxae ve kalp", min_length=3) == ["coxae", "kalp"]
    assert content_tokens("") == []


def test_every_stopword_is_already_in_folded_form() -> None:
    # A stopword that does not survive fold() could never match a token,
    # so it would silently stop filtering anything.
    assert isinstance(STOPWORDS, frozenset)
    assert {word for word in STOPWORDS if fold(word) != word} == set()


def test_stemming_is_a_blunt_prefix_and_says_so() -> None:
    assert stem("kemiklerinden") == "kemik"
    assert stem("kalp") == "kalp"
    assert stem("kemiklerinden", length=3) == "kem"
    assert stems("kemiklerinden gelen") == ["kemik", "gelen"]
    assert has_stem(["kemikleri", "yapisi"], ["kemik"]) is True
    assert has_stem(["kalp"], ["kemik", "kas"]) is False
    assert has_stem([], ["kemik"]) is False


# ---------------------------------------------------------------------------
# sentences and lines
# ---------------------------------------------------------------------------


def test_sentences_split_on_terminal_punctuation_before_a_capital() -> None:
    assert sentences("Kalp bir organdır. Akciğer başkadır! Neden? Evet.") == [
        "Kalp bir organdır.",
        "Akciğer başkadır!",
        "Neden?",
        "Evet.",
    ]
    # A lowercase continuation is not a new sentence, so decimals survive.
    assert sentences("Değer 3.5 mm olarak ölçülür.") == ["Değer 3.5 mm olarak ölçülür."]
    assert sentences("İki   satır\nbirleşir.") == ["İki satır birleşir."]
    assert sentences("   ") == [] and sentences(None) == []


def test_clean_lines_collapses_runs_and_drops_blanks() -> None:
    assert clean_lines("bir\r\n\r\n  iki   üç \t\n\n") == ["bir", "iki üç"]
    assert clean_lines("tek") == ["tek"]
    assert clean_lines("\n\n") == [] and clean_lines(None) == []


# ---------------------------------------------------------------------------
# Latin heuristics
# ---------------------------------------------------------------------------


def test_looks_latin_recognises_anatomical_shapes_not_turkish_words() -> None:
    for word in ("musculus", "Musculus", "ligamentum", "arteria", "fossa", "foramen"):
        assert looks_latin(word) is True, word
    for word in ("kemik", "kalp", "damar", "hücre", "büyük"):
        assert looks_latin(word) is False, word
    # Short words carry no signal, and common English "-us" words are
    # explicitly excluded so prose does not read as anatomy.
    assert looks_latin("os") is False
    assert looks_latin("") is False
    assert looks_latin("kemik1") is False
    for word in ("this", "focus", "status", "virus", "campus"):
        assert looks_latin(word) is False, word
    assert looks_latin("species") is False


def test_latin_density_is_a_ratio_over_alphabetic_words() -> None:
    assert latin_density("musculus biceps brachii") == 2 / 3
    assert latin_density("Ligamentum patellae") == 1.0
    assert latin_density("kalp ve akciğer") == 0.0
    # Digits are not words, so they never dilute or inflate the ratio.
    assert latin_density("musculus 12 kalp") == 0.5
    assert latin_density("") == 0.0
    assert latin_density("123 456") == 0.0


# ---------------------------------------------------------------------------
# headings
# ---------------------------------------------------------------------------


def test_is_heading_accepts_short_titles_and_rejects_prose() -> None:
    for line in ("Kemik Dokusu", "KEMİK DOKUSU", "1. Giriş", "1.2 Kemik Yapısı",
                 "2) Osteoblastlar", "Tablo 2 Kemik Tipleri"):
        assert is_heading(line) is True, line
    for line in ("Kemik dokusu vücudun temel yapı taşıdır.", "Giriş:", "kemik dokusu",
                 "", "   ", "AB"):
        assert is_heading(line) is False, line
    # Length and word-count ceilings keep a paragraph from posing as a title.
    assert is_heading("A" * 81) is False
    assert is_heading("Bir İki Üç Dört Beş Altı Yedi Sekiz Dokuz On Onbir") is False
    assert is_heading("  Kemik Dokusu  ") is True


# ---------------------------------------------------------------------------
# chunking
# ---------------------------------------------------------------------------


DOCUMENT = """Kemik Dokusu

Kemik dokusu vucudun destek sistemidir. Osteoblastlar yeni kemik yapar.
Osteoklastlar kemigi yikar.

1. Kaslar
Musculus biceps brachii dirsegi fleksiyona getirir.
"""


def test_chunk_offsets_point_back_at_the_source_text() -> None:
    spans = chunk_text(DOCUMENT, target_chars=80, max_chars=200)

    assert spans and all(isinstance(span, TextSpan) for span in spans)
    for span in spans:
        assert 0 <= span.start < span.end <= len(DOCUMENT)
        # Lines are joined with a single space, so the slice matches the
        # chunk once the source's own newlines are collapsed.
        assert _squashed(DOCUMENT[span.start : span.end]) == span.text
    # A chunk that never crosses a line break maps back byte for byte.
    single_line = [span for span in spans if "\n" not in DOCUMENT[span.start : span.end]]
    assert single_line
    for span in single_line:
        assert DOCUMENT[span.start : span.end] == span.text
    assert [span.start for span in spans] == sorted(span.start for span in spans)


def test_headings_label_their_chunks_instead_of_becoming_prose() -> None:
    spans = chunk_text(DOCUMENT, target_chars=80, max_chars=200)

    assert {span.heading for span in spans} == {"Kemik Dokusu", "1. Kaslar"}
    for span in spans:
        assert span.heading not in span.text
    # A heading closes the previous chunk: no span reaches across it.
    kaslar = [span for span in spans if span.heading == "1. Kaslar"]
    assert [span.text for span in kaslar] == [
        "Musculus biceps brachii dirsegi fleksiyona getirir."
    ]
    assert DOCUMENT.index("1. Kaslar") < kaslar[0].start
    # Headings alone carry no prose, so they produce nothing to cite.
    assert chunk_text("Kemik Dokusu\nOsteoloji\n") == []


def test_leading_indentation_is_trimmed_out_of_the_span() -> None:
    source = "   Girintili cumle burada.   "
    (span,) = chunk_text(source)
    assert span.text == "Girintili cumle burada."
    assert source[span.start : span.end] == span.text
    assert span.start == 3


def test_chunks_are_sentence_aligned_and_overlap_by_one_sentence() -> None:
    body = " ".join(f"Cumle numarasi {index} burada bulunur." for index in range(1, 12))

    overlapped = chunk_text(body, target_chars=90, max_chars=300, overlap_sentences=1)
    plain = chunk_text(body, target_chars=90, max_chars=300, overlap_sentences=0)

    assert len(overlapped) > len(plain) > 1
    for span in overlapped:
        assert body[span.start : span.end] == span.text
        assert span.text.endswith(".")
    # Each chunk re-opens with the sentence its predecessor closed on.
    for previous, current in zip(overlapped, overlapped[1:]):
        tail = sentences(previous.text)[-1]
        assert current.text.startswith(tail)
        assert previous.start < current.start
    # Without overlap the chunks tile the text: every sentence appears once.
    assert sum(span.text.count("Cumle numarasi") for span in plain) == 11
    for previous, current in zip(plain, plain[1:]):
        assert previous.end < current.start


def test_a_sentence_wider_than_the_target_is_kept_whole() -> None:
    sentence = "Kelime " * 40 + "son."

    (span,) = chunk_text(sentence, target_chars=50, max_chars=1000)

    assert span.text == sentence.strip()
    assert len(span.text) > 50


def test_a_pathological_run_on_line_is_hard_split_without_gaps() -> None:
    run_on = "A" + "b" * 500

    spans = chunk_text(run_on, target_chars=100, max_chars=120)

    assert len(spans) == 5
    assert all(len(span.text) <= 120 for span in spans)
    assert all(run_on[span.start : span.end] == span.text for span in spans)
    # The parts tile the line exactly: nothing lost, nothing duplicated.
    assert "".join(span.text for span in spans) == run_on
    for previous, current in zip(spans, spans[1:]):
        assert previous.end == current.start


def test_empty_and_blank_input_produces_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n\n  \t ") == []
    assert chunk_text(None) == []


# ---------------------------------------------------------------------------
# page ranges
# ---------------------------------------------------------------------------


def test_parse_page_range_reads_turkish_and_english_forms() -> None:
    assert parse_page_range("20–40. sayfalar") == (20, 40)
    assert parse_page_range("sayfa 20 ile 40") == (20, 40)
    assert parse_page_range("sayfalar 20-40") == (20, 40)
    assert parse_page_range("sayfalari 20-40") == (20, 40)
    assert parse_page_range("20 ila 40 sayfa") == (20, 40)
    assert parse_page_range("12-13 s") == (12, 13)
    assert parse_page_range("pages 20-40") == (20, 40)
    assert parse_page_range("pp. 12-15") == (12, 15)
    assert parse_page_range("page 20 to 40") == (20, 40)
    # Case and Turkish letters fold away before matching.
    assert parse_page_range("SAYFA 20 İLE 40") == (20, 40)


def test_parse_page_range_orders_the_pair_and_reads_single_pages() -> None:
    assert parse_page_range("sayfa 40 ile 20") == (20, 40)
    assert parse_page_range("sayfa 20") == (20, 20)
    assert parse_page_range("page 7") == (7, 7)
    assert parse_page_range("s. 33") == (33, 33)


def test_parse_page_range_declines_text_without_a_range() -> None:
    assert parse_page_range("kalp nedir") is None
    assert parse_page_range("hiç sayı yok") is None
    assert parse_page_range("") is None
    assert parse_page_range(None) is None
    # "ile" carries a range only next to a page word, and a clock word
    # owns the numbers here anyway.
    assert parse_page_range("saat 20 ile 40 arasi") is None
    # Five digits overflow the 1-4 digit page field.
    assert parse_page_range("sayfa 12345") is None


def test_parse_page_range_reads_a_bare_pair_with_no_page_word() -> None:
    # Students state the scope as a naked pair; losing it would quietly
    # widen the request to the whole document.
    assert parse_page_range("20-40 arası soru hazırla") == (20, 40)
    assert parse_page_range("20–40 arasından sor") == (20, 40)
    assert parse_page_range("20 — 40") == (20, 40)
    assert parse_page_range("bu pdf'nin 12-15") == (12, 15)
    # The pair fills the page field to its edges.
    assert parse_page_range("1000-9999") == (1000, 9999)
    assert parse_page_range("1-2") == (1, 2)


def test_parse_page_range_refuses_bare_numbers_that_are_not_pages() -> None:
    for text in (
        # A neighbouring word gives the numbers another unit.
        "saat 20-40 arası",
        "zorluk 4-5 olsun",
        "seviye 2-3",
        "20-40 soru hazırla",
        "3-5 dakika sürsün",
        "45-60 saniye ver",
        "4-5 şık olsun",
        "zorluk 4/5",
        # A pair that does not climb states no span at all.
        "40-20",
        "30-30",
        # Numbers glued to another group: phones, ids, decimals, years.
        "0532-1234567",
        "numaram 555-1234",
        "telefon 0212-5551234",
        "12345678-1234-5678",
        "3.5-4.2 arası",
        "sürüm 1.2-1.3",
        "20:30-21:45 arası",
        "2024-2025 döneminden sor",
        "9999-10000",
    ):
        assert parse_page_range(text) is None, text


def test_parse_page_range_refuses_a_non_positive_page() -> None:
    # Pages are 1-based downstream (documents.py indexes page_number - 1),
    # so page 0 selects nothing: it is a rejected range, not an empty one.
    assert parse_page_range("sayfa 0 ile 5") is None
    assert parse_page_range("page 0 to 9") is None
    assert parse_page_range("sayfa 0") is None
    assert parse_page_range("page 0") is None
    assert parse_page_range("s. 0") is None
    assert parse_page_range("sayfa 0-9") is None


# ---------------------------------------------------------------------------
# similarity
# ---------------------------------------------------------------------------


def test_ngrams_and_jaccard_stay_inside_zero_to_one() -> None:
    assert ngrams(["a", "b", "c", "d"]) == {("a", "b", "c"), ("b", "c", "d")}
    # Too few items to form one window: the whole sequence is the gram.
    assert ngrams(["a", "b"]) == {("a", "b")}
    assert ngrams([]) == set()
    assert ngrams(["a", "b", "c"], size=2) == {("a", "b"), ("b", "c")}
    assert jaccard(set(), set()) == 0.0
    assert jaccard({1}, {1, 2}) == 0.5
    assert jaccard({1}, {2}) == 0.0


def test_similarity_is_symmetric_and_ranks_related_text_higher() -> None:
    text = "Kalp dört odacıktan oluşan kaslı bir organdır"
    same = "Kalp dört odacıktan oluşan kaslı bir organdır"
    related = "Kalp kaslı bir organdır ve dört odacığı bulunur"
    unrelated = "Böbrekler idrarı süzerek üretir"

    assert similarity(text, same) == 1.0
    assert similarity(text, unrelated) == 0.0
    assert 0.0 < similarity(text, related) < 1.0
    assert similarity(text, related) == similarity(related, text)
    # Accents and case must not lower the score.
    assert similarity(text, text.upper()) == 1.0
    # Nothing to compare against is honestly zero, never a false match.
    assert similarity("", text) == 0.0
    assert similarity(text, "ve bu") == 0.0


# ---------------------------------------------------------------------------
# excerpt
# ---------------------------------------------------------------------------


def test_excerpt_collapses_whitespace_and_respects_the_limit() -> None:
    assert excerpt("kısa   metin\n") == "kısa metin"
    assert excerpt("a" * 20, 20) == "a" * 20
    clipped = excerpt("a" * 21, 20)
    assert clipped == "a" * 19 + "…" and len(clipped) == 20
    assert excerpt("ab cd ef", 6) == "ab cd…"
    assert len(excerpt("uzun " * 200)) == 220
    assert excerpt("") == "" and excerpt(None) == ""
