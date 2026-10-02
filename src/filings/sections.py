"""
10-K section extraction: HTML -> text -> Item 1A (Risk Factors) -> chunks.

Item headings appear at least twice in a 10-K (table of contents, then the
body), and sometimes again in cross-references. The body is found by taking,
for each "Item 1A" heading, the span up to the next "Item 1B"/"Item 2"
heading and keeping the longest one: the table of contents yields spans of a
few dozen characters, the real section tens of thousands.
"""
from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser

_BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"}
# ix:header is the hidden inline-XBRL block at the top of every modern filing.
_SKIP_TAGS = {"script", "style", "ix:header"}
_START = re.compile(r"^\s*item\s*1a\b\.?\s*[:.\-]?\s*risk\s+factors", re.IGNORECASE | re.MULTILINE)
_END = re.compile(r"^\s*item\s*(1b|1c|2)\b", re.IGNORECASE | re.MULTILINE)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def extract_risk_factors(text: str) -> str:
    best = ""
    for start in _START.finditer(text):
        end = _END.search(text, start.end())
        span = text[start.end() : end.start() if end else len(text)]
        if len(span) > len(best):
            best = span
    return best.strip()


# Page furniture repeated on every printed page of a 10-K: "Table of Contents"
# links, bare page numbers, "Apple Inc. | 2025 Form 10-K | 12" footers.
_FURNITURE = re.compile(
    r"^\s*(?:\d{1,3}\s*)?table of contents\s*$|^\s*\d{1,3}\s*$|^.{0,60}\|\s*\d{4} form 10-k\s*\|\s*\d{1,3}\s*$",
    re.IGNORECASE,
)


def chunk_text(text: str, target_chars: int = 1200, max_chars: int = 1800) -> list[str]:
    """Paragraph-aligned chunks of roughly `target_chars`. A paragraph is never
    split unless it alone exceeds `max_chars` (then on sentence boundaries),
    so a risk factor's heading usually stays attached to its explanation.
    Page furniture is dropped so it neither pollutes retrieval nor becomes a
    quoted "number" (a page number) in an answer."""
    paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) > 1 and not _FURNITURE.match(p)]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        pieces = [para] if len(para) <= max_chars else re.split(r"(?<=[.;])\s+", para)
        for piece in pieces:
            if buf and len(buf) + len(piece) + 1 > target_chars:
                chunks.append(buf)
                buf = ""
            buf = f"{buf}\n{piece}" if buf else piece
    if buf:
        chunks.append(buf)
    return chunks


def passage_id(ticker: str, accession: str, index: int, text: str) -> str:
    digest = hashlib.sha256(text.encode()).hexdigest()[:8]
    return f"{ticker}:{accession}:1A:{index:03d}:{digest}"
