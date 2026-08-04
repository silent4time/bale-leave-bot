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
BTN_REGION_LEAVES = "📋 وضعیت مرخصی منطقه من"
BTN_OVER_CAP_LEAVE = "➕ درخواست مرخصی اضافه بر ظرفیت"
BTN_BROADCAST = "📢 پیام همگانی"
CONTACT_CANCEL_TEXT = "❌ انصراف از افزودن مخاطب"
BTN_RESET_BOT = "🔄 ریست ربات"

def term_labels(term_region: str = "منطقه کاری", term_group: str = "گروه کاری") -> dict:
    """برچسب‌های منو/دکمه وابسته به واژهٔ منطقه و گروه."""
    tr = term_region or "منطقه کاری"
    tg = term_group or "گروه کاری"
    return {
        "region_leaves": f"📋 وضعیت مرخصی {tr} من",
        "admin_groups": f"📋 {tg}‌ها",
        "admin_calendar": f"📅 نمایش تقویم {tr}",
        "lead_groups": f"📋 {tg}‌های {tr} من",
        "lead_calendar": f"📅 نمایش تقویم {tr}",
        "lead_report": f"📊 گزارش {tr} من",
        "lead_settings": f"⚙️ تنظیمات {tr} من",
        "snr_queue": f"🕓 صف مرخصی اعضای {tr}",
        "snr_members": f"👥 اعضای {tr} من",
        "snr_groups": f"📋 {tg}‌های {tr} من",
        "snr_calendar": f"📅 تقویم {tr} من",
        "snr_report": f"📊 گزارش مرخصی {tr}",
        "settings_regions": f"🗺 مدیریت {tr} و {tg}",
        "settings_colors": f"🎨 رنگ {tg}‌ها",
        "settings_terms": f"🏷 واژه‌های {tr}/{tg}",
        "no_region": f"بدون {tr}",
        "no_group": f"بدون {tg}",
        "region_new": f"➕ ساخت {tr} جدید",
        "region_groups": f"📋 {tg}‌های این {tr}",
        "region_addgroup": f"➕ ساخت {tg} در این {tr}",
        "member_chgrp": f"📋 تغییر {tg}",
        "member_chreg": f"🗺 تغییر {tr}",
        "lead_cfg_groups": f"📋 مدیریت {tg}‌ها",
    }




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
    ADMIN_BTN_SHIFT_LEADS, ADMIN_BTN_SETTINGS, ADMIN_BTN_REPLACE_ADMIN, BTN_ADD_PEOPLE, BTN_REGION_LEAVES, BTN_OVER_CAP_LEAVE,
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
    LEAD_BTN_REPORT, LEAD_BTN_MY_SHIFT, LEAD_BTN_TRANSFER, LEAD_BTN_SETTINGS, BTN_ADD_PEOPLE, BTN_REGION_LEAVES, BTN_OVER_CAP_LEAVE,
}

# --- تکنسین ارشد ---
SNR_BTN_QUEUE = "🕓 صف مرخصی اعضای منطقه"
SNR_BTN_MEMBERS = "👥 اعضای منطقه من"
SNR_BTN_PENDING = "⏳ افراد در انتظار تایید"
SNR_BTN_GROUPS = "📋 گروه‌های منطقه من"
SNR_BTN_CALENDAR = "📅 تقویم منطقه من"
SNR_BTN_STATUS = "ℹ️ وضعیت من"
SNR_BTN_REPORT = "📊 گزارش مرخصی منطقه"

SNR_MENU_TEXTS = {
    SNR_BTN_QUEUE, SNR_BTN_MEMBERS, SNR_BTN_PENDING, SNR_BTN_GROUPS, SNR_BTN_CALENDAR, SNR_BTN_STATUS,
    SNR_BTN_REPORT, BTN_ADD_PEOPLE, BTN_REGION_LEAVES, BTN_OVER_CAP_LEAVE,
}


# --- عضو عادی ---
USER_BTN_CALENDAR = "📅 تقویم مرخصی"
USER_BTN_STATUS = "ℹ️ وضعیت من"

