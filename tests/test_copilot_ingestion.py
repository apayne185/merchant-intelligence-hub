"""
Tests for the Ingestion tool (src/copilot/tools/ingestion.py).

PDF fixtures are built in-memory with pypdf's writer (a hand-written
content stream showing text via BT/Tj/ET operators — see
_build_text_pdf below) rather than checked into the repo as binary
assets, matching this repo's general preference for generated over
committed test data (e.g. scripts/generate_copilot_fixture.py). A blank
page with no content stream stands in for a scanned/image-only page with
no text layer, exercising the OCR-unavailable path without needing the
tesseract binary this environment doesn't have installed.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject
from pypdf.generic._data_structures import ContentStream
from src.copilot.tools import ingestion as ing_module
from src.copilot.tools.ingestion import (
    OCR_FAILED_MARKER,
    OCR_UNAVAILABLE_MARKER,
    _ocr_page_image,
    chunk_text,
    extract_pdf_pages,
    ingest_and_index,
    ingest_pdf,
    is_ocr_available,
)


def _add_text_page(writer: PdfWriter, text: str) -> None:
    page = writer.add_blank_page(width=612, height=792)
    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    content_bytes = f"BT\n/F1 12 Tf\n72 720 Td\n({escaped}) Tj\nET".encode()
    cs = ContentStream(None, writer)
    cs.set_data(content_bytes)
    page.replace_contents(cs)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
        })
    })


def _build_pdf(tmp_path: Path, texts: list[str | None]) -> Path:
    """Builds a PDF with one page per entry in `texts` — a string page gets
    real extractable text; None gets a blank page with no content stream
    (simulating a scanned page with no text layer)."""
    writer = PdfWriter()
    for text in texts:
        if text is None:
            writer.add_blank_page(width=612, height=792)
        else:
            _add_text_page(writer, text)
    path = tmp_path / "test.pdf"
    with path.open("wb") as f:
        writer.write(f)
    return path


# -----------------------------------------------------------------------------
# is_ocr_available
# -----------------------------------------------------------------------------
def test_is_ocr_available_returns_bool() -> None:
    assert isinstance(is_ocr_available(), bool)


# -----------------------------------------------------------------------------
# extract_pdf_pages
# -----------------------------------------------------------------------------
def test_extract_pdf_pages_reads_real_text_layer(tmp_path: Path) -> None:
    pdf = _build_pdf(tmp_path, ["Refund policy: 30 days from purchase."])
    pages = extract_pdf_pages(pdf)
    assert len(pages) == 1
    assert pages[0]["page"] == 1
    assert pages[0]["method"] == "text_layer"
    assert "Refund policy" in pages[0]["text"]


def test_extract_pdf_pages_blank_page_marked_unavailable_without_tesseract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.copilot.tools.ingestion.shutil.which", lambda _: None)
    pdf = _build_pdf(tmp_path, [None])
    pages = extract_pdf_pages(pdf)
    assert pages[0]["method"] == "unavailable"
    assert pages[0]["text"] == OCR_UNAVAILABLE_MARKER


def test_extract_pdf_pages_mixed_text_and_blank(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.copilot.tools.ingestion.shutil.which", lambda _: None)
    pdf = _build_pdf(tmp_path, ["Page one has real text.", None])
    pages = extract_pdf_pages(pdf)
    assert pages[0]["method"] == "text_layer"
    assert pages[1]["method"] == "unavailable"


def test_ocr_page_image_returns_none_on_pytesseract_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # Direct unit test of _ocr_page_image's own exception handling — a
    # fake page whose .images raises when iterated, standing in for a
    # corrupt/undecodable embedded image or a tesseract runtime failure.
    class _ExplodingImages:
        def __iter__(self):
            raise RuntimeError("simulated tesseract/Pillow failure")

    class _FakePage:
        images = _ExplodingImages()

    assert _ocr_page_image(_FakePage()) is None


def test_extract_pdf_pages_ocr_failure_is_marked_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # tesseract IS "available" (binary on PATH) but fails on this specific
    # page (corrupt image, missing language data, etc.) — is_ocr_available()
    # only proves the binary exists, not that OCR will succeed. A prior bug:
    # this used to propagate the exception and kill the entire multi-page
    # ingest instead of marking just this page and continuing.
    monkeypatch.setattr("src.copilot.tools.ingestion.shutil.which", lambda _: "/usr/bin/tesseract")
    # _ocr_page_image itself already catches the real failure and returns
    # None (see its own docstring/implementation) — mocked here directly
    # to exercise extract_pdf_pages' handling of that None without needing
    # to actually trigger a real tesseract/Pillow failure.
    monkeypatch.setattr(ing_module, "_ocr_page_image", lambda page: None)
    pdf = _build_pdf(tmp_path, ["Real text page.", None])
    pages = extract_pdf_pages(pdf)
    assert pages[0]["method"] == "text_layer"
    assert pages[1]["method"] == "ocr_failed"
    assert pages[1]["text"] == OCR_FAILED_MARKER


def test_ingest_pdf_skips_ocr_failed_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.copilot.tools.ingestion.shutil.which", lambda _: "/usr/bin/tesseract")
    monkeypatch.setattr(ing_module, "_ocr_page_image", lambda page: None)
    pdf = _build_pdf(tmp_path, ["Real text here.", None])
    records = ingest_pdf(pdf, doc_id_prefix="DOC", title="Mixed doc")
    # Only the text-layer page produces a record — the ocr_failed page is
    # skipped entirely, same as the unavailable case.
    assert len(records) == 1
    assert records[0]["source_page"] == 1


# -----------------------------------------------------------------------------
# chunk_text
# -----------------------------------------------------------------------------
def test_chunk_text_short_text_is_one_chunk() -> None:
    assert chunk_text("A short paragraph.") == ["A short paragraph."]


def test_chunk_text_splits_on_paragraph_boundaries() -> None:
    text = "First paragraph.\n\nSecond paragraph."
    chunks = chunk_text(text, max_chars=1000)
    assert chunks == ["First paragraph.", "Second paragraph."]


def test_chunk_text_splits_long_paragraph_on_sentences() -> None:
    sentence = "This is one sentence that repeats. "
    long_para = sentence * 20  # comfortably over any small max_chars
    chunks = chunk_text(long_para, max_chars=100)
    assert len(chunks) > 1
    assert all(len(c) <= 100 or " " not in c for c in chunks)  # never split mid-word


def test_chunk_text_empty_string_returns_no_chunks() -> None:
    assert chunk_text("") == []


def test_chunk_text_wraps_line_broken_text_with_no_punctuation() -> None:
    # Realistic pypdf.extract_text() shape: line-broken by the PDF's own
    # layout, no blank lines, no [.!?] sentence punctuation at all — the
    # paragraph and sentence splits alone both fail to fire here, which
    # used to return the entire input as one oversized chunk (silently
    # breaking chunk_text's own max_chars contract on real PDF text).
    lines = [f"word{i}" for i in range(500)]
    text = "\n".join(lines)
    chunks = chunk_text(text, max_chars=600)
    assert all(len(c) <= 600 for c in chunks)
    # No content lost or duplicated in the process.
    assert " ".join(chunks).split() == text.split()


def test_chunk_text_wraps_single_token_longer_than_max_chars() -> None:
    # Pathological: no whitespace anywhere in the source text to wrap on.
    text = "a" * 2000
    chunks = chunk_text(text, max_chars=600)
    assert all(len(c) <= 600 for c in chunks)
    assert "".join(chunks) == text  # exact content preserved, nothing dropped


def test_chunk_text_never_exceeds_max_chars_on_mixed_realistic_text() -> None:
    # A paragraph with some sentence punctuation but also one very long
    # unbroken run (e.g. a table row or code-like text pypdf sometimes
    # extracts) — the sentence split alone can still leave one oversized
    # piece; the whitespace-wrap fallback must catch it.
    para = "Short sentence. " + ("data " * 200) + "Another short sentence."
    chunks = chunk_text(para, max_chars=100)
    assert all(len(c) <= 100 for c in chunks)


# -----------------------------------------------------------------------------
# ingest_pdf
# -----------------------------------------------------------------------------
def test_ingest_pdf_returns_policy_doc_shaped_records(tmp_path: Path) -> None:
    pdf = _build_pdf(tmp_path, ["Chargebacks must be disputed within 10 business days."])
    records = ingest_pdf(pdf, doc_id_prefix="RB", title="Refund Booklet")
    assert len(records) == 1
    record = records[0]
    assert {"id", "title", "category", "text", "source_page", "extraction_method"}.issubset(record.keys())
    assert record["id"] == "RB-001"
    assert record["title"] == "Refund Booklet"
    assert record["category"] == "ingested"
    assert record["source_page"] == 1
    assert record["extraction_method"] == "text_layer"


def test_ingest_pdf_multi_page_numbers_chunks_sequentially(tmp_path: Path) -> None:
    pdf = _build_pdf(tmp_path, ["First page text.", "Second page text."])
    records = ingest_pdf(pdf, doc_id_prefix="DOC", title="Multi-page doc")
    assert [r["id"] for r in records] == ["DOC-001", "DOC-002"]
    assert records[0]["source_page"] == 1
    assert records[1]["source_page"] == 2


def test_ingest_pdf_skips_unavailable_ocr_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.copilot.tools.ingestion.shutil.which", lambda _: None)
    pdf = _build_pdf(tmp_path, ["Real text here.", None])
    records = ingest_pdf(pdf, doc_id_prefix="DOC", title="Mixed doc")
    # Only the text-layer page produces a record — the blank/unavailable
    # page is skipped entirely rather than indexed with a useless marker.
    assert len(records) == 1
    assert records[0]["source_page"] == 1


def test_ingest_pdf_custom_category(tmp_path: Path) -> None:
    pdf = _build_pdf(tmp_path, ["Some onboarding text."])
    records = ingest_pdf(pdf, doc_id_prefix="OB", title="Onboarding Guide", category="onboarding")
    assert records[0]["category"] == "onboarding"


# -----------------------------------------------------------------------------
# ingest_and_index — persists to disk. Retrieval over the result is tested
# in tests/test_copilot_grounding.py, since grounding._load_policy_docs()
# is what actually merges data/ingested_docs/*.json into the searchable
# corpus (see DECISIONS.md D38) — this module never queries anything
# itself. INGESTED_DOCS_DIR is monkeypatched to a tmp_path so this never
# touches the real data/ingested_docs/.
# -----------------------------------------------------------------------------
def test_ingest_and_index_persists_one_json_file_per_doc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ing_module, "INGESTED_DOCS_DIR", tmp_path / "ingested_docs")
    pdf = _build_pdf(tmp_path, ["Chargeback dispute window is 10 business days."])
    ingest_and_index(pdf, doc_id_prefix="CB", title="Chargeback Policy")

    out_dir = tmp_path / "ingested_docs"
    assert (out_dir / "CB.json").exists()


def test_ingest_and_index_overwrites_same_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ing_module, "INGESTED_DOCS_DIR", tmp_path / "ingested_docs")
    pdf_v1 = _build_pdf(tmp_path, ["Version one text."])
    ingest_and_index(pdf_v1, doc_id_prefix="V", title="Doc")

    pdf_v2 = _build_pdf(tmp_path, ["Version two text, completely different."])
    records = ingest_and_index(pdf_v2, doc_id_prefix="V", title="Doc")

    assert len(records) == 1
    assert "Version two" in records[0]["text"]


# -----------------------------------------------------------------------------
# ingest_and_index — doc_id_prefix path-traversal guard. Previously built
# the output path directly from doc_id_prefix with no validation, so
# "../../evil" resolved outside INGESTED_DOCS_DIR entirely.
# -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad_prefix",
    ["../../evil", "../escape", "sub/dir", "/absolute/path", "..", "with space", "semi;colon"],
)
def test_ingest_and_index_rejects_unsafe_doc_id_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_prefix: str
) -> None:
    monkeypatch.setattr(ing_module, "INGESTED_DOCS_DIR", tmp_path / "ingested_docs")
    pdf = _build_pdf(tmp_path, ["Some text."])
    with pytest.raises(ValueError, match="doc_id_prefix"):
        ingest_and_index(pdf, doc_id_prefix=bad_prefix, title="Doc")
    # Confirms the guard actually prevents escape, not just that it raises:
    # nothing should have been written outside the intended directory.
    assert not (tmp_path / "evil.json").exists()
    assert not (tmp_path.parent / "evil.json").exists()


@pytest.mark.parametrize("good_prefix", ["RB", "policy_01", "doc-2024", "ABC123"])
def test_ingest_and_index_accepts_safe_doc_id_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, good_prefix: str
) -> None:
    monkeypatch.setattr(ing_module, "INGESTED_DOCS_DIR", tmp_path / "ingested_docs")
    pdf = _build_pdf(tmp_path, ["Some text."])
    ingest_and_index(pdf, doc_id_prefix=good_prefix, title="Doc")
    assert (tmp_path / "ingested_docs" / f"{good_prefix}.json").exists()
