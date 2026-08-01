# -*- coding: utf-8 -*-
"""
کیبوردهای ربات مرخصی.

نقش‌ها:
  - مدیر (is_admin)
  - مسئول شیفت (is_shift_lead) — هم‌رده عملیاتی روی مناطق تخصیص‌یافته
  - تکنسین ارشد (is_senior)
  - عضو عادی

چیدمان دکمه‌های اینلاین ترجیحاً دو ستونه است؛ اگر تعداد فرد باشد دکمهٔ آخر تمام‌عرض می‌شود.
"""
from __future__ import annotations

from bale import InlineKeyboardMarkup, InlineKeyboardButton, MenuKeyboardMarkup, MenuKeyboardButton

import config

# ============================================================== متن منوها ====

# --- مشترک بین مدیر/مسئول شیفت/ارشد ---
BTN_ADD_PEOPLE = "➕ اضافه کردن افراد"
BTN_ADD_CONTACT = BTN_ADD_PEOPLE  # سازگاری

# --- مدیر ---
# صف مرخصی در نقش «فقط مدیر» نیست.
# اگر مدیر هم‌زمان مسئول شیفت باشد، دکمه صف مسئول شیفت به منویش اضافه می‌شود.
ADMIN_BTN_PENDING = "⏳ افراد در انتظار تایید"
ADMIN_BTN_REGIONS = "🗺 مناطق کاری"
ADMIN_BTN_GROUPS = "📋 گروه‌ها"
ADMIN_BTN_MEMBERS = "👥 اعضا"
ADMIN_BTN_INVITE = "🔗 لینک دعوت"  # از منوی اصلی حذف؛ زیر «اضافه کردن افراد»
ADMIN_BTN_CALENDAR = "📅 نمایش تقویم منطقه"
ADMIN_BTN_REPORT = "📊 گزارش مرخصی‌ها"
ADMIN_BTN_SHIFT_LEADS = "👔 مسئولان شیفت"
ADMIN_BTN_SETTINGS = "⚙️ تنظیمات"
ADMIN_BTN_REPLACE_ADMIN = "🔁 جایگزینی مدیر"

ADMIN_MENU_TEXTS = {
    ADMIN_BTN_PENDING, ADMIN_BTN_REGIONS, ADMIN_BTN_GROUPS,
    ADMIN_BTN_MEMBERS, ADMIN_BTN_CALENDAR, ADMIN_BTN_REPORT,
    ADMIN_BTN_SHIFT_LEADS, ADMIN_BTN_SETTINGS, ADMIN_BTN_REPLACE_ADMIN, BTN_ADD_PEOPLE,
}

# --- مسئول شیفت ---
LEAD_BTN_QUEUE = "🕓 صف مرخصی ارشدهای من"
LEAD_BTN_GROUPS = "📋 گروه‌های مناطق من"
LEAD_BTN_MEMBERS = "👥 اعضای مناطق من"
LEAD_BTN_PENDING = "⏳ افراد در انتظار تایید"
LEAD_BTN_CALENDAR = "📅 نمایش تقویم منطقه"
LEAD_BTN_REPORT = "📊 گزارش مناطق من"
LEAD_BTN_MY_SHIFT = "🔄 شیفت کاری من"
LEAD_BTN_TRANSFER = "🔁 انتقال نقش مسئول شیفت"
LEAD_BTN_SETTINGS = "⚙️ تنظیمات مناطق من"

LEAD_MENU_TEXTS = {
    LEAD_BTN_QUEUE, LEAD_BTN_GROUPS, LEAD_BTN_MEMBERS, LEAD_BTN_PENDING, LEAD_BTN_CALENDAR,
    LEAD_BTN_REPORT, LEAD_BTN_MY_SHIFT, LEAD_BTN_TRANSFER, LEAD_BTN_SETTINGS, BTN_ADD_PEOPLE,
}

# --- تکنسین ارشد ---
SNR_BTN_QUEUE = "🕓 صف مرخصی اعضای منطقه"
SNR_BTN_MEMBERS = "👥 اعضای منطقه من"
SNR_BTN_PENDING = "⏳ افراد در انتظار تایید"
SNR_BTN_GROUPS = "📋 گروه‌های منطقه من"
SNR_BTN_CALENDAR = "📅 تقویم منطقه من"
SNR_BTN_STATUS = "ℹ️ وضعیت من"