USER_MENU_TEXTS = {USER_BTN_CALENDAR, USER_BTN_STATUS, BTN_REGION_LEAVES, BTN_OVER_CAP_LEAVE}

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


def admin_menu(also_shift_lead: bool = False, term_region: str = "منطقه کاری", term_group: str = "گروه کاری") -> MenuKeyboardMarkup:
    """منوی مدیر."""
    L = term_labels(term_region, term_group)
    kb = MenuKeyboardMarkup()
    labels = [
        ADMIN_BTN_PENDING,
        ADMIN_BTN_MEMBERS,
        BTN_ADD_PEOPLE,
        L["region_leaves"],
        BTN_OVER_CAP_LEAVE,
        L["admin_calendar"],
        ADMIN_BTN_REPORT,
        BTN_BROADCAST,
        BTN_RESET_BOT,
        ADMIN_BTN_SETTINGS,
    ]
    if also_shift_lead:
        labels.insert(1, LEAD_BTN_QUEUE)
        labels.extend([LEAD_BTN_MY_SHIFT, LEAD_BTN_TRANSFER])
    _add_menu_two_col(kb, labels)
    return kb



def shift_lead_menu(term_region: str = "منطقه کاری", term_group: str = "گروه کاری") -> MenuKeyboardMarkup:
    L = term_labels(term_region, term_group)
    labels = [
        LEAD_BTN_QUEUE,
        L["lead_groups"],
        LEAD_BTN_MEMBERS,
        LEAD_BTN_PENDING,
        BTN_ADD_PEOPLE,
        L["region_leaves"],
        BTN_OVER_CAP_LEAVE,
        L["lead_calendar"],
        L["lead_report"],
        LEAD_BTN_MY_SHIFT,
        LEAD_BTN_TRANSFER,
        L["lead_settings"],
    ]
    kb = MenuKeyboardMarkup()
    _add_menu_two_col(kb, labels)
    return kb



def senior_menu(term_region: str = "منطقه کاری", term_group: str = "گروه کاری") -> MenuKeyboardMarkup:
    L = term_labels(term_region, term_group)
    labels = [
        L["snr_queue"],
        L["snr_members"],
        SNR_BTN_PENDING,
        BTN_ADD_PEOPLE,
        L["region_leaves"],
        BTN_OVER_CAP_LEAVE,
        L["snr_groups"],
        L["snr_calendar"],
        L["snr_report"],
        SNR_BTN_STATUS,
    ]
    kb = MenuKeyboardMarkup()
    _add_menu_two_col(kb, labels)
    return kb



def user_menu(term_region: str = "منطقه کاری", term_group: str = "گروه کاری") -> MenuKeyboardMarkup:
    L = term_labels(term_region, term_group)
    kb = MenuKeyboardMarkup()
    _add_menu_two_col(kb, [USER_BTN_CALENDAR, L["region_leaves"], BTN_OVER_CAP_LEAVE, USER_BTN_STATUS])
    return kb



def contact_request_menu() -> MenuKeyboardMarkup:
    """کیبورد موقتی که با زدنش، بله مخاطبی از دفترچه‌تلفن کاربر برای ربات ارسال می‌کند."""
    kb = MenuKeyboardMarkup()
    try:
        kb.add(MenuKeyboardButton("📱 انتخاب و ارسال مخاطب", request_contact=True))
    except TypeError:
        # اگر API پارامتر request_contact نداشت
        kb.add(MenuKeyboardButton("📱 انتخاب و ارسال مخاطب"))
    cancel = globals().get("CONTACT_CANCEL_TEXT") or "❌ انصراف از افزودن مخاطب"
    kb.add(MenuKeyboardButton(cancel))
    return kb


def menu_for_user(db_user: dict, term_region: str = "منطقه کاری", term_group: str = "گروه کاری") -> MenuKeyboardMarkup:
    """انتخاب منوی مناسب (اولویت: مدیر > مسئول شیفت > ارشد > عضو)."""
    if db_user.get("is_admin"):
        return admin_menu(
            also_shift_lead=bool(db_user.get("is_shift_lead")),
            term_region=term_region,
            term_group=term_group,
        )
    if db_user.get("is_shift_lead"):
        return shift_lead_menu(term_region, term_group)
    if db_user.get("is_senior") or db_user.get("role") == "snr":
        return senior_menu(term_region, term_group)
    return user_menu(term_region, term_group)



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


