"""Turn an uploaded product PDF into the markdown shape the crew expects ('#' title, '##' sections).

The Orchestrator works on '##' sections, so headings matter more than layout. A line is a heading
when its font is clearly larger than the body text, or when it is a short bold line that does not
end like a sentence. The first heading becomes the '#' title. Consecutive heading lines of the
same size are one wrapped heading. Text is NFKC-normalised, so ligatures like 'ﬀ' become 'ff'.
"""

import re
import unicodedata
from collections import Counter

import pymupdf

MAX_PAGES = 30


def _lines(pdf: bytes) -> list[tuple[str, float, bool]]:
    """(text, font size, all bold) per visual line, in reading order."""
    out = []
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        if doc.page_count > MAX_PAGES:
            raise ValueError(f"PDF has more than {MAX_PAGES} pages")
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    spans = [s for s in line["spans"] if s["text"].strip()]
                    if not spans:
                        continue
                    text = unicodedata.normalize("NFKC", " ".join(s["text"].strip() for s in spans))
                    size = max(s["size"] for s in spans)
                    bold = all(s["flags"] & 16 or "bold" in s["font"].lower() for s in spans)
                    out.append((text, size, bold))
    return out


def pdf_to_markdown(pdf: bytes) -> str:
    lines = _lines(pdf)
    if not lines:
        raise ValueError("no text found in the PDF (scanned PDFs are not supported)")
    body = Counter(round(size) for _, size, _ in lines).most_common(1)[0][0]
    md, titled, prev_heading_size = [], False, None
    for text, size, bold in lines:
        heading = len(text) <= 90 and (
            size >= body + 1.5 or (bold and not re.search(r"[.,;:]$", text))
        )
        text = text.lstrip("#").strip() if heading else text
        if heading and prev_heading_size is not None and abs(size - prev_heading_size) < 0.5:
            md[-1] = md[-1].rstrip("\n") + f" {text}\n"  # wrapped heading
        elif heading:
            md.append(f"\n{'##' if titled else '#'} {text}\n")
            titled = True
        else:
            md.append(text)
        prev_heading_size = size if heading else None
    return re.sub(r"\n{3,}", "\n\n", "\n".join(md)).strip() + "\n"
