"""Build deposit-agreement.pdf from sources/deposit-agreement.md (the source of truth).

    backend/.venv/Scripts/python -m pip install reportlab==5.1.0   # this tool only
    backend/.venv/Scripts/python data/bank/tools/build_agreement_pdf.py

The corpus loads the PDF, not the Markdown, so the PDF parser
(ingestion/parse.py parse_pdf) works on a real bank document: a title,
numbered section headings, ruled tables, and on every page a running header
and a "Page N of M" footer for the noise removal to strip. The Markdown it
reads is the small subset the agreement uses: # and ## headings, paragraphs,
a block quote, **bold**, and pipe tables. The file's dates are fixed, so a
rebuild from the same source gives the same PDF.
"""
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BANK = Path(__file__).resolve().parents[1]
SOURCE, TARGET = BANK / "sources" / "deposit-agreement.md", BANK / "deposit-agreement.pdf"
HEADER = "FinPilot Bank Deposit Account Agreement"

# Vera ships with reportlab and has the agreement's few non-ASCII characters.
pdfmetrics.registerFont(TTFont("Vera", "Vera.ttf"))
pdfmetrics.registerFont(TTFont("Vera-Bold", "VeraBd.ttf"))
pdfmetrics.registerFontFamily("Vera", normal="Vera", bold="Vera-Bold", italic="Vera", boldItalic="Vera-Bold")

BODY = ParagraphStyle("body", fontName="Vera", fontSize=9.5, leading=13, spaceAfter=7)
QUOTE = ParagraphStyle("quote", parent=BODY, leftIndent=14, textColor=colors.HexColor("#444444"))
TITLE = ParagraphStyle("title", fontName="Vera-Bold", fontSize=18, leading=22, spaceAfter=10)
SECTION = ParagraphStyle("section", fontName="Vera-Bold", fontSize=13, leading=17, spaceBefore=8, spaceAfter=6,
                         keepWithNext=True)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.5, leading=11, spaceAfter=0)


def _inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


def _table(lines: list[str], width: float) -> Table:
    rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in lines if not line.startswith("|---")]
    data = [[Paragraph(_inline(c), CELL) for c in row] for row in rows]
    table = Table(data, colWidths=[width / len(rows[0])] * len(rows[0]), repeatRows=1, splitByRow=0)
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.6, colors.black),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return table


def story(markdown: str, width: float) -> list:
    out = []
    for para in re.split(r"\n\s*\n", markdown.strip()):
        lines = para.split("\n")
        if lines[0].startswith("# "):
            out.append(Paragraph(_inline(lines[0][2:]), TITLE))
        elif lines[0].startswith("## "):
            out.append(Paragraph(_inline(lines[0][3:]), SECTION))
        elif lines[0].startswith("|"):
            out += [_table(lines, width), Spacer(1, 8)]
        elif lines[0].startswith(">"):
            out.append(Paragraph(_inline(" ".join(line.lstrip("> ") for line in lines)), QUOTE))
        else:
            out.append(Paragraph(_inline(" ".join(lines)), BODY))
    return out


class _Numbered(SimpleDocTemplate):
    """Two passes, so the footer can say "Page N of M"."""

    total = 0

    def afterFlowable(self, flowable):
        _Numbered.total = max(_Numbered.total, self.page)


def _furniture(canvas, doc):
    canvas.saveState()
    canvas.setFont("Vera", 8)
    canvas.drawString(doc.leftMargin, LETTER[1] - 0.55 * inch, HEADER)
    canvas.drawCentredString(LETTER[0] / 2, 0.5 * inch, f"Page {doc.page} of {_Numbered.total or doc.page}")
    canvas.restoreState()


def build() -> None:
    markdown = SOURCE.read_text(encoding="utf-8")
    for _ in range(2):  # the first pass counts the pages
        doc = _Numbered(str(TARGET), pagesize=LETTER, leftMargin=inch, rightMargin=inch, topMargin=0.9 * inch,
                        bottomMargin=0.9 * inch, title="Deposit Account Agreement", author="FinPilot Bank (fictional)",
                        invariant=1)
        doc.build(story(markdown, doc.width), onFirstPage=_furniture, onLaterPages=_furniture)
    print(f"Wrote {TARGET} ({_Numbered.total} pages)")


if __name__ == "__main__":
    build()
