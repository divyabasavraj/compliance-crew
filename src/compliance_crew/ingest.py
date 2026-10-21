"""Download RBI regulations and split them into citable paragraph-level chunks.

Each chunk keeps the exact paragraph label (e.g. ``5(i)(a)``) so every agent claim
can cite a paragraph that really exists in the corpus.
"""

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
RAW_DIR = DATA_DIR / "raw"
CORPUS_PATH = DATA_DIR / "corpus.jsonl"

SOURCES = [
    {
        "doc_id": "DLD2025",
        "title": "Reserve Bank of India (Digital Lending) Directions, 2025",
        "ref": "RBI/2025-26/36 DOR.STR.REC.19/21.07.001/2025-26, May 8, 2025",
        "url": "https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12848",
        "start": r"^1\.\s+Preamble",
        "end": None,
    },
    {
        "doc_id": "KFS2024",
        "title": "Key Facts Statement (KFS) for Loans & Advances",
        "ref": "RBI/2024-25/18 DOR.STR.REC.13/13.03.00/2024-25, April 15, 2024",
        "url": "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12663&Mode=0",
        "start": r"^Please refer to our instructions on Key Facts Statement",
        "end": r"^Yours faithfully",
        "first_para": "1",  # the circular's opening paragraph is unnumbered
    },
]

ROMAN = r"(?:i{1,3}|iv|v|vi{1,3}|ix|x|xi{1,3}|xiv|xv|xvi{1,3}|xix|xx)"


@dataclass
class Chunk:
    id: str
    doc_id: str
    doc_title: str
    para: str
    section_title: str
    text: str
    url: str

    def search_text(self) -> str:
        return f"{self.doc_title}. Para {self.para} {self.section_title}. {self.text}"


def download(source: dict) -> str:
    path = RAW_DIR / f"{source['doc_id']}.html"
    if not path.exists():
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        resp = httpx.get(source["url"], headers={"User-Agent": "Mozilla/5.0"}, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        path.write_text(resp.text, encoding="utf-8")
    return path.read_text(encoding="utf-8", errors="ignore")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _blocks(soup: BeautifulSoup):
    """Yield (text, kind) for each paragraph or table row, in document order.

    kind is "head" for heading paragraphs, "row" for table rows, else "p".
    """
    for el in soup.find_all(["p", "tr"]):
        if el.name == "tr":
            if el.find("p"):
                continue  # rows wrapping <p> are handled through the <p> itself
            cells = [_clean(td.get_text(" ")) for td in el.find_all(["td", "th"])]
            text = " | ".join(c for c in cells if c)
            if text:
                yield text, "row"
        else:
            text = _clean(el.get_text(" "))
            if text:
                yield text, "head" if "head" in (el.get("class") or []) else "p"


def parse(source: dict, html: str) -> list[Chunk]:
    soup = BeautifulSoup(html, "lxml")
    started = False
    section, section_title, roman = source.get("first_para", "0"), "", None
    label = section
    pieces: list[tuple[str, str, str]] = []  # (para label, section title, text)

    for text, kind in _blocks(soup):
        is_head = kind == "head"
        if not started:
            if re.match(source["start"], text):
                started = True
            else:
                continue
        if source["end"] and re.match(source["end"], text):
            break

        if re.match(r"^Annex\s+[IVX]+\b", text) and len(text) < 200:
            section, section_title, roman = re.match(r"^(Annex\s+[IVX]+)", text).group(1), text, None
            label = section
            continue
        if re.match(r"^Chapter\s+[IVX]+", text):
            continue
        if kind == "row":
            pass  # table rows continue the current paragraph; their numbering is not a para label
        elif is_head and len(text) < 80 and not re.match(r"^[\d(]", text):
            section_title = text  # unnumbered sub-heading such as "Exemptions"
            continue
        elif m := re.match(r"^(\d{1,2})\.\s+(.*)", text):
            section, roman, label = m.group(1), None, m.group(1)
            if is_head and len(m.group(2)) < 120:
                section_title = m.group(2)
                continue
            section_title = section_title if is_head else ""
            text = m.group(2)
        elif m := re.match(rf"^({ROMAN})\.\s+(.*)", text):
            roman = m.group(1)
            label = f"{section}({roman})"
        elif m := re.match(r"^\(([a-z]{1,2})\)\s+(.*)", text):
            label = f"{section}({roman})({m.group(1)})" if roman else f"{section}({m.group(1)})"

        pieces.append((label, section_title, text))

    # merge consecutive pieces that share a paragraph label
    merged: list[list[str]] = []
    for lbl, title, text in pieces:
        if merged and merged[-1][0] == lbl:
            merged[-1][2] += "\n" + text
        else:
            merged.append([lbl, title, text])

    chunks, seen = [], {}
    for lbl, title, text in _split_long(merged):
        n = seen.get(lbl, 0)
        seen[lbl] = n + 1
        cid = f"{source['doc_id']}:{lbl}" + (f"#{n + 1}" if n else "")
        chunks.append(Chunk(cid, source["doc_id"], source["title"], lbl, title, text, source["url"]))
    return chunks


def _split_long(merged: list[list[str]], limit: int = 1500):
    """Split very long paragraphs (mostly annex tables) on line breaks so they fit the reranker."""
    for lbl, title, text in merged:
        part = ""
        for line in text.split("\n"):
            if part and len(part) + len(line) > limit:
                yield lbl, title, part
                part = ""
            part = f"{part}\n{line}" if part else line
        yield lbl, title, part


def build_corpus() -> list[Chunk]:
    chunks = []
    for source in SOURCES:
        chunks.extend(parse(source, download(source)))
    with CORPUS_PATH.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    # pin the corpus: guarded reviews refuse to run if the file changes afterwards (security/secure.py)
    import hashlib

    (DATA_DIR / "corpus.sha256").write_text(hashlib.sha256(CORPUS_PATH.read_bytes()).hexdigest() + "\n")
    return chunks


def load_corpus() -> list[Chunk]:
    if not CORPUS_PATH.exists():
        return build_corpus()
    with CORPUS_PATH.open(encoding="utf-8") as f:
        return [Chunk(**json.loads(line)) for line in f]


if __name__ == "__main__":
    chunks = build_corpus()
    for doc in {c.doc_id for c in chunks}:
        print(doc, sum(c.doc_id == doc for c in chunks), "chunks")
