"""Structure-aware parsers: each source type becomes a list of Blocks.

A Block is the smallest unit the chunker may not split unless it alone is
too big: a paragraph, a list item, a whole table, or one API operation.
Each block knows its section (the heading path above it), so a chunk can
say where it came from and Phase 4 can join a chunk to its parent section.

  - Markdown: headings set the section; tables (lines starting with "|")
    stay whole; lists split into items.
  - CSV: one table per effective_date when the column exists (versions
    never share a chunk), otherwise one table.
  - XLSX: per sheet, the header is the widest row (published sheets have
    title rows above it); rows above and below it are text.
  - OpenAPI YAML: one block per path and method; schemas one block each.
  - PDF, DOCX, TXT: the existing extractors (extract.py), one block per
    paragraph, keeping the page number.
"""
import csv
import io
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

import yaml

from config import Settings
from ingestion.extract import IngestionError, PageText, validate_and_extract


@dataclass
class Block:
    text: str
    section: str = ""
    kind: str = "text"   # text | item | table | operation
    page_number: int = 1
    header: str = ""     # tables: the header lines, repeated when a table is split
    part: int = 0        # tables: 0 for a whole table or its first part, then 1, 2, ...


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_LIST_ITEM = re.compile(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+")


def _join_path(path: list[str]) -> str:
    return " > ".join(p for p in path if p)


def parse_markdown(text: str) -> list[Block]:
    blocks: list[Block] = []
    path: list[str] = []
    for para in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        lines = [line for line in para.split("\n") if line.strip()]
        while lines and (m := _HEADING.match(lines[0])):
            level = len(m.group(1))
            path = path[: level - 1] + [""] * (level - 1 - len(path)) + [m.group(2)]
            lines = lines[1:]
        if not lines:
            continue
        section = _join_path(path)
        if all(line.lstrip().startswith("|") for line in lines):
            blocks.append(Block("\n".join(lines[2:]), section, "table", header="\n".join(lines[:2])))
        elif _LIST_ITEM.match(lines[0]):
            items: list[list[str]] = []
            for line in lines:
                if _LIST_ITEM.match(line) or not items:
                    items.append([line])
                else:
                    items[-1].append(line)
            blocks.extend(Block("\n".join(item), section, "item") for item in items)
        else:
            blocks.append(Block("\n".join(lines), section))
    return blocks


def _cell(value) -> str:
    return "" if value is None else str(value).replace("|", "/").strip()


def _table_block(header: list, rows: list[list], section: str) -> Block:
    head = "| " + " | ".join(_cell(h) for h in header) + " |"
    rule = "|" + "---|" * len(header)
    body = "\n".join("| " + " | ".join(_cell(v) for v in row) + " |" for row in rows)
    return Block(body, section, "table", header=f"{head}\n{rule}")


def parse_csv(text: str, title: str) -> list[Block]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header, data = rows[0], [r for r in rows[1:] if any(c.strip() for c in r)]
    if "effective_date" not in header:
        return [_table_block(header, data, title)]
    col = header.index("effective_date")
    groups: dict[str, list[list[str]]] = {}
    for row in data:
        groups.setdefault(row[col], []).append(row)
    return [_table_block(header, group, f"{title} > effective {when}") for when, group in groups.items()]


def parse_xlsx(data: bytes) -> list[Block]:
    from openpyxl import load_workbook

    blocks: list[Block] = []
    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    for sheet in workbook.worksheets:
        rows = [list(r) for r in sheet.iter_rows(values_only=True)]
        filled = [sum(v is not None for v in r) for r in rows]
        if not rows or max(filled) == 0:
            continue
        head = filled.index(max(filled))
        end = head + 1
        while end < len(rows) and filled[end]:
            end += 1
        width = max(i + 1 for i, v in enumerate(rows[head]) if v is not None)

        def text_of(part: list[list]) -> str:
            return "\n".join(" ".join(_cell(v) for v in r if v is not None) for r in part if any(r))

        if above := text_of(rows[:head]):
            blocks.append(Block(above, sheet.title))
        blocks.append(_table_block(rows[head][:width], [r[:width] for r in rows[head + 1:end]], sheet.title))
        if below := text_of(rows[end:]):
            blocks.append(Block(below, sheet.title))
    return blocks


_METHODS = ("get", "put", "post", "delete", "patch", "head", "options")


def parse_openapi(text: str) -> list[Block]:
    spec = yaml.safe_load(text)
    info = spec.get("info", {})
    title = info.get("title", "API")
    blocks = [Block(yaml.safe_dump({"info": info, "servers": spec.get("servers", [])},
                                   sort_keys=False, allow_unicode=True).strip(), title)]
    for path, item in (spec.get("paths") or {}).items():
        for method in _METHODS:
            if op := item.get(method):
                head = f"{method.upper()} {path}"
                body = yaml.safe_dump(op, sort_keys=False, allow_unicode=True).strip()
                blocks.append(Block(f"{head}\n{body}", f"{title} > {head}", "operation"))
    for name, schema in ((spec.get("components") or {}).get("schemas") or {}).items():
        body = yaml.safe_dump({name: schema}, sort_keys=False, allow_unicode=True).strip()
        blocks.append(Block(body, f"{title} > schemas"))
    return blocks


def parse_pages(pages: list[PageText]) -> list[Block]:
    return [Block(para.strip(), "", "text", page.page_number)
            for page in pages for para in re.split(r"\n\s*\n", page.text) if para.strip()]


_PDF_BODY_RATIO = 1.25  # a line this much larger than the body text is a heading
_SENTENCE_END = re.compile(r"[.:;!?)\"”|]\s*$")


def pdf_pages(data: bytes, max_pages: int) -> list[PageText]:
    """A PDF's pages as Markdown, structure recovered from the layout:
    lines much larger than the body text become headings (the largest size
    is the title), ruled tables become pipe tables, and a gap between lines
    is a paragraph break. Headers and footers are still in the text, for
    noise.strip_page_furniture."""
    import pdfplumber

    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:
        raise IngestionError("Could not read the PDF (is it damaged or password-protected?).") from exc
    with pdf:
        if len(pdf.pages) > max_pages:
            raise IngestionError(f"PDF has {len(pdf.pages)} pages; the limit is {max_pages}.")
        laid_out = []  # per page: [(top, kind, payload)]
        sizes: Counter[float] = Counter()
        for page in pdf.pages:
            tables = page.find_tables()
            boxes = [t.bbox for t in tables]

            def outside(obj, boxes=boxes):
                return not any(x0 <= obj["x0"] and obj["x1"] <= x1 and top <= obj["top"] and obj["bottom"] <= bottom
                               for x0, top, x1, bottom in boxes)

            items = [(t.bbox[1], "table", t.extract()) for t in tables]
            for line in page.filter(outside).extract_text_lines():
                size = round(max(c["size"] for c in line["chars"]), 1)
                sizes[size] += len(line["text"])
                items.append((line["top"], "line", (line["text"].strip(), size, line["bottom"])))
            laid_out.append(sorted(items, key=lambda item: item[0]))
    if not sizes:
        raise IngestionError("No extractable text in the PDF (a scan needs OCR, which isn't supported).")
    body = sizes.most_common(1)[0][0]
    title = max(sizes)

    pages = []
    for number, items in enumerate(laid_out, start=1):
        out, last_bottom = [], None
        for top, kind, payload in items:
            if kind == "table":
                rows = [[_cell((c or "").replace("\n", " ")) for c in row] for row in payload if row]
                if rows:
                    block = _table_block(rows[0], rows[1:], "")
                    out += ["", block.header, block.text, ""]
                last_bottom = None
                continue
            text, size, bottom = payload
            if size >= body * _PDF_BODY_RATIO:
                out += ["", ("# " if size == title else "## ") + text, ""]
            else:
                if last_bottom is not None and top - last_bottom > size * 0.45:
                    out.append("")
                out.append(text)
            last_bottom = bottom
        pages.append(PageText(number, "\n".join(out).strip()))
    return pages


def parse_pdf(data: bytes, settings: Settings) -> tuple[list[Block], int]:
    """A PDF through the Markdown parser: pages as Markdown, page furniture
    stripped, and a paragraph that runs over a page break joined again."""
    from ingestion.noise import strip_page_furniture

    pages, removed = strip_page_furniture(pdf_pages(data, settings.max_pages))
    text = ""
    for page in pages:
        body = page.text.strip()
        if body:
            joined = text and not _SENTENCE_END.search(text) and not body.startswith(("#", "|"))
            text += ("\n" if joined else "\n\n") + body
    return parse_markdown(text), removed


def parse_file(data: bytes, filename: str, settings: Settings) -> tuple[list[Block], int]:
    """Dispatch on the extension; returns the blocks and the characters of
    noise removed (page furniture, navigation lines)."""
    from ingestion.noise import strip_nav_lines, strip_page_furniture

    name = PurePosixPath(filename.replace("\\", "/")).name
    suffix = PurePosixPath(name).suffix.lower()
    if suffix == ".md":
        return strip_nav_lines(parse_markdown(data.decode("utf-8-sig")))
    if suffix == ".csv":
        return parse_csv(data.decode("utf-8-sig"), PurePosixPath(name).stem), 0
    if suffix == ".xlsx":
        return parse_xlsx(data), 0
    if suffix in (".yaml", ".yml"):
        return parse_openapi(data.decode("utf-8-sig")), 0
    if suffix == ".pdf":
        blocks, removed = parse_pdf(data, settings)
        blocks, nav = strip_nav_lines(blocks)
        return blocks, removed + nav
    pages, _ = validate_and_extract(data, name, settings)
    pages, removed = strip_page_furniture(pages)
    blocks, nav = strip_nav_lines(parse_pages(pages))
    return blocks, removed + nav