SNR_MENU_TEXTS = {
    SNR_BTN_QUEUE, SNR_BTN_MEMBERS, SNR_BTN_PENDING, SNR_BTN_GROUPS, SNR_BTN_CALENDAR, SNR_BTN_STATUS,
    BTN_ADD_PEOPLE,
}


# --- عضو عادی ---
USER_BTN_CALENDAR = "📅 تقویم مرخصی"
USER_BTN_STATUS = "ℹ️ وضعیت من"

USER_MENU_TEXTS = {USER_BTN_CALENDAR, USER_BTN_STATUS}

ALL_MENU_TEXTS = ADMIN_MENU_TEXTS | LEAD_MENU_TEXTS | SNR_MENU_TEXTS | USER_MENU_TEXTS


# ============================================================== منوی اصلی ====

def _add_menu_two_col(kb: MenuKeyboardMarkup, labels: list) -> None:
    """
    دکمه‌های منوی اصلی (کیبورد پایین صفحه) را دو ستونه می‌چیند.
    اگر تعداد فرد باشد، دکمه‌ی آخر تنها در ردیف خودش می‌ماند و طبیعتاً
    (چون تنهاست) عرض کل ردیف را می‌گیرد — دقیقاً همان چیدمانی که خواسته شده.
    """
    row = 1
    i = 0
    n = len(labels)
    while i < n:
        remaining = n - i
        if remaining == 1:
            kb.add(MenuKeyboardButton(labels[i]), row=row)
            i += 1
        else:
            kb.add(MenuKeyboardButton(labels[i]), row=row)
            kb.add(MenuKeyboardButton(labels[i + 1]), row=row)
            i += 2
        row += 1


def admin_menu(also_shift_lead: bool = False) -> MenuKeyboardMarkup:
    """منوی مدیر. صف مرخصی فقط اگر هم‌زمان مسئول شیفت باشد.
    آیتم‌های «ساخت/پیکربندی» (مناطق، گروه‌ها، مسئولان شیفت، جایگزینی مدیر)
    از این منو حذف شده و داخل «⚙️ تنظیمات» جمع شده‌اند."""
    kb = MenuKeyboardMarkup()
    labels = [
        ADMIN_BTN_PENDING,
        ADMIN_BTN_MEMBERS,
        BTN_ADD_PEOPLE,
        ADMIN_BTN_CALENDAR,
        ADMIN_BTN_REPORT,
        ADMIN_BTN_SETTINGS,
    ]
    if also_shift_lead:
        # صف مرخصی در نقش مسئول شیفت — نه به‌عنوان مدیر
        labels.insert(1, LEAD_BTN_QUEUE)
        labels.extend([LEAD_BTN_MY_SHIFT, LEAD_BTN_TRANSFER])
    _add_menu_two_col(kb, labels)
    return kb


def shift_lead_menu() -> MenuKeyboardMarkup:
    labels = [
        LEAD_BTN_QUEUE,
        LEAD_BTN_GROUPS,
        LEAD_BTN_MEMBERS,
        LEAD_BTN_PENDING,
        BTN_ADD_PEOPLE,
        LEAD_BTN_CALENDAR,
        LEAD_BTN_REPORT,
        LEAD_BTN_MY_SHIFT,
        LEAD_BTN_TRANSFER,
        LEAD_BTN_SETTINGS,
    ]
    kb = MenuKeyboardMarkup()
    _add_menu_two_col(kb, labels)
    return kb


def senior_menu() -> MenuKeyboardMarkup:
    labels = [
        SNR_BTN_QUEUE,
        SNR_BTN_MEMBERS,
        SNR_BTN_PENDING,
        BTN_ADD_PEOPLE,
        SNR_BTN_GROUPS,
        SNR_BTN_CALENDAR,
        SNR_BTN_STATUS,
    ]
    kb = MenuKeyboardMarkup()
    _add_menu_two_col(kb, labels)
    return kb


