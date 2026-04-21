import os
import re
import random

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate,
    Paragraph, Spacer, HRFlowable, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── Color palette ─────────────────────────────────────────────────────────────
INDIGO      = colors.HexColor("#6366f1")
INDIGO_DARK = colors.HexColor("#4f46e5")
INDIGO_LIGHT = colors.HexColor("#e0e7ff")
VIOLET      = colors.HexColor("#8b5cf6")
SLATE_900   = colors.HexColor("#0f172a")
SLATE_600   = colors.HexColor("#475569")
SLATE_400   = colors.HexColor("#94a3b8")
SLATE_100   = colors.HexColor("#f1f5f9")
WHITE       = colors.white

# ── Font registration ─────────────────────────────────────────────────────────
_FONT_PATHS_REG = [
    # Linux (Debian/Ubuntu)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/DejaVuSans.ttf",
    # macOS
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    # Bundled fallback
    os.path.join(os.path.dirname(__file__), "..", "data", "DejaVuSans.ttf"),
]
_FONT_PATHS_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/DejaVuSans-Bold.ttf",
    # macOS (use same file for bold; reportlab will synthesize bold)
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    os.path.join(os.path.dirname(__file__), "..", "data", "DejaVuSans-Bold.ttf"),
]

_fonts_registered = False

def _register_fonts():
    global _fonts_registered
    if _fonts_registered:
        return True

    reg_path = next((p for p in _FONT_PATHS_REG if os.path.exists(p)), None)
    bold_path = next((p for p in _FONT_PATHS_BOLD if os.path.exists(p)), None)

    if not reg_path:
        # Try to download DejaVu fonts from the official release archive
        try:
            reg_path, bold_path = _download_fonts()
        except Exception as e:
            print(f"❌ Could not find or download fonts: {e}")
            return False

    try:
        pdfmetrics.registerFont(TTFont("MedReg", reg_path))
        pdfmetrics.registerFont(TTFont("MedBold", bold_path or reg_path))
        from reportlab.pdfbase.ttfonts import TTFontFace
        from reportlab.lib.fonts import addMapping
        addMapping("Med", 0, 0, "MedReg")
        addMapping("Med", 1, 0, "MedBold")
        _fonts_registered = True
        return True
    except Exception as e:
        print(f"❌ Font registration failed: {e}")
        return False


def _download_fonts():
    """Download DejaVu fonts from the official GitHub release."""
    import urllib.request, tarfile, io

    url = "https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.tar.bz2"
    os.makedirs("data", exist_ok=True)
    reg_path  = os.path.join("data", "DejaVuSans.ttf")
    bold_path = os.path.join("data", "DejaVuSans-Bold.ttf")

    if os.path.exists(reg_path) and os.path.exists(bold_path):
        return reg_path, bold_path

    print("⏳ Downloading DejaVu fonts archive (~7 MB)…")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2") as tar:
        for member in tar.getmembers():
            if member.name.endswith("DejaVuSans.ttf"):
                with tar.extractfile(member) as f:
                    open(reg_path, "wb").write(f.read())
            elif member.name.endswith("DejaVuSans-Bold.ttf"):
                with tar.extractfile(member) as f:
                    open(bold_path, "wb").write(f.read())

    print("✅ Fonts downloaded.")
    return reg_path, bold_path


# ── Text normalizer ───────────────────────────────────────────────────────────
_EMOJI_MAP = {
    "🔹": "•", "📌": "▶", "✅": "✓", "❌": "✗",
    "🔬": "◈", "📖": "§", "🧬": "◆", "💊": "○",
}

def _normalize(text: str) -> str:
    for emoji, rep in _EMOJI_MAP.items():
        text = text.replace(emoji, rep)
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text)
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", text)
    # Escape reportlab XML special chars
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.strip()


def _classify_line(line: str):
    s = line.strip()
    if not s:
        return "blank", ""
    if s.startswith(("▶", "◈", "§", "◆")):
        return "heading", re.sub(r"^[▶◈§◆]\s*", "", s)
    if re.match(r"^\d+[\.\)]\s+", s):
        return "numbered", re.sub(r"^\d+[\.\)]\s+", "", s, count=1)
    if s.startswith(("•", "-", "–", "—")):
        return "bullet", re.sub(r"^[•\-–—]\s*", "", s, count=1)
    return "plain", s


# ── PDF localization ──────────────────────────────────────────────────────────
PDF_I18N = {
    "ru": {
        "subtitle": "Учебный материал",
        "page": "Страница",
        "default_topic": "Медицинская теория",
    },
    "en": {
        "subtitle": "Study material",
        "page": "Page",
        "default_topic": "Medical theory",
    },
    "uz": {
        "subtitle": "O'quv materiali",
        "page": "Sahifa",
        "default_topic": "Tibbiy nazariya",
    },
}


def _get_pdf_labels(lang: str):
    return PDF_I18N.get(lang, PDF_I18N["en"])


# ── Page canvas callbacks ─────────────────────────────────────────────────────
def _draw_cover_header(canvas, doc):
    """Indigo cover strip on page 1."""
    canvas.saveState()
    w, h = A4

    # Dark background strip
    canvas.setFillColor(INDIGO_DARK)
    canvas.rect(0, h - 55*mm, w, 55*mm, fill=1, stroke=0)
    # Lighter foreground strip
    canvas.setFillColor(INDIGO)
    canvas.rect(0, h - 50*mm, w, 50*mm, fill=1, stroke=0)

    # "AI STUDY ASSISTANT" label
    canvas.setFont("MedBold", 11)
    canvas.setFillColor(WHITE)
    canvas.drawCentredString(w / 2, h - 16*mm, "AI STUDY ASSISTANT")

    # Subtitle
    canvas.setFont("MedReg", 8.5)
    canvas.setFillColor(colors.HexColor("#c7d2fe"))  # indigo-200
    canvas.drawCentredString(w / 2, h - 23*mm, doc.subtitle)

    # Topic title (wrap if long)
    topic = doc.topic[:80] + ("…" if len(doc.topic) > 80 else "")
    canvas.setFont("MedBold", 15)
    canvas.setFillColor(WHITE)
    canvas.drawCentredString(w / 2, h - 36*mm, topic)

    canvas.restoreState()


