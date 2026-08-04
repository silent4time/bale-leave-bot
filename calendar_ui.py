# -*- coding: utf-8 -*-
"""
ساخت کیبورد تقویم.

- روزهای گذشته غیرفعال
- ماه‌های آینده آزاد
- علامت مرخصی خود کاربر: ★ کنار آیکون وضعیت
- روزهای دارای مرخصی تاییدشده دیگران قابل کلیک برای دیدن نام (dayinfo)
- انتخاب برای ثبت/لغو با ☑ و 🗑
- برچسب شیفت کوتاه در صورت فعال بودن حالت شیفتی
"""
from __future__ import annotations

from bale import InlineKeyboardMarkup, InlineKeyboardButton

import jalali

STATUS_ICON = {"pending": "🕓", "reviewing": "🔍", "approved": "✅"}

def _shift_letter_only(raw: str) -> str:
    """فقط حرف نوع شیفت: ص1→ص ، ع2→ع ، ش1→ش ، ر2→ر."""
    if not raw:
        return ""
    s = str(raw).strip().replace("|", "/").split("/")[0].strip()
    if "صبح" in s:
        return "ص"
    if "عصر" in s or "ظهر" in s:
        return "ع"
    if "شب" in s:
        return "ش"
    if "رست" in s or "استراحت" in s:
        return "ر"
    letters = "".join(
        ch for ch in s
        if not ch.isdigit() and not ch.isspace() and ch not in "-_/."
    )
    if not letters:
        return ""
    return letters[0]



# رنگ‌نمای روز (API بله رنگ پس‌زمینه دکمه ندارد → ایموجی رنگی)
DAY_TYPE_EMOJI = {
    "morning": "🟢︎",
    "afternoon": "🟡︎",
    "night": "🔵︎",
    "rest": "🔴︎",
}
DAY_TYPE_LEGEND = (
    "🟢صبح  🟡عصر  🔵شب  🔴استراحت/تعطیل  |  ★مرخصی شما  ●دیگران  ☑انتخاب"
)


def classify_slot_label(name: str = "", short: str = "") -> str:
    """تشخیص نوع روز از نام کامل یا علامت کوتاه اسلات."""
    name = (name or "").strip()
    short = (short or "").strip().replace("|", "/").split("/")[0]
    text = f"{name} {short}"
    low = text.lower()
    # استراحت اول
    if any(k in text for k in ("رست", "استراحت", "آف")) or "off" in low:
        return "rest"
    if short.startswith(("ر", "R", "r")):
        return "rest"
    if "صبح" in text or short.startswith("ص"):
        return "morning"
    if any(k in text for k in ("عصر", "ظهر")) or short.startswith("ع"):
        return "afternoon"
    if "شب" in text or short.startswith("ش"):
        return "night"
    return "unknown"


