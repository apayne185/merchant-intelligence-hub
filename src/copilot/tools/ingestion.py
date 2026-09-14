"""
Ingestion tool — PDF (text + scanned/image) document intake for RAG.

Extends the Grounding tool's corpus model (data/policy_docs.json, a flat
JSON list of {id, title, category, text}) to documents that don't start
out as clean JSON: a real PDF, uploaded once and turned into the same
record shape via extraction -> chunking -> the existing
retrieval_core.get_corpus_store() machinery. See DECISIONS.md D38 for why
this is a separate ingestion *pipeline* feeding the same corpus format,
not a new retrieval mechanism.

Two extraction paths per page, tried in order:
  1. pypdf text extraction — pure-Python, no system binary, always
     available. Handles any PDF with a real text layer (the large
     majority of business documents: exported reports, Word-to-PDF, etc).
  2. OCR fallback (pytesseract + a rendered page image) — for scanned/
     photographed pages with no text layer. Requires the `tesseract`
     system binary, which is NOT installed in this repo's CI or (unless
     added deliberately) any dev machine — see is_ocr_available(). Real
     OCR only ever runs when that binary is actually present; otherwise a
     no-text-layer page is skipped with an explicit marker, never
     silently dropped or faked. This keeps MOCK_LLM=1 (and the whole test
     suite) working standalone, offline, with zero system dependencies —
     the same invariant every other real/mock split in this repo upholds
     (e.g. retrieval_core.py's OpenAIEmbedder vs MockEmbedder).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from src.copilot.retrieval_core import dedupe_by_field, fit_to_budget, get_corpus_store

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
INGESTED_DOCS_DIR = DATA_DIR / "ingested_docs"

CORPUS_NAME = "ingested_docs"
DEFAULT_MAX_CONTEXT_CHARS = 800
# Chunk size for splitting extracted page text into corpus records — same
# order of magnitude as DEFAULT_MAX_CONTEXT_CHARS (D20's budget), so a
# single retrieved chunk doesn't itself blow the context budget before
# fit_to_budget() even gets to truncate it.
DEFAULT_CHUNK_CHARS = 600

OCR_UNAVAILABLE_MARKER = "[page has no extractable text layer; OCR not available in this environment]"


def is_ocr_available() -> bool:
    """True only if the `tesseract` binary is actually on PATH — pytesseract
    itself imports fine without it (it just shells out to the binary at
    call time), so importing the library proves nothing about whether OCR
    will actually work. Checked once per call, not cached: this is a cheap
    PATH lookup, and caching would freeze a stale answer across a process
    that installs/uninstalls tesseract without restarting (e.g. a
    long-running dev session)."""
    return shutil.which("tesseract") is not None


def _ocr_page_image(page) -> str:
    """Renders `page` to an image and OCRs it. Only called when
    is_ocr_available() is True. pdf2image (poppler) would be the standard
    way to rasterize a PDF page to an image for OCR input, but that's a
    second system binary this repo doesn't need to require: pypdf's own
    page.images gives direct access to embedded raster images without a
    render step, which covers the actual scanned-document case (a scan is
    one full-page image embedded in the PDF, not vector content that needs
    rasterizing) without adding poppler as a dependency alongside
    tesseract.
    """
    import pytesseract

    texts = []
    for img_file in page.images:
        texts.append(pytesseract.image_to_string(img_file.image))
    return "\n".join(t.strip() for t in texts if t.strip())


def extract_pdf_pages(pdf_path: str | Path) -> list[dict[str, Any]]:
    """Extracts each page of `pdf_path` as {page, text, method}, where
    method is "text_layer", "ocr", or "unavailable" (a scanned page with no
    text layer, encountered in an environment without tesseract — text is
    OCR_UNAVAILABLE_MARKER, not silently empty, so a caller/test can tell
    apart "this page is genuinely blank" from "OCR was skipped here").
    """
    reader = PdfReader(pdf_path)
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text().strip()
        method = "text_layer"
        if not text:
            if is_ocr_available():
                text = _ocr_page_image(page)
                method = "ocr"
            else:
                text = OCR_UNAVAILABLE_MARKER
                method = "unavailable"
        pages.append({"page": i + 1, "text": text, "method": method})
    return pages


def chunk_text(text: str, max_chars: int = DEFAULT_CHUNK_CHARS) -> list[str]:
    """Splits `text` into <=max_chars chunks on paragraph boundaries first
    (blank-line-separated), falling back to sentence boundaries for any
    single paragraph that's still too long on its own — never mid-word.
    Deliberately simple (no overlap, no token-aware splitting): this
    repo's corpora are small business documents (policy PDFs, KYC forms),
    not the long-context-window use case a production chunker would need
    to optimize for.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    for para in paragraphs:
        if len(para) <= max_chars:
            chunks.append(para)
            continue
        sentences = re.split(r"(?<=[.!?])\s+", para)
        current = ""
        for sentence in sentences:
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) > max_chars and current:
                chunks.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            chunks.append(current)
    return chunks