def user_menu() -> MenuKeyboardMarkup:
    kb = MenuKeyboardMarkup()
    _add_menu_two_col(kb, [USER_BTN_CALENDAR, USER_BTN_STATUS])
    return kb


CONTACT_CANCEL_TEXT = "❌ انصراف از افزودن مخاطب"


def contact_request_menu() -> MenuKeyboardMarkup:
    """کیبورد موقتی که با زدنش، بله مخاطبی از دفترچه‌تلفن کاربر برای ربات ارسال می‌کند."""
    kb = MenuKeyboardMarkup()
    kb.add(MenuKeyboardButton("📱 انتخاب و ارسال مخاطب", request_contact=True))
    kb.add(MenuKeyboardButton(CONTACT_CANCEL_TEXT))
    return kb


def menu_for_user(db_user: dict) -> MenuKeyboardMarkup:
    """انتخاب منوی مناسب (اولویت: مدیر > مسئول شیفت > ارشد > عضو)."""
    if db_user.get("is_admin"):
        return admin_menu(also_shift_lead=bool(db_user.get("is_shift_lead")))
    if db_user.get("is_shift_lead"):
        return shift_lead_menu()
    # is_senior یا نقش snr هر دو منوی ارشد می‌گیرند
    if db_user.get("is_senior") or db_user.get("role") == "snr":
        return senior_menu()
    return user_menu()


# ============================================================== کمک‌کننده ====

def _add_two_col(kb: InlineKeyboardMarkup, buttons: list, start_row: int = 1) -> int:
    """
    دکمه‌ها را دو ستونه می‌چیند — اما اگر متن هرکدام بلند باشد (مثل نام منطقه/گروه)
    به‌صورت خودکار تک‌ستونه و تمام‌عرض می‌چیند تا فشرده و غیرقابل‌خواندن نشود.
    (این باگِ گزارش‌شده — دکمه‌های ریز و فشرده — دقیقاً همین‌جا بود.)
    buttons: list of InlineKeyboardButton
    خروجی: شماره ردیف بعدی آزاد.
    """
    row = start_row
    LONG_LABEL_THRESHOLD = 14
    force_single_col = any(len(b.text) > LONG_LABEL_THRESHOLD for b in buttons)

    if force_single_col:
        for b in buttons:
            kb.add(b, row=row)
            row += 1
        return row

    i = 0
    n = len(buttons)
    while i < n:
        remaining = n - i
        if remaining == 1:
            kb.add(buttons[i], row=row)
            row += 1
            i += 1
        else:
            kb.add(buttons[i], row=row)
            kb.add(buttons[i + 1], row=row)
            row += 1
            i += 2
    return row


# ============================================================== اینلاین ====

def role_select_keyboard(callback_prefix: str, allowed_roles: list = None) -> InlineKeyboardMarkup:
    """انتخاب نقش. allowed_roles مثل ['op','tech','snr','lead'] — اگر None همه."""
    kb = InlineKeyboardMarkup()
    items = list(config.ROLE_LABELS.items())
    if allowed_roles is not None:
        allowed = set(allowed_roles)
        items = [(c, lab) for c, lab in items if c in allowed]
    buttons = [
        InlineKeyboardButton(text=f"نقش: {label}", callback_data=f"{callback_prefix}:{code}")
        for code, label in items
    ]
    _add_two_col(kb, buttons)
    return kb


def add_people_method_keyboard() -> InlineKeyboardMarkup:
    """دو روش افزودن: لینک دعوت یا دفترچه تلفن."""
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(text="🔗 لینک دعوت", callback_data="addvia:link"), row=1)
    kb.add(InlineKeyboardButton(text="📱 دفترچه تلفن", callback_data="addvia:contact"), row=2)
    return kb


