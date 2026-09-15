"""
Ingestion tool — PDF (text + scanned/image) document intake for RAG.

Extends the Grounding tool's corpus model (data/policy_docs.json, a flat
JSON list of {id, title, category, text}) to documents that don't start
out as clean JSON: a real PDF, uploaded once and turned into the same
record shape via extraction -> chunking -> data/ingested_docs/*.json.
src.copilot.tools.grounding._load_policy_docs() merges those files into
its own corpus loader, so retrieval stays a single unified search over
retrieval_core's vector-store machinery — this module never queries
anything itself, it only produces records grounding.py's existing
retrieve_policy()/known_policy_ids() already know how to serve. See
DECISIONS.md D38 for why this is a separate ingestion *pipeline* feeding
one shared corpus, not a second retrieval mechanism.

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

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
INGESTED_DOCS_DIR = DATA_DIR / "ingested_docs"

# Chunk size for splitting extracted page text into corpus records — same
# order of magnitude as grounding.py's DEFAULT_MAX_CONTEXT_CHARS (D20's
# budget), so a single retrieved chunk doesn't itself blow the context
# budget before fit_to_budget() even gets to truncate it.
DEFAULT_CHUNK_CHARS = 600

OCR_UNAVAILABLE_MARKER = "[page has no extractable text layer; OCR not available in this environment]"

# ingest_and_index() builds a filesystem path directly from doc_id_prefix
# (INGESTED_DOCS_DIR / f"{doc_id_prefix}.json") with no validation — a
# prefix like "../../evil" resolves outside INGESTED_DOCS_DIR entirely.
# Not reachable from any HTTP endpoint today (ingestion has no /ingest
# route), but the module's own docstring frames this as an upload
# pipeline ("a real PDF, uploaded once") — the moment anyone wires a
# user-supplied prefix into this function, an unvalidated one becomes an
# arbitrary file write. A safe-slug allowlist costs nothing to check now
# and closes the gap before it's load-bearing. See DECISIONS.md.
_SAFE_DOC_ID_PREFIX = re.compile(r"^[A-Za-z0-9_-]+$")


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


def _wrap_on_whitespace(text: str, max_chars: int) -> list[str]:
    """Last-resort hard wrap for a chunk still over max_chars after both
    the paragraph and sentence splits — the case where a "paragraph" (or a
    single "sentence" inside one, per re.split's own fallback of treating
    unsplittable text as one sentence) has no blank lines AND no
    [.!?]-terminated sentence boundaries at all. This is the normal shape
    of real pypdf.extract_text() output: line-broken by the PDF's own
    layout, not by paragraph/sentence punctuation. Without this, a whole
    ingested PDF page could become a single oversized chunk, silently
    breaking the max_chars contract every other caller (chunk_text's own
    docstring, grounding.py's context budget) relies on. Wraps on any run
    of whitespace (not just literal spaces — real pypdf-extracted text
    commonly uses newlines between words/lines; `text.split(" ")` on
    newline-joined text doesn't split at all, silently falling through to
    the single-oversized-word branch below and cutting mid-word at every
    max_chars boundary — caught by
    test_chunk_text_wraps_line_broken_text_with_no_punctuation) so words
    aren't split; falls back to a raw character cut only if a single
    "word" is itself longer than max_chars (pathological, but still must
    not loop forever or exceed the budget).
    """
    words = text.split()
    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(word) > max_chars:
            # A single "word" longer than the whole budget (e.g. no spaces
            # at all in the source text) — cut it into max_chars-sized
            # pieces directly, since there's no whitespace left to wrap on.
            for i in range(0, len(word), max_chars):
                chunks.append(word[i : i + max_chars])
            current = ""
        else:
            current = word
    if current:
        chunks.append(current)
    return chunks


def chunk_text(text: str, max_chars: int = DEFAULT_CHUNK_CHARS) -> list[str]:
    """Splits `text` into <=max_chars chunks on paragraph boundaries first
    (blank-line-separated), falling back to sentence boundaries for any
    single paragraph that's still too long on its own, then to a hard
    whitespace wrap (_wrap_on_whitespace) for any chunk still over budget
    after that — never mid-word except in the pathological single-token
    case. The whitespace-wrap fallback matters in practice: real
    pypdf-extracted PDF text is typically line-broken by the page's own
    layout, with no blank lines and no [.!?] sentence punctuation at all,
    so the first two splits alone can silently return one oversized chunk
    per page — confirmed on realistic PDF-shaped text before this fallback
    existed. Otherwise deliberately simple (no overlap, no token-aware
    splitting): this repo's corpora are small business documents (policy
    PDFs, KYC forms), not the long-context-window use case a production
    chunker would need to optimize for.
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

    # Final pass: any chunk still over budget (a "sentence" with no
    # [.!?] boundaries at all, e.g. line-broken PDF text) gets hard-wrapped
    # on whitespace instead of being returned oversized.
    final_chunks: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            final_chunks.append(chunk)
        else:
            final_chunks.extend(_wrap_on_whitespace(chunk, max_chars))
    return final_chunks


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
    data/ingested_docs/<doc_id_prefix>.json — the next call to
    grounding.retrieve_policy()/known_policy_ids() picks it up
    automatically via grounding._load_policy_docs()'s merge of this
    directory (subject to retrieval_core's per-process corpus-store cache,
    same as any other change to policy_docs.json — see DECISIONS.md
    D34/D35). One file per source document rather than one shared file for
    every ingested PDF — an ingest re-run for the same doc_id_prefix
    cleanly overwrites just that document's file, and a caller can inspect
    what a specific ingestion produced without loading everything else
    ingested.

    Raises ValueError if `doc_id_prefix` isn't a safe filename slug
    (letters/digits/underscore/hyphen only) — a prefix like "../../evil"
    would otherwise resolve the output path outside INGESTED_DOCS_DIR.
    """
    if not _SAFE_DOC_ID_PREFIX.match(doc_id_prefix):
        raise ValueError(
            f"doc_id_prefix must match {_SAFE_DOC_ID_PREFIX.pattern!r} (letters, digits, _, - only); "
            f"got {doc_id_prefix!r}"
        )
    records = ingest_pdf(pdf_path, doc_id_prefix, title, category, max_chunk_chars)
    INGESTED_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = INGESTED_DOCS_DIR / f"{doc_id_prefix}.json"
    out_path.write_text(json.dumps(records, indent=2))
    return records