def broadcast_options_keyboard(with_btn: bool = True) -> InlineKeyboardMarkup:
    """فعال/غیرفعال بودن دکمه همراه پیام همگانی."""
    kb = InlineKeyboardMarkup()
    tick_on = "☑️" if with_btn else "☐"
    tick_off = "☑️" if not with_btn else "☐"
    kb.add(InlineKeyboardButton(text=f"{tick_on} ارسال با دکمه «ریست ربات»", callback_data="bcastopt:1"), row=1)
    kb.add(InlineKeyboardButton(text=f"{tick_off} ارسال بدون دکمه", callback_data="bcastopt:0"), row=2)
    kb.add(InlineKeyboardButton(text="✏️ ادامه و نوشتن موضوع", callback_data="bcastopt:go"), row=3)
    return kb


def succession_method_keyboard(purpose: str) -> InlineKeyboardMarkup:
    """purpose: replace_admin | transfer_lead | appoint_lead"""
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(text="📱 انتخاب از دفترچه تلفن", callback_data=f"succvia:contact:{purpose}"), row=1)
    if purpose == "appoint_lead":
        kb.add(InlineKeyboardButton(text="👥 انتخاب از لیست اعضا", callback_data=f"succvia:list:{purpose}"), row=2)
        kb.add(InlineKeyboardButton(text="🔗 ساخت لینک دعوت مسئول شیفت", callback_data=f"succvia:link:{purpose}"), row=3)
    else:
        kb.add(InlineKeyboardButton(text="🔗 ساخت لینک واگذاری", callback_data=f"succvia:link:{purpose}"), row=2)
    kb.add(InlineKeyboardButton(text="↩️ بازگشت", callback_data="nav_back_admin"), row=4)
    kb.add(InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="nav_main"), row=5)
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
            InlineKeyboardButton(text="بدون منطقه", callback_data=f"{callback_prefix}:none")  # override via caller if needed
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
    row += 1
    kb.add(InlineKeyboardButton(text="↩️ بازگشت", callback_data="nav_back_admin"), row=row)
    row += 1
    kb.add(InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="nav_main"), row=row)
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


def regions_pick_for_groups_keyboard(regions, prefix: str = "myg_reg") -> InlineKeyboardMarkup:
    """مرحله ۱: انتخاب منطقه برای دیدن/تنظیم گروه‌ها."""
    kb = InlineKeyboardMarkup()
    row = 1
    for r in regions:
        kb.add(
            InlineKeyboardButton(
                text=f"🗺 {r.get('name') or r.get('id')}",
                callback_data=f"{prefix}:{r['id']}",
            ),
            row=row,
        )
        row += 1
    kb.add(InlineKeyboardButton(text="↩️ بازگشت", callback_data="nav_back_admin"), row=row)
    row += 1
    kb.add(InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="nav_main"), row=row)
    return kb


def groups_edit_keyboard(groups, *, back_callback: str = "nav_back_admin") -> InlineKeyboardMarkup:

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
    kb.add(InlineKeyboardButton(text="↩️ بازگشت", callback_data=back_callback), row=80)
    kb.add(InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="nav_main"), row=81)
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
    row += 1
    kb.add(InlineKeyboardButton(text="↩️ بازگشت", callback_data="nav_back_admin"), row=row)
    row += 1
    kb.add(InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="nav_main"), row=row)
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
    kb.add(InlineKeyboardButton(text="↩️ بازگشت", callback_data="settings_regions"), row=20)
    kb.add(InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="nav_main"), row=21)
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