def _draw_page_header(canvas, doc):
    """Thin indigo bar on continuation pages."""
    canvas.saveState()
    w, h = A4
    canvas.setFillColor(INDIGO)
    canvas.rect(0, h - 8*mm, w, 8*mm, fill=1, stroke=0)

    canvas.setFont("MedReg", 7.5)
    canvas.setFillColor(WHITE)
    canvas.drawRightString(w - 15*mm, h - 5.5*mm,
                           f"AI Study Assistant  |  {doc.topic[:55]}")
    canvas.restoreState()


def _draw_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("MedReg", 8)
    canvas.setFillColor(SLATE_400)
    canvas.drawCentredString(A4[0] / 2, 8*mm, f"{doc.page_label} {doc.page}")
    canvas.restoreState()


def _page1(canvas, doc):
    _draw_cover_header(canvas, doc)
    _draw_footer(canvas, doc)


def _page_n(canvas, doc):
    _draw_page_header(canvas, doc)
    _draw_footer(canvas, doc)


# ── Build PDF ─────────────────────────────────────────────────────────────────
def generate_theory_pdf(text: str, user_id: int, topic: str = None, lang: str = "en") -> str:
    if not _register_fonts():
        print("❌ Fonts unavailable — PDF skipped.")
        return None

    os.makedirs("data", exist_ok=True)
    out_path = os.path.join("data", f"Theory_{user_id}_{random.randint(1000, 9999)}.pdf")

    labels = _get_pdf_labels(lang)
    topic_clean = _normalize(topic or labels["default_topic"])

    # ── Styles ────────────────────────────────────────────────────────────────
    heading_style = ParagraphStyle(
        "Heading",
        fontName="MedBold",
        fontSize=12,
        leading=16,
        textColor=INDIGO,
        spaceBefore=10,
        spaceAfter=4,
        leftIndent=8,
        borderPad=0,
    )
    body_style = ParagraphStyle(
        "Body",
        fontName="MedReg",
        fontSize=11,
        leading=16,
        textColor=SLATE_900,
        spaceBefore=2,
        spaceAfter=4,
        leftIndent=0,
    )
    bullet_style = ParagraphStyle(
        "Bullet",
        fontName="MedReg",
        fontSize=11,
        leading=15,
        textColor=SLATE_900,
        leftIndent=16,
        firstLineIndent=0,
        spaceBefore=1,
        spaceAfter=2,
        bulletText="•",
        bulletFontName="MedBold",
        bulletFontSize=11,
        bulletColor=VIOLET,
        bulletIndent=4,
    )
    numbered_style = ParagraphStyle(
        "Numbered",
        parent=bullet_style,
        leftIndent=20,
        bulletColor=INDIGO,
    )

    # ── Document setup ────────────────────────────────────────────────────────
    class _Doc(BaseDocTemplate):
        def __init__(self, *args, **kwargs):
            self.topic = topic_clean
            self.subtitle = labels["subtitle"]
            self.page_label = labels["page"]
            super().__init__(*args, **kwargs)

    doc = _Doc(
        out_path,
        pagesize=A4,
        title=topic_clean,
        author="AI Study Assistant",
    )

    w, h = A4
    margin_lr = 18*mm
    top_margin_p1 = 58*mm   # leave room for the cover strip
    top_margin_pn = 14*mm   # leave room for thin header bar
    bot_margin    = 18*mm

    frame1 = Frame(margin_lr, bot_margin, w - 2*margin_lr,
                   h - top_margin_p1 - bot_margin, id="page1")
    frameN = Frame(margin_lr, bot_margin, w - 2*margin_lr,
                   h - top_margin_pn - bot_margin, id="pageN")

    doc.addPageTemplates([
        PageTemplate(id="Cover", frames=[frame1], onPage=_page1),
        PageTemplate(id="Body",  frames=[frameN], onPage=_page_n),
    ])

    # ── Story ─────────────────────────────────────────────────────────────────
    from reportlab.platypus import NextPageTemplate
    story = [NextPageTemplate("Body")]

    num_counter = [0]

    for line in _normalize(text).split("\n"):
        kind, content = _classify_line(line)

        if kind == "blank":
            story.append(Spacer(1, 3*mm))

        elif kind == "heading":
            num_counter[0] = 0
            # Indigo left-border accent via a coloured box + text side by side
            p = Paragraph(content, heading_style)
            rule = HRFlowable(width="100%", thickness=0.5,
                              color=INDIGO_LIGHT, spaceAfter=2)
            story.append(KeepTogether([Spacer(1, 4*mm), p, rule]))

        elif kind == "numbered":
            num_counter[0] += 1
            p = Paragraph(content,
                          ParagraphStyle("Num", parent=numbered_style,
                                         bulletText=f"{num_counter[0]}."))
            story.append(p)

        elif kind == "bullet":
            num_counter[0] = 0
            story.append(Paragraph(content, bullet_style))

        else:  # plain
            num_counter[0] = 0
            story.append(Paragraph(content, body_style))

    try:
        doc.build(story)
        print(f"✅ PDF generated: {out_path}")
        return out_path
    except Exception as e:
        print(f"❌ PDF build failed: {e}")
        return None
