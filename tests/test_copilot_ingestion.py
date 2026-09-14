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
from src.copilot.retrieval_core import _CORPUS_STORE_CACHE
from src.copilot.tools import ingestion as ing_module
from src.copilot.tools.ingestion import (
    CORPUS_NAME,
    OCR_UNAVAILABLE_MARKER,
    chunk_text,
    extract_pdf_pages,
    ingest_and_index,
    ingest_pdf,
    is_ocr_available,
    known_ingested_ids,
    retrieve_ingested,
)


@pytest.fixture(autouse=True)
def _reset_ingested_corpus_cache():
    """retrieval_core.get_corpus_store() caches by (corpus_name, mock) for
    the process lifetime (D19) — every test in this file uses the same
    CORPUS_NAME ("ingested_docs") with different underlying data, so
    without clearing the cache between tests, only the first test to call
    known_ingested_ids()/retrieve_ingested() would ever see real data;
    every test after it would see that first test's stale cached store.
    """
    _CORPUS_STORE_CACHE.pop((CORPUS_NAME, True), None)
    yield
    _CORPUS_STORE_CACHE.pop((CORPUS_NAME, True), None)


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
# ingest_and_index / known_ingested_ids / retrieve_ingested — the persisted,
# retrievable end of the pipeline. INGESTED_DOCS_DIR is monkeypatched to a
# tmp_path per test so these never touch the real data/ingested_docs/.
# -----------------------------------------------------------------------------
def test_ingest_and_index_persists_one_json_file_per_doc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ing_module, "INGESTED_DOCS_DIR", tmp_path / "ingested_docs")
    pdf = _build_pdf(tmp_path, ["Chargeback dispute window is 10 business days."])
    ingest_and_index(pdf, doc_id_prefix="CB", title="Chargeback Policy")

    out_dir = tmp_path / "ingested_docs"
    assert (out_dir / "CB.json").exists()


def test_known_ingested_ids_reflects_indexed_docs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ing_module, "INGESTED_DOCS_DIR", tmp_path / "ingested_docs")
    pdf = _build_pdf(tmp_path, ["Onboarding requires a valid ID."])
    ingest_and_index(pdf, doc_id_prefix="OB", title="Onboarding Guide")

    assert known_ingested_ids() == {"OB-001"}


def test_known_ingested_ids_empty_when_nothing_ingested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ing_module, "INGESTED_DOCS_DIR", tmp_path / "ingested_docs")
    assert known_ingested_ids() == set()


def test_retrieve_ingested_finds_relevant_chunk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ing_module, "INGESTED_DOCS_DIR", tmp_path / "ingested_docs")
    pdf = _build_pdf(tmp_path, ["Refund requests must be submitted within 30 days of purchase."])
    ingest_and_index(pdf, doc_id_prefix="RF", title="Refund Policy")

    results = retrieve_ingested("refund window in days")
    assert len(results) == 1
    assert "30 days" in results[0]["text"]


def test_retrieve_ingested_returns_empty_for_empty_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ing_module, "INGESTED_DOCS_DIR", tmp_path / "ingested_docs")
    assert retrieve_ingested("anything") == []
