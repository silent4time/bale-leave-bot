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


def build_calendar(
    year: int,
    month: int,
    today_str: str,
    own_status: dict,
    selection: dict,
    shift_short_labels: dict = None,
    approved_others: dict = None,
    *,
    interactive: bool = True,
    show_actions: bool = True,
) -> InlineKeyboardMarkup:
    """
    own_status: {date_str: status} مرخصی‌های خود کاربر
    selection:  {"to_submit": set, "to_cancel": set}
    shift_short_labels: {date_str: 'ص1'}
    approved_others: {date_str: count} تعداد مرخصی تاییدشده دیگران در آن روز
    interactive: اگر False فقط نمایش (برای مدیر/مسئول شیفت بدون ثبت)
    """
    to_submit = selection.get("to_submit", set()) if selection else set()
    to_cancel = selection.get("to_cancel", set()) if selection else set()
    shift_short_labels = shift_short_labels or {}
    approved_others = approved_others or {}

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
        # خانه بزرگ‌تر و خوانا: «۱۲/ص۱» یا فقط روز
        if date_str in shift_short_labels and shift_short_labels[date_str]:
            short = str(shift_short_labels[date_str]).replace("|", "/").strip()
            base = f"{day}/{short}"
        else:
            base = f"{day}"

        if date_str < today_str:
            label = f"˙{base}"
            cb = f"dayinfo:{date_str}" if date_str in approved_others or date_str in own_status else "noop"
        elif interactive and date_str in to_submit:
            label = f"☑{base}"
            cb = f"pick:{year}:{month}:{day}"
        elif interactive and date_str in to_cancel:
            label = f"🗑{base}"
            cb = f"pick:{year}:{month}:{day}"
        elif date_str in own_status:
            icon = STATUS_ICON.get(own_status[date_str], "")
            # ★ = مرخصی خود شخص
            label = f"★{icon}{base}"
            if interactive:
                cb = f"pick:{year}:{month}:{day}"
            else:
                cb = f"dayinfo:{date_str}"
        elif date_str in approved_others:
            # مرخصی تاییدشده دیگران — نمایش می‌شود ولی در حالت تعاملی قابل انتخاب برای درخواست خود
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
        "راهنما:\n"
        "☑ انتخاب برای ثبت    🗑 انتخاب برای لغو\n"
        "★ مرخصی خودتان    🕓 موقت    🔍 بررسی    ✅ تایید\n"
        "● مرخصی تاییدشده دیگران (برای دیدن نام بزنید)\n"
        "˙ روز گذشته — ماه‌های آینده بدون محدودیت"
    )