def region_select_keyboard(regions, callback_prefix: str, allow_none: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    buttons = [
        InlineKeyboardButton(text=f"🗺 {r['name']}", callback_data=f"{callback_prefix}:{r['id']}")
        for r in regions
    ]
    if allow_none:
        buttons.append(
            InlineKeyboardButton(text="بدون منطقه", callback_data=f"{callback_prefix}:none")
        )
    _add_two_col(kb, buttons)
    return kb


def group_select_keyboard(groups, callback_prefix: str, allow_none: bool = True) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    buttons = []
    for g in groups:
        color = g.get("color") or ""
        label = f"{g['name']} (ظرفیت {g['max_concurrent']})"
        if color:
            label = f"● {label}"
        buttons.append(
            InlineKeyboardButton(text=label, callback_data=f"{callback_prefix}:{g['id']}")
        )
    if allow_none:
        buttons.append(
            InlineKeyboardButton(text="بدون گروه", callback_data=f"{callback_prefix}:none")
        )
    _add_two_col(kb, buttons)
    return kb


def multi_region_toggle_keyboard(regions, selected_ids: set, callback_prefix: str,
                                  done_callback: str = "sl_regions_done") -> InlineKeyboardMarkup:
    """انتخاب چند منطقه برای مسئول شیفت (تیک‌دار)."""
    kb = InlineKeyboardMarkup()
    buttons = []
    for r in regions:
        mark = "✅ " if r["id"] in selected_ids else ""
        buttons.append(
            InlineKeyboardButton(
                text=f"{mark}{r['name']}",
                callback_data=f"{callback_prefix}:{r['id']}",
            )
        )
    row = _add_two_col(kb, buttons)
    kb.add(InlineKeyboardButton(text="✔️ تأیید مناطق انتخاب‌شده", callback_data=done_callback), row=row)
    return kb


def shift_letter_keyboard(shift_count: int, callback_prefix: str) -> InlineKeyboardMarkup:
    import shift as shift_mod
    kb = InlineKeyboardMarkup()
    buttons = [
        InlineKeyboardButton(
            text=f"شیفت {letter}",
            callback_data=f"{callback_prefix}:{idx}",
        )
        for idx, letter in enumerate(shift_mod.shift_letters(shift_count))
    ]
    _add_two_col(kb, buttons)
    return kb


def slot_select_keyboard(labels, callback_prefix: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    buttons = [
        InlineKeyboardButton(text=item["name"], callback_data=f"{callback_prefix}:{i}")
        for i, item in enumerate(labels)
    ]
    _add_two_col(kb, buttons)
    return kb


def groups_edit_keyboard(groups) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    row = 1
    for g in groups:
        kb.add(
            InlineKeyboardButton(text=f"✏️ ظرفیت «{g['name']}» (فعلی {g['max_concurrent']})",
                                  callback_data=f"editcap:{g['id']}"),
            row=row,
        )
        row += 1
        kb.add(
            InlineKeyboardButton(text=f"🏷 تغییر نام «{g['name']}»", callback_data=f"grp_rename:{g['id']}"),
            row=row,
        )
        row += 1
        kb.add(
            InlineKeyboardButton(text=f"🗑 حذف «{g['name']}»", callback_data=f"grp_delete:{g['id']}"),
            row=row,
        )
        row += 1
    return kb





def nav_row_keyboard(*, back_callback: str = None, back_label: str = "↩️ بازگشت",
                     show_main: bool = False) -> InlineKeyboardMarkup:
    """ردیف ناوبری زیرمنو. show_main فقط وقتی لازم است True شود."""
    kb = InlineKeyboardMarkup()
    row = 1
    if back_callback:
        kb.add(InlineKeyboardButton(text=back_label, callback_data=back_callback), row=row)
        row += 1
    if show_main:
        kb.add(InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="nav_main"), row=row)
    return kb


def after_action_keyboard(*, add_callback: str = None, add_label: str = "➕ مورد دیگر",
                          back_callback: str = "nav_back_admin",
                          back_label: str = "↩️ بازگشت",
                          show_main: bool = False) -> InlineKeyboardMarkup:
    """بعد از یک عمل: افزودن دوباره + بازگشت (+ منوی اصلی فقط در صورت نیاز)."""
    kb = InlineKeyboardMarkup()
    row = 1
    if add_callback:
        kb.add(InlineKeyboardButton(text=add_label, callback_data=add_callback), row=row)
        row += 1
    kb.add(InlineKeyboardButton(text=back_label, callback_data=back_callback), row=row)
    row += 1
    if show_main:
        kb.add(InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="nav_main"), row=row)
    return kb

