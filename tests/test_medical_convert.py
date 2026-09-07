"""Presentations reach the academy as the PDF PowerPoint exports from them.

The converter is the one place a lecture can silently turn into nothing, so
its contract is spelled out: cached by the deck's bytes, loud about a deck
it cannot open, refusing what it was never meant to convert, and honest
about not being available at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.medical.convert import (
    OFFICE_SUFFIXES,
    ConversionError,
    OfficeConverter,
    find_powerpoint,
    sha256_of_file,
)

PDF = b"%PDF-1.4\n%fake deck\n"


def fake_runner(calls: list[tuple[Path, Path]], *, payload: bytes | None = PDF):
    def run(source: Path, target: Path) -> None:
        calls.append((source, target))
        if payload is not None:
            target.write_bytes(payload)

    return run


def test_find_powerpoint_answers_a_path_or_none_without_raising() -> None:
    found = find_powerpoint()
    assert found is None or (isinstance(found, Path) and found.is_file())


def test_a_deck_is_converted_once_and_then_served_from_the_cache(tmp_path) -> None:
    calls: list[tuple[Path, Path]] = []
    converter = OfficeConverter(tmp_path / "cache", runner=fake_runner(calls))
    deck = tmp_path / "Anatomi 7 - Neurocranium.pptx"
    deck.write_bytes(b"PK\x03\x04deck")

    assert converter.available is True
    assert converter.describe() == "test runner"
    first = converter.to_pdf(deck)
    assert first == tmp_path / "cache" / f"{sha256_of_file(deck)}.pdf"
    assert first.read_bytes() == PDF and converter.conversions == 1
    # The runner wrote a partial file that was renamed into place: no
    # half-written PDF can be mistaken for a finished one.
    assert [item.name for item in (tmp_path / "cache").iterdir()] == [first.name]

    # Same bytes under another name: the cache answers, PowerPoint is not asked.
    twin = tmp_path / "kopya.ppt"
    twin.write_bytes(deck.read_bytes())
    assert converter.to_pdf(twin) == first
    assert len(calls) == 1 and converter.conversions == 1
    assert converter.cached_pdf(twin) == first

    # Different bytes are a different deck.
    other = tmp_path / "baska.pptx"
    other.write_bytes(b"PK\x03\x04other")
    assert converter.to_pdf(other) != first and len(calls) == 2


def test_the_converter_refuses_what_it_should_and_names_an_empty_export(tmp_path) -> None:
    calls: list[tuple[Path, Path]] = []
    converter = OfficeConverter(tmp_path / "cache", runner=fake_runner(calls, payload=None))
    deck = tmp_path / "bos.pptx"
    deck.write_bytes(b"PK\x03\x04")
    with pytest.raises(ConversionError, match="boş çıktı"):
        converter.to_pdf(deck)
    assert not list((tmp_path / "cache").iterdir()), "a failed conversion leaves nothing behind"

    text = tmp_path / "not.txt"
    text.write_text("x", encoding="utf-8")
    with pytest.raises(ConversionError, match="Yalnızca .ppt ve .pptx"):
        converter.to_pdf(text)
    with pytest.raises(ConversionError, match="bulunamadı"):
        converter.to_pdf(tmp_path / "yok.pptx")
    assert OfficeConverter.supports(Path("x.PPTX")) and not OfficeConverter.supports(Path("x.pdf"))
    assert OFFICE_SUFFIXES == {".ppt", ".pptx"}


def test_without_powerpoint_the_converter_says_so_instead_of_pretending(tmp_path) -> None:
    converter = OfficeConverter(tmp_path / "cache", detect=False)
    assert converter.available is False
    assert converter.describe() == "PowerPoint bulunamadı"
    deck = tmp_path / "ders.pptx"
    deck.write_bytes(b"PK\x03\x04")
    with pytest.raises(ConversionError, match="PowerPoint bulunamadı"):
        converter.to_pdf(deck)


def test_without_a_cache_directory_the_pdf_lands_in_a_temporary_folder(tmp_path) -> None:
    calls: list[tuple[Path, Path]] = []
    converter = OfficeConverter(None, runner=fake_runner(calls))
    deck = tmp_path / "ders.pptx"
    deck.write_bytes(b"PK\x03\x04deck")
    pdf = converter.to_pdf(deck)
    assert pdf.is_file() and pdf.read_bytes() == PDF and pdf.name == "ders.pdf"
    assert converter.cached_pdf(deck) is None