def pick_member_keyboard(users, letters: list = None, prefix: str = "appoint_sl_pick") -> InlineKeyboardMarkup:
    """انتخاب عضو از لیست (بدون user_id دستی)."""
    kb = InlineKeyboardMarkup()
    letters = letters or []
    row = 1
    for u in users:
        name = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip() or str(u["user_id"])
        region = u.get("region_name") or "-"
        si = u.get("shift_index")
        if si is not None and letters and 0 <= int(si) < len(letters):
            shift_part = f"شیفت {letters[int(si)]}"
        else:
            shift_part = "—"
        label = f"{name} | {shift_part} | {region}"
        if len(label) > 60:
            label = label[:57] + "…"
        kb.add(InlineKeyboardButton(text=label, callback_data=f"{prefix}:{u['user_id']}"), row=row)
        row += 1
    kb.add(InlineKeyboardButton(text="↩️ بازگشت", callback_data="sl_appoint"), row=row)
    row += 1
    kb.add(InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="nav_main"), row=row)
    return kb


def all_users_keyboard(users, shift_mode: bool, letters: list = None) -> InlineKeyboardMarkup:
    """دکمه شیشه‌ای: نام | شیفت | منطقه | نقش"""
    kb = InlineKeyboardMarkup()
    if not users:
        return kb
    letters = letters or []
    row = 1
    for u in users:
        name = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip() or str(u["user_id"])
        if u.get("is_admin"):
            role = "مدیر"
        elif u.get("is_shift_lead"):
            role = "مسئول شیفت"
        elif u.get("is_senior") or u.get("role") == "snr":
            role = "ارشد"
        else:
            from config import ROLE_LABELS
            role = ROLE_LABELS.get(u.get("role"), u.get("role") or "عضو")
        region = u.get("region_name") or "-"
        group = u.get("group_name") or ""
        si = u.get("shift_index")
        if si is not None and letters and 0 <= int(si) < len(letters):
            shift_part = f"شیفت {letters[int(si)]}"
        elif si is not None:
            shift_part = f"شیفت {si}"
        else:
            shift_part = "بدون شیفت"
        # نام | شیفت | منطقه | گروه | نقش (نقش برای مسئول شیفت/مدیر/ارشد ضروری است)
        parts = [name, shift_part, region]
        if group:
            parts.append(group)
        parts.append(role)
        label = " | ".join(parts)
        if len(label) > 64:
            label = label[:61] + "…"
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


def settings_keyboard(current_mode: str, *, is_admin: bool = True, shift_count: int = None,
                     term_region: str = "منطقه کاری", term_group: str = "گروه کاری") -> InlineKeyboardMarkup:
    """منوی تنظیمات مدیر — مدیریت شیفت‌ها فقط از یک دکمه."""
    kb = InlineKeyboardMarkup()
    L = term_labels(term_region, term_group)

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
    kb.add(InlineKeyboardButton(text=L["settings_regions"], callback_data="settings_regions"), row=row)
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
    kb.add(InlineKeyboardButton(text=L["settings_colors"], callback_data="settings_colors"), row=row)
    row += 1
    kb.add(InlineKeyboardButton(text=L["settings_terms"], callback_data="settings_terms"), row=row)
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
    row += 1
    kb.add(InlineKeyboardButton(text="↩️ بازگشت", callback_data="nav_back_admin"), row=row)
    row += 1
    kb.add(InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="nav_main"), row=row)
    return kb


def shift_lead_actions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    buttons = [
        InlineKeyboardButton(text="🗺 تغییر مناطق", callback_data=f"sl_setregs:{user_id}"),
        InlineKeyboardButton(text="🔄 تغییر شیفت", callback_data=f"sl_setshift:{user_id}"),
        InlineKeyboardButton(text="🗑 عزل", callback_data=f"sl_remove:{user_id}"),
    ]
    _add_two_col(kb, buttons)
    row = 10
    kb.add(InlineKeyboardButton(text="↩️ بازگشت", callback_data="settings_shiftleads"), row=row)
    row += 1
    kb.add(InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="nav_main"), row=row)
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


def report_months_keyboard(months: list) -> InlineKeyboardMarkup:
    """months: list of (jy, jm, label)"""
    kb = InlineKeyboardMarkup()
    row = 1
    for jy, jm, label in months:
        kb.add(
            InlineKeyboardButton(text=label, callback_data=f"rptm:{jy}:{jm}"),
            row=row,
        )
        row += 1
    return kb