def ingest_pdf(
    pdf_path: str | Path,
    doc_id_prefix: str,
    title: str,
    category: str = "ingested",
    max_chunk_chars: int = DEFAULT_CHUNK_CHARS,
) -> list[dict[str, Any]]:
    """Extracts + chunks `pdf_path` into corpus records shaped exactly like
    data/policy_docs.json's ({id, title, category, text}) — the ingestion
    pipeline's whole job is producing that shape so grounding.py's
    retrieve_policy()-style retrieval logic works unmodified against
    ingested documents too. Does NOT write to disk or the corpus store
    itself (see ingest_and_index below for that) — a pure function over
    one file, easy to test without touching global state.
    """
    pages = extract_pdf_pages(pdf_path)
    records = []
    chunk_num = 0
    for page in pages:
        if page["method"] == "unavailable":
            continue
        for chunk in chunk_text(page["text"], max_chunk_chars):
            chunk_num += 1
            records.append({
                "id": f"{doc_id_prefix}-{chunk_num:03d}",
                "title": title,
                "category": category,
                "text": chunk,
                "source_page": page["page"],
                "extraction_method": page["method"],
            })
    return records


def ingest_and_index(
    pdf_path: str | Path,
    doc_id_prefix: str,
    title: str,
    category: str = "ingested",
    max_chunk_chars: int = DEFAULT_CHUNK_CHARS,
) -> list[dict[str, Any]]:
    """Runs ingest_pdf() and persists the result as
    data/ingested_docs/<doc_id_prefix>.json, so a later process (or the
    same one, after this file's module-level corpus-store cache is
    invalidated by restarting) can find it via _load_ingested_docs(). One
    file per source document rather than one shared file for every
    ingested PDF — an ingest re-run for the same doc_id_prefix cleanly
    overwrites just that document's file, and a caller can inspect what a
    specific ingestion produced without loading everything else ingested.
    """
    records = ingest_pdf(pdf_path, doc_id_prefix, title, category, max_chunk_chars)
    INGESTED_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = INGESTED_DOCS_DIR / f"{doc_id_prefix}.json"
    out_path.write_text(json.dumps(records, indent=2))
    return records


def _load_ingested_docs() -> list[dict[str, Any]]:
    if not INGESTED_DOCS_DIR.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(INGESTED_DOCS_DIR.glob("*.json")):
        records.extend(json.loads(path.read_text()))
    return records


def known_ingested_ids(mock: bool = True) -> set[str]:
    """Mirrors grounding.py's known_policy_ids() — same
    hallucination-check use case (DECISIONS.md D34/D35), same reasoning
    for reading the cached store rather than re-reading disk."""
    store, _ = get_corpus_store(CORPUS_NAME, _load_ingested_docs, text_field="text", mock=mock)
    return {d["id"] for d in store.records}


def retrieve_ingested(
    query_text: str,
    k: int = 3,
    mock: bool = True,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> list[dict[str, Any]]:
    """Retrieves the top-k ingested-document chunks most similar to
    `query_text`. Identical shape to grounding.py's retrieve_policy() —
    intentionally, so the orchestrator (or a future graph node) can treat
    ingested PDFs as just another citeable corpus."""
    store, embedder = get_corpus_store(CORPUS_NAME, _load_ingested_docs, text_field="text", mock=mock)
    if len(store) == 0:
        return []
    query_vec = embedder.embed([query_text])[0]
    raw_results = store.query(query_vec, k=k * 2)
    docs = dedupe_by_field(raw_results, field="text")[:k]
    return fit_to_budget(docs, max_context_chars, field="text")