def build_calendar(
    year: int,
    month: int,
    today_str: str,
    own_status: dict,
    selection: dict,
    shift_short_labels: dict = None,
    approved_others: dict = None,
    day_types: dict = None,
    *,
    interactive: bool = True,
    show_actions: bool = True,
) -> InlineKeyboardMarkup:
    """
    own_status: {date_str: status}
    selection:  {"to_submit": set, "to_cancel": set}
    shift_short_labels: {date_str: 'ص1'}  — فقط برای راهنما؛ نمایش اصلی با رنگ
    day_types: {date_str: morning|afternoon|night|rest}
    approved_others: {date_str: count}
    """
    to_submit = selection.get("to_submit", set()) if selection else set()
    to_cancel = selection.get("to_cancel", set()) if selection else set()
    shift_short_labels = shift_short_labels or {}
    approved_others = approved_others or {}
    day_types = day_types or {}

    kb = InlineKeyboardMarkup()
    row = 1
    kb.add(
        InlineKeyboardButton(
            text=f"📆 {jalali.PERSIAN_MONTHS[month - 1]} {year}",
            callback_data="noop",
        ),
        row=row,
    )
    row += 1
    for h in jalali.WEEKDAY_SHORT:
        kb.add(InlineKeyboardButton(text=h, callback_data="noop"), row=row)
    row += 1

    first_wd = jalali.jalali_weekday(year, month, 1)
    ndays = jalali.days_in_jalali_month(year, month)

    col = 0
    for _ in range(first_wd):
        kb.add(InlineKeyboardButton(text="－", callback_data="noop"), row=row)
        col += 1

    for day in range(1, ndays + 1):
        date_str = jalali.parse_date_str(year, month, day)
        # فقط شماره روز + حرف کوتاه شیفت (بدون / و بدون عدد اسلات) — مثال: 23ص
        short = ""
        if date_str in shift_short_labels and shift_short_labels[date_str]:
            raw = str(shift_short_labels[date_str]).replace("|", "/").strip()
            # فقط بخش حروفی اول (ص1 → ص ، ع2 → ع ، رست → ر)
            short = _shift_letter_only(raw)
        base = f"{day}{short}" if short else f"{day}"

        if date_str < today_str:
            label = f"·{base}"
            cb = f"dayinfo:{date_str}" if date_str in approved_others or date_str in own_status else "noop"
        elif interactive and date_str in to_submit:
            label = f"☑{base}"
            cb = f"pick:{year}:{month}:{day}"
        elif interactive and date_str in to_cancel:
            label = f"🗑{base}"
            cb = f"pick:{year}:{month}:{day}"
        elif date_str in own_status:
            icon = STATUS_ICON.get(own_status[date_str], "")
            label = f"★{icon}{base}"
            if interactive:
                cb = f"pick:{year}:{month}:{day}"
            else:
                cb = f"dayinfo:{date_str}"
        elif date_str in approved_others:
            n = approved_others[date_str]
            label = f"●{base}" if n == 1 else f"●{n}{base}"
            if interactive:
                cb = f"pick:{year}:{month}:{day}"
            else:
                cb = f"dayinfo:{date_str}"
        else:
            label = base
            if interactive:
                cb = f"pick:{year}:{month}:{day}"
            else:
                cb = f"dayinfo:{date_str}"

        kb.add(InlineKeyboardButton(text=label, callback_data=cb), row=row)
        col += 1
        if col == 7:
            row += 1
            col = 0

    
    if col != 0:
        row += 1

    prev_y, prev_m = jalali.add_months(year, month, -1)
    next_y, next_m = jalali.add_months(year, month, 1)
    kb.add(InlineKeyboardButton(text="◀️ ماه قبل", callback_data=f"nav:{prev_y}:{prev_m}"), row=row)
    kb.add(InlineKeyboardButton(text="ماه بعد ▶️", callback_data=f"nav:{next_y}:{next_m}"), row=row)
    row += 1

    if show_actions and interactive:
        suffix = f"{year}:{month}"
        kb.add(
            InlineKeyboardButton(
                text="✅ ثبت درخواستِ روزهای تیک‌خورده",
                callback_data=f"confirm_submit:{suffix}",
            ),
            row=row,
        )
        row += 1
        kb.add(
            InlineKeyboardButton(
                text="🗑 لغو مرخصیِ روزهای تیک‌خورده",
                callback_data=f"confirm_cancel:{suffix}",
            ),
            row=row,
        )
        row += 1
        kb.add(
            InlineKeyboardButton(
                text="♻️ پاک‌کردن تیک‌های فعلی",
                callback_data=f"clear_selection:{suffix}",
            ),
            row=row,
        )
        row += 1
        kb.add(
            InlineKeyboardButton(
                text="📋 مرخصی‌های هم‌گروهی",
                callback_data=f"show_group_leaves:{suffix}",
            ),
            row=row,
        )

    return kb


def legend_text() -> str:
    return (
        "فرمت روز: شماره+حرف شیفت (مثلاً 23ص)\n"
        "☑ثبت  🗑لغو  ★مرخصی شما  ●دیگران  ·روز گذشته"
    )
