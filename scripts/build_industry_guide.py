"""Build the detailed PDF manual from its editable Markdown source.

Install requirements-docs.txt for manual authoring. Analysis has no dependency
on this script or on the documentation packages.
"""

from html import escape
from pathlib import Path
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, PageBreak,
    Preformatted, Table, TableStyle, CondPageBreak,
)
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/INDUSTRY_AND_USER_GUIDE.md"
TARGET = ROOT / "docs/Mini_STAAD_Implementation_and_Industry_Guide.pdf"
WIDTH, HEIGHT = A4
MARGIN = 48
CONTENT_WIDTH = WIDTH - 2*MARGIN


def inline(text):
    text = escape(text)
    text = re.sub(r"`([^`]+)`", r'<font name="Courier" size="8.8">\1</font>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<link href="\2" color="#24546b">\1</link>', text)
    return text


def styles():
    s = getSampleStyleSheet()
    s["Normal"].fontName = "Helvetica"
    s["Normal"].fontSize = 10.2
    s["Normal"].leading = 14.2
    s["Normal"].textColor = colors.HexColor("#253038")
    s["Normal"].spaceAfter = 8
    for name, size, leading in [("Title", 26, 31), ("Heading1", 19, 24), ("Heading2", 12.5, 17)]:
        s[name].fontName = "Helvetica-Bold"
        s[name].fontSize = size
        s[name].leading = leading
        s[name].textColor = colors.black
        s[name].alignment = TA_LEFT
        s[name].spaceBefore = 12 if name != "Title" else 0
        s[name].spaceAfter = 10
        s[name].keepWithNext = True
    s.add(ParagraphStyle("ManualCode", fontName="Courier", fontSize=8.5, leading=10.5,
                         spaceBefore=5, spaceAfter=10, leftIndent=9, rightIndent=9,
                         backColor=colors.HexColor("#f3f5f6"), borderPadding=8))
    s.add(ParagraphStyle("ManualCell", parent=s["Normal"], fontSize=8.7, leading=11.4,
                         spaceAfter=0, spaceBefore=0))
    s.add(ParagraphStyle("ManualHeaderCell", parent=s["ManualCell"], textColor=colors.white,
                         fontName="Helvetica-Bold"))
    s.add(ParagraphStyle("ManualList", parent=s["Normal"], leftIndent=16, firstLineIndent=-13,
                         spaceAfter=5))
    s.add(ParagraphStyle("ManualTOC", fontName="Helvetica", fontSize=11, leading=20,
                         textColor=colors.black, leftIndent=0, firstLineIndent=0, spaceBefore=4))
    return s


class ManualDocument(BaseDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name == "Heading1":
            title = flowable.getPlainText()
            if title != "Contents":
                key = "chapter-" + re.sub(r"[^a-z0-9]+", "-", title.lower())
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(title, key, 0)
                self.notify("TOCEntry", (0, title, self.page, key))


def page_decoration(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#65727b"))
    canvas.drawString(MARGIN, 26, "MINI STAAD  |  v0.3.0  |  Implementation and industry guide")
    canvas.drawRightString(WIDTH-MARGIN, 26, str(doc.page))
    canvas.restoreState()


def table(rows, s):
    count = len(rows[0])
    ratios = {2: [.34,.66], 3:[.28,.29,.43], 4:[.31,.25,.22,.22], 5:[.12,.13,.17,.16,.42]}[count]
    widths = [CONTENT_WIDTH*r for r in ratios]
    cells = [[Paragraph(inline(cell), s["ManualHeaderCell" if i == 0 else "ManualCell"])
              for cell in row] for i,row in enumerate(rows)]
    t = Table(cells, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#274653")),
        ("GRID", (0,0), (-1,-1), .45, colors.HexColor("#d9d9d9")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING", (0,0), (-1,-1), 7),
        ("RIGHTPADDING", (0,0), (-1,-1), 7),
        ("TOPPADDING", (0,0), (-1,-1), 7),
        ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f6f8f9")]),
    ]))
    return t


def build():
    s = styles()
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    story, i = [], 0
    after_contents = False
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line == "<!-- pagebreak -->":
            story.append(PageBreak() if after_contents else CondPageBreak(170))
            after_contents = False
            i += 1; continue
        if line == "## Contents":
            story.extend([PageBreak(), Paragraph("Contents", s["Heading1"])])
            toc = TableOfContents(); toc.levelStyles = [s["ManualTOC"]]
            story.append(toc)
            after_contents = True
            i += 1
            while i < len(lines) and lines[i].strip() != "<!-- pagebreak -->": i += 1
            continue
        if line.startswith("```"):
            code = []; i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i]); i += 1
            if any(len(record) > 90 for record in code):
                raise ValueError("Manual code lines must be explicitly broken at 90 characters")
            story.append(Preformatted("\n".join(code), s["ManualCode"]))
            i += 1; continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = [v.strip() for v in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r"[-: ]+", v) for v in row): rows.append(row)
                i += 1
            story.extend([table(rows,s), Spacer(1,12)])
            continue
        if line.startswith("#"):
            hashes = len(line)-len(line.lstrip("#"))
            story.append(Paragraph(inline(line[hashes:].strip()), s[{1:"Title",2:"Heading1",3:"Heading2"}[hashes]]))
            i += 1; continue
        if re.match(r"^\d+\. ", line):
            story.append(Paragraph(inline(line), s["ManualList"]))
            i += 1; continue
        paragraph = [line]; i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].lstrip().startswith(("#","|","```","<!--")) and not re.match(r"^\d+\. ",lines[i].strip()):
            paragraph.append(lines[i].strip()); i += 1
        story.append(Paragraph(inline(" ".join(paragraph)),s["Normal"]))
    document = ManualDocument(str(TARGET), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                              topMargin=43, bottomMargin=43, title="Mini STAAD implementation and industry user guide",
                              author="Mini STAAD project", subject="Version 0.3.0 capabilities, workflows and validation")
    frame = Frame(MARGIN,43,CONTENT_WIDTH,HEIGHT-86,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
    document.addPageTemplates(PageTemplate(id="manual",frames=frame,onPage=page_decoration))
    document.multiBuild(story)
    from pypdf import PdfReader
    reader = PdfReader(TARGET)
    print(f"Created {TARGET.name}: {len(reader.pages)} pages, {TARGET.stat().st_size} bytes")


if __name__ == "__main__":
    build()
