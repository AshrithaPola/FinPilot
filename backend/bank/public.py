"""Public sources (data/public/sources.yaml): SEC 10-K Items 1A and 7, ETF
summary prospectuses, CFPB and Investor.gov pages, as chunks; and their
loader.

    python -m bank.public fetch    # download what isn't cached (data/.cache/public/, gitignored)
    python -m bank.public          # documents, sections, chunks and tokens, from the cache
    python -m bank.public load     # + chunk metadata (LLM, cached), then the database

Fetching: SEC requests (www.sec.gov, investor.gov) send SEC_USER_AGENT, as
EDGAR requires, and stay well under SEC's 10 requests a second; CFPB pages
get a plain project User-Agent. Fetched text is never committed
(data/SOURCES.md). Each document is HTML turned into Markdown (headings,
paragraphs, tables as rows), opening with its attribution, then parsed and
chunked like the bank's own documents, public (clearance 0) in the bank's
org, with doc_type, department "public", version, effective date (the
filing date) and superseded date (the same company's next filing of that
kind) for the metadata filters.
"""
import argparse
import asyncio
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "data" / "public" / "sources.yaml"
CACHE = ROOT / "data" / ".cache" / "public"
EDGAR = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{document}"
SEC_PAUSE, WEB_PAUSE = 0.25, 1.0  # seconds between requests: SEC allows 10 a second
DOC_TYPES = {"sec_10k": "sec_10k", "sec_prospectus": "fund_prospectus",
             "cfpb": "consumer_guide", "investor_gov": "investor_education"}
ATTRIBUTION = {
    "sec_10k": "Source: {title}, filed with the U.S. Securities and Exchange Commission on {filed} "
               "(SEC EDGAR, {url}). Items 1A and 7 only.",
    "sec_prospectus": "Source: {title}, filed with the U.S. Securities and Exchange Commission on "
                      "{filed} (SEC EDGAR, {url}).",
    "cfpb": "Source: Consumer Financial Protection Bureau, consumerfinance.gov ({url}).",
    "investor_gov": "Source: Investor.gov, U.S. Securities and Exchange Commission ({url}).",
}


def load_sources() -> list[dict]:
    return yaml.safe_load(SOURCES.read_text(encoding="utf-8"))["sources"]


def url_of(source: dict) -> str:
    if "url" in source:
        return source["url"]
    folder = source["accession"].replace("-", "")
    return EDGAR.format(cik=source["cik"], folder=folder, document=source["document"])


# --- fetching -------------------------------------------------------------------

def fetch(sources: list[dict], user_agent: str | None) -> list[str]:
    """Download every source not cached yet. Returns the ids fetched."""
    import requests

    CACHE.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    fetched = []
    for source in sources:
        path = CACHE / f"{source['id']}.html"
        if path.exists():
            continue
        url = url_of(source)
        sec = "sec.gov" in url or "investor.gov" in url
        if sec and not user_agent:
            raise SystemExit("SEC_USER_AGENT is not set (.env): SEC requires a User-Agent with a contact.")
        headers = {"User-Agent": user_agent if sec else "FinPilot research project (educational)"}
        response = session.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        path.write_bytes(response.content)
        fetched.append(source["id"])
        time.sleep(SEC_PAUSE if sec else WEB_PAUSE)
    return fetched


# --- HTML to Markdown -----------------------------------------------------------

_BLOCKS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "section",
           "article", "ul", "ol", "blockquote", "dd", "dt", "td", "th", "main", "header", "footer"}
_SKIP = {"script", "style", "noscript", "nav", "aside", "footer", "form", "svg", "button", "head",
         "header", "select", "iframe"}
# Page furniture: running headers and footers, page numbers, and the bare item
# labels some filers print at the top of every page ("Item 1A", "Item 1B, 1C").
_FURNITURE = re.compile(r"^(table of contents|\d{1,3}|page \d+|part i{1,3}v?|item \d+[a-c]?(, \d+[a-c]?)*"
                        r"|.*\|\s*\d{4}\s+form 10-k\s*\|\s*\d+)$", re.I)


def _tag(el) -> str:
    return el.tag.split("}")[-1].lower() if isinstance(el.tag, str) else ""


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.text_content()).strip()


