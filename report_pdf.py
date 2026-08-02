# -*- coding: utf-8 -*-
"""تولید PDF جدول‌بندی‌شده برای گزارش مرخصی."""
from __future__ import annotations

import io
import os
from datetime import datetime
from typing import List, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

try:
    import arabic_reshaper
    from bidi.algorithm import get_display

    def fa(text) -> str:
        s = str(text if text is not None else "")
        if not s:
            return ""
        try:
            return get_display(arabic_reshaper.reshape(s))
        except Exception:
            return s
except Exception:
    def fa(text) -> str:
        return str(text if text is not None else "")


_FONT_REG = "LeaveReport"
_FONT_BOLD = "LeaveReport-Bold"
_FONTS_READY = False


def _find_font() -> tuple:
    candidates = [
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
        (
            "/usr/share/fonts/SlidesCarnival/google/Noto Sans Arabic/static/NotoSansArabic-Regular.ttf",
            "/usr/share/fonts/SlidesCarnival/google/Noto Sans Arabic/static/NotoSansArabic-Bold.ttf",
        ),
        (
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ),
    ]
    # Prefer fonts that exist
    for reg, bold in candidates:
        if os.path.isfile(reg):
            if not os.path.isfile(bold):
                bold = reg
            return reg, bold
    return None, None


def _ensure_fonts():
    global _FONTS_READY
    if _FONTS_READY:
        return
    reg, bold = _find_font()
    if not reg:
        raise RuntimeError("فونت مناسب برای PDF پیدا نشد.")
    pdfmetrics.registerFont(TTFont(_FONT_REG, reg))
    pdfmetrics.registerFont(TTFont(_FONT_BOLD, bold))
    _FONTS_READY = True


def build_leaves_pdf(
    rows: list,
    *,
    letters: Optional[list] = None,
    title: str = "گزارش مرخصی‌های فعال",
    subtitle: str = "",
    term_region: str = "منطقه",
    term_group: str = "گروه",
) -> bytes:
    """
    rows: لیست دیکشنری با کلیدهای first_name/last_name/shift_index/
          region_name/group_name/leave_date (+ اختیاری display helpers از بیرون)
    letters: حروف شیفت مثل ['A','B','C','D']
    خروجی: bytes فایل PDF
    """
    _ensure_fonts()
    letters = letters or []

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=14 * mm,
        bottomMargin=12 * mm,
        title=title,
        author="Bale Leave Bot",
    )

    styles = getSampleStyleSheet()
    style_title = ParagraphStyle(
        "FaTitle",
        parent=styles["Title"],
        fontName=_FONT_BOLD,
        fontSize=16,
        leading=22,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#0f4c81"),
        spaceAfter=4,
    )
    style_sub = ParagraphStyle(
        "FaSub",
        parent=styles["Normal"],
        fontName=_FONT_REG,
        fontSize=10,
        leading=14,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#555555"),
        spaceAfter=10,
    )
    style_cell = ParagraphStyle(
        "FaCell",
        parent=styles["Normal"],
        fontName=_FONT_REG,
        fontSize=9,
        leading=12,
        alignment=TA_RIGHT,
    )
    style_head = ParagraphStyle(
        "FaHead",
        parent=styles["Normal"],
        fontName=_FONT_BOLD,
        fontSize=10,
        leading=13,
        alignment=TA_CENTER,
        textColor=colors.white,
    )

    story = []
    story.append(Paragraph(fa(title), style_title))
    if subtitle:
        story.append(Paragraph(fa(subtitle), style_sub))
    else:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        story.append(Paragraph(fa(f"تاریخ تهیه: {now}"), style_sub))
    story.append(Spacer(1, 4 * mm))

    # ترتیب منطقی (از راست به چپ در خروجی)
    headers_rtl = ["نوع", "وضعیت", "روز مرخصی", term_group or "گروه", term_region or "منطقه", "شیفت", "نام و نام‌خانوادگی", "ردیف"]
    table_data = [[Paragraph(fa(h), style_head) for h in headers_rtl]]

    def _name(r):
        first = r.get("first_name") or ""
        last = r.get("last_name") or ""
        n = f"{first} {last}".strip()
        return n or str(r.get("user_id") or "-")

    def _shift(r):
        si = r.get("shift_index")
        if si is not None and letters and 0 <= int(si) < len(letters):
            return str(letters[int(si)])
        if si is not None:
            return str(si)
        return "-"

    def _day(r):
        if r.get("leave_date_fa"):
            return r["leave_date_fa"]
        return str(r.get("leave_date") or "-")

    STATUS_FA = {
        "pending": "در انتظار",
        "reviewing": "در بررسی",
        "approved": "تایید شده",
        "rejected": "رد شده",
        "cancelled": "لغو شده",
    }

    def _status(r):
        return STATUS_FA.get(r.get("status"), r.get("status") or "-")

    def _kind(r):
        try:
            if int(r.get("over_capacity") or 0) == 1:
                return "اضافه بر ظرفیت"
        except (TypeError, ValueError):
            pass
        return "عادی"

    for i, r in enumerate(rows, 1):
        # همان ترتیب RTL: نوع ... ردیف (ردیف در سمت راست صفحه)
        cells = [
            _kind(r),
            _status(r),
            _day(r),
            r.get("group_name") or "-",
            r.get("region_name") or "-",
            _shift(r),
            _name(r),
            str(i),
        ]
        table_data.append([Paragraph(fa(c), style_cell) for c in cells])

    if len(table_data) == 1:
        table_data.append([Paragraph(fa("موردی ثبت نشده"), style_cell)] + [""] * 7)

    # عرض ستون‌ها متناظر با headers_rtl
    col_widths = [28 * mm, 26 * mm, 28 * mm, 32 * mm, 32 * mm, 18 * mm, 48 * mm, 14 * mm]
    table = Table(table_data, colWidths=col_widths, repeatRows=1)

    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f4c81")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
        ("FONTNAME", (0, 1), (-1, -1), _FONT_REG),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (7, 0), (7, -1), "CENTER"),  # ردیف

        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#9db4c8")),
        ("BOX", (0, 0), (-1, -1), 1.2, colors.HexColor("#0f4c81")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef5fb")]),
    ]
    table.setStyle(TableStyle(style_cmds))
    story.append(table)

    footer = ParagraphStyle(
        "FaFoot",
        parent=styles["Normal"],
        fontName=_FONT_REG,
        fontSize=8,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#888888"),
        spaceBefore=8,
    )
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(fa(f"تعداد رکورد: {len(rows)}"), footer))

    doc.build(story)
    return buf.getvalue()