def after_add_keyboard(*, add_callback: str, add_label: str = "➕ افزودن مورد دیگر",
                       back_callback: str = "nav_back_admin",
                       back_label: str = "↩️ بازگشت",
                       show_main: bool = False) -> InlineKeyboardMarkup:
    """بعد از هر افزودن موفق: افزودن دوباره + بازگشت (+ منوی اصلی فقط اگر لازم)."""
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(text=add_label, callback_data=add_callback), row=1)
    kb.add(InlineKeyboardButton(text=back_label, callback_data=back_callback), row=2)
    if show_main:
        kb.add(InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="nav_main"), row=3)
    return kb


def regions_manage_keyboard(regions) -> InlineKeyboardMarkup:
    """مدیریت مناطق — فقط مدیر."""
    kb = InlineKeyboardMarkup()
    buttons = [
        InlineKeyboardButton(text=f"🗺 {r['name']}", callback_data=f"region_info:{r['id']}")
        for r in regions
    ]
    row = _add_two_col(kb, buttons)
    kb.add(InlineKeyboardButton(text="➕ ساخت منطقه جدید", callback_data="region_new"), row=row)
    return kb


def region_actions_keyboard(region_id: int, max_seniors_label: str = "") -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    buttons = [
        InlineKeyboardButton(text="✏️ تغییر نام", callback_data=f"region_rename:{region_id}"),
        InlineKeyboardButton(text="🗑 حذف", callback_data=f"region_del:{region_id}"),
        InlineKeyboardButton(text="📋 گروه‌های این منطقه", callback_data=f"region_groups:{region_id}"),
        InlineKeyboardButton(text="➕ ساخت گروه در این منطقه", callback_data=f"region_addgroup:{region_id}"),
        InlineKeyboardButton(
            text=f"🎓 سقف ارشد این منطقه{max_seniors_label}", callback_data=f"region_maxsnr:{region_id}"
        ),
        InlineKeyboardButton(text="📅 تقویم", callback_data=f"cal_region:{region_id}"),
    ]
    _add_two_col(kb, buttons)
    return kb


def pending_users_keyboard(users) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    for i, u in enumerate(users, start=1):
        name = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip() or str(u["user_id"])
        pnum = f" ({u['personnel_number']})" if u.get("personnel_number") else ""
        kb.add(
            InlineKeyboardButton(text=f"✅ تایید {name}{pnum}", callback_data=f"approve:{u['user_id']}"),
            row=i,
        )
    return kb


def all_users_keyboard(users, shift_mode: bool, letters: list = None) -> InlineKeyboardMarkup:
    """یک دکمه برای هر نفر: نام (شیفت / منطقه / نقش)."""
    kb = InlineKeyboardMarkup()
    if not users:
        return kb
    letters = letters or []
    row = 1
    for u in users:
        name = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip() or str(u["user_id"])
        # نقش
        if u.get("is_admin"):
            role = "مدیر"
        elif u.get("is_shift_lead"):
            role = "مسئول شیفت"
        elif u.get("is_senior"):
            role = "ارشد"
        else:
            from config import ROLE_LABELS
            role = ROLE_LABELS.get(u.get("role"), u.get("role") or "عضو")
        region = u.get("region_name") or u.get("group_name") or "-"
        # اگر region_name نباشد از group_name استفاده می‌کنیم
        if u.get("region_name"):
            region = u["region_name"]
        elif u.get("group_name"):
            region = u["group_name"]
        else:
            region = "-"
        shift_part = ""
        if shift_mode:
            si = u.get("shift_index")
            if si is not None and 0 <= int(si) < len(letters):
                shift_part = f"شیفت {letters[int(si)]}"
            else:
                shift_part = "شیفت؟"
        parts = [p for p in (shift_part, region, role) if p]
        label = f"👤 {name} ({' / '.join(parts)})"
        # محدودیت طول دکمه
        if len(label) > 60:
            label = label[:57] + "…"
        kb.add(
            InlineKeyboardButton(text=label, callback_data=f"member_info:{u['user_id']}"),
            row=row,
        )
        row += 1
    return kb


