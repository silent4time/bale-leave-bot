# -*- coding: utf-8 -*-
"""
گزارش پایان‌ماه برای مدیر و تکنسین‌ارشد.

منطق:
- هر بار که ربات بیدار است، ماه جلالی جاری را با «آخرین ماه گزارش‌شده» مقایسه می‌کند.
- اگر ماه عوض شده باشد، خلاصهٔ ماه *قبل* را برای:
    • همهٔ مدیران (is_admin)
    • تکنسین‌ارشد هر گروه (is_senior) — فقط همان گروه
  ارسال می‌کند.
- یک بار در هر ماه ارسال می‌شود (کلید settings / کش: monthly_report:last_sent).

نکته دربارهٔ «اسکرین»:
  در پیام‌رسان بله نمی‌توان از کیبورد اینلاین اسکرین‌شات واقعی گرفت.
  به‌جای آن یک گزارش متنی خوانا + (در صورت امکان) همان نمای تقویم ماه قبل
  به‌صورت پیام جداگانه فرستاده می‌شود تا معادل عملی اسکرین باشد.
"""
from __future__ import annotations

import logging
from typing import Callable, Awaitable, Optional

import jalali
from cache import cache

logger = logging.getLogger("leave_bot.monthly_report")

SETTING_KEY = "monthly_report_last_sent"  # مقدار: "YYYY-MM" ماه جلالی که گزارشش ارسال شده


def previous_month(year: int, month: int) -> tuple[int, int]:
    return jalali.add_months(year, month, -1)


def current_ym() -> tuple[int, int, str]:
    y, m, _ = jalali.today_jalali()
    return y, m, f"{y:04d}-{m:02d}"


def format_leave_line(row: dict) -> str:
    """یک خط خلاصه برای یک رکورد مرخصی."""
    name = f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip() or str(row.get("user_id"))
    status_map = {
        "pending": "🕓 موقت",
        "reviewing": "🔍 بررسی",
        "approved": "✅ تایید",
        "rejected": "❌ رد",
    }
    st = status_map.get(row.get("status"), row.get("status") or "?")
    group = row.get("group_name") or "—"
    region = row.get("region_name") or "—"
    note = (row.get("note_user") or "").strip()
    note_part = f" — «{note}»" if note else ""
    return f"• {row['leave_date']} | {name} | {group} / {region} | {st}{note_part}"


def build_month_summary_text(
    year: int,
    month: int,
    leaves: list[dict],
    region_name: Optional[str] = None,
) -> str:
    title = f"📋 گزارش مرخصی‌های {jalali.PERSIAN_MONTHS[month - 1]} {year}"
    if region_name:
        title += f"\nمنطقه: {region_name}"

    if not leaves:
        body = "در این ماه هیچ مرخصی‌ای ثبت نشده است."
    else:
        # گروه‌بندی بر اساس وضعیت
        by_status: dict[str, list] = {}
        for r in leaves:
            by_status.setdefault(r.get("status") or "?", []).append(r)

        parts = []
        order = ["approved", "reviewing", "pending", "rejected"]
        labels = {
            "approved": "✅ تاییدشده",
            "reviewing": "🔍 در حال بررسی",
            "pending": "🕓 ثبت موقت",
            "rejected": "❌ ردشده",
        }
        for key in order:
            rows = by_status.pop(key, [])
            if not rows:
                continue
            parts.append(f"\n{labels[key]} ({len(rows)}):")
            for r in sorted(rows, key=lambda x: x["leave_date"]):
                parts.append(format_leave_line(r))
        for key, rows in by_status.items():
            parts.append(f"\n{key} ({len(rows)}):")
            for r in sorted(rows, key=lambda x: x["leave_date"]):
                parts.append(format_leave_line(r))
        body = "\n".join(parts)

    footer = (
        "\n\n—\n"
        "این پیام به‌صورت خودکار در ابتدای ماه جدید ارسال می‌شود.\n"
        "کاربران می‌توانند برای ماه‌های آینده بدون محدودیت درخواست ثبت کنند؛ "
        "فقط روزهای گذشته قابل انتخاب نیستند."
    )
    return f"{title}\n{body}{footer}"


async def maybe_send_monthly_reports(
    *,
    get_setting: Callable[[str], Awaitable[Optional[str]]],
    set_setting: Callable[[str, str], Awaitable[None]],
    list_admin_ids: Callable[[], Awaitable[list[int]]],
    list_seniors_by_group: Callable[[], Awaitable[list[dict]]],
    # seniors: [{user_id, group_id, group_name, region_name}, ...]
    fetch_leaves_for_month: Callable[[int, int, Optional[int]], Awaitable[list[dict]]],
    # (year, month, region_id|None برای مدیر، group_id برای ارشد) -> leaves with joined names
    send_message: Callable[[int, str], Awaitable[None]],
    # user_id, text
) -> bool:
    """
    اگر ماه عوض شده باشد، گزارش ماه قبل را می‌فرستد و فلگ را به‌روز می‌کند.
    True = گزارشی ارسال شد.
    """
    y, m, cur_ym = current_ym()
    last = await get_setting(SETTING_KEY)
    # همچنین از کش بخوان (سریع‌تر در حلقه)
    cached = cache.get("monthly_report:last_sent")
    if cached:
        last = cached

    if last == cur_ym:
        return False  # همین ماه قبلاً گزارش داده شده

    # ماه قبل
    py, pm = previous_month(y, m)

    # اگر last خالی است (اولین اجرا)، فقط فلگ را روی ماه جاری بگذار —
    # تا با راه‌اندازی اولیه یک‌باره گزارش ماه قبل نریزد مگر بخواهید.
    # اگر می‌خواهید در اولین اجرا هم ماه قبل برود، این بلوک را حذف کنید.
    if not last:
        await set_setting(SETTING_KEY, cur_ym)
        cache.set("monthly_report:last_sent", cur_ym)
        logger.info("monthly_report: first run — marker set to %s (no backfill)", cur_ym)
        return False

    logger.info("monthly_report: month rolled %s → %s; sending report for %04d-%02d", last, cur_ym, py, pm)

    # مدیران: خلاصه همه مناطق
    admin_ids = await list_admin_ids()
    all_leaves = await fetch_leaves_for_month(py, pm, None)
    admin_text = build_month_summary_text(py, pm, all_leaves, region_name=None)
    for aid in admin_ids:
        try:
            await send_message(aid, admin_text)
        except Exception:
            logger.exception("monthly_report: failed to send to admin %s", aid)

    # تکنسین‌ارشد: فقط گروهِ خودش (هر گروه یک ارشد دارد)
    seniors = await list_seniors_by_group()
    for s in seniors:
        gid = s["group_id"]
        gname = s.get("group_name") or f"گروه {gid}"
        rname = s.get("region_name")
        label = f"{gname} ({rname})" if rname else gname
        leaves = await fetch_leaves_for_month(py, pm, gid)
        text = build_month_summary_text(py, pm, leaves, region_name=label)
        try:
            await send_message(s["user_id"], text)
        except Exception:
            logger.exception("monthly_report: failed to send to senior %s", s["user_id"])

    await set_setting(SETTING_KEY, cur_ym)
    cache.set("monthly_report:last_sent", cur_ym)
    return True
