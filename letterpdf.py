"""Render a saved cover letter (from coverletter.py) into a polished PDF
matching the existing Century Schoolbook InDesign template.

This does no AI work at all and never touches the saved .md — it just reads
whatever is currently there (draft, revised, or hand-edited) and lays it into
the template. Run it whenever the text is finalized; rerun anytime after an
edit. The one exception is the signature block: "Sincerely," + your name is
hardcoded here rather than trusted to the saved text, so coverletter.py's
prompt no longer asks the model to write a sign-off at all (see
build_prompt's OTHER INSTRUCTIONS) — this avoids ever ending up with two.

Template values (page size, margins, fonts, sizes, tracking) were pulled
directly from the candidate's real InDesign template (an .idml export),
not eyeballed from a rendered PDF, so this should match closely on the
first try. Century Schoolbook is a Windows/Office-bundled font — this reads
the .ttf files straight out of C:\\Windows\\Fonts, so it only runs on a
machine that actually has that font installed (true of the author's own
machine; there's no bundled fallback because embedding a licensed font in
this repo isn't appropriate).

Usage:
    python letterpdf.py <row-# or job-id>
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer

import config
import coverletter
import db

_PAGE_W, _PAGE_H = LETTER  # 612 x 792 — matches the template's Letter-size page
_MARGIN = 36  # pt — matches the template's 0.5in margins on all sides

# PDFs live alongside the .md files but in their own subfolder, so a
# directory listing of digests/cover_letters/ stays one draft-text file per
# job rather than mixing in a second, regenerable-anytime artifact per job.
_PDF_DIR = coverletter._OUT_DIR / "pdf"

_FONT_DIR = Path(r"C:\Windows\Fonts")
_FONT_FILES = {
    "CenturySchoolbook": "CENSCBK.TTF",
    "CenturySchoolbook-Bold": "SCHLBKB.TTF",
    "CenturySchoolbook-Italic": "SCHLBKI.TTF",
    "CenturySchoolbook-BoldItalic": "SCHLBKBI.TTF",
    "Century": "CENTURY.TTF",
}

_NAME_SIZE = 34
_NAME_TRACKING = 0.150 * _NAME_SIZE  # IDML Tracking="150" = 0.150 em
_PRONOUN_SIZE = 8
_TEXT_SIZE = 11
_LEADING = 15.5

# Named so a future "custom colors" pass (e.g. an accent color) is a
# constant swap here, not a hunt through draw calls. All three currently
# come straight from the IDML template, not a design choice made here.
_INK = (0, 0, 0)
_MUTED = (89 / 255, 90 / 255, 92 / 255)  # IDML Color/u23d, used for the pronoun tag
_LINK = (79 / 255, 92 / 255, 214 / 255)  # IDML Color/Hyperlink

# A footer rule mirroring the header one — not part of the original InDesign
# template, added deliberately so a shorter letter's trailing whitespace
# reads as a framed page rather than as trailing off. FOOTER_GAP is the
# rule's distance above the bottom margin; FOOTER_CLEARANCE is the extra
# buffer kept between the last line of body text and the rule itself.
_FOOTER_GAP = 20
_FOOTER_CLEARANCE = 14

_registered = False


class LetterPDFError(Exception):
    """A PDF-rendering step failed for a reason worth showing the user
    (missing font, no saved letter yet)."""


def _register_fonts() -> None:
    global _registered
    if _registered:
        return
    for name, filename in _FONT_FILES.items():
        path = _FONT_DIR / filename
        if not path.exists():
            raise LetterPDFError(
                f"Couldn't find {filename} in {_FONT_DIR} — Century Schoolbook "
                "needs to be installed (it ships with Windows/Office) to render "
                "this template.")
        pdfmetrics.registerFont(TTFont(name, str(path)))
    _registered = True


def _draw_tracked(c, x: float, y: float, text: str, font: str, size: float,
                   tracking: float) -> None:
    """Draw text with extra space after each character — reportlab's
    Paragraph/Canvas have no native letter-tracking, so this steps through
    manually. Matches the template's Tracking="150" on the name header."""
    c.setFont(font, size)
    cx = x
    for ch in text:
        c.drawString(cx, y, ch)
        cx += c.stringWidth(ch, font, size) + tracking