def member_actions_keyboard(user_id: int, *, can_change_region: bool, can_set_senior: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    buttons = [
        InlineKeyboardButton(text="📋 تغییر گروه", callback_data=f"member_chgrp:{user_id}"),
    ]
    if can_change_region:
        buttons.append(
            InlineKeyboardButton(text="🗺 تغییر منطقه", callback_data=f"member_chreg:{user_id}")
        )
    if can_set_senior:
        buttons.append(
            InlineKeyboardButton(text="⭐ تکنسین ارشد", callback_data=f"member_senior:{user_id}")
        )
        buttons.append(
            InlineKeyboardButton(text="⭐ لغو ارشدی", callback_data=f"member_unsenior:{user_id}")
        )
    buttons.append(
        InlineKeyboardButton(text="🗑 حذف کاربر", callback_data=f"member_remove:{user_id}")
    )
    _add_two_col(kb, buttons)
    return kb


def leave_decision_keyboard(leave_id: int, current_status: str) -> InlineKeyboardMarkup:
    """سه دکمه در ردیف‌های جدا (برای پیام تک‌مرخصی)."""
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton(
            text=("☑️ " if current_status == "approved" else "") + "✅ تایید",
            callback_data=f"decide:{leave_id}:approved",
        ),
        row=1,
    )
    kb.add(
        InlineKeyboardButton(
            text=("☑️ " if current_status == "reviewing" else "") + "🔍 بررسی",
            callback_data=f"decide:{leave_id}:reviewing",
        ),
        row=2,
    )
    kb.add(
        InlineKeyboardButton(
            text=("☑️ " if current_status == "rejected" else "") + "❌ عدم تایید",
            callback_data=f"decide:{leave_id}:rejected",
        ),
        row=3,
    )
    return kb


def batch_day_decision_keyboard(items: list) -> InlineKeyboardMarkup:
    """
    پیام چندروزه: کنار هر روز سه آیکون.
    items: [{leave_id, date_str, status}, ...]
    در پایان دو کلید ثبت و ویرایش.
    """
    kb = InlineKeyboardMarkup()
    row = 1
    for it in items:
        lid = it["leave_id"]
        label = it.get("date_str", str(lid))
        st = it.get("status", "pending")
        # ردیف تاریخ
        kb.add(InlineKeyboardButton(text=f"📅 {label} ({st})", callback_data="noop"), row=row)
        row += 1
        # سه آیکون در یک ردیف
        kb.add(InlineKeyboardButton(text="✅", callback_data=f"bdecide:{lid}:approved"), row=row)
        kb.add(InlineKeyboardButton(text="🔍", callback_data=f"bdecide:{lid}:reviewing"), row=row)
        kb.add(InlineKeyboardButton(text="❌", callback_data=f"bdecide:{lid}:rejected"), row=row)
        row += 1
    kb.add(InlineKeyboardButton(text="✔️ ثبت نهایی و ارسال به کاربر", callback_data="batch_commit"), row=row)
    row += 1
    kb.add(InlineKeyboardButton(text="✏️ ویرایش / ادامه", callback_data="batch_edit"), row=row)
    return kb