def _bold(el) -> bool:
    """Every piece of text in `el` sits in bold markup."""
    total = bold = 0
    for node in el.iter():
        pieces = [node.text] if node.text else []
        heavy = _tag(node) in {"b", "strong"} or re.search(r"font-weight:\s*(bold|[6-9]00)",
                                                           node.get("style", "") or "")
        for piece in pieces:
            n = len(piece.strip())
            total += n
            if heavy or any(_tag(a) in {"b", "strong"} or re.search(
                    r"font-weight:\s*(bold|[6-9]00)", a.get("style", "") or "") for a in node.iterancestors()):
                bold += n
    return total > 0 and bold == total


def _cells(tr) -> list[str]:
    cells = []
    for cell in tr:
        if _tag(cell) not in {"td", "th"}:
            continue
        text = _text(cell)
        if not text or text == "$":
            continue
        if text in {"%", ")", "%)"} and cells:
            cells[-1] += text
        else:
            cells.append(text.replace("|", "/"))
    return cells


def blocks_of(html: bytes) -> list[tuple[str, object]]:
    """The page's content in reading order: ("heading", text), ("text", text)
    or ("table", rows). Hidden XBRL headers, navigation and scripts are
    dropped; a block is an element holding no other block."""
    import lxml.html

    root = lxml.html.fromstring(html)
    main = root.find(".//main")
    root = main if main is not None else root
    for el in list(root.iter()):
        if _tag(el) in _SKIP or "display:none" in (el.get("style", "") or "").replace(" ", ""):
            el.drop_tree()
    out: list[tuple[str, object]] = []

    def walk(el):
        tag = _tag(el)
        if tag == "table":
            rows = [r for r in (_cells(tr) for tr in el.iter() if _tag(tr) == "tr") if r]
            if len(rows) == 1 and len(rows[0]) == 1:
                out.append(("text", rows[0][0]))
            elif rows:
                out.append(("table", rows))
            return
        if tag in _BLOCKS and not any(_tag(d) in _BLOCKS for d in el.iterdescendants()):
            text = _text(el)
            if text and not _FURNITURE.match(text):
                heading = tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or (
                    len(text) < 120 and not text.endswith((".", ":", ";", ",")) and _bold(el))
                out.append(("heading" if heading else "text", text))
            return
        for child in el:
            walk(child)

    walk(root)
    return out


_ITEMS = {  # 10-K item: (start, end) patterns on a heading or paragraph
    "1A": (r"^item\s*1a\b\.?\s*[-—:]?\s*risk factors", r"^item\s*(1b|1c|2)\b"),
    "7": (r"^item\s*7\b\.?\s*[-—:]?\s*management", r"^item\s*(7a|8)\b"),
}


def item_span(blocks: list, item: str) -> tuple[int, int] | None:
    """Indexes of one 10-K item: from its heading to the next item's. The
    table of contents names it too, so the longest span wins."""
    start_re, end_re = (re.compile(p, re.I) for p in _ITEMS[item])
    texts = [b[1] if b[0] != "table" else "" for b in blocks]
    best = None
    for i, text in enumerate(texts):
        if start_re.match(text.replace("’", "'")):
            end = next((j for j in range(i + 1, len(texts)) if end_re.match(texts[j])), len(texts))
            if best is None or end - i > best[1] - best[0]:
                best = (i, end)
    return best