def _parse_drafted_date(header: str) -> str:
    """Pull the 'Drafted: <Month Day, Year>' line out of a saved letter's
    header comment and return it as ISO (matching the template's date
    format), so regenerating a PDF later doesn't change the date on a
    letter that's already been sent. Falls back to today if unparseable."""
    m = re.search(r"Drafted:\s*(.+)", header)
    if m:
        try:
            return datetime.strptime(m.group(1).strip(), "%B %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    return datetime.today().strftime("%Y-%m-%d")


def _paragraphs(body: str) -> list[str]:
    """Split saved letter text into paragraphs on blank lines, collapsing
    any single newlines within a paragraph to spaces so reportlab re-wraps
    based on the page width rather than the source file's own line breaks."""
    chunks = re.split(r"\n\s*\n", body.strip())
    return [re.sub(r"\s*\n\s*", " ", chunk).strip() for chunk in chunks if chunk.strip()]


def _draw_header(c, job, letterhead: dict, drafted: str) -> float:
    """Draw the fixed header block (pronoun, name, rule, contact row, date,
    company) and return the y-coordinate the body text should start below."""
    x = _MARGIN
    y = _PAGE_H - _MARGIN

    pronoun = letterhead.get("pronouns", "")
    if pronoun:
        y -= _PRONOUN_SIZE + 4
        c.setFont("Century", _PRONOUN_SIZE)
        c.setFillColorRGB(*_MUTED)
        c.drawString(x, y, pronoun)
        c.setFillColorRGB(*_INK)

    y -= _NAME_SIZE
    name = (letterhead.get("name") or "").upper()
    _draw_tracked(c, x, y, name, "CenturySchoolbook-Bold", _NAME_SIZE, _NAME_TRACKING)

    y -= 10
    c.setLineWidth(0.25)  # IDML GraphicLine StrokeWeight="0.25"
    c.line(x, y, _PAGE_W - _MARGIN, y)

    y -= 8 + _TEXT_SIZE
    c.setFont("CenturySchoolbook", _TEXT_SIZE)
    contact = [(k, letterhead.get(k, "")) for k in ("phone", "email", "location", "website")]
    contact = [(k, v) for k, v in contact if v]
    if contact:
        # Distribute with even gaps sized to the ACTUAL string widths (not a
        # fixed column grid) — first item at the left margin, last ending
        # exactly at the right margin, no overlap regardless of how long any
        # individual value (e.g. an email address) happens to be.
        widths = [c.stringWidth(v, "CenturySchoolbook", _TEXT_SIZE) for _, v in contact]
        available = _PAGE_W - 2 * _MARGIN
        gap = (available - sum(widths)) / (len(contact) - 1) if len(contact) > 1 else 0
        cx = x
        for (key, value), w in zip(contact, widths):
            if key == "website":
                # Matches the template's own defined Hyperlink character
                # style rather than introducing a new color.
                c.setFillColorRGB(*_LINK)
                c.drawString(cx, y, value)
                c.setFillColorRGB(*_INK)
                url = value if re.match(r"^https?://", value) else f"https://{value}"
                c.linkURL(url, (cx, y - 2, cx + w, y + _TEXT_SIZE), relative=0)
            else:
                c.drawString(cx, y, value)
            cx += w + gap

    y -= 32
    c.setFont("CenturySchoolbook", _TEXT_SIZE)
    c.drawString(x, y, drafted)
    y -= _LEADING
    c.drawString(x, y, job.company or "the organization")

    return y - 20


def _draw_footer(c) -> None:
    """Thin rule near the bottom margin, mirroring the header rule, so a
    shorter letter's leftover whitespace reads as a deliberately framed
    page rather than as the letter trailing off early."""
    y = _MARGIN + _FOOTER_GAP
    c.setLineWidth(0.25)
    c.line(_MARGIN, y, _PAGE_W - _MARGIN, y)


def render_letter_pdf(job, cfg: dict) -> Path:
    """Render the job's currently-saved letter into a PDF, returning its
    path. Raises LetterPDFError if there's no saved letter yet, or if the
    required fonts aren't installed."""
    _register_fonts()

    path = coverletter.letter_path(job)
    if not path.exists():
        raise LetterPDFError(
            f"No saved letter at {path} — draft one first: "
            f"python coverletter.py {job.id}")
    header, body = coverletter._split_letter(path.read_text(encoding="utf-8"))
    drafted = _parse_drafted_date(header)

    letterhead = cfg.get("letterhead", {})
    _PDF_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _PDF_DIR / path.with_suffix(".pdf").name

    doc = BaseDocTemplate(str(out_path), pagesize=LETTER,
                           leftMargin=_MARGIN, rightMargin=_MARGIN,
                           topMargin=_MARGIN, bottomMargin=_MARGIN)

    body_style = ParagraphStyle(
        "Body", fontName="CenturySchoolbook", fontSize=_TEXT_SIZE,
        leading=_LEADING, alignment=TA_LEFT, spaceAfter=_LEADING,
    )
    bold_style = ParagraphStyle(
        "Bold", parent=body_style, fontName="CenturySchoolbook-Bold", spaceAfter=0,
    )

    flowables = [Paragraph(escape(p), body_style) for p in _paragraphs(body)]
    flowables += [
        Spacer(1, _LEADING),
        Paragraph("Sincerely,", body_style),
        Spacer(1, _LEADING),
        Paragraph(escape(letterhead.get("name", "")), bold_style),
    ]

    # Header is drawn directly on the canvas (needs manual letter-tracking on
    # the name, which Paragraph can't do); the body flows in a Frame below it.
    # header_bottom is computed once against a throwaway canvas measurement
    # since the Frame's geometry has to be fixed before the doc builds, but
    # the header itself is actually drawn per-page in onFirstPage.
    from reportlab.pdfgen.canvas import Canvas
    _measure = Canvas(str(out_path))
    header_bottom = _draw_header(_measure, job, letterhead, drafted)

    frame_bottom = _MARGIN + _FOOTER_GAP + _FOOTER_CLEARANCE
    frame = Frame(_MARGIN, frame_bottom, _PAGE_W - 2 * _MARGIN, header_bottom - frame_bottom,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    def _on_first_page(c, _doc):
        c.saveState()
        _draw_header(c, job, letterhead, drafted)
        _draw_footer(c)
        c.restoreState()

    doc.addPageTemplates([
        PageTemplate(id="first", frames=[frame], onPage=_on_first_page),
    ])
    doc.build(flowables)
    return out_path


class _JobArg:
    """Thin re-use of coverletter.py's row-#/job-id resolution and CLI exit
    behavior, so this script's usage matches it exactly."""


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("\n  Usage: python letterpdf.py <row-# or job-id>\n")
        sys.exit(1)

    job = coverletter._resolve_or_exit(args[0])
    cfg = config.load_config()
    try:
        path = render_letter_pdf(job, cfg)
    except LetterPDFError as e:
        print(f"\n  {e}\n")
        sys.exit(1)
    print(f"\n  Saved: {path}\n")


if __name__ == "__main__":
    main()
