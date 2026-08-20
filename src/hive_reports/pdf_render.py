"""PDF + PNG render. One default template; custom via JSON in templates/."""
from __future__ import annotations
from pathlib import Path

from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
)
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.barcode.qr import QrCodeWidget

from .calc import Transaction, money


DEFAULT_TEMPLATE = {
    "title": "RECEIPT",
    "company": "Hive Reports Inc.",
    "address": "123 Bee Lane, Hive City",
    "footer": "Thank you for your business.",
    "show_qr": True,
    "page_size": "A4",  # or "letter" or "thermal80" (80mm)
}


def _page_size(name: str):
    # ponytail: thermal80 is one width, real thermal printers vary.
    # Add per-model widths if you ship for actual POS hardware.
    return {
        "A4": A4,
        "letter": letter,
        "thermal80": (80 * mm, 297 * mm),
    }.get(name, A4)


def _styles():
    ss = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontSize=18, spaceAfter=8),
        "normal": ss["BodyText"],
        "small": ParagraphStyle("small", parent=ss["BodyText"], fontSize=8, textColor=colors.grey),
    }


def render_pdf(
    tx: Transaction,
    out_path: str | Path,
    template: dict | None = None,
    receipt_id: str = "",
) -> Path:
    """Render a Transaction to a PDF file. Returns the path."""
    tmpl = {**DEFAULT_TEMPLATE, **(template or {})}
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out),
        pagesize=_page_size(tmpl.get("page_size", "A4")),
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
    )
    s = _styles()
    story = []

    story.append(Paragraph(tmpl["company"], s["h1"]))
    story.append(Paragraph(tmpl["address"], s["small"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(tmpl["title"], s["h1"]))
    story.append(Paragraph(f"Receipt #: {receipt_id}", s["normal"]))
    story.append(Spacer(1, 10))

    rows = [["Item", "Qty", "Unit Price", "Line Total"]]
    for it in tx.items:
        unit = (it.line_total() / it.qty) if it.qty else 0
        rows.append([
            it.name,
            str(it.qty),
            f"{tx.currency} {money(unit)}",
            f"{tx.currency} {money(it.line_total())}",
        ])
    t = Table(rows, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#222")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))

    summary = [
        ["Subtotal", f"{tx.currency} {tx.subtotal()}"],
        ["Discount", f"{tx.currency} {money(tx.discount)}"],
        ["Tax", f"{tx.currency} {tx.tax_total()}"],
        ["TOTAL", f"{tx.currency} {tx.total()}"],
    ]
    st = Table(summary, colWidths=[40 * mm, 40 * mm], hAlign="RIGHT")
    st.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.5, colors.black),
    ]))
    story.append(st)
    story.append(Spacer(1, 12))

    if tx.notes:
        story.append(Paragraph(f"<b>Notes:</b> {tx.notes}", s["normal"]))
        story.append(Spacer(1, 12))

    if tmpl.get("show_qr") and receipt_id:
        qr = QrCodeWidget(receipt_id)
        d = Drawing(40 * mm, 40 * mm)
        d.add(qr)
        story.append(d)
        story.append(Spacer(1, 8))

    story.append(Paragraph(tmpl["footer"], s["small"]))
    doc.build(story)
    return out


def render_png(
    tx: Transaction,
    out_path: str | Path,
    template: dict | None = None,
    receipt_id: str = "",
) -> Path:
    """Render to PNG by generating the PDF and rasterizing page 1.
    Requires `pypdfium2` (pure-Python wheel, no system deps). Optional dep."""
    try:
        import pypdfium2 as pdfium  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "PNG export needs pypdfium2: `pip install pypdfium2`"
        ) from e

    tmp_pdf = Path(out_path).with_suffix(".tmp.pdf")
    try:
        render_pdf(tx, tmp_pdf, template, receipt_id)
        pdf = pdfium.PdfDocument(str(tmp_pdf))
        page = pdf[0]
        bitmap = page.render(scale=300 / 72)  # 300 DPI
        pil_img = bitmap.to_pil()
        pil_img.save(out_path, "PNG")
    finally:
        tmp_pdf.unlink(missing_ok=True)
    return Path(out_path)