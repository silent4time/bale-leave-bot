# -*- coding: utf-8 -*-
"""
حافظه‌ی موقت و در-حافظه برای:
1) مراحل ورودی متنی (فرم ثبت‌نام، ساخت گروه، تنظیمات شیفت و ...)
2) بافر انتخاب چندگانه‌ی روزهای تقویم پیش از دکمه‌ی «ثبت» / «لغو»

چون ربات با polling و در یک پردازش تک اجرا می‌شود، دیکشنری ساده کافی است.
اگر ربات را روی چند پردازش/سرور اجرا می‌کنید، این‌ها را باید به Redis یا مشابه
منتقل کنید.
"""

PENDING_INPUT: dict = {}
SELECTION: dict = {}  # user_id -> {"year": int, "month": int, "to_submit": set(), "to_cancel": set()}


def set_state(user_id: int, **kwargs):
    PENDING_INPUT[user_id] = kwargs


def get_state(user_id: int):
    return PENDING_INPUT.get(user_id)


def clear_state(user_id: int):
    PENDING_INPUT.pop(user_id, None)


def get_selection(user_id: int, year: int, month: int) -> dict:
    sel = SELECTION.get(user_id)
    if not sel or sel["year"] != year or sel["month"] != month:
        sel = {"year": year, "month": month, "to_submit": set(), "to_cancel": set()}
        SELECTION[user_id] = sel
    return sel


def clear_selection(user_id: int):
    SELECTION.pop(user_id, None)


def toggle_submit(user_id: int, year: int, month: int, date_str: str):
    sel = get_selection(user_id, year, month)
    if date_str in sel["to_submit"]:
        sel["to_submit"].discard(date_str)
    else:
        sel["to_submit"].add(date_str)


def toggle_cancel(user_id: int, year: int, month: int, date_str: str):
    sel = get_selection(user_id, year, month)
    if date_str in sel["to_cancel"]:
        sel["to_cancel"].discard(date_str)
    else:
        sel["to_cancel"].add(date_str)