def _markdown(blocks: list, level: int = 3) -> list[str]:
    lines = []
    for kind, value in blocks:
        if kind == "heading":
            lines.append(f"{'#' * level} {value}")
        elif kind == "text":
            lines.append(value)
        else:
            width = max(len(r) for r in value)
            rows = [r + [""] * (width - len(r)) for r in value]
            lines.append("\n".join(["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
                                   + ["| " + " | ".join(r) + " |" for r in rows[1:]]))
    return lines


def to_markdown(source: dict, html: bytes) -> str:
    """The document as Markdown: title, attribution, then the content (for a
    10-K, Items 1A and 7 each under their own heading)."""
    blocks = blocks_of(html)
    head = [f"# {source['title']}",
            ATTRIBUTION[source["kind"]].format(**{**source, "url": url_of(source)})]
    if source["kind"] != "sec_10k":
        body = [b for b in blocks if not (b[0] == "heading" and b[1] == source["title"])]
        # CFPB answers end in link lists ("Browse related questions", "Learn more about ...").
        end = next((i for i, b in enumerate(body) if b[0] == "heading"
                    and re.match(r"(browse related questions|learn more about)", b[1], re.I)), len(body))
        body = body[:end]
        return "\n\n".join(head + _markdown(body, level=2)) + "\n"
    parts = []
    for item in _ITEMS:
        span = item_span(blocks, item)
        if span is None:
            raise ValueError(f"{source['id']}: Item {item} not found")
        title, *rest = blocks[span[0]:span[1]]
        parts += [f"## {title[1]}"] + _markdown(rest)
    return "\n\n".join(head + parts) + "\n"


# --- documents and loading ------------------------------------------------------

@dataclass
class PublicDocument:
    file: str
    meta: dict
    chunks: list
    noise_share: float
    source: dict


def superseded_dates(sources: list[dict]) -> dict[str, object]:
    """Source id -> the filing date of the same company's next filing of the
    same kind (its 10-K for the next fiscal year), or None for the latest: so
    on any day the search sees the filing that existed then."""
    out = {}
    for s in sources:
        later = [o["filed"] for o in sources
                 if o.get("ticker") and o.get("ticker") == s.get("ticker") and o["kind"] == s["kind"]
                 and o.get("filed") and s.get("filed") and o["filed"] > s["filed"]]
        out[s["id"]] = min(later) if later else None
    return out


def load_public(sources: list[dict] | None = None) -> list:
    """Every cached source as a corpus document (bank.corpus.CorpusDocument)."""
    from bank.corpus import CorpusDocument, doc_id_for
    from ingestion.chunking import chunk_blocks
    from ingestion.noise import strip_boilerplate
    from ingestion.parse import parse_markdown

    parsed, meta = {}, {}
    sources = sources or load_sources()
    superseded = superseded_dates(sources)
    for source in sources:
        path = CACHE / f"{source['id']}.html"
        if not path.exists():
            raise SystemExit(f"{source['id']} isn't fetched: run `python -m bank.public fetch`")
        file = f"public/{source['id']}.md"
        parsed[file] = parse_markdown(to_markdown(source, path.read_bytes()))
        meta[file] = {"clearance": 0, "doc_type": DOC_TYPES[source["kind"]], "department": "public",
                      "version": str(source.get("version") or ""), "effective_date": source.get("filed"),
                      "superseded_on": superseded[source["id"]]}
    original = {f: sum(len(b.text) for b in blocks) for f, blocks in parsed.items()}
    removed = strip_boilerplate(parsed)
    return [CorpusDocument(file, meta[file], chunk_blocks(blocks, file, doc_id_for(file)),
                           removed[file] / max(1, original[file] + removed[file]))
            for file, blocks in parsed.items()]


def _load_command(no_metadata: bool) -> None:
    from bank.corpus import load
    from bank.synthetic import BANK_ORG
    from config import get_settings
    from db.access import Access
    from deps import get_embeddings, get_llm
    from ingestion.pacing import PacedEmbedder
    from ingestion.store import HybridStore
    from ingestion.metadata import load_cache, save_cache

    settings = get_settings()
    # Paced embeddings: a whole document's chunks and questions are embedded
    # at once, which can exceed the free tier's per-minute limits.
    store = HybridStore(settings, PacedEmbedder(get_embeddings())).scoped(
        Access(org_id=BANK_ORG, clearance=2, role="admin"))
    cache = load_cache()
    try:
        counts = asyncio.run(load(store, load_public(), llm=None if no_metadata else get_llm(),
                                  model=settings.grading_model, cache=cache))
    finally:
        save_cache(cache)  # keep what was paid for, even if a later step failed
    print(", ".join(f"{k} {v}" for k, v in counts.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch, show or load the public sources.")
    parser.add_argument("command", nargs="?", choices=["fetch", "load"])
    parser.add_argument("--no-metadata", action="store_true", help="skip the LLM metadata calls")
    args = parser.parse_args()
    if args.command == "fetch":
        from config import get_settings

        print("fetched:", ", ".join(fetch(load_sources(), get_settings().sec_user_agent)) or "nothing new")
        return 0
    if args.command == "load":
        _load_command(args.no_metadata)
        return 0
    from ingestion.chunking import tokens

    docs = load_public()
    print(f"{'document':44} {'sections':>8} {'chunks':>6} {'tokens':>8} {'<256':>5}")
    for d in docs:
        sizes = [tokens(c.text) for c in d.chunks]
        print(f"{d.file:44} {len({c.section for c in d.chunks}):8} {len(sizes):6} {sum(sizes):8} "
              f"{sum(s < 256 for s in sizes):5}")
    all_sizes = [tokens(c.text) for d in docs for c in d.chunks]
    print(f"{'total':44} {'':8} {len(all_sizes):6} {sum(all_sizes):8} {sum(s < 256 for s in all_sizes):5}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