def settings_keyboard(current_mode: str, *, is_admin: bool = True, shift_count: int = None) -> InlineKeyboardMarkup:
    """منوی تنظیمات مدیر — مدیریت شیفت‌ها فقط از یک دکمه."""
    kb = InlineKeyboardMarkup()
    row = 1
    if not is_admin:
        return kb
    kb.add(
        InlineKeyboardButton(
            text=("☑️ " if current_mode == "workday" else "") + "🗓 نوع تقویم: روزکار",
            callback_data="setmode:workday",
        ),
        row=row,
    )
    row += 1
    kb.add(
        InlineKeyboardButton(
            text=("☑️ " if current_mode == "shift" else "") + "🗓 نوع تقویم: شیفتی",
            callback_data="setmode:shift",
        ),
        row=row,
    )
    row += 1
    if current_mode == "shift":
        kb.add(
            InlineKeyboardButton(text="🔧 پیکربندی کامل چرخه‌ی شیفت (از نو)", callback_data="cfgshift:start"),
            row=row,
        )
        row += 1
        kb.add(
            InlineKeyboardButton(
                text="⚙️ تنظیمات شیفت‌ها (روز کاری / مسئول / مناطق)",
                callback_data="settings_shifts",
            ),
            row=row,
        )
        row += 1
    kb.add(InlineKeyboardButton(text="🗺 مدیریت مناطق و گروه‌ها", callback_data="settings_regions"), row=row)
    row += 1
    kb.add(InlineKeyboardButton(text="👔 لیست همهٔ مسئولان شیفت", callback_data="settings_shiftleads"), row=row)
    row += 1
    kb.add(InlineKeyboardButton(text="👔 سقف تعداد مسئول شیفت", callback_data="settings_max_leads"), row=row)
    row += 1
    kb.add(InlineKeyboardButton(text="🎓 سقف پیش‌فرض ارشد", callback_data="settings_max_seniors"),
        row=row,
    )
    row += 1
    kb.add(
        InlineKeyboardButton(text="📅 ظرفیت مرخصی هم‌زمان ارشد در هر شیفت", callback_data="settings_max_snr_leave"), row=row)
    row += 1
    kb.add(InlineKeyboardButton(text="🎨 رنگ گروه‌ها", callback_data="settings_colors"), row=row)
    row += 1
    kb.add(InlineKeyboardButton(text="🏷 واژه‌های منطقه/گروه", callback_data="settings_terms"), row=row)
    row += 1
    kb.add(InlineKeyboardButton(text="🔁 جایگزینی مدیر", callback_data="settings_replaceadmin"), row=row)
    return kb



def lead_settings_keyboard() -> InlineKeyboardMarkup:
    """تنظیمات عملیاتی مسئول شیفت روی مناطق خودش."""
    kb = InlineKeyboardMarkup()
    buttons = [
        InlineKeyboardButton(text="📋 مدیریت گروه‌ها", callback_data="lead_cfg_groups"),
        InlineKeyboardButton(text="👥 مدیریت اعضا", callback_data="lead_cfg_members"),
        InlineKeyboardButton(text="🔄 شیفت کاری من", callback_data="lead_cfg_myshift"),
    ]
    _add_two_col(kb, buttons)
    return kb


def shift_leads_manage_keyboard(leads: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    row = 1
    for lead in leads:
        name = f"{lead.get('first_name') or ''} {lead.get('last_name') or ''}".strip() or str(lead["user_id"])
        regs = "، ".join(r["name"] for r in lead.get("regions") or []) or "—"
        kb.add(
            InlineKeyboardButton(
                text=f"👔 {name} ({regs})",
                callback_data=f"sl_info:{lead['user_id']}",
            ),
            row=row,
        )
        row += 1
    kb.add(InlineKeyboardButton(text="➕ انتصاب مسئول شیفت جدید", callback_data="sl_appoint"), row=row)
    return kb


def shift_lead_actions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    buttons = [
        InlineKeyboardButton(text="🗺 تغییر مناطق", callback_data=f"sl_setregs:{user_id}"),
        InlineKeyboardButton(text="🔄 تغییر شیفت", callback_data=f"sl_setshift:{user_id}"),
        InlineKeyboardButton(text="🗑 عزل", callback_data=f"sl_remove:{user_id}"),
    ]
    _add_two_col(kb, buttons)
    return kb


def color_picker_keyboard(group_id: int, colors: list = None) -> InlineKeyboardMarkup:
    """رنگ‌ها ثابت و فقط توسط مدیر انتخاب می‌شوند."""
    if colors is None:
        colors = [
            ("#4fa9a2", "فیروزه‌ای"),
            ("#d6a94a", "طلایی"),
            ("#c1554b", "آجری"),
            ("#5b5487", "بنفش"),
            ("#3d8b6e", "سبز"),
            ("#4a7cc5", "آبی"),
            ("#c47a3a", "نارنجی"),
            ("#8b5a7c", "ارغوانی"),
        ]
    kb = InlineKeyboardMarkup()
    buttons = [
        InlineKeyboardButton(text=name, callback_data=f"setcolor:{group_id}:{hex_c}")
        for hex_c, name in colors
    ]
    _add_two_col(kb, buttons)
    return kb
