"""Helpers for reading product documents and showing regulation paragraphs to a model."""

import re
from dataclasses import dataclass

from .ingest import Chunk

PARA_CHARS = 900  # long annex chunks are cut so prompts fit the local model's context


@dataclass
class Section:
    number: int
    heading: str
    text: str


def split_sections(doc: str) -> list[Section]:
    """Split a markdown product doc on '##' headings. Text before the first heading is section 0."""
    parts = re.split(r"^##\s+(.+)$", doc, flags=re.M)
    sections = [Section(0, "Overview", parts[0].strip())] if parts[0].strip() else []
    for n, (heading, body) in enumerate(zip(parts[1::2], parts[2::2]), start=1):
        sections.append(Section(n, heading.strip(), body.strip()))
    return sections


def format_paragraphs(chunks: list[Chunk]) -> str:
    out = []
    for c in chunks:
        text = c.text if len(c.text) <= PARA_CHARS else c.text[:PARA_CHARS] + " ..."
        title = f" ({c.section_title})" if c.section_title else ""
        out.append(f"[{c.id}]{title} {text}")
    return "\n\n".join(out)
