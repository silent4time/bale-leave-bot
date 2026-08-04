# -*- coding: utf-8 -*-
import asyncio
import logging
import secrets

from bale import Bot, Message, CallbackQuery

import config
import database as db
import jalali
import shift
import calendar_ui
import keyboards as kb
import states
import cache
import report_pdf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("leave_bot")

if not config.BOT_TOKEN:
    raise SystemExit("متغیر محیطی BALE_BOT_TOKEN تنظیم نشده است.")

client = Bot(token=config.BOT_TOKEN)

# برچسب‌های منو — مقاوم به نسخهٔ قدیمی keyboards
ADD_PEOPLE_LABELS = (
    getattr(kb, "BTN_ADD_PEOPLE", None),
    getattr(kb, "BTN_ADD_CONTACT", None),
    "➕ اضافه کردن افراد",
    "📇 افزودن عضو با مخاطب",
)
ADD_PEOPLE_LABELS = tuple(x for x in ADD_PEOPLE_LABELS if x)
_bot_username = {"value": None}


async def run_db(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)



def filter_members_for_viewer(users: list, viewer: dict) -> list:
    """مسئول شیفت فقط ارشد/تکنسین/اپراتور را می‌بیند — نه مسئول شیفت و نه مدیر."""
    if not users:
        return []
    if viewer and viewer.get("is_shift_lead") and not viewer.get("is_admin"):
        out = []
        for u in users:
            if u.get("is_admin") or u.get("is_shift_lead"):
                continue
            # فقط snr / tech / op (و نقش‌های عادی عضو)
            out.append(u)
        return out
    return list(users)


def allowed_add_roles(db_user: dict) -> list:
    """نقش‌هایی که این کاربر مجاز است اضافه کند."""
    if not db_user:
        return []
    if db_user.get("is_admin"):
        return ["lead", "snr", "tech", "op"]
    if db_user.get("is_shift_lead"):
        return ["snr", "tech", "op"]
    if db_user.get("is_senior") or db_user.get("role") == "snr":
        return ["tech", "op"]
    return []


def display_name(row: dict) -> str:
    first = row.get("first_name") or row.get("bale_first_name")
    last = row.get("last_name")
    return f"{first or ''} {last or ''}".strip() or "کاربر"


def today_str() -> str:
    y, m, d = jalali.today_jalali()
    return jalali.parse_date_str(y, m, d)


STATUS_FA = {"pending": "ثبت موقت (در انتظار تایید)", "reviewing": "در حال بررسی", "approved": "تایید شده"}

_ERROR_FA_MAP = {
    "not a shift lead": "این فرد هنوز به‌عنوان مسئول شیفت منصوب نشده — ابتدا باید با «انتصاب مسئول شیفت جدید» منصوب شود.",
    "source is not a shift lead": "شما مسئول شیفت نیستید، نمی‌توانید نقش را منتقل کنید.",
    "target user not found": "کاربر مقصد پیدا نشد.",
    "cannot transfer to self": "نمی‌توانید نقش را به خودتان منتقل کنید.",
    "user not found": "کاربر پیدا نشد.",
    "user not approved": "این کاربر هنوز تایید نشده است.",
    "at least one region required": "حداقل یک منطقه را انتخاب کنید.",
    "region not found": "منطقه پیدا نشد.",
}


async def viewer_can_manage_user(viewer_uid: int, target_uid: int) -> bool:
    """آیا viewer_uid اجازه دارد روی target_uid عملیات سطح-منطقه (تغییر گروه/منطقه/ارشدسازی) انجام دهد؟
    فقط مدیر و مسئول‌شیفتِ همان منطقه — تکنسین ارشد این سطح دسترسی را ندارد."""
    viewer = await run_db(db.get_user, viewer_uid)
    if not viewer:
        return False
    if viewer.get("is_admin"):
        return True
    target = await run_db(db.get_user, target_uid)
    if not target or not target.get("region_id"):
        return False
    return await run_db(db.can_manage_region, viewer_uid, target["region_id"])


async def viewer_can_manage_user_or_own_group(viewer_uid: int, target_uid: int) -> bool:
    """مثل viewer_can_manage_user، به‌علاوه‌ی تکنسین ارشدِ همان گروهِ عضو هدف (برای حذف عضو)."""
    if await viewer_can_manage_user(viewer_uid, target_uid):
        return True
    target = await run_db(db.get_user, target_uid)
    if not target or not target.get("group_id"):
        return False
    return await run_db(db.can_manage_group, viewer_uid, target["group_id"])


def fa_error(e: Exception) -> str:
    """پیام‌های ValueError داخلی (که به انگلیسی نوشته شده‌اند) را برای کاربر فارسی می‌کند."""
    text = str(e)
    if text in _ERROR_FA_MAP:
        return _ERROR_FA_MAP[text]
    for key, fa in _ERROR_FA_MAP.items():
        if key in text:
            return fa
    if text.startswith("max shift leads reached"):
        return "به سقف مجاز تعداد مسئولان شیفت رسیده‌اید (از تنظیمات قابل تغییر است)."
    if text.startswith("region") and "not found" in text:
        return "منطقه‌ی انتخاب‌شده پیدا نشد."
    return f"خطا: {text}"


# ==========================================================================
#  رویدادهای پایه
# ==========================================================================

@client.event
async def on_ready():
    await run_db(db.init_db)
    _bot_username["value"] = getattr(client.user, "username", None)
    logger.info("ربات آماده است: %s", client.user)
    asyncio.create_task(_monthly_report_loop())


async def _monthly_report_loop():
    """هر ساعت یک‌بار چک می‌کند آیا ماه عوض شده و گزارش ماه قبل لازم است."""
    import monthly_report

    async def _get_setting(key):
        return await run_db(db.get_setting, key)

    async def _set_setting(key, value):
        await run_db(db.set_setting, key, value)

    async def _admins():
        return await run_db(db.list_admin_ids)

    async def _seniors():
        return await run_db(db.list_seniors_by_group)

    async def _fetch(year, month, group_id):
        if group_id is None:
            return await run_db(db.all_leaves_in_month, year, month)
        return await run_db(db.group_leaves_in_month, group_id, year, month)

    async def _send(uid, text):
        await client.send_message(uid, text)

    while True:
        try:
            await monthly_report.maybe_send_monthly_reports(
                get_setting=_get_setting,
                set_setting=_set_setting,
                list_admin_ids=_admins,
                list_seniors_by_group=_seniors,
                fetch_leaves_for_month=_fetch,
                send_message=_send,
            )
        except Exception:
            logger.exception("monthly_report loop error")
        await asyncio.sleep(3600)


@client.event
async def on_message(message: Message):
    author = message.author
    if author is None or getattr(author, "is_bot", False):
        return

    contact = getattr(message, "contact", None)
    if contact is not None:
        st = states.get_state(author.id) or {}
        if st.get("action") == "awaiting_contact":
            await handle_contact_shared(message, author, contact)
        return

    if not message.content:
        return
    text = message.content.strip()

    if text.startswith("/start"):
        try:
            await handle_start(message, author, text)
        except Exception:
            logger.exception("خطا در /start برای %s", getattr(author, "id", None))
            try:
                await message.reply("خطایی رخ داد. دوباره /start را بزنید یا به مدیر اطلاع دهید.")
            except Exception:
                pass
        return

    state = states.get_state(author.id)
    if state and state.get("action") == "awaiting_contact":
        cancel_txt = getattr(kb, "CONTACT_CANCEL_TEXT", "❌ انصراف از افزودن مخاطب")
        if text == cancel_txt:
            states.clear_state(author.id)
            db_user = await run_db(db.get_user, author.id)
            await message.reply("لغو شد.", components=await menu_with_terms(db_user))
            return
        # اگر کاربر دکمه منوی اصلی زد، از حالت مخاطب خارج شو
        states.clear_state(author.id)
        # ادامه می‌دهد تا دکمه منو پردازش شود
        state = None
    if state:
        consumed = await handle_stateful_text(message, author, text, state)
        if consumed:
            return

    db_user = await run_db(db.get_user, author.id)
    if not db_user or not db_user["profile_complete"]:
        await message.reply("لطفاً ابتدا با دستور /start ثبت‌نام را تکمیل کنید.")
        return

    if text in ADD_PEOPLE_LABELS:
        roles = allowed_add_roles(db_user)
        if not roles:
            await message.reply("شما اجازه‌ی افزودن عضو ندارید.")
            return
        await message.reply(
            "روش افزودن عضو را انتخاب کنید:",
            components=kb.add_people_method_keyboard(),
        )
        return

    # هدایت به منوی نقش
    try:
        if db_user.get("is_admin"):
            await handle_admin_menu(message, author, text)
            return
        if db_user.get("is_shift_lead"):
            await handle_shift_lead_menu(message, author, db_user, text)
            return
        if db_user.get("is_senior") or db_user.get("role") == "snr":
            await handle_senior_menu(message, author, db_user, text)
            return
        if db_user.get("approved"):
            await handle_user_menu(message, author, db_user, text)
            return
        await message.reply(
            "هنوز در انتظار تایید هستید. پس از تعیین نقش و گروه می‌توانید از منو استفاده کنید."
        )
    except Exception:
        logger.exception("خطا در پردازش منو text=%r user=%s", text, author.id)
        try:
            await message.reply(
                "خطا در پردازش دکمه. دوباره /start را بزنید.",
                components=await menu_with_terms(db_user),
            )
        except Exception:
            pass


async def handle_start(message: Message, author, text: str):
    parts = text.split(maxsplit=1)
    token = parts[1].strip() if len(parts) > 1 else None
    uid = author.id

    await run_db(db.touch_user_bale_info, uid, author.first_name, author.username)
    db_user = await run_db(db.get_user, uid)

    if db_user and db_user["profile_complete"]:
        await show_returning_menu(message, db_user)
        return

    states.set_state(uid, action="reg_first_name", token=token)
    await message.reply(
        "👋 خوش آمدید!\n"
        "برای شروع لازم است یک فرم کوتاه ثبت‌نام تکمیل کنید (حتی اگر مدیر هستید).\n\n"
        "لطفاً «نام» خود را وارد کنید:"
    )


async def show_returning_menu(message: Message, db_user: dict):
    name = display_name(db_user)
    menu = await menu_with_terms(db_user)
    if db_user.get("is_admin"):
        await message.reply(f"سلام {name}! به پنل مدیریت خوش برگشتید.", components=menu)
    elif not db_user.get("approved"):
        await message.reply("سلام! هنوز در انتظار تایید مدیر (تعیین نقش و گروه) هستید.")
    elif db_user.get("is_shift_lead"):
        await message.reply(f"سلام {name}! به پنل مسئول شیفت خوش برگشتید.", components=menu)
    elif db_user.get("is_senior") or db_user.get("role") == "snr":
        await message.reply(f"سلام {name}! به پنل تکنسین ارشد خوش برگشتید.", components=menu)
    else:
        await message.reply(f"سلام {name}! خوش برگشتید.", components=menu)


async def handle_registration_step(message: Message, author, text: str, state: dict) -> bool:
    action = state["action"]
    uid = author.id

    if action == "reg_first_name":
        v = text.strip()
        if not v:
            await message.reply("نام نمی‌تواند خالی باشد. دوباره وارد کنید:")
            return True
        states.set_state(uid, action="reg_last_name", token=state.get("token"), first_name=v)
        await message.reply("نام خانوادگی خود را وارد کنید:")
        return True

    if action == "reg_last_name":
        v = text.strip()
        if not v:
            await message.reply("نام خانوادگی نمی‌تواند خالی باشد. دوباره وارد کنید:")
            return True
        states.set_state(uid, action="reg_personnel", token=state.get("token"),
                          first_name=state["first_name"], last_name=v)
        await message.reply("شماره پرسنلی خود را وارد کنید:")
        return True

    if action == "reg_personnel":
        v = text.strip()
        if not v:
            await message.reply("شماره پرسنلی نمی‌تواند خالی باشد. دوباره وارد کنید:")
            return True
        await run_db(db.complete_profile, uid, state["first_name"], state["last_name"], v)
        token = state.get("token")
        states.clear_state(uid)
        await finalize_registration(message, author, token)
        return True

    return False


async def finalize_registration(message: Message, author, token):
    uid = author.id
    claimed, _ = await run_db(db.try_claim_admin, uid)
    if claimed:
        mode = await run_db(db.get_calendar_mode)
        await message.reply(
            "🎉 تبریک! شما به‌عنوان اولین کاربری که ثبت‌نام را تکمیل کرد، مدیر ربات شدید.\n\n"
            "قبل از هر کار دیگری، لازم است نوع تقویم را مشخص کنید (روزکار یا شیفتی؛ اگر شیفتی را "
            "انتخاب کنید، مراحل تعریف شیفت هم همین‌جا از شما پرسیده می‌شود).\n"
            "بعد از این مرحله، از منوی مدیر می‌توانید مناطق کاری بسازید — هر منطقه به‌طور خودکار "
            "با گروه‌های «مسئول شیفت / تکنسین ارشد / تکنسین / اپراتور» ساخته می‌شود و می‌توانید "
            "همان‌جا آن‌ها را ویرایش، اضافه یا حذف کنید."
        )
        await message.reply("نوع تقویم را انتخاب کنید:", components=kb.settings_keyboard(mode, is_admin=True))
        return

    if token:
        invite = await run_db(db.get_invite, token)
        # دعوت مسئول شیفت (چند منطقه، بدون گروه زیرمنطقه)
        if invite and (invite.get("is_shift_lead") or invite.get("role") == "lead"):
            await run_db(db.increment_invite_use, token)
            region_ids = invite.get("region_ids") or []
            if not region_ids and invite.get("region_id"):
                region_ids = [invite["region_id"]]
            try:
                purpose = await run_db(db.get_setting, f"invite_purpose_{token}", "")
                creator = invite.get("created_by")
                if purpose == "transfer_lead" and creator:
                    await run_db(db.transfer_shift_lead, int(creator), uid)
                else:
                    await run_db(
                        db.appoint_shift_lead, uid, region_ids, invite.get("shift_index")
                    )
            except ValueError as e:
                await message.reply(fa_error(e))
                return
            except Exception as e:
                await message.reply(f"خطا در انتصاب: {e}")
                return
            names = []
            for rid in region_ids:
                r = await run_db(db.get_region, rid)
                if r:
                    names.append(r["name"])
            shift_txt = ""
            if invite.get("shift_index") is not None:
                cfg = await run_db(db.get_shift_config)
                if cfg:
                    letters = shift.shift_letters(cfg["shift_count"])
                    if invite["shift_index"] < len(letters):
                        shift_txt = f"\nشیفت: {letters[invite['shift_index']]}"
            await message.reply(
                f"✅ ثبت‌نام شما تکمیل شد.\nنقش: مسئول شیفت (سرگروه مناطق){shift_txt}\n"
                f"مناطق تحت مدیریت: {', '.join(names) or '-'}",
                components=await menu_with_terms(db_user),
            )
            return
        if invite and invite.get("role") and invite.get("group_id"):
            await run_db(db.increment_invite_use, token)
            role = invite["role"]
            if role == "lead":
                role = "tech"
            is_senior = 1 if role == "snr" else 0
            region_id = invite.get("region_id")
            await run_db(
                db.approve_user, uid, role, invite["group_id"],
                invite.get("shift_index"), region_id, is_senior,
            )
            group = await run_db(db.get_group, invite["group_id"])
            rid = region_id or (group.get("region_id") if group else None)
            region = await run_db(db.get_region, rid) if rid else None
            role_label = config.ROLE_LABELS.get(role, role)
            shift_txt = ""
            if invite.get("shift_index") is not None:
                cfg = await run_db(db.get_shift_config)
                if cfg:
                    letters = shift.shift_letters(cfg["shift_count"])
                    if invite["shift_index"] < len(letters):
                        shift_txt = f"\nشیفت: {letters[invite['shift_index']]}"
            db_user = await run_db(db.get_user, uid)
            term_r = await _term_region()
            term_g = await _term_group()
            await message.reply(
                f"✅ ثبت‌نام شما تکمیل و عضویتتان تایید شد.\nنقش: {role_label}{shift_txt}\n"
                f"{term_r}: {region['name'] if region else '-'}\n{term_g}: {group['name'] if group else '-'}",
                components=await menu_with_terms(db_user) if db_user else kb.user_menu(),
            )
            return
        if invite:
            await run_db(db.increment_invite_use, token)
            purpose = await run_db(db.get_setting, f"invite_purpose_{token}", "")
            if purpose == "replace_admin":
                creator = invite.get("created_by")
                if creator and int(creator) != uid:
                    try:
                        await run_db(db.replace_admin, int(creator), uid)
                        db_user = await run_db(db.get_user, uid)
                        await message.reply(
                            "🎉 شما مدیر جدید ربات شدید.",
                            components=await menu_with_terms(db_user),
                        )
                        return
                    except Exception as e:
                        await message.reply(f"خطا در انتقال مدیریت: {e}")
                        return

    await message.reply("✅ ثبت‌نام شما تکمیل شد؛ اکنون منتظر تایید مدیر (تعیین نقش و گروه) بمانید.")
    db_user = await run_db(db.get_user, uid)
    await notify_admins_new_pending(db_user)


async def notify_admins_new_pending(db_user: dict):
    admin_ids = await run_db(db.list_admin_ids)
    for admin_id in admin_ids:
        try:
            await client.send_message(
                admin_id,
                f"👤 کاربر جدید «{display_name(db_user)}» ({db_user.get('personnel_number') or '-'}) "
                f"منتظر تایید است. از «⏳ افراد در انتظار تایید» اقدام کنید.",
            )
        except Exception:
            logger.exception("خطا در اطلاع‌رسانی به مدیر %s", admin_id)


async def handle_contact_shared(message: Message, author, contact):
    """دفترچه تلفن: افزودن عضو / جانشینی مدیر / انتقال مسئول شیفت."""
    st = states.get_state(author.id) or {}
    purpose = st.get("purpose") or "add"
    states.clear_state(author.id)
    db_user = await run_db(db.get_user, author.id)
    if not db_user:
        return

    target_user = getattr(contact, "user", None)
    shared_first = getattr(contact, "first_name", None) or ""
    shared_last = getattr(contact, "last_name", None) or ""
    name = f"{shared_first} {shared_last}".strip()
    phone = getattr(contact, "phone_number", None) or "؟"

    # --- جانشینی مدیر ---
    if purpose == "replace_admin":
        if not db_user.get("is_admin"):
            await message.reply("فقط مدیر می‌تواند جانشین تعیین کند.")
            return
        if target_user is None:
            await message.reply(
                "این مخاطب به حساب بله وصل نشد. از «لینک واگذاری» استفاده کنید یا مخاطبی که در بله است انتخاب کنید.",
                components=await menu_with_terms(db_user),
            )
            return
        target_uid = target_user.id
        await run_db(db.touch_user_bale_info, target_uid, getattr(target_user, "first_name", None) or shared_first, getattr(target_user, "username", None))
        try:
            await run_db(db.replace_admin, author.id, target_uid)
        except Exception as e:
            await message.reply(f"خطا در جایگزینی مدیر: {e}", components=await menu_with_terms(db_user))
            return
        await message.reply(
            f"✅ مدیریت به «{name or target_uid}» منتقل شد. شما دیگر مدیر نیستید.",
            components=kb.user_menu(),
        )
        try:
            nu = await run_db(db.get_user, target_uid)
            await client.send_message(target_uid, "🎉 شما مدیر جدید ربات شدید.", components=await menu_with_terms(nu) if nu else None)
        except Exception:
            logger.exception("notify new admin")
        return

    # --- انتقال مسئول شیفت ---
    if purpose == "transfer_lead":
        if not db_user.get("is_shift_lead"):
            await message.reply("فقط مسئول شیفت می‌تواند نقش را منتقل کند.")
            return
        if target_user is None:
            await message.reply(
                "این مخاطب به حساب بله وصل نشد. از «لینک واگذاری» استفاده کنید.",
                components=await menu_with_terms(db_user),
            )
            return
        target_uid = target_user.id
        await run_db(db.touch_user_bale_info, target_uid, getattr(target_user, "first_name", None) or shared_first, getattr(target_user, "username", None))
        try:
            await run_db(db.transfer_shift_lead, author.id, target_uid)
        except Exception as e:
            await message.reply(f"خطا در انتقال مسئول شیفت: {e}", components=await menu_with_terms(db_user))
            return
        await message.reply(
            f"✅ نقش مسئول شیفت به «{name or target_uid}» منتقل شد.",
            components=await menu_with_terms(await run_db(db.get_user, author.id)),
        )
        try:
            nu = await run_db(db.get_user, target_uid)
            await client.send_message(target_uid, "🎉 شما مسئول شیفت شدید.", components=await menu_with_terms(nu) if nu else None)
        except Exception:
            logger.exception("notify new lead")
        return

    # --- انتصاب مسئول شیفت از مخاطب ---
    if purpose == "appoint_lead":
        if not db_user.get("is_admin"):
            await message.reply("فقط مدیر.")
            return
        if target_user is None:
            await message.reply(
                "مخاطب به حساب بله وصل نشد. از لینک دعوت یا لیست اعضا استفاده کنید.",
                components=await menu_with_terms(db_user),
            )
            return
        target_uid = target_user.id
        await run_db(db.touch_user_bale_info, target_uid, getattr(target_user, "first_name", None) or shared_first, getattr(target_user, "username", None))
        regions = await run_db(db.list_regions)
        if not regions:
            await message.reply("ابتدا منطقه بسازید.")
            return
        states.set_state(author.id, action="sl_pick_regions", target_uid=target_uid, selected=[], appointing=True)
        await message.reply(
            f"مناطق تحت مدیریت «{name or target_uid}» را انتخاب کنید:",
            components=kb.multi_region_toggle_keyboard(regions, set(), "sl_tog"),
        )
        return

    # --- افزودن عضو ---
    can_add = bool(allowed_add_roles(db_user))

    if not can_add:
        await message.reply("شما اجازه‌ی افزودن عضو ندارید.")
        return

    if target_user is not None:
        target_uid = target_user.id
        await run_db(
            db.touch_user_bale_info, target_uid,
            getattr(target_user, "first_name", None) or shared_first,
            getattr(target_user, "username", None),
        )
        existing = await run_db(db.get_user, target_uid)
        if existing and existing.get("is_admin"):
            await message.reply("این شخص از قبل مدیر ربات است.", components=await menu_with_terms(db_user))
            return
        if existing and existing.get("approved"):
            await message.reply(
                f"«{name or display_name(existing)}» از قبل عضو تایید‌شده است.",
                components=await menu_with_terms(db_user),
            )
            return
        roles = allowed_add_roles(db_user)
        await message.reply(
            f"✅ مخاطب دریافت شد. نقش «{name or target_uid}» را یک‌بار انتخاب کنید:",
            components=kb.role_select_keyboard(f"setrole:{target_uid}", allowed_roles=roles),
        )
        return

    roles = allowed_add_roles(db_user)
    await message.reply(
        f"این شماره به حساب بله وصل نشد ({phone}). نقش لینک دعوت را یک‌بار انتخاب کنید:",
        components=kb.role_select_keyboard("invrole", allowed_roles=roles),
    )



async def handle_stateful_text(message: Message, author, text: str, state: dict) -> bool:
    action = state.get("action")

    if action in ("reg_first_name", "reg_last_name", "reg_personnel"):
        return await handle_registration_step(message, author, text, state)

    if text in kb.ALL_MENU_TEXTS or text.startswith("/start"):
        states.clear_state(author.id)
        return False

    if action == "new_group_name":
        name = text.strip()
        region_id = state.get("region_id")
        if not name:
            await message.reply("نام گروه نمی‌تواند خالی باشد. دوباره وارد کنید:")
            return True
        if not region_id:
            await message.reply("خطای داخلی: منطقه مشخص نیست. لطفاً از «📋 مناطق» دوباره شروع کنید.")
            states.clear_state(author.id)
            return True
        states.set_state(author.id, action="new_group_capacity", name=name, region_id=region_id)
        await message.reply(f"ظرفیت هم‌زمان مجاز برای گروه «{name}» را به‌صورت عدد وارد کنید (مثلاً 2):")
        return True

    if action == "new_group_capacity":
        if not text.strip().isdigit() or int(text.strip()) < 1:
            await message.reply("لطفاً یک عدد صحیح مثبت وارد کنید:")
            return True
        capacity = int(text.strip())
        name = state["name"]
        region_id = state.get("region_id")
        gid = await run_db(db.create_group, name, capacity, region_id)
        states.clear_state(author.id)
        if gid is None:
            await message.reply(
                f"⚠️ گروهی با نام «{name}» از قبل وجود دارد.",
                components=kb.after_add_keyboard(
                    add_callback=f"region_addgroup:{region_id}" if region_id else "nav_back_admin",
                    add_label="➕ ساخت گروه دیگر",
                    back_callback=f"region_groups:{region_id}" if region_id else "nav_back_admin",
                ),
            )
        else:
            await message.reply(
                f"✅ گروه «{name}» با ظرفیت هم‌زمان {capacity} نفر ساخته شد.",
                components=kb.after_add_keyboard(
                    add_callback=f"region_addgroup:{region_id}" if region_id else "nav_back_admin",
                    add_label="➕ ساخت گروه دیگر",
                    back_callback=f"region_groups:{region_id}" if region_id else "nav_back_admin",
                ),
            )
        return True

    if action == "rename_group":
        name = text.strip()
        gid = state.get("group_id")
        if not name or not gid:
            await message.reply("نام نامعتبر.")
            return True
        ok = await run_db(db.rename_group, gid, name)
        states.clear_state(author.id)
        await message.reply("✅ تغییر نام انجام شد." if ok else "نام تکراری است.")
        return True

    if action == "edit_capacity":
        if not text.strip().isdigit() or int(text.strip()) < 1:
            await message.reply("لطفاً یک عدد صحیح مثبت وارد کنید:")
            return True
        capacity = int(text.strip())
        await run_db(db.update_group_capacity, state["group_id"], capacity)
        states.clear_state(author.id)
        await message.reply("✅ ظرفیت گروه به‌روزرسانی شد.", components=await menu_with_terms(await run_db(db.get_user, author.id)))
        return True

    if action == "cfg_shift_count":
        t = text.strip()
        if not t.isdigit() or not (2 <= int(t) <= 26):
            await message.reply("لطفاً عددی بین ۲ تا ۲۶ وارد کنید (تعداد شیفت‌ها):")
            return True
        n = int(t)
        states.set_state(author.id, action="cfg_cycle_length", shift_count=n)
        await message.reply(
            f"شیفت‌های {', '.join(shift.shift_letters(n))} در نظر گرفته شد.\n"
            f"حالا تعداد روزهای سیکل را وارد کنید (باید بر {n} بخش‌پذیر باشد، مثلاً {n * 2}):"
        )
        return True

    if action == "cfg_cycle_length":
        t = text.strip()
        if not t.isdigit() or int(t) < 1 or int(t) % state["shift_count"] != 0:
            await message.reply(f"عدد باید صحیح، مثبت و بر {state['shift_count']} بخش‌پذیر باشد. دوباره وارد کنید:")
            return True
        cycle_length = int(t)
        states.set_state(author.id, action="cfg_slot_name", shift_count=state["shift_count"],
                          cycle_length=cycle_length, slot_index=0, labels=[])
        await message.reply(
            f"حالا نام و علامتِ کوتاهِ هرکدام از {cycle_length} ردیف سیکل را یکی‌یکی وارد کنید.\n"
            "فرمت: نام کامل/علامت کوتاه   (مثال: صبح اول/ص1)\n\nردیف ۱ را ارسال کنید:"
        )
        return True

    if action == "cfg_slot_name":
        sep = "|" if "|" in text else ("/" if "/" in text else None)
        if not sep:
            await message.reply("فرمت درست نیست. به این شکل ارسال کنید: نام کامل/علامت کوتاه   (مثال: صبح اول/ص1)")
            return True
        name, short = (p.strip() for p in text.split(sep, 1))
        if not name or not short:
            await message.reply("هم نام کامل و هم علامت کوتاه لازم است. دوباره ارسال کنید:")
            return True
        labels = state["labels"] + [{"name": name, "short": short}]
        idx = state["slot_index"] + 1
        if idx < state["cycle_length"]:
            states.set_state(author.id, action="cfg_slot_name", shift_count=state["shift_count"],
                              cycle_length=state["cycle_length"], slot_index=idx, labels=labels)
            await message.reply(f"ثبت شد ✅ حالا ردیف {idx + 1} از {state['cycle_length']} را ارسال کنید:")
        else:
            states.set_state(author.id, action="cfg_own_shift", shift_count=state["shift_count"],
                              cycle_length=state["cycle_length"], labels=labels)
            await message.reply(
                "همه‌ی ردیف‌ها ثبت شد ✅\nحالا شیفتِ خودتان را انتخاب کنید:",
                components=kb.shift_letter_keyboard(state["shift_count"], "cfgshift_own"),
            )
        return True

    if action == "replace_admin_uid":
        states.clear_state(author.id)
        await message.reply(
            "دیگر شناسه پذیرفته نمی‌شود. از تنظیمات → جایگزینی مدیر با لینک یا مخاطب استفاده کنید.",
            components=await menu_with_terms(await run_db(db.get_user, author.id)),
        )
        return True
    if action == "replace_admin_uid_DISABLED":
        t = text.strip()
        if not t.isdigit():
            await message.reply("شناسه باید عدد باشد. دوباره وارد کنید:")
            return True
        to_uid = int(t)
        try:
            await run_db(db.replace_admin, author.id, to_uid)
            states.clear_state(author.id)
            await message.reply(
                f"✅ مدیریت به کاربر {to_uid} منتقل شد. شما دیگر مدیر نیستید.",
                components=await menu_with_terms(db_user) if db_user else kb.user_menu(),
            )
        except ValueError as e:
            await message.reply(f"{fa_error(e)}\nدوباره شناسه را وارد کنید یا از منو خارج شوید.")
        return True

    if action == "transfer_shift_lead_uid":
        states.clear_state(author.id)
        await message.reply(
            "دیگر شناسه پذیرفته نمی‌شود. از دکمه انتقال نقش با لینک یا مخاطب استفاده کنید.",
            components=await menu_with_terms(await run_db(db.get_user, author.id)),
        )
        return True
    if action == "transfer_shift_lead_uid_DISABLED":
        t = text.strip()
        if not t.isdigit():
            await message.reply("شناسه باید عدد باشد. دوباره وارد کنید:")
            return True
        to_uid = int(t)
        try:
            await run_db(db.transfer_shift_lead, author.id, to_uid)
            states.clear_state(author.id)
            await message.reply(
                f"✅ نقش مسئول شیفت به کاربر {to_uid} منتقل شد.",
                components=await menu_with_terms(db_user) if db_user else kb.user_menu(),
            )
        except ValueError as e:
            await message.reply(f"{fa_error(e)}\nدوباره شناسه را وارد کنید.")
        return True

    if action == "new_region_name":
        name = text.strip()
        if not name:
            await message.reply("نام منطقه نمی‌تواند خالی باشد:")
            return True
        rid = await run_db(db.create_region, name)
        states.clear_state(author.id)
        if rid is None:
            await message.reply(
                f"منطقه‌ای با نام «{name}» از قبل هست.",
                components=kb.after_add_keyboard(add_callback="region_new", add_label="➕ ساخت منطقه جدید"),
            )
        else:
            await message.reply(
                f"✅ منطقه «{name}» ساخته شد.",
                components=kb.after_add_keyboard(add_callback="region_new", add_label="➕ ساخت منطقه جدید"),
            )
        return True

    if action == "set_term_region":
        name = text.strip()
        if not name:
            await message.reply("نام نمی‌تواند خالی باشد:")
            return True
        try:
            await run_db(db.set_term_region, name)
        except Exception:
            # fallback مستقیم
            await run_db(db.set_setting, "term_region", name)
        states.clear_state(author.id)
        u = await run_db(db.get_user, author.id)
        await message.reply(
            f"✅ واژه منطقه به «{name}» تغییر کرد.\nمنوی جدید با /start هم هماهنگ می‌شود.",
            components=await menu_with_terms(u),
        )
        return True

    if action == "set_term_group":
        name = text.strip()
        if not name:
            await message.reply("نام نمی‌تواند خالی باشد:")
            return True
        try:
            await run_db(db.set_term_group, name)
        except Exception:
            await run_db(db.set_setting, "term_group", name)
        states.clear_state(author.id)
        u = await run_db(db.get_user, author.id)
        await message.reply(
            f"✅ واژه گروه به «{name}» تغییر کرد.\nمنوی جدید با /start هم هماهنگ می‌شود.",
            components=await menu_with_terms(u),
        )
        return True

    if action == "set_max_shift_leads":
        t = text.strip()
        if not t.isdigit() or int(t) < 0:
            await message.reply("یک عدد صحیح ≥ ۰ وارد کنید:")
            return True
        await run_db(db.set_max_shift_leads, int(t))
        states.clear_state(author.id)
        await message.reply(
            f"✅ سقف مسئول شیفت: {t}",
            components=kb.after_add_keyboard(
                add_callback="settings_shiftleads",
                add_label="👔 مدیریت مسئولان شیفت",
            ),
        )
        return True

    if action == "cfg_leads_per_shift":
        t = text.strip()
        if not t.isdigit() or int(t) < 0:
            await message.reply("یک عدد صحیح ≥ ۰ وارد کنید:")
            return True
        n = int(t)
        await run_db(db.set_max_shift_leads, n)
        sc = state.get("shift_count") or 1
        states.clear_state(author.id)
        await message.reply(
            f"✅ سقف مسئول شیفت روی {n} تنظیم شد.\n\n"
            "حالا مناطق را بسازید و برای هر مسئول شیفت مناطق تحت مدیریت را مشخص کنید.\n"
            "پس از پیکربندی ساختار برای یک شیفت (مناطق + گروه‌ها)، "
            "همان الگو برای بقیه‌ی شیفت‌ها قابل استفاده است — "
            "مناطق و گروه‌ها مشترک‌اند و فقط اعضای هر شیفت فرق می‌کنند.\n\n"
            "از دکمه‌های زیر ادامه دهید:",
            components=kb.after_add_keyboard(
                add_callback="region_new",
                add_label="➕ ساخت منطقه",
                back_callback="settings_shiftleads",
                back_label="👔 انتصاب مسئولان شیفت",
            ),
        )
        return True

    if action == "set_max_seniors":
        t = text.strip()
        if not t.isdigit() or int(t) < 1:
            await message.reply("یک عدد صحیح حداقل ۱ وارد کنید:")
            return True
        await run_db(db.set_max_seniors_per_region, int(t))
        states.clear_state(author.id)
        await message.reply(
            f"✅ سقف تکنسین ارشد در هر منطقه: {t} نفر",
            components=await menu_with_terms(await run_db(db.get_user, author.id)),
        )
        return True

    if action == "set_max_snr_leave":
        t = text.strip()
        if not t.isdigit() or int(t) < 0:
            await message.reply("یک عدد صحیح ≥ ۰ وارد کنید (۰ = نامحدود):")
            return True
        await run_db(db.set_max_senior_leave_per_shift, int(t))
        states.clear_state(author.id)
        await message.reply(
            f"✅ ظرفیت مرخصی هم‌زمان ارشد در هر شیفت: {t}",
            components=await menu_with_terms(await run_db(db.get_user, author.id)),
        )
        return True


    if action == "set_region_max_seniors":
        t = text.strip()
        if not t.isdigit():
            await message.reply("یک عدد صحیح وارد کنید (0 = پیروی از سقف سراسری):")
            return True
        rid = state["region_id"]
        n = int(t)
        await run_db(db.set_region_max_seniors, rid, None if n == 0 else n)
        states.clear_state(author.id)
        cur = await run_db(db.get_region_max_seniors, rid)
        await message.reply(f"✅ سقف ارشد این منطقه: {cur} نفر", components=await menu_with_terms(await run_db(db.get_user, author.id)))
        return True

    if action == "appoint_sl_uid":
        states.clear_state(author.id)
        await message.reply(
            "دیگر شناسه پذیرفته نمی‌شود.\n"
            "از تنظیمات → مسئولان شیفت → انتصاب جدید، لینک، مخاطب یا لیست اعضا را استفاده کنید.",
            components=await menu_with_terms(await run_db(db.get_user, author.id)),
        )
        return True

    if action == "rename_region":
        name = text.strip()
        rid = state.get("region_id")
        if not name or not rid:
            await message.reply("نام نامعتبر.")
            return True
        ok = await run_db(db.rename_region, rid, name)
        states.clear_state(author.id)
        await message.reply("✅ تغییر نام انجام شد." if ok else "نام تکراری است.")
        return True

    if action == "leave_note_input":
        # توضیحات حذف شده — هر متنی به‌عنوان صرف‌نظر از توضیح تلقی می‌شود
        y, m, dates = state.get("year"), state.get("month"), state.get("dates") or []
        states.clear_state(author.id)
        msg = await finalize_leave_submit(author.id, y, m, dates, None)
        db_user = await run_db(db.get_user, author.id)
        await message.reply(msg)
        if db_user:
            await send_fresh_calendar(message, db_user, y, m)
        return True
    if action == "_removed_leave_note_input":
        note = text.strip() or None
        y, m = state["year"], state["month"]
        dates = state["dates"]
        msg = await finalize_leave_submit(author.id, y, m, dates, note)
        db_user = await run_db(db.get_user, author.id)
        await message.reply(msg)
        if db_user:
            await send_fresh_calendar(message, db_user, y, m, interactive=True)
        return True

    if action == "decide_note_input":
        lid = state.get("leave_id")
        st = state.get("new_status")
        states.clear_state(author.id)
        if lid and st:
            result = await apply_leave_decision(author.id, lid, st, None)
            await message.reply(result)
        return True
    if action == "_removed_decide_note_input":
        note = text.strip() or None
        leave_id = state["leave_id"]
        new_status = state["new_status"]
        states.clear_state(author.id)
        result = await apply_leave_decision(author.id, leave_id, new_status, note)
        await message.reply(result)
        return True


    if action == "over_cap_date":
        raw = text.strip().replace("/", "-").replace(".", "-")
        # پشتیبانی از 1404-05-03 یا 3-5-1404
        parts = [p for p in raw.replace(" ", "").split("-") if p]
        date_str = None
        try:
            if len(parts) == 3:
                a, b, c = parts
                if len(a) == 4:  # y-m-d
                    y, m, d = int(a), int(b), int(c)
                elif len(c) == 4:  # d-m-y
                    d, m, y = int(a), int(b), int(c)
                else:
                    raise ValueError("bad")
                date_str = jalali.parse_date_str(y, m, d)
        except Exception:
            date_str = None
        if not date_str:
            await message.reply(
                "تاریخ نامعتبر است. نمونه: 1404-05-12 یا 12-5-1404"
            )
            return True
        if date_str < today_str():
            await message.reply("تاریخ نمی‌تواند قبل از امروز باشد. دوباره وارد کنید:")
            return True
        status, extra = await run_db(db.request_over_capacity_leave, author.id, date_str, "اضافه بر ظرفیت")
        states.clear_state(author.id)
        db_user = await run_db(db.get_user, author.id)
        if status == "exists":
            await message.reply(
                "برای این روز از قبل مرخصی فعال دارید.",
                components=await menu_with_terms(db_user),
            )
            return True
        if status != "created":
            await message.reply("ثبت انجام نشد.", components=await menu_with_terms(db_user))
            return True
        lid = extra
        # اطلاع به مافوق با برچسب اضافه بر ظرفیت
        await notify_over_capacity_leave(db_user, date_str, lid)
        await message.reply(
            f"📝 درخواست مرخصی اضافه بر ظرفیت برای {jalali.format_jalali(date_str)} ثبت شد "
            "و برای مافوق ارسال شد.",
            components=await menu_with_terms(db_user),
        )
        return True

    if action == "broadcast_subject":
        subj = text.strip()
        if not subj:
            await message.reply("موضوع نمی‌تواند خالی باشد:")
            return True
        states.set_state(author.id, action="broadcast_body", subject=subj)
        await message.reply("متن اصلی پیام را وارد کنید:")
        return True

    if action == "broadcast_body":
        body = text.strip()
        if not body:
            await message.reply("متن پیام نمی‌تواند خالی باشد:")
            return True
        subject = state.get("subject") or "بدون موضوع"
        with_btn = bool(state.get("with_btn"))
        if not with_btn:
            raw = await run_db(db.get_setting, "broadcast_with_button", "1")
            with_btn = str(raw) not in ("0", "false", "False")
        states.clear_state(author.id)
        users = await run_db(db.list_all_user_ids_for_broadcast)
        msg = f"📢 پیام همگانی\n\n📌 موضوع: {subject}\n\n{body}"
        components = None
        if with_btn:
            from bale import InlineKeyboardMarkup, InlineKeyboardButton
            components = InlineKeyboardMarkup()
            components.add(
                InlineKeyboardButton(text="🔄 ریست ربات", callback_data="bcast_reset:1"),
                row=1,
            )
        ok = fail = 0
        for uid in users:
            try:
                if components is not None:
                    await client.send_message(uid, msg, components=components)
                else:
                    await client.send_message(uid, msg)
                ok += 1
            except Exception:
                fail += 1
        await message.reply(
            f"✅ پیام همگانی ارسال شد.\n"
            f"دکمه همراه: {'فعال' if with_btn else 'غیرفعال'}\n"
            f"موفق: {ok} | ناموفق: {fail}",
            components=await menu_with_terms(await run_db(db.get_user, author.id)),
        )
        return True

    states.clear_state(author.id)
    return False


# ==========================================================================
#  منوی مدیر
# ==========================================================================




def last_n_jalali_months(n: int = 6):
    """از ماه جاری به عقب، n ماه: [(jy, jm, label), ...]"""
    y, m, _ = jalali.today_jalali()
    out = []
    cy, cm = int(y), int(m)
    for i in range(n):
        yy, mm = cy, cm - i
        while mm <= 0:
            mm += 12
            yy -= 1
        label = f"{jalali.PERSIAN_MONTHS[mm - 1]} {yy}"
        out.append((yy, mm, label))
    return out


async def resolve_report_region_ids(db_user: dict):
    """محدوده گزارش بر اساس نقش."""
    if not db_user:
        return []
    if db_user.get("is_admin"):
        return None  # همه مناطق
    if db_user.get("is_shift_lead"):
        return await run_db(db.list_shift_lead_region_ids, db_user["user_id"]) or []
    if db_user.get("is_senior") or db_user.get("role") == "snr":
        rid = db_user.get("region_id")
        return [rid] if rid else []
    return []


async def start_monthly_report_picker(message: Message, db_user: dict):
    if not db_user or not (
        db_user.get("is_admin")
        or db_user.get("is_shift_lead")
        or db_user.get("is_senior")
        or db_user.get("role") == "snr"
    ):
        await message.reply("شما به بخش گزارش دسترسی ندارید.")
        return
    months = last_n_jalali_months(6)
    await message.reply(
        "📊 گزارش مرخصی ماهانه\n"
        "یکی از ۶ ماه اخیر (شامل ماه جاری) را انتخاب کنید:\n"
        "گزارش به‌صورت فایل PDF ارسال می‌شود.",
        components=kb.report_months_keyboard(months),
    )


async def open_over_cap_calendar(message: Message, author, db_user: dict):
    """تقویم انتخاب؛ روزهای پر = مازاد، بقیه = عادی."""
    if not db_user:
        await message.reply("کاربر پیدا نشد.")
        return
    try:
        y, m = jalali.today_jalali()[:2]
    except Exception:
        ty, tm, td = jalali.gregorian_to_jalali(__import__("datetime").date.today())
        y, m = ty, tm
    sel = states.get_selection(author.id, int(y), int(m))
    sel["over_mode"] = True
    states.set_state(author.id, action="cal_over_mode", year=int(y), month=int(m))
    await message.reply(
        "📅 تقویم مرخصی (حالت هوشمند)\n"
        "روزها را انتخاب و سپس «ثبت» را بزنید.\n"
        "• بدون تداخل ظرفیت → درخواست عادی\n"
        "• ظرفیت تکمیل → درخواست اضافه بر ظرفیت برای مافوق"
    )
    await show_calendar(message, db_user, interactive=True)



async def show_region_leaves_status(message: Message, db_user: dict):
    """وضعیت مرخصی فعال بر اساس نقش:
    - مسئول شیفت: مناطق تحت مدیریت
    - ارشد / تکنسین / اپراتور: منطقه خود
    - مدیر (بدون نقش مسئول شیفت): همه مناطق
    """
    from_date = today_str()
    rows = []
    title = "📋 وضعیت مرخصی منطقه"

    if db_user.get("is_shift_lead"):
        region_ids = await run_db(db.list_shift_lead_region_ids, db_user["user_id"]) or []
        collected = []
        for rid in region_ids:
            part = await run_db(db.list_region_active_leaves, rid, from_date) or []
            collected.extend(part)
        seen = set()
        for r in collected:
            lid = r.get("id")
            if lid in seen:
                continue
            seen.add(lid)
            rows.append(r)
        title = "📋 وضعیت مرخصی مناطق تحت مدیریت شما"
    elif db_user.get("is_admin"):
        rows = await run_db(db.list_all_future_leaves, from_date) or []
        title = "📋 وضعیت مرخصی همه مناطق"
    else:
        rid = db_user.get("region_id")
        if not rid:
            await message.reply("منطقه کاری شما مشخص نیست.")
            return
        rows = await run_db(db.list_region_active_leaves, rid, from_date) or []
        reg = await run_db(db.get_region, rid)
        rname = reg["name"] if reg else str(rid)
        title = f"📋 وضعیت مرخصی منطقه «{rname}»"

    if not rows:
        await message.reply(f"{title}\n\nمرخصی فعالی ثبت نشده است.")
        return

    lines = []
    for i, r in enumerate(rows, 1):
        name = display_name(r)
        day = jalali.format_jalali(r["leave_date"])
        st = STATUS_FA.get(r.get("status"), r.get("status") or "-")
        g = r.get("group_name") or "-"
        rn = r.get("region_name") or "-"
        kind = "مازاد" if int(r.get("over_capacity") or 0) == 1 else "عادی"
        lines.append(
            f"{i}. {name} | {day} | {g} | {rn} | وضعیت: {st} | نوع: {kind}"
        )

    text = title + "\n\n" + "\n".join(lines)
    if len(text) <= 3500:
        await message.reply(text)
        return
    buf = title + "\n\n"
    for line in lines:
        if len(buf) + len(line) + 1 > 3500:
            await message.reply(buf)
            buf = line + "\n"
        else:
            buf += line + "\n"
    if buf.strip():
        await message.reply(buf)



async def handle_admin_menu(message: Message, author, text: str):
    tr, tg, L = await _terms()

    if text in (getattr(kb, "BTN_BROADCAST", "📢 پیام همگانی"), "📢 پیام همگانی"):
        u = await run_db(db.get_user, author.id)
        if not u or not u.get("is_admin"):
            await message.reply("فقط مدیر می‌تواند پیام همگانی بفرستد.")
            return
        raw = await run_db(db.get_setting, "broadcast_with_button", "1")
        with_btn = str(raw) not in ("0", "false", "False", "")
        states.set_state(author.id, action="broadcast_options", with_btn=with_btn)
        await message.reply(
            "📢 پیام همگانی\nآیا دکمه «ریست ربات» همراه پیام برای گیرندگان ارسال شود؟",
            components=kb.broadcast_options_keyboard(with_btn),
        )
        return

    if text in (getattr(kb, "BTN_RESET_BOT", "🔄 ریست ربات"), "🔄 ریست ربات"):
        u = await run_db(db.get_user, author.id)
        if not u or not u.get("is_admin"):
            await message.reply("فقط مدیر.")
            return
        from bale import InlineKeyboardMarkup, InlineKeyboardButton
        conf = InlineKeyboardMarkup()
        conf.add(InlineKeyboardButton(text="✅ بله، ریست شود", callback_data="resetbot:yes"), row=1)
        conf.add(InlineKeyboardButton(text="❌ انصراف", callback_data="resetbot:no"), row=2)
        await message.reply(
            "🔄 ریست ربات\n"
            "حافظه موقت (حالت‌های در جریان کاربران) پاک می‌شود.\n"
            "داده پایگاه (اعضا و مرخصی‌ها) حذف نمی‌شود.\n"
            "ادامه؟",
            components=conf,
        )
        return

    if text in (L["region_leaves"], getattr(kb, "BTN_REGION_LEAVES", "")):
        db_user = await run_db(db.get_user, author.id)
        await show_region_leaves_status(message, db_user)
        return

    if text == getattr(kb, "BTN_OVER_CAP_LEAVE", "➕ درخواست مرخصی اضافه بر ظرفیت"):
        _u = await run_db(db.get_user, author.id)
        await open_over_cap_calendar(message, author, _u)
        return

    if text == kb.ADMIN_BTN_PENDING:
        pending = await run_db(db.list_pending_users)
        if not pending:
            await message.reply("در حال حاضر کسی در انتظار تایید نیست.")
            return
        await message.reply("افراد در انتظار تایید:", components=kb.pending_users_keyboard(pending))
        return

    # صف مرخصی فقط در نقش مسئول شیفت — حتی اگر کاربر مدیر هم باشد
    if text == kb.LEAD_BTN_QUEUE:
        db_user = await run_db(db.get_user, author.id)
        if not db_user or not db_user.get("is_shift_lead"):
            await message.reply(
                "صف مرخصی فقط برای مسئول شیفت است. "
                "اگر می‌خواهید مرخصی ارشدها را بررسی کنید، ابتدا به‌عنوان مسئول شیفت منصوب شوید."
            )
            return
        await handle_shift_lead_menu(message, author, db_user, text)
        return

    if text in (kb.LEAD_BTN_MY_SHIFT, kb.LEAD_BTN_TRANSFER):
        db_user = await run_db(db.get_user, author.id)
        if not db_user or not db_user.get("is_shift_lead"):
            await message.reply("این گزینه فقط برای مسئول شیفت است.")
            return
        await handle_shift_lead_menu(message, author, db_user, text)
        return

    if text == kb.ADMIN_BTN_MEMBERS:
        await show_all_users(message)
        return

    if text == kb.ADMIN_BTN_INVITE:
        db_user = await run_db(db.get_user, author.id)
        roles = allowed_add_roles(db_user)
        if not roles:
            await message.reply("شما اجازه‌ی افزودن عضو ندارید.")
            return
        await message.reply(
            "روش افزودن عضو را انتخاب کنید:",
            components=kb.add_people_method_keyboard(),
        )
        return


    if text in (L["admin_calendar"], kb.ADMIN_BTN_CALENDAR):
        regions = await run_db(db.list_regions)
        if not regions:
            await message.reply("هنوز منطقه‌ای تعریف نشده.")
            return
        await message.reply(
            "کدام منطقه را در تقویم ببینید؟",
            components=kb.region_select_keyboard(regions, "cal_region"),
        )
        return

    if text == kb.ADMIN_BTN_REPORT:
        db_user = await run_db(db.get_user, author.id)
        await start_monthly_report_picker(message, db_user)
        return

    if text == kb.ADMIN_BTN_SETTINGS:
        states.set_state(author.id, nav_stack=["settings"])
        mode = await run_db(db.get_calendar_mode)
        cfg = await run_db(db.get_shift_config) if mode == "shift" else None
        await message.reply(
            "⚙️ تنظیمات ربات",
            components=kb.settings_keyboard(
                mode, is_admin=True, shift_count=cfg["shift_count"] if cfg else None,
                term_region=tr if "tr" in dir() else "منطقه کاری",
                term_group=tg if "tg" in dir() else "گروه کاری",
            ),
        )
        return

    await message.reply("لطفاً از دکمه‌های منو استفاده کنید.", components=await menu_with_terms(await run_db(db.get_user, author.id)))



async def cb_report_month(callback: CallbackQuery, data: str):
    """rptm:jy:jm — تولید PDF گزارش ماهانه با محدوده دسترسی."""
    _, jy, jm = data.split(":")
    jy, jm = int(jy), int(jm)
    viewer = await run_db(db.get_user, callback.user.id)
    if not viewer:
        return
    # دسترسی: مدیر / مسئول شیفت / ارشد
    if not (
        viewer.get("is_admin")
        or viewer.get("is_shift_lead")
        or viewer.get("is_senior")
        or viewer.get("role") == "snr"
    ):
        await _ui_reply(callback, "شما به گزارش دسترسی ندارید.")
        return
    region_ids = await resolve_report_region_ids(viewer)
    if region_ids is not None and len(region_ids) == 0:
        await _ui_reply(callback, "محدوده منطقه‌ای برای گزارش تعریف نشده است.")
        return
    month_label = f"{jalali.PERSIAN_MONTHS[jm - 1]} {jy}"
    await callback.message.reply(
        f"⏳ ربات در حال تهیه گزارش «{month_label}» است.\n"
        "لطفاً چند لحظه صبر کنید؛ فایل PDF به‌زودی ارسال می‌شود."
    )
    rows = await run_db(db.list_leaves_for_month, jy, jm, region_ids)
    if not rows:
        await callback.message.reply(f"برای {month_label} مرخصی ثبت‌شده‌ای یافت نشد.")
        return
    mode = await run_db(db.get_calendar_mode)
    cfg = await run_db(db.get_shift_config) if mode == "shift" else None
    letters = shift.shift_letters(cfg["shift_count"]) if cfg else []
    scope = "همه مناطق" if region_ids is None else "محدوده دسترسی شما"
    title = f"گزارش مرخصی {month_label} — {scope}"
    await send_leaves_report_pdf(
        callback.message, rows, title=title, letters=letters
    )


async def send_leaves_report_pdf(message: Message, rows: list, *, title: str, letters: list = None):
    """ساخت PDF جدول‌دار و ارسال به کاربر؛ در صورت خطا متن جدول ارسال می‌شود."""
    letters = letters or []
    # پیش‌پردازش روز مرخصی شمسی کوتاه
    prepared = []
    for r in rows:
        item = dict(r)
        try:
            item["leave_date_fa"] = jalali.format_jalali_day_month(r["leave_date"])
        except Exception:
            item["leave_date_fa"] = str(r.get("leave_date") or "-")
        prepared.append(item)
    try:
        tr, tg, _ = await _terms()
        pdf_bytes = await run_db(
            report_pdf.build_leaves_pdf,
            prepared,
            letters=letters,
            title=title,
            term_region=tr,
            term_group=tg,
        )
        from bale import InputFile
        from datetime import datetime
        fname = f"leave_report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        await message.reply_document(
            InputFile(pdf_bytes, file_name=fname),
            caption=f"📄 {title}\nتعداد: {len(rows)}",
        )
    except Exception:
        logger.exception("pdf report failed; falling back to text table")
        table = format_leaves_table(rows, letters=letters)
        await message.reply(f"📊 {title}\n\n" + table)


# ==========================================================================
#  منوی مسئول شیفت
# ==========================================================================

async def handle_shift_lead_menu(message: Message, author, db_user: dict, text: str):
    region_ids = await run_db(db.list_shift_lead_region_ids, author.id)

    tr, tg, L = await _terms()
    if text in (L["region_leaves"], getattr(kb, "BTN_REGION_LEAVES", "")):
        await show_region_leaves_status(message, db_user)
        return

    if text == getattr(kb, "BTN_OVER_CAP_LEAVE", "➕ درخواست مرخصی اضافه بر ظرفیت"):
        _u = await run_db(db.get_user, author.id)
        await open_over_cap_calendar(message, author, _u)
        return

    if text == kb.LEAD_BTN_QUEUE:
        any_row = False
        for rid in region_ids:
            # صف ارشدهای همان مناطق برای مسئول شیفت (مثل مدیر روی منطقه)
            rows = await run_db(db.list_pending_for_shift_lead, region_ids)
            rows = [r for r in rows if (await run_db(db.get_user, r["user_id"]) or {}).get("region_id") in region_ids]
            for r in rows:
                any_row = True
                requester = await run_db(db.get_user, r["user_id"])
                txt = format_admin_leave_text(requester, r, r.get("group_name"))
                await message.reply(txt, components=kb.leave_decision_keyboard(r["id"], r["status"]))
        if not any_row:
            await message.reply("صف مرخصی ارشدهای مناطق شما خالی است.")
        return

    if text in (L["lead_groups"], kb.LEAD_BTN_GROUPS):
        if not region_ids:
            await message.reply("منطقه‌ای به شما تخصیص داده نشده.")
            return
        await show_regions_then_groups(message, region_ids)
        return

    if text == kb.LEAD_BTN_MEMBERS:
        users_acc = []
        seen = set()
        for rid in region_ids:
            users = await run_db(db.list_all_active_users, rid)
            for u in users:
                if u["user_id"] in seen:
                    continue
                seen.add(u["user_id"])
                users_acc.append(u)
        # مسئول شیفت: فقط ارشد، تکنسین، اپراتور (بدون مسئول شیفت/مدیر)
        users_acc = filter_members_for_viewer(users_acc, db_user)
        if not users_acc:
            await message.reply("عضوی (ارشد/تکنسین/اپراتور) در مناطق شما نیست.")
            return
        mode = await run_db(db.get_calendar_mode)
        cfg = await run_db(db.get_shift_config) if mode == "shift" else None
        letters = shift.shift_letters(cfg["shift_count"]) if cfg else []
        await message.reply(
            f"اعضای مناطق شما ({len(users_acc)} نفر) — ارشد، تکنسین و اپراتور:",
            components=kb.all_users_keyboard(users_acc, True, letters=letters),
        )
        return

    if text == kb.LEAD_BTN_PENDING:
        if not region_ids:
            await message.reply("شما هنوز مسئولِ هیچ منطقه‌ای نیستید.")
            return
        pending = await run_db(db.list_pending_users_in_regions, region_ids)
        if not pending:
            await message.reply("کسی در انتظار تایید نیست.")
            return
        await message.reply(
            "افراد در انتظار تایید (با تایید، فقط می‌توانید آن‌ها را به گروه‌های مناطق خودتان بفرستید):",
            components=kb.pending_users_keyboard(pending),
        )
        return

    if text in (L["lead_calendar"], kb.LEAD_BTN_CALENDAR):
        regions = []
        for rid in region_ids:
            r = await run_db(db.get_region, rid)
            if r:
                regions.append(r)
        if not regions:
            await message.reply("منطقه‌ای به شما تخصیص داده نشده.")
            return
        await message.reply(
            "کدام منطقه را ببینید؟",
            components=kb.region_select_keyboard(regions, "cal_region"),
        )
        return

    if text in (L["lead_report"], kb.LEAD_BTN_REPORT):
        await start_monthly_report_picker(message, db_user)
        return


    if text == kb.LEAD_BTN_MY_SHIFT:
        mode = await run_db(db.get_calendar_mode)
        if mode != "shift":
            await message.reply("تقویم در حالت روزکار است؛ شیفت معنا ندارد.")
            return
        cfg = await run_db(db.get_shift_config)
        if not cfg:
            await message.reply("پیکربندی شیفت هنوز توسط مدیر انجام نشده.")
            return
        await message.reply(
            "شیفت کاری خود را انتخاب کنید:",
            components=kb.shift_letter_keyboard(cfg["shift_count"], "lead_own_shift"),
        )
        return

    if text == kb.LEAD_BTN_TRANSFER:
        await message.reply(
            "واگذاری نقش مسئول شیفت — فقط با لینک یا دفترچه تلفن (بدون شناسه):",
            components=kb.succession_method_keyboard("transfer_lead"),
        )
        return

    if text in (L["lead_settings"], kb.LEAD_BTN_SETTINGS):
        await message.reply(
            "تنظیمات عملیاتی مناطق شما:",
            components=kb.lead_settings_keyboard(),
        )
        return

    await message.reply("لطفاً از دکمه‌های منو استفاده کنید.", components=await menu_with_terms(db_user))


# ==========================================================================
#  منوی تکنسین ارشد
# ==========================================================================

async def handle_senior_menu(message: Message, author, db_user: dict, text: str):
    tr, tg, L = await _terms()
    if text in (L.get("snr_report", ""), getattr(kb, "SNR_BTN_REPORT", "")):
        await start_monthly_report_picker(message, db_user)
        return

    if text in (L["region_leaves"], getattr(kb, "BTN_REGION_LEAVES", "")):
        await show_region_leaves_status(message, db_user)
        return

    if text == getattr(kb, "BTN_OVER_CAP_LEAVE", "➕ درخواست مرخصی اضافه بر ظرفیت"):
        _u = await run_db(db.get_user, author.id)
        await open_over_cap_calendar(message, author, _u)
        return

    region_id = db_user.get("region_id")

    if text in (L["snr_queue"], kb.SNR_BTN_QUEUE):
        if not region_id:
            await message.reply("منطقه کاری شما مشخص نیست.")
            return
        rows = await run_db(db.list_pending_for_senior, region_id)
        if not rows:
            await message.reply("صف مرخصی اعضای منطقه خالی است.")
            return
        for r in rows:
            requester = await run_db(db.get_user, r["user_id"])
            txt = format_admin_leave_text(requester, r, r.get("group_name"))
            await message.reply(txt, components=kb.leave_decision_keyboard(r["id"], r["status"]))
        return

    if text in (L["snr_members"], kb.SNR_BTN_MEMBERS):
        if not region_id:
            await message.reply("منطقه کاری شما مشخص نیست.")
            return
        users = await run_db(db.list_all_active_users, region_id)
        if not users:
            await message.reply("عضوی در منطقه نیست.")
            return
        mode = await run_db(db.get_calendar_mode)
        cfg = await run_db(db.get_shift_config) if mode == "shift" else None
        letters = shift.shift_letters(cfg["shift_count"]) if cfg else []
        filtered = [u for u in users if not u.get("is_senior") or u["user_id"] == author.id]
        await message.reply(
            f"اعضای منطقه ({len(filtered)} نفر):",
            components=kb.all_users_keyboard(filtered, True, letters=letters),
        )
        return
        users = await run_db(db.list_all_active_users, region_id)
        if not users:
            await message.reply("عضوی در منطقه نیست.")
            return
        lines = [
            f"• {display_name(u)} — {u.get('group_name') or '-'} "
            f"{'(ارشد)' if u.get('is_senior') else ''}"
            for u in users if not u.get("is_senior") or u["user_id"] == author.id
        ]
        mode = await run_db(db.get_calendar_mode)
        cfg = await run_db(db.get_shift_config) if mode == "shift" else None
        letters = shift.shift_letters(cfg["shift_count"]) if cfg else []
        filtered = [u for u in users if not u.get("is_senior") or u["user_id"] == author.id]
        await message.reply(
            f"اعضای منطقه ({len(filtered)} نفر):",
            components=kb.all_users_keyboard(filtered, True, letters=letters),
        )
        return

    if text == kb.SNR_BTN_PENDING:
        if not region_id:
            await message.reply("منطقه کاری شما مشخص نیست.")
            return
        pending = await run_db(db.list_pending_users_in_regions, [region_id])
        if not pending:
            await message.reply("کسی در انتظار تایید نیست.")
            return
        await message.reply(
            "افراد در انتظار تایید (فقط می‌توانید آن‌ها را به گروه‌های منطقه‌ی خودتان بفرستید):",
            components=kb.pending_users_keyboard(pending),
        )
        return

    if text in (L["snr_groups"], kb.SNR_BTN_GROUPS):
        if not region_id:
            await message.reply("منطقه کاری شما مشخص نیست.")
            return
        await show_regions_then_groups(message, [region_id])
        return


    if text in (L["snr_calendar"], kb.SNR_BTN_CALENDAR):
        await show_calendar(message, db_user)
        return

    if text == kb.SNR_BTN_STATUS:
        await show_status(message, db_user)
        return

    await message.reply("لطفاً از دکمه‌های منو استفاده کنید.", components=await menu_with_terms(db_user))



def format_leaves_table(rows: list, *, letters: list = None) -> str:
    """جدول خط‌کشی‌شده گزارش مرخصی (کادر + خطوط ستون)."""
    letters = letters or []

    def _shift(r):
        si = r.get("shift_index")
        if si is not None and letters and 0 <= int(si) < len(letters):
            return str(letters[int(si)])
        if si is not None:
            return str(si)
        return "-"

    def _day(r):
        try:
            return jalali.format_jalali_day_month(r["leave_date"])
        except Exception:
            return str(r.get("leave_date") or "-")

    # داده‌های خام هر ردیف
    data = []
    for i, r in enumerate(rows, 1):
        data.append([
            str(i),
            display_name(r) or "-",
            _shift(r),
            r.get("region_name") or "-",
            r.get("group_name") or "-",
            _day(r),
        ])

    headers = ["ردیف", "نام و نام‌خانوادگی", "شیفت", "منطقه", "گروه", "روز مرخصی"]

    # عرض هر ستون = max(طول هدر، طول سلول‌ها) با محدودیت
    widths = []
    for col in range(6):
        w = len(headers[col])
        for row in data:
            w = max(w, len(row[col]))
        # سقف برای خوانایی در موبایل
        widths.append(min(max(w, 2), 18 if col == 1 else 12))

    def _fit(text: str, w: int) -> str:
        t = text if len(text) <= w else text[: max(1, w - 1)] + "…"
        return t + " " * (w - len(t))

    def _line(left: str, mid: str, right: str, fill: str = "─") -> str:
        parts = [fill * (widths[i] + 2) for i in range(6)]
        return left + mid.join(parts) + right

    top = _line("┌", "┬", "┐")
    mid = _line("├", "┼", "┤")
    bot_line = _line("└", "┴", "┘")

    def _row(cells: list) -> str:
        cells_f = [_fit(cells[i], widths[i]) for i in range(6)]
        return "│ " + " │ ".join(cells_f) + " │"

    out = [top, _row(headers), mid]
    for row in data:
        out.append(_row(row))
    out.append(bot_line)
    return "\n".join(out)



async def format_group_line(g: dict, letters: list = None) -> str:
    """نام گروه + منطقه + شیفت‌های مرتبط با منطقه."""
    letters = letters or []
    name = g.get("name") or "-"
    rid = g.get("region_id")
    r = await run_db(db.get_region, rid) if rid else None
    rname = r["name"] if r else "-"
    shift_txt = ""
    if rid is not None:
        # شیفت مسئولان این منطقه یا اعضای گروه
        try:
            leads = await run_db(db.list_shift_leads_for_region, rid)
        except Exception:
            leads = []
        idxs = []
        for ld in leads or []:
            si = ld.get("shift_index")
            if si is not None and si not in idxs:
                idxs.append(si)
        if not idxs:
            # از اعضای گروه
            try:
                members = await run_db(db.list_users_in_group, g["id"])
                for m in members or []:
                    si = m.get("shift_index")
                    if si is not None and si not in idxs:
                        idxs.append(si)
            except Exception:
                pass
        if idxs and letters:
            labs = [letters[int(i)] if 0 <= int(i) < len(letters) else str(i) for i in idxs]
            shift_txt = f" — شیفت {'،'.join(labs)}"
        elif idxs:
            shift_txt = f" — شیفت {','.join(str(i) for i in idxs)}"
    return f"• {name} — {rname}{shift_txt} (ظرفیت {g.get('max_concurrent', '-')})"



async def show_regions_then_groups(message_or_cb, region_ids: list, *, title: str = None):
    """مرحله ۱: لیست شیشه‌ای مناطق؛ با کلیک، گروه‌های همان منطقه."""
    regions = []
    for rid in region_ids:
        r = await run_db(db.get_region, rid)
        if r:
            regions.append(r)
    if not regions:
        # try load all regions for admin if ids empty was intentional
        text = "منطقه‌ای برای نمایش وجود ندارد."
        if hasattr(message_or_cb, "reply"):
            await message_or_cb.reply(text)
        else:
            await message_or_cb.message.reply(text)
        return
    tr, tg, _ = await _terms()
    title = title or f"ابتدا {tr} را انتخاب کنید؛ سپس {tg}‌های همان {tr} نمایش داده می‌شود:"
    markup = kb.regions_pick_for_groups_keyboard(regions, "myg_reg")
    if hasattr(message_or_cb, "reply") and not hasattr(message_or_cb, "message"):
        await message_or_cb.reply(title, components=markup)
    else:
        # Message object from callback.message
        try:
            await message_or_cb.reply(title, components=markup)
        except Exception:
            await _ui_reply(message_or_cb, title, markup)


async def show_groups_of_region(callback_or_msg, region_id: int):
    region = await run_db(db.get_region, region_id)
    if not region:
        if hasattr(callback_or_msg, "message"):
            await _ui_reply(callback_or_msg, "منطقه پیدا نشد.")
        else:
            await callback_or_msg.reply("منطقه پیدا نشد.")
        return
    groups = await run_db(db.list_groups, region_id)
    tr, tg, _ = await _terms()
    if not groups:
        text = f"در {tr} «{region['name']}» هنوز {tg}‌ای نیست."
        nav = kb.nav_row_keyboard(back_callback="myg_list", show_main=True)
        if hasattr(callback_or_msg, "data"):
            await _ui_reply(callback_or_msg, text, nav)
        else:
            await callback_or_msg.reply(text, components=nav)
        return
    text = f"{tg}‌های {tr} «{region['name']}» — برای تنظیم روی هر مورد بزنید:"
    markup = kb.groups_edit_keyboard(groups, back_callback="myg_list")
    if hasattr(callback_or_msg, "data"):
        await _ui_reply(callback_or_msg, text, markup)
    else:
        await callback_or_msg.reply(text, components=markup)


async def show_all_users(message: Message):

    users = await run_db(db.list_all_active_users)
    if not users:
        await message.reply("هنوز عضو فعالی ثبت نشده است.")
        return
    mode = await run_db(db.get_calendar_mode)
    cfg = await run_db(db.get_shift_config) if mode == "shift" else None
    letters = shift.shift_letters(cfg["shift_count"]) if cfg else []
    # یک‌بار برای هر نفر: نام (شیفت / منطقه / نقش)
    keyboard = kb.all_users_keyboard(users, True, letters=letters)
    await message.reply(
        f"لیست اعضا ({len(users)} نفر) — برای مدیریت روی نام بزنید:",
        components=keyboard,
    )


def format_admin_leave_text(requester: dict, leave: dict, group_name) -> str:
    pnum = f" ({requester.get('personnel_number')})" if requester and requester.get("personnel_number") else ""
    note = (leave.get("note_user") or "").strip()
    note_line = f"\nتوضیح کاربر: {note}" if note else ""
    return (
        "📝 درخواست مرخصی\n"
        f"نام: {display_name(requester) if requester else '-'}{pnum}\n"
        f"گروه: {group_name or '-'}\n"
        f"تاریخ مرخصی: {jalali.format_jalali(leave['leave_date'])}\n"
        f"زمان ثبت درخواست: {jalali.format_datetime_display(leave['requested_at'])}"
        f"{note_line}"
    )


async def _recipients_for_leave_request(requester: dict):
    """سلسله‌مراتب تایید مرخصی:
    - مسئول شیفت → فقط مدیر
    - تکنسین ارشد → فقط مسئول شیفتِ همان منطقه
    - تکنسین / اپراتور → فقط تکنسین ارشدِ همان منطقه
    """
    recipients = []
    rid = requester.get("region_id")

    # مسئول شیفت → فقط مدیر
    if requester.get("is_shift_lead"):
        recipients = await run_db(db.list_admin_ids)
        return list(dict.fromkeys(recipients))

    # تکنسین ارشد → مسئولان شیفتِ همان منطقه
    if requester.get("is_senior") or requester.get("role") == "snr":
        leads = await run_db(db.list_shift_leads)
        for lead in leads:
            region_ids = []
            for r in (lead.get("regions") or []):
                if isinstance(r, dict):
                    region_ids.append(r.get("id"))
                else:
                    region_ids.append(r)
            # list_shift_leads may embed regions differently
            if not region_ids:
                region_ids = await run_db(db.list_shift_lead_region_ids, lead["user_id"])
            if rid is None or rid in region_ids:
                recipients.append(lead["user_id"])
        if not recipients:
            recipients = await run_db(db.list_admin_ids)
        return list(dict.fromkeys(recipients))

    # تکنسین / اپراتور → ارشدهای همان منطقه
    if rid:
        users = await run_db(db.list_all_active_users, rid) or []
        for u in users:
            if (u.get("is_senior") or u.get("role") == "snr") and u["user_id"] != requester.get("user_id"):
                recipients.append(u["user_id"])
    if not recipients:
        # fallback مسئول شیفت
        leads = await run_db(db.list_shift_leads)
        for lead in leads:
            region_ids = await run_db(db.list_shift_lead_region_ids, lead["user_id"])
            if rid is None or rid in region_ids:
                recipients.append(lead["user_id"])
    if not recipients:
        recipients = await run_db(db.list_admin_ids)
    return list(dict.fromkeys(recipients))


async def notify_over_capacity_leave(requester: dict, date_str: str, leave_id: int):
    """ارسال درخواست اضافه بر ظرفیت به مافوق با دکمه تایید/رد."""
    text = (
        f"⚠️ درخواست مرخصی اضافه بر ظرفیت\n"
        f"از: {display_name(requester)}\n"
        f"روز: {jalali.format_jalali(date_str)}\n"
        f"این درخواست خارج از ظرفیت عادی گروه/شیفت است."
    )
    from bale import InlineKeyboardMarkup, InlineKeyboardButton
    kb_i = InlineKeyboardMarkup()
    kb_i.add(InlineKeyboardButton(text="✅ تایید", callback_data=f"decide:{leave_id}:approved"), row=1)
    kb_i.add(InlineKeyboardButton(text="❌ رد", callback_data=f"decide:{leave_id}:rejected"), row=2)
    for uid in await _recipients_for_leave_request(requester):
        try:
            await client.send_message(uid, text, components=kb_i)
        except Exception:
            logger.exception("notify over_cap to %s", uid)


async def notify_admin_new_leave_request(
requester: dict, date_str: str, leave_id: int):
    leave = await run_db(db.get_leave, leave_id)
    group = await run_db(db.get_group, requester["group_id"]) if requester.get("group_id") else None
    text = format_admin_leave_text(requester, leave, group["name"] if group else None)
    for uid in await _recipients_for_leave_request(requester):
        try:
            await client.send_message(
                uid, text, components=kb.leave_decision_keyboard(leave_id, "pending")
            )
        except Exception:
            logger.exception("خطا در اطلاع‌رسانی درخواست مرخصی به %s", uid)


async def notify_leave_batch(requester: dict, items: list):
    """
    items: [(date_str, leave_id), ...]
    یک پیام چندروزه با آیکون کنار هر روز برای تصمیم‌گیرنده.
    """
    if not items:
        return
    group = await run_db(db.get_group, requester["group_id"]) if requester.get("group_id") else None
    note = ""
    leaves_info = []
    for date_str, lid in items:
        lv = await run_db(db.get_leave, lid)
        if lv and lv.get("note_user"):
            note = lv["note_user"]
        leaves_info.append({
            "leave_id": lid,
            "date_str": jalali.format_jalali(date_str),
            "status": (lv or {}).get("status", "pending"),
        })
    header = (
        f"📝 درخواست مرخصی چندروزه\n"
        f"نام: {display_name(requester)}\n"
        f"گروه: {group['name'] if group else '-'}\n"
    )
    if note:
        header += f"توضیح: {note}\n"
    header += "برای هر روز یکی از آیکون‌ها را بزنید، سپس «ثبت نهایی»:"
    for uid in await _recipients_for_leave_request(requester):
        try:
            await client.send_message(
                uid, header, components=kb.batch_day_decision_keyboard(leaves_info)
            )
        except Exception:
            logger.exception("خطا در ارسال batch leave به %s", uid)


async def notify_admins_leave_cancelled(db_user: dict, dates):
    dates_txt = "، ".join(jalali.format_jalali(d) for d in dates)
    text = f"❌ کاربر {display_name(db_user)} مرخصیِ روز(های) {dates_txt} را لغو کرد."
    for uid in await _recipients_for_leave_request(db_user):
        try:
            await client.send_message(uid, text)
        except Exception:
            logger.exception("خطا در اطلاع‌رسانی لغو مرخصی به %s", uid)


# ==========================================================================
#  منوی کاربر عادی
# ==========================================================================

async def handle_user_menu(message: Message, author, db_user: dict, text: str):
    tr, tg, L = await _terms()
    if text in (L["region_leaves"], getattr(kb, "BTN_REGION_LEAVES", "")):
        await show_region_leaves_status(message, db_user)
        return
    if text == getattr(kb, "BTN_OVER_CAP_LEAVE", "➕ درخواست مرخصی اضافه بر ظرفیت"):
        _u = await run_db(db.get_user, author.id)
        await open_over_cap_calendar(message, author, _u)
        return
    if text == kb.USER_BTN_CALENDAR:
        await show_calendar(message, db_user)
        return
    if text == kb.USER_BTN_STATUS:
        await show_status(message, db_user)
        return
    await message.reply("لطفاً از دکمه‌های منو استفاده کنید.", components=await menu_with_terms(db_user) if db_user else kb.user_menu())


async def show_status(message: Message, db_user: dict):
    if db_user.get("is_admin"):
        role_label = "مدیر"
    elif db_user.get("is_shift_lead"):
        role_label = "مسئول شیفت"
    elif db_user.get("is_senior") or db_user.get("role") == "snr":
        role_label = "تکنسین ارشد"
    else:
        role_label = config.ROLE_LABELS.get(db_user.get("role"), "هنوز تعیین نشده")
    group = await run_db(db.get_group, db_user["group_id"]) if db_user["group_id"] else None
    group_name = group["name"] if group else "هنوز تعیین نشده"
    mode = await run_db(db.get_calendar_mode)
    shift_txt = ""
    if mode == "shift":
        cfg = await run_db(db.get_shift_config)
        if cfg and db_user["shift_index"] is not None:
            letters = shift.shift_letters(cfg["shift_count"])
            shift_txt = f"\nشیفت: {letters[db_user['shift_index']]}"
        else:
            shift_txt = "\nشیفت: هنوز تعیین نشده"
    pnum = f"\nشماره پرسنلی: {db_user['personnel_number']}" if db_user.get("personnel_number") else ""
    await message.reply(
        f"👤 وضعیت شما:\nنام: {display_name(db_user)}{pnum}\nنقش: {role_label}\nگروه: {group_name}{shift_txt}"
    )


async def show_calendar(message: Message, db_user: dict, *, region_id: int = None, interactive: bool = None):
    """نمایش تقویم. interactive=True اجباری برای ثبت."""
    y, m, _ = jalali.today_jalali()
    if interactive is None:
        st = states.get_state(db_user["user_id"]) or {}
        sel = states.get_selection(db_user["user_id"], y, m)
        over_mode = bool(sel.get("over_mode") or st.get("action") == "cal_over_mode")
        if over_mode:
            interactive = True
        else:
            interactive = bool(
                db_user.get("group_id")
                or db_user.get("is_senior")
                or db_user.get("role") == "snr"
            )
            if db_user.get("is_admin") and not db_user.get("is_senior") and not db_user.get("is_shift_lead"):
                interactive = False
    await send_fresh_calendar(
        message, db_user, y, m, region_id=region_id, interactive=bool(interactive)
    )



async def own_status_for_month(user_id: int, year: int, month: int) -> dict:
    leaves = await run_db(db.list_user_active_leaves, user_id)
    prefix = f"{year:04d}-{month:02d}"
    return {lv["leave_date"]: lv["status"] for lv in leaves if lv["leave_date"].startswith(prefix)}


async def approved_others_for_month(viewer: dict, year: int, month: int, region_id: int = None) -> dict:
    """تعداد مرخصی تاییدشده دیگران در هر روز (برای علامت ●)."""
    rid = region_id or viewer.get("region_id")
    if rid is None and viewer.get("is_admin"):
        rows = await run_db(db.all_leaves_in_month, year, month)
    elif rid is not None:
        rows = await run_db(db.region_leaves_in_month, rid, year, month)
    else:
        return {}
    out = {}
    uid = viewer["user_id"]
    for r in rows:
        if r.get("status") != "approved":
            continue
        if r.get("user_id") == uid:
            continue
        out[r["leave_date"]] = out.get(r["leave_date"], 0) + 1
    return out


def _shift_slot_index(cfg: dict, target_shift_index: int, date_str: str) -> int:
    """اسلاتِ یک شیفت در یک تاریخ؛ اگر برای آن شیفت override اختصاصی تنظیم شده باشد از همان
    به‌عنوان لنگر استفاده می‌شود، وگرنه از فاصله‌ی مساوی نسبت به لنگر سراسری محاسبه می‌شود."""
    override = (cfg.get("overrides") or {}).get(str(target_shift_index))
    if override:
        return shift.slot_index_for(
            cfg["cycle_length"], cfg["shift_count"], override["ref_date"],
            target_shift_index, override["ref_slot_index"], date_str, target_shift_index,
        )
    return shift.slot_index_for(
        cfg["cycle_length"], cfg["shift_count"], cfg["ref_date"],
        cfg["ref_shift_index"], cfg["ref_slot_index"], date_str, target_shift_index,
    )


def _iran_official_holiday(date_str: str) -> bool:
    """تعطیلات رسمی ثابت شمسی (نمونهٔ پرکاربرد؛ قابل گسترش)."""
    # MM-DD
    try:
        _, m, d = date_str.split("-")
        md = f"{int(m):02d}-{int(d):02d}"
    except Exception:
        return False
    fixed = {
        "01-01", "01-02", "01-03", "01-04",  # نوروز
        "01-12",  # روز جمهوری اسلامی
        "01-13",  # طبیعت
        "11-22",  # پیروزی انقلاب
        "12-29",  # ملی شدن نفت
    }
    return md in fixed


async def shift_labels_for_month(db_user: dict, year: int, month: int) -> dict:
    """برچسب کوتاه روز برای شیفت کاربر — برای سازگاری."""
    labels_map, _ = await shift_day_meta_for_month(db_user, year, month)
    return labels_map


async def shift_day_meta_for_month(db_user: dict, year: int, month: int):
    """
    برمی‌گرداند: (shift_short_labels, day_types)
    day_types: morning|afternoon|night|rest
    """
    mode = await run_db(db.get_calendar_mode)
    ndays = jalali.days_in_jalali_month(year, month)
    short_map = {}
    type_map = {}

    if mode == "workday":
        # فقط پنجشنبه، جمعه و تعطیل رسمی = قرمز
        for day in range(1, ndays + 1):
            ds = jalali.parse_date_str(year, month, day)
            wd = jalali.jalali_weekday(year, month, day)  # 0=شنبه ... 5=پنجشنبه 6=جمعه
            if wd in (5, 6) or _iran_official_holiday(ds):
                type_map[ds] = "rest"
        return short_map, type_map

    if mode != "shift":
        return short_map, type_map

    cfg = await run_db(db.get_shift_config)
    if not cfg:
        return short_map, type_map
    si = db_user.get("shift_index")
    if si is None:
        si = 0
    si = int(si)
    labels = cfg.get("labels") or []
    if not labels:
        return short_map, type_map
    for day in range(1, ndays + 1):
        ds = jalali.parse_date_str(year, month, day)
        idx = _shift_slot_index(cfg, si, ds)
        if 0 <= idx < len(labels):
            lab = labels[idx] or {}
            name = str(lab.get("name") or "")
            short = str(lab.get("short") or name or "").replace("|", "/").strip()
            short_map[ds] = short
            type_map[ds] = calendar_ui.classify_slot_label(name, short)
    return short_map, type_map


async def build_calendar_view(
    db_user: dict, year: int, month: int, *, region_id: int = None, interactive: bool = True
):
    own_status = await own_status_for_month(db_user["user_id"], year, month)
    shift_labels, day_types = await shift_day_meta_for_month(db_user, year, month)
    sel = states.get_selection(db_user["user_id"], year, month) if interactive else {"to_submit": set(), "to_cancel": set()}
    others = await approved_others_for_month(db_user, year, month, region_id)
    keyboard = calendar_ui.build_calendar(
        year, month, today_str(), own_status, sel, shift_labels, others, day_types,
        interactive=interactive, show_actions=interactive,
    )
    title = "📅 تقویم مرخصی"
    if region_id:
        region = await run_db(db.get_region, region_id)
        if region:
            title += f" — {region['name']}"
    mode = await run_db(db.get_calendar_mode)
    legend = calendar_ui.legend_text()
    if mode == "workday":
        legend = "🔴 پنجشنبه، جمعه و تعطیل رسمی"
    text = title + "\n" + legend
    return text, keyboard


async def send_fresh_calendar(target, db_user: dict, year: int, month: int,
                              region_id: int = None, interactive: bool = True):
    text, keyboard = await build_calendar_view(
        db_user, year, month, region_id=region_id, interactive=interactive
    )
    await target.reply(text, components=keyboard)


async def render_calendar_edit(callback: CallbackQuery, db_user: dict, year: int, month: int,
                               region_id: int = None, interactive: bool = True):
    text, keyboard = await build_calendar_view(
        db_user, year, month, region_id=region_id, interactive=interactive
    )
    await callback.message.edit(text, components=keyboard)


# ==========================================================================
#  کال‌بک‌های اینلاین
# ==========================================================================

# === shift/settings handlers ===
async def _term_region() -> str:
    try:
        return await run_db(db.get_term_region)
    except Exception:
        return "منطقه کاری"

async def _term_group() -> str:
    try:
        return await run_db(db.get_term_group)
    except Exception:
        return "گروه کاری"


async def menu_with_terms(db_user: dict):
    if not db_user:
        return kb.user_menu()
    tr = await _term_region()
    tg = await _term_group()
    return kb.menu_for_user(db_user, tr, tg)


async def _terms():

    """(term_region, term_group, labels_dict)"""
    tr = await _term_region()
    tg = await _term_group()
    return tr, tg, kb.term_labels(tr, tg)

async def _ui_reply(callback: CallbackQuery, text: str, components=None):
    """همیشه پاسخ بده — اول edit، اگر نشد reply."""
    try:
        if components is not None:
            await callback.message.edit(text, components=components)
        else:
            await callback.message.edit(text)
        return
    except Exception:
        pass
    try:
        if components is not None:
            await callback.message.reply(text, components=components)
        else:
            await callback.message.reply(text)
    except Exception:
        logger.exception("ui reply failed")


async def cb_settings_terms(callback: CallbackQuery):
    try:
        tr = await _term_region()
        tg = await _term_group()
        from bale import InlineKeyboardMarkup, InlineKeyboardButton
        kb_i = InlineKeyboardMarkup()
        kb_i.add(InlineKeyboardButton(text=f"تغییر: {tr}", callback_data="settings_term_region"), row=1)
        kb_i.add(InlineKeyboardButton(text=f"تغییر: {tg}", callback_data="settings_term_group"), row=2)
        kb_i.add(InlineKeyboardButton(text="↩️ بازگشت", callback_data="nav_back_admin"), row=3)
        await _ui_reply(callback, f"🏷 واژه‌های قابل تغییر\n• منطقه: {tr}\n• گروه: {tg}", kb_i)
    except Exception:
        logger.exception("settings_terms failed")
        await _ui_reply(callback, "خطا در باز کردن واژه‌ها. اگر تازه آپدیت کردید، database.py را هم جایگزین کنید.")



async def cb_settings_shifts(callback: CallbackQuery):
    """لیست شیفت‌ها — تنها نقطهٔ ورود مدیریت هر شیفت."""
    nav_push(callback.user.id, "settings")
    cfg = await run_db(db.get_shift_config)
    if not cfg:
        await _ui_reply(callback, "ابتدا چرخهٔ شیفت را با «پیکربندی کامل چرخه» بسازید.")
        return
    letters = shift.shift_letters(cfg["shift_count"])
    from bale import InlineKeyboardMarkup, InlineKeyboardButton
    kb_i = InlineKeyboardMarkup()
    for i, letter in enumerate(letters):
        try:
            leads = await run_db(db.list_leads_for_shift, i)
        except Exception:
            leads = []
        n = len(leads) if leads else 0
        kb_i.add(
            InlineKeyboardButton(
                text=f"شیفت {letter}  —  {n} مسئول",
                callback_data=f"shmgmt:{i}",
            ),
            row=i + 1,
        )
    await _ui_reply(callback, "⚙️ تنظیمات شیفت‌ها\nیک شیفت را انتخاب کنید:", kb_i)


async def cb_shift_mgmt(callback: CallbackQuery, data: str):
    """پنل یک شیفت: روز کاری + مسئولان + مناطق + آمار."""
    nav_push(callback.user.id, "settings_shifts")
    try:
        idx = int(data.split(":")[1])
    except (IndexError, ValueError):
        await _ui_reply(callback, "دکمه نامعتبر.")
        return
    cfg = await run_db(db.get_shift_config)
    if not cfg:
        await _ui_reply(callback, "چرخهٔ شیفت پیکربندی نشده.")
        return
    letters = shift.shift_letters(cfg["shift_count"])
    letter = letters[idx] if idx < len(letters) else str(idx)
    try:
        leads = await run_db(db.list_leads_for_shift, idx) or []
    except Exception:
        leads = []
    try:
        cur_slot = _shift_slot_index(cfg, idx, today_str())
        slot_name = cfg["labels"][cur_slot]["name"] if cfg.get("labels") else "?"
    except Exception:
        slot_name = "?"

    from bale import InlineKeyboardMarkup, InlineKeyboardButton
    kb_i = InlineKeyboardMarkup()
    row = 1
    kb_i.add(
        InlineKeyboardButton(
            text=f"📅 تعیین روز کاری (الان: {slot_name})",
            callback_data=f"sfix:{idx}",
        ),
        row=row,
    )
    row += 1
    kb_i.add(
        InlineKeyboardButton(text="➕ تخصیص مسئول شیفت جدید", callback_data=f"shlead_add:{idx}"),
        row=row,
    )
    row += 1

    if not leads:
        kb_i.add(InlineKeyboardButton(text="(هنوز مسئولی نیست)", callback_data="noop"), row=row)
        row += 1
    else:
        for L in leads:
            name = f"{L.get('first_name') or ''} {L.get('last_name') or ''}".strip() or str(L["user_id"])
            regs = L.get("regions") or []
            rnames = "، ".join(r.get("name", "") for r in regs if isinstance(r, dict)) or "بدون منطقه"
            kb_i.add(
                InlineKeyboardButton(text=f"👔 {name}", callback_data=f"shlead_regions:{L['user_id']}:{idx}"),
                row=row,
            )
            row += 1
            kb_i.add(
                InlineKeyboardButton(
                    text=f"🗺 مناطق «{name}»: {rnames[:24]}",
                    callback_data=f"shlead_regions:{L['user_id']}:{idx}",
                ),
                row=row,
            )
            row += 1
            kb_i.add(
                InlineKeyboardButton(text=f"🗑 حذف «{name}»", callback_data=f"shlead_del:{L['user_id']}:{idx}"),
                row=row,
            )
            row += 1
            for r in regs:
                if isinstance(r, dict) and r.get("id") is not None:
                    kb_i.add(
                        InlineKeyboardButton(
                            text=f"ℹ️ آمار {r.get('name', '?')}",
                            callback_data=f"shreg_info:{r['id']}",
                        ),
                        row=row,
                    )
                    row += 1

    kb_i.add(InlineKeyboardButton(text="↩️ بازگشت به لیست شیفت‌ها", callback_data="settings_shifts"), row=row)
    n = len(leads)
    warn = "" if n >= 2 else "\n⚠️ پیشنهاد: حداقل ۲ مسئول برای هر شیفت."
    await _ui_reply(
        callback,
        f"⚙️ شیفت {letter}\nامروز این شیفت روی «{slot_name}» است.\nمسئولان: {n}{warn}",
        kb_i,
    )


async def cb_shlead_add(callback: CallbackQuery, data: str):
    idx = int(data.split(":")[1])
    users = await run_db(db.list_all_active_users) or []
    from bale import InlineKeyboardMarkup, InlineKeyboardButton
    kb_i = InlineKeyboardMarkup()
    row = 1
    count = 0
    for u in users:
        if u.get("is_shift_lead") or u.get("is_admin"):
            continue
        name = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip() or str(u["user_id"])
        kb_i.add(InlineKeyboardButton(text=name, callback_data=f"shlead_pick:{u['user_id']}:{idx}"), row=row)
        row += 1
        count += 1
        if count >= 40:
            break
    kb_i.add(InlineKeyboardButton(text="↩️ بازگشت", callback_data=f"shmgmt:{idx}"), row=row)
    if count == 0:
        await _ui_reply(callback, "کاربر فعالی برای انتصاب نیست. ابتدا اعضا را تایید کنید.", kb_i)
    else:
        await _ui_reply(callback, "کاربر را برای مسئول شیفت انتخاب کنید:", kb_i)


async def cb_shlead_pick(callback: CallbackQuery, data: str):
    parts = data.split(":")
    uid, idx = int(parts[1]), int(parts[2])
    regions = await run_db(db.list_regions) or []
    if not regions:
        await _ui_reply(callback, "ابتدا منطقه بسازید.")
        return
    states.set_state(
        callback.user.id,
        action="shl_pick_regions",
        target_uid=uid,
        shift_index=idx,
        selected=[],
    )
    await _ui_reply(
        callback,
        "مناطق تحت مدیریت این مسئول را تیک بزنید، سپس تأیید کنید:",
        kb.multi_region_toggle_keyboard(regions, set(), "shl_tog", done_callback="shl_done"),
    )


async def cb_shlead_regions_start(callback: CallbackQuery, data: str):
    parts = data.split(":")
    uid, idx = int(parts[1]), int(parts[2])
    regions = await run_db(db.list_regions) or []
    current = set(await run_db(db.list_shift_lead_region_ids, uid) or [])
    states.set_state(
        callback.user.id,
        action="shl_pick_regions",
        target_uid=uid,
        shift_index=idx,
        selected=list(current),
    )
    await _ui_reply(
        callback,
        "مناطق تحت مدیریت را ویرایش کنید، سپس تأیید:",
        kb.multi_region_toggle_keyboard(regions, current, "shl_tog", done_callback="shl_done"),
    )


async def cb_shl_tog(callback: CallbackQuery, data: str):
    rid = int(data.split(":")[1])
    st = states.get_state(callback.user.id) or {}
    if st.get("action") != "shl_pick_regions":
        await _ui_reply(callback, "جلسه منقضی شده؛ دوباره از تنظیمات شیفت‌ها وارد شوید.")
        return
    selected = set(st.get("selected") or [])
    if rid in selected:
        selected.discard(rid)
    else:
        selected.add(rid)
    states.set_state(
        callback.user.id,
        action="shl_pick_regions",
        target_uid=st.get("target_uid"),
        shift_index=st.get("shift_index"),
        selected=list(selected),
    )
    regions = await run_db(db.list_regions) or []
    await _ui_reply(
        callback,
        f"انتخاب‌شده: {len(selected)} منطقه — تیک بزنید و تأیید کنید:",
        kb.multi_region_toggle_keyboard(regions, selected, "shl_tog", done_callback="shl_done"),
    )


async def cb_shl_done(callback: CallbackQuery, data: str):
    st = states.get_state(callback.user.id) or {}
    if st.get("action") != "shl_pick_regions":
        await _ui_reply(callback, "جلسه منقضی شده.")
        return
    selected = st.get("selected") or []
    if not selected:
        await _ui_reply(callback, "حداقل یک منطقه لازم است.")
        return
    uid = int(st["target_uid"])
    idx = int(st.get("shift_index") or 0)
    try:
        await run_db(db.appoint_shift_lead, uid, selected, idx)
    except ValueError as e:
        await _ui_reply(callback, fa_error(e))
        return
    states.clear_state(callback.user.id)
    await _ui_reply(callback, "✅ مسئول شیفت و مناطق ذخیره شد.")
    await cb_shift_mgmt(callback, f"shmgmt:{idx}")


async def cb_shlead_del(callback: CallbackQuery, data: str):
    parts = data.split(":")
    uid, idx = int(parts[1]), int(parts[2])
    await run_db(db.remove_shift_lead, uid)
    await _ui_reply(callback, "✅ مسئول شیفت حذف شد.")
    await cb_shift_mgmt(callback, f"shmgmt:{idx}")


async def cb_shreg_info(callback: CallbackQuery, data: str):
    rid = int(data.split(":")[1])
    region = await run_db(db.get_region, rid)
    stats = await run_db(db.region_group_stats, rid) or {}
    groups = await run_db(db.list_member_groups, rid) or []
    term_r = await _term_region()
    term_g = await _term_group()
    lines = [f"• {g['name']} (ظرفیت {g['max_concurrent']})" for g in groups]
    text = (
        f"{term_r}: {region['name'] if region else rid}\n"
        f"ارشد: {stats.get('snr', 0)} | تکنسین: {stats.get('tech', 0)} | اپراتور: {stats.get('op', 0)}\n"
        f"{term_g}ها:\n" + ("\n".join(lines) if lines else "—")
    )
    await _ui_reply(callback, text)


@client.event
async def on_callback(callback: CallbackQuery):
    data = callback.data or ""
    if data == "noop":
        return
    try:
        if data.startswith("nav:"):
            await cb_nav(callback, data)
        elif data.startswith("pick:"):
            await cb_pick(callback, data)
        elif data.startswith("confirm_submit:"):
            await cb_confirm_submit(callback, data)
        elif data.startswith("leave_note_skip:"):
            _, y, m = data.split(":")
            y, m = int(y), int(m)
            st = states.get_state(callback.user.id) or {}
            dates = st.get("dates") or []
            msg = await finalize_leave_submit(callback.user.id, y, m, dates, None)
            db_user = await run_db(db.get_user, callback.user.id)
            await callback.message.reply(msg)
            if db_user:
                await send_fresh_calendar(callback.message, db_user, y, m, interactive=True)
        elif data.startswith("decide_note_skip:"):
            _, leave_id, new_status = data.split(":")
            states.clear_state(callback.user.id)
            result = await apply_leave_decision(
                callback.user.id, int(leave_id), new_status, None
            )
            await callback.message.reply(result)
        elif data.startswith("confirm_cancel:"):
            await cb_confirm_cancel(callback, data)
        elif data.startswith("clear_selection:"):
            await cb_clear_selection(callback, data)
        elif data.startswith("show_group_leaves:"):
            await cb_show_group_leaves(callback, data)
        elif data.startswith("dayinfo:"):
            await cb_dayinfo(callback, data)
        elif data.startswith("snr_cancel:"):
            await cb_snr_cancel_leave(callback, data)
        elif data.startswith("quick_leave:"):
            await cb_quick_leave(callback, data)
        elif data.startswith("decide:"):
            await cb_decide(callback, data)
        elif data.startswith("bdecide:"):
            await cb_batch_decide(callback, data)
        elif data == "batch_commit":
            await cb_batch_commit(callback)
        elif data == "batch_edit":
            await callback.message.reply("وضعیت‌ها ذخیره شده‌اند. می‌توانید دوباره آیکون‌ها را تغییر دهید.")
        elif data.startswith("rptm:"):
            await cb_report_month(callback, data)
        elif data.startswith("bcastopt:"):
            await cb_bcastopt(callback, data)
        elif data.startswith("resetbot:"):
            await cb_resetbot(callback, data)
        elif data.startswith("succvia:"):
            await cb_succvia(callback, data)
        elif data.startswith("bcast_reset:"):
            await cb_bcast_reset(callback, data)
        elif data.startswith("bcast_ack:"):
            await cb_bcast_reset(callback, data)
        elif data == "lead_cfg_groups":
            rids = await run_db(db.list_shift_lead_region_ids, callback.user.id) or []
            if not rids:
                await _ui_reply(callback, "منطقه‌ای به شما تخصیص داده نشده.")
            else:
                await show_regions_then_groups(callback.message, rids)
        elif data.startswith("myg_reg:"):
            rid = int(data.split(":")[1])
            # دسترسی
            viewer = await run_db(db.get_user, callback.user.id)
            if not viewer:
                return
            ok = viewer.get("is_admin") or await run_db(db.can_manage_region, callback.user.id, rid)
            if viewer.get("is_senior") and viewer.get("region_id") == rid:
                ok = True
            if not ok:
                await _ui_reply(callback, "به این منطقه دسترسی ندارید.")
            else:
                await show_groups_of_region(callback, rid)
        elif data == "myg_list":
            viewer = await run_db(db.get_user, callback.user.id)
            if not viewer:
                return
            if viewer.get("is_admin"):
                regs = await run_db(db.list_regions) or []
                rids = [r["id"] for r in regs]
            elif viewer.get("is_shift_lead"):
                rids = await run_db(db.list_shift_lead_region_ids, callback.user.id) or []
            else:
                rids = [viewer["region_id"]] if viewer.get("region_id") else []
            await show_regions_then_groups(callback.message, rids)
        elif data.startswith("addvia:"):
            await cb_addvia(callback, data)
        elif data.startswith("invrole:"):
            await cb_invite_role(callback, data)
        elif data.startswith("invshift:"):
            await cb_invite_shift(callback, data)
        elif data.startswith("invregion:"):
            await cb_invite_region(callback, data)
        elif data.startswith("invgroup:"):
            await cb_invite_group(callback, data)
        elif data.startswith("invlead_tog:"):
            await cb_invlead_toggle(callback, data)
        elif data == "invlead_done" or data.startswith("invlead_done"):
            await cb_invlead_done(callback, data)
        elif data == "nav_invite_again":
            await callback.message.reply(
                "نقش فرد دعوت‌شده را انتخاب کنید:",
                components=kb.role_select_keyboard("invrole", allowed_roles=allowed_add_roles(await run_db(db.get_user, callback.user.id))),
            )
        elif data.startswith("editcap:"):
            await cb_edit_capacity(callback, data)
        elif data.startswith("approve:"):
            await cb_approve_start(callback, data)
        elif data.startswith("setrole:"):
            await cb_set_role(callback, data)
        elif data.startswith("aprv_shift:"):
            await cb_aprv_shift(callback, data)
        elif data.startswith("aprv_region:"):
            await cb_aprv_region(callback, data)
        elif data.startswith("aprv_group:"):
            await cb_aprv_group(callback, data)
        elif data.startswith("aprvlead_tog:"):
            await cb_aprvlead_toggle(callback, data)
        elif data == "aprvlead_done" or data.startswith("aprvlead_done"):
            await cb_aprvlead_done(callback, data)
        elif data.startswith("setmembershift:"):
            await cb_set_member_shift_start(callback, data)
        elif data.startswith("applymembershift:"):
            await cb_apply_member_shift(callback, data)
        elif data.startswith("setmode:"):
            await cb_set_mode(callback, data)
        elif data == "cfgshift:start":
            await cb_cfgshift_start(callback, data)
        elif data.startswith("cfgshift_own:"):
            await cb_cfgshift_own(callback, data)
        elif data.startswith("cfgshift_ref:"):
            await cb_cfgshift_ref(callback, data)
        elif data.startswith("cfgfirst:"):
            await cb_cfg_first_day(callback, data)
        elif data == "nav_main":
            await cb_nav_main(callback)
        elif data == "nav_back_admin":
            await cb_nav_back_admin(callback)
        elif data.startswith("sslot:") or data.startswith("shiftcfgslot:"):
            await cb_shift_own_settings_slot(callback, data)
        elif data.startswith("sfix:") or data.startswith("shiftcfg:"):
            await cb_shift_own_settings(callback, data)
        # ---- مناطق / مسئول شیفت / رنگ / تقویم منطقه ----
        elif data == "region_new":
            states.set_state(callback.user.id, action="new_region_name")
            await callback.message.reply("نام منطقه جدید را وارد کنید:")
        elif data.startswith("region_info:"):
            rid = int(data.split(":")[1])
            region = await run_db(db.get_region, rid)
            if region:
                cap = await run_db(db.get_region_max_seniors, rid)
                custom = " (اختصاصی)" if region.get("max_seniors") else " (سراسری)"
                await callback.message.reply(
                    f"منطقه: {region['name']}",
                    components=kb.region_actions_keyboard(rid, max_seniors_label=f" [{cap}{custom}]"),
                )
        elif data.startswith("region_maxsnr:"):
            rid = int(data.split(":")[1])
            states.set_state(callback.user.id, action="set_region_max_seniors", region_id=rid)
            cur = await run_db(db.get_region_max_seniors, rid)
            await callback.message.reply(
                f"سقف فعلیِ تکنسین ارشد در این منطقه: {cur}\n"
                "عدد جدید را وارد کنید؛ یا برای پیروی دوباره از سقف سراسری، عدد 0 را بفرستید:"
            )
        elif data.startswith("region_rename:"):
            rid = int(data.split(":")[1])
            states.set_state(callback.user.id, action="rename_region", region_id=rid)
            await callback.message.reply("نام جدید منطقه را وارد کنید:")
        elif data.startswith("region_del:"):
            rid = int(data.split(":")[1])
            ok = await run_db(db.delete_region, rid)
            await callback.message.reply("✅ حذف شد." if ok else "حذف ممکن نیست (گروه یا عضو دارد).")
        elif data.startswith("region_groups:"):
            rid = int(data.split(":")[1])
            groups = await run_db(db.list_groups, rid)
            region = await run_db(db.get_region, rid)
            rname = region["name"] if region else "؟"
            from bale import InlineKeyboardButton
            gk = kb.groups_edit_keyboard(groups)
            next_row = len(groups) * 3 + 1  # هر گروه ۳ ردیف دارد (ظرفیت/نام/حذف)
            gk.add(
                InlineKeyboardButton(text="➕ ساخت گروه در این منطقه", callback_data=f"region_addgroup:{rid}"),
                row=next_row,
            )
            if not groups:
                await callback.message.reply(f"گروهی در منطقه «{rname}» نیست.", components=gk)
            else:
                lines = [f"• {g['name']} — ظرفیت {g['max_concurrent']}" for g in groups]
                await callback.message.reply(f"گروه‌های منطقه «{rname}»:\n" + "\n".join(lines), components=gk)
        elif data.startswith("region_addgroup:"):
            rid = int(data.split(":")[1])
            region = await run_db(db.get_region, rid)
            if not region:
                await callback.message.reply("منطقه پیدا نشد.")
                return
            states.set_state(callback.user.id, action="new_group_name", region_id=rid)
            await callback.message.reply(f"نام گروه جدید در منطقه «{region['name']}» را وارد کنید:")
        elif data.startswith("grp_rename:"):
            gid = int(data.split(":")[1])
            states.set_state(callback.user.id, action="rename_group", group_id=gid)
            await callback.message.reply("نام جدید گروه را وارد کنید:")
        elif data.startswith("grp_delete:"):
            gid = int(data.split(":")[1])
            ok = await run_db(db.delete_group, gid)
            await callback.message.reply(
                "✅ گروه حذف شد." if ok else "❌ حذف ممکن نیست: این گروه هنوز عضو دارد. ابتدا اعضا را جابه‌جا کنید."
            )
        elif data.startswith("cal_region:"):
            rid = int(data.split(":")[1])
            db_user = await run_db(db.get_user, callback.user.id)
            if not db_user:
                return
            if not (db_user.get("is_admin") or await run_db(db.can_manage_region, callback.user.id, rid)):
                await callback.message.reply("دسترسی به این منطقه ندارید.")
                return
            y, m, _ = jalali.today_jalali()
            await send_fresh_calendar(
                callback.message, db_user, y, m, region_id=rid, interactive=False
            )
        
        elif data == "settings_terms":
            nav_push(callback.user.id, "settings")
            await cb_settings_terms(callback)
        elif data == "settings_term_region":
            states.set_state(callback.user.id, action="set_term_region")
            cur = await _term_region()
            await callback.message.reply(f"نام فعلی «منطقه»: {cur}\nنام جدید را وارد کنید:")
        elif data == "settings_term_group":
            states.set_state(callback.user.id, action="set_term_group")
            cur = await _term_group()
            await callback.message.reply(f"نام فعلی «گروه»: {cur}\nنام جدید را وارد کنید:")
        elif data == "settings_shifts":
            await cb_settings_shifts(callback)
        elif data.startswith("shmgmt:"):
            await cb_shift_mgmt(callback, data)
        elif data.startswith("shlead_add:"):
            await cb_shlead_add(callback, data)
        elif data.startswith("shlead_pick:"):
            await cb_shlead_pick(callback, data)
        elif data.startswith("shlead_regions:"):
            await cb_shlead_regions_start(callback, data)
        elif data.startswith("shl_tog:"):
            await cb_shl_tog(callback, data)
        elif data == "shl_done" or data.startswith("shl_done"):
            await cb_shl_done(callback, data)
        elif data.startswith("shlead_del:"):
            await cb_shlead_del(callback, data)
        elif data.startswith("shreg_info:"):
            await cb_shreg_info(callback, data)

        elif data == "settings_regions":
            nav_push(callback.user.id, "settings")
            regions = await run_db(db.list_regions)
            await _ui_reply(callback, "🗺 مناطق:", kb.regions_manage_keyboard(regions))
        elif data == "settings_shiftleads":
            nav_push(callback.user.id, "settings")
            leads = await run_db(db.list_shift_leads)
            cap = await run_db(db.get_max_shift_leads)
            await _ui_reply(
                callback,
                f"👔 مسئولان شیفت ({len(leads)} از سقف {cap}):",
                kb.shift_leads_manage_keyboard(leads),
            )
        elif data == "settings_replaceadmin":
            await _ui_reply(
                callback,
                "جایگزینی مدیر — فقط با لینک یا دفترچه تلفن (بدون شناسه):",
                kb.succession_method_keyboard("replace_admin"),
            )
        elif data == "settings_max_leads":
            states.set_state(callback.user.id, action="set_max_shift_leads")
            cur = await run_db(db.get_max_shift_leads)
            await callback.message.reply(f"سقف فعلی: {cur}\nعدد جدید را وارد کنید:")
        elif data == "settings_max_seniors":
            states.set_state(callback.user.id, action="set_max_seniors")
            cur = await run_db(db.get_max_seniors_per_region)
            await callback.message.reply(f"سقف فعلیِ تکنسین ارشد در هر منطقه: {cur}\nعدد جدید را وارد کنید:")
        elif data == "settings_max_snr_leave":
            states.set_state(callback.user.id, action="set_max_snr_leave")
            cur = await run_db(db.get_max_senior_leave_per_shift)
            await callback.message.reply(
                f"ظرفیت مرخصی هم‌زمان ارشد در هر شیفت: {cur}\n"
                "حداکثر چند ارشد هم‌شیفت در یک روز می‌توانند مرخصی تاییدشده داشته باشند.\n"
                "عدد جدید (۰ = نامحدود):"
            )
        elif data == "settings_colors":
            groups = await run_db(db.list_groups)
            if not groups:
                await callback.message.reply("گروهی نیست.")
                return
            for g in groups:
                await callback.message.reply(
                    f"رنگ گروه «{g['name']}» (فعلی: {g.get('color')}):",
                    components=kb.color_picker_keyboard(g["id"]),
                )
        elif data.startswith("setcolor:"):
            _, gid, color = data.split(":", 2)
            await run_db(db.update_group_color, int(gid), color)
            await callback.message.reply(f"✅ رنگ گروه به {color} تغییر کرد.")
        elif data.startswith("appoint_sl_pick:"):
            target = int(data.split(":")[1])
            regions = await run_db(db.list_regions)
            if not regions:
                await _ui_reply(callback, "ابتدا منطقه بسازید.")
            else:
                states.set_state(
                    callback.user.id,
                    action="sl_pick_regions",
                    target_uid=target,
                    selected=[],
                    appointing=True,
                )
                await _ui_reply(
                    callback,
                    "مناطق تحت مدیریت این مسئول شیفت را انتخاب کنید:",
                    kb.multi_region_toggle_keyboard(regions, set(), "sl_tog"),
                )
        elif data == "sl_appoint":
            await _ui_reply(
                callback,
                "انتصاب مسئول شیفت جدید — روش را انتخاب کنید (بدون وارد کردن شناسه):",
                kb.succession_method_keyboard("appoint_lead"),
            )
        elif data.startswith("sl_info:"):
            uid = int(data.split(":")[1])
            await callback.message.reply(
                f"مسئول شیفت {uid}:",
                components=kb.shift_lead_actions_keyboard(uid),
            )
        elif data.startswith("sl_remove:"):
            uid = int(data.split(":")[1])
            await run_db(db.remove_shift_lead, uid)
            await callback.message.reply("✅ عزل شد.")
        elif data.startswith("sl_setregs:"):
            uid = int(data.split(":")[1])
            regions = await run_db(db.list_regions)
            selected = set(await run_db(db.list_shift_lead_region_ids, uid))
            states.set_state(callback.user.id, action="sl_pick_regions", target_uid=uid, selected=list(selected))
            await callback.message.reply(
                "مناطق را تیک بزنید:",
                components=kb.multi_region_toggle_keyboard(regions, selected, "sl_tog"),
            )
        elif data.startswith("sl_tog:"):
            rid = int(data.split(":")[1])
            st = states.get_state(callback.user.id) or {}
            selected = set(st.get("selected") or [])
            if rid in selected:
                selected.discard(rid)
            else:
                selected.add(rid)
            states.set_state(
                callback.user.id,
                action="sl_pick_regions",
                target_uid=st.get("target_uid"),
                selected=list(selected),
                appointing=st.get("appointing"),  # رفع باگ: قبلاً اینجا پاک می‌شد
            )
            regions = await run_db(db.list_regions)
            await callback.message.edit(
                "مناطق را تیک بزنید:",
                components=kb.multi_region_toggle_keyboard(regions, selected, "sl_tog"),
            )
        elif data == "sl_regions_done":
            st = states.get_state(callback.user.id) or {}
            target = st.get("target_uid")
            selected = st.get("selected") or []
            if target and selected:
                try:
                    if st.get("appointing"):
                        await run_db(db.appoint_shift_lead, target, selected)
                        msg = "✅ مسئول شیفت منصوب شد."
                    else:
                        await run_db(db.set_shift_lead_regions, target, selected)
                        msg = "✅ مناطق مسئول شیفت به‌روز شد."
                    states.clear_state(callback.user.id)
                    await callback.message.reply(msg)
                except ValueError as e:
                    await callback.message.reply(fa_error(e))
            else:
                await callback.message.reply("حداقل یک منطقه انتخاب کنید.")
        elif data.startswith("lead_own_shift:"):
            idx = int(data.split(":")[1])
            await run_db(db.set_user_shift, callback.user.id, idx)
            await callback.message.reply("✅ شیفت کاری شما ثبت شد.")
        elif data.startswith("member_chgrp:"):
            uid = int(data.split(":")[1])
            if not await viewer_can_manage_user(callback.user.id, uid):
                await callback.message.reply("شما اجازه‌ی مدیریت این عضو را ندارید.")
                return
            u = await run_db(db.get_user, uid)
            if not u or not u.get("region_id"):
                await callback.message.reply("منطقه کاربر مشخص نیست.")
                return
            groups = await run_db(db.list_groups, u["region_id"])
            await callback.message.reply(
                "گروه جدید را انتخاب کنید:",
                components=kb.group_select_keyboard(groups, f"do_chgrp:{uid}", allow_none=False),
            )
        elif data.startswith("do_chgrp:"):
            # do_chgrp:{uid}:{gid}
            parts = data.split(":")
            uid, gid = int(parts[1]), parts[2]
            if gid == "none":
                return
            if not await viewer_can_manage_user(callback.user.id, uid):
                await callback.message.reply("شما اجازه‌ی مدیریت این عضو را ندارید.")
                return
            try:
                await run_db(db.change_user_group, uid, int(gid))
                await callback.message.reply("✅ گروه کاربر تغییر کرد (مرخصی‌ها حفظ شدند).")
            except ValueError as e:
                await callback.message.reply(fa_error(e))
        elif data.startswith("member_chreg:"):
            uid = int(data.split(":")[1])
            if not await viewer_can_manage_user(callback.user.id, uid):
                await callback.message.reply("شما اجازه‌ی مدیریت این عضو را ندارید.")
                return
            viewer = await run_db(db.get_user, callback.user.id)
            if viewer and viewer.get("is_admin"):
                regions = await run_db(db.list_regions)
            else:
                allowed = await run_db(db.managed_region_ids, callback.user.id)
                all_regions = await run_db(db.list_regions)
                regions = [r for r in all_regions if r["id"] in allowed]
            await callback.message.reply(
                "منطقه جدید (مرخصی‌های آینده حذف می‌شوند):",
                components=kb.region_select_keyboard(regions, f"do_chreg:{uid}"),
            )
        elif data.startswith("do_chreg:"):
            parts = data.split(":")
            uid, rid = int(parts[1]), parts[2]
            if rid == "none":
                return
            if not await viewer_can_manage_user(callback.user.id, uid):
                await callback.message.reply("شما اجازه‌ی مدیریت این عضو را ندارید.")
                return
            viewer = await run_db(db.get_user, callback.user.id)
            if not (viewer and viewer.get("is_admin")) and not await run_db(
                db.can_manage_region, callback.user.id, int(rid)
            ):
                await callback.message.reply("منطقه‌ی مقصد خارج از محدوده‌ی دسترسی شماست.")
                return
            try:
                info = await run_db(db.move_user_to_region, uid, int(rid), None)
                await callback.message.reply(
                    f"✅ منتقل شد. مرخصی آینده حذف‌شده: {info['deleted_future_leaves']}"
                )
            except ValueError as e:
                await callback.message.reply(fa_error(e))
        elif data.startswith("member_senior:"):
            uid = int(data.split(":")[1])
            if not await viewer_can_manage_user(callback.user.id, uid):
                await callback.message.reply("شما اجازه‌ی این کار را ندارید.")
                return
            u = await run_db(db.get_user, uid)
            if not u or not u.get("group_id"):
                await callback.message.reply("این کاربر هنوز به هیچ گروهی اختصاص داده نشده است.")
                return
            status, extra = await run_db(db.set_senior, uid, True)
            if status == "full":
                cap = await run_db(db.get_max_seniors_per_region)
                await callback.message.reply(
                    f"❌ سقفِ تعداد تکنسین ارشد در این منطقه پر است ({extra} از {cap} نفر).\n"
                    "برای افزودن این فرد، ابتدا یکی از ارشدهای فعلیِ همان منطقه را عزل کنید."
                )
                return
            group = await run_db(db.get_group, u["group_id"])
            await callback.message.reply(
                f"✅ «{display_name(u)}» تکنسین ارشدِ گروه «{group['name'] if group else u['group_id']}» شد."
            )
        elif data.startswith("member_unsenior:"):
            uid = int(data.split(":")[1])
            if not await viewer_can_manage_user(callback.user.id, uid):
                await callback.message.reply("شما اجازه‌ی این کار را ندارید.")
                return
            await run_db(db.set_senior, uid, False)
            await callback.message.reply("✅ ارشدی لغو شد.")
        elif data.startswith("member_remove:"):
            uid = int(data.split(":")[1])
            if not await viewer_can_manage_user_or_own_group(callback.user.id, uid):
                await callback.message.reply("شما اجازه‌ی حذف این عضو را ندارید.")
                return
            info = await run_db(db.remove_user_from_system, uid)
            await callback.message.reply(
                f"✅ حذف شد. مرخصی آینده پاک‌شده: {info['deleted_future_leaves']}"
            )
        elif data.startswith("member_info:"):
            uid = int(data.split(":")[1])
            viewer = await run_db(db.get_user, callback.user.id)
            if not await viewer_can_manage_user_or_own_group(callback.user.id, uid):
                await callback.message.reply("شما به این عضو دسترسی ندارید.")
                return
            can_reg = bool(viewer and (viewer.get("is_admin") or viewer.get("is_shift_lead")))
            can_snr = bool(viewer and (viewer.get("is_admin") or viewer.get("is_shift_lead")))
            await callback.message.reply(
                f"عملیات روی کاربر {uid}:",
                components=kb.member_actions_keyboard(uid, can_change_region=can_reg, can_set_senior=can_snr),
            )
    except Exception:
        logger.exception("خطا در پردازش کال‌بک: %s", data)
        try:
            await callback.message.reply(f"خطا در پردازش دکمه.\nکد: {data[:40]}")
        except Exception:
            pass


# ------------------------------------------------------- تقویم کاربر -------

async def cb_nav(callback: CallbackQuery, data: str):
    _, y, m = data.split(":")
    y, m = int(y), int(m)
    db_user = await run_db(db.get_user, callback.user.id)
    if not db_user:
        return
    st = states.get_state(callback.user.id) or {}
    sel = states.get_selection(callback.user.id, y, m)
    over_mode = bool(sel.get("over_mode") or st.get("action") == "cal_over_mode")
    if over_mode:
        sel["over_mode"] = True
        interactive = True
    else:
        interactive = bool(db_user.get("group_id") or db_user.get("is_senior") or db_user.get("role") == "snr")
        if db_user.get("is_admin") and not db_user.get("is_senior") and not db_user.get("is_shift_lead"):
            # مدیر خالص: تقویم نمایشی مگر over_mode
            interactive = False
        if db_user.get("is_shift_lead") and not over_mode:
            # مسئول شیفت در حالت عادی می‌تواند برای خودش ثبت کند اگر گروه دارد
            interactive = bool(db_user.get("group_id") or db_user.get("approved"))
    await render_calendar_edit(callback, db_user, y, m, interactive=interactive)


async def cb_pick(callback: CallbackQuery, data: str):
    _, y, m, d = data.split(":")
    y, m, d = int(y), int(m), int(d)
    date_str = jalali.parse_date_str(y, m, d)
    if date_str < today_str():
        return
    uid = callback.user.id
    db_user = await run_db(db.get_user, uid)
    if not db_user:
        return
    st = states.get_state(uid) or {}
    sel = states.get_selection(uid, y, m)
    over_mode = bool(sel.get("over_mode") or st.get("action") == "cal_over_mode")
    # ارشد بدون گروه، یا حالت مازاد: مجاز به انتخاب
    can_pick = bool(
        db_user.get("group_id")
        or db_user.get("is_senior")
        or db_user.get("role") == "snr"
        or over_mode
        or db_user.get("approved")
    )
    if not can_pick:
        await callback.message.reply("برای ثبت مرخصی باید عضو یک گروه باشید.")
        return
    if over_mode:
        sel["over_mode"] = True
    own_status = await own_status_for_month(uid, y, m)
    if date_str in own_status:
        states.toggle_cancel(uid, y, m, date_str)
    else:
        states.toggle_submit(uid, y, m, date_str)
    # حفظ over_mode بعد از toggle
    if over_mode:
        states.get_selection(uid, y, m)["over_mode"] = True
    await render_calendar_edit(callback, db_user, y, m, interactive=True)


async def cb_confirm_submit(callback: CallbackQuery, data: str):
    """ثبت مستقیم مرخصی بدون پرسش توضیح."""
    _, y, m = data.split(":")
    y, m = int(y), int(m)
    uid = callback.user.id
    sel = states.get_selection(uid, y, m)
    to_submit = sorted(sel["to_submit"])
    if not to_submit:
        await callback.message.reply("روزی برای ثبت انتخاب نشده بود.")
        return
    msg = await finalize_leave_submit(uid, y, m, to_submit, None)
    db_user = await run_db(db.get_user, uid)
    await callback.message.reply(msg)
    if db_user:
        await send_fresh_calendar(callback.message, db_user, y, m)



async def finalize_leave_submit(uid: int, y: int, m: int, dates: list, note: str = None):
    db_user = await run_db(db.get_user, uid)
    if not db_user:
        return "کاربر پیدا نشد."
    sel = states.get_selection(uid, y, m)
    over_mode = bool(sel.get("over_mode"))
    st = states.get_state(uid) or {}
    if st.get("action") == "cal_over_mode":
        over_mode = True

    normal_submitted = []   # (date, lid)
    over_submitted = []
    blocked = []            # only when not over_mode

    batch_id = secrets.token_hex(8) if len(dates) > 1 else None
    for date_str in dates:
        # پیش‌چک ظرفیت برای تصمیم نوع درخواست
        status_chk, owners = await run_db(db.request_leave, uid, date_str, note, batch_id)
        if status_chk == "created":
            normal_submitted.append((date_str, owners))
            continue
        if status_chk == "exists":
            continue
        if status_chk == "full":
            if over_mode:
                st2, lid2 = await run_db(db.request_over_capacity_leave, uid, date_str, note or "اضافه بر ظرفیت")
                if st2 == "created":
                    over_submitted.append((date_str, lid2, owners or []))
                elif st2 == "exists":
                    pass
                else:
                    blocked.append((date_str, owners or []))
            else:
                blocked.append((date_str, owners or []))

    sel["to_submit"].clear()
    sel["over_mode"] = False
    states.clear_state(uid)

    parts = []
    if blocked:
        for date_str, owners in blocked:
            names = "، ".join(display_name(o) for o in owners) if owners else "-"
            parts.append(
                f"❌ {jalali.format_jalali(date_str)}: ظرفیت تکمیل است. "
                f"دارنده(های) مرخصی: {names}\n"
                "درخواستی ثبت نشد. برای ثبت مازاد از دکمه «مرخصی اضافه بر ظرفیت» استفاده کنید."
            )
    if normal_submitted:
        dates_txt = "، ".join(jalali.format_jalali(d) for d, _ in normal_submitted)
        if len(normal_submitted) > 1:
            await notify_leave_batch(db_user, normal_submitted)
        else:
            for date_str, lid in normal_submitted:
                await notify_admin_new_leave_request(db_user, date_str, lid)
        parts.append(
            f"📝 درخواست عادی برای روز(های) {dates_txt} ثبت و برای بررسی ارسال شد."
        )
    if over_submitted:
        for date_str, lid, owners in over_submitted:
            await notify_over_capacity_leave(db_user, date_str, lid)
        dates_txt = "، ".join(jalali.format_jalali(d) for d, _, __ in over_submitted)
        parts.append(
            f"⚠️ درخواست اضافه بر ظرفیت برای روز(های) {dates_txt} ثبت و برای مافوق ارسال شد."
        )
    if not parts:
        return "روزی برای ثبت انتخاب نشده بود یا از قبل ثبت شده است."
    return "\n\n".join(parts)



async def cb_confirm_cancel(callback: CallbackQuery, data: str):
    _, y, m = data.split(":")
    y, m = int(y), int(m)
    uid = callback.user.id
    db_user = await run_db(db.get_user, uid)
    sel = states.get_selection(uid, y, m)
    to_cancel = sorted(sel["to_cancel"])

    cancelled = []
    for date_str in to_cancel:
        lv = await run_db(db.get_user_leave_on_date, uid, date_str)
        if lv and lv["status"] in db.ACTIVE_STATUSES:
            await run_db(db.cancel_leave, lv["id"])
            cancelled.append(date_str)
    sel["to_cancel"].clear()

    if cancelled:
        dates_txt = "، ".join(jalali.format_jalali(d) for d in cancelled)
        await callback.message.reply(f"❌ مرخصیِ روز(های) {dates_txt} توسط شما لغو شد.")
        await notify_admins_leave_cancelled(db_user, cancelled)
    else:
        await callback.message.reply("روزی برای لغو انتخاب نشده بود.")

    await send_fresh_calendar(callback.message, db_user, y, m)


async def cb_clear_selection(callback: CallbackQuery, data: str):
    _, y, m = data.split(":")
    y, m = int(y), int(m)
    uid = callback.user.id
    sel = states.get_selection(uid, y, m)
    sel["to_submit"].clear()
    sel["to_cancel"].clear()
    db_user = await run_db(db.get_user, uid)
    await render_calendar_edit(callback, db_user, y, m)


async def cb_show_group_leaves(callback: CallbackQuery, data: str):
    """همه اعضا مرخصی‌های فعال منطقه خود را می‌بینند (نه فقط هم‌گروه)."""
    _, y, m = data.split(":")
    y, m = int(y), int(m)
    uid = callback.user.id
    db_user = await run_db(db.get_user, uid)
    rid = db_user.get("region_id") if db_user else None
    if not rid:
        await callback.message.reply("منطقه کاری شما مشخص نیست.")
    else:
        rows = await run_db(db.list_region_active_leaves, rid, today_str())
        if not rows:
            await callback.message.reply("مرخصی فعالی در منطقه شما ثبت نشده است.")
        else:
            lines = [
                f"• {jalali.format_jalali(r['leave_date'])} — {display_name(r)} "
                f"— {r.get('group_name') or '-'} — {STATUS_FA.get(r['status'], r['status'])}"
                for r in rows
            ]
            await callback.message.reply("مرخصی‌های فعال منطقه شما:\n" + "\n".join(lines))
    await send_fresh_calendar(callback.message, db_user, y, m)


# ------------------------------------------------------- تصمیم مدیر -------

async def cb_decide(callback: CallbackQuery, data: str):
    """اعمال مستقیم تصمیم بدون پرسش توضیح."""
    _, leave_id, new_status = data.split(":")
    leave_id = int(leave_id)
    leave = await run_db(db.get_leave, leave_id)
    if leave is None:
        try:
            await callback.message.edit("این درخواست دیگر معتبر نیست.")
        except Exception:
            await callback.message.reply("این درخواست دیگر معتبر نیست.")
        return
    result = await apply_leave_decision(callback.user.id, leave_id, new_status, None)
    try:
        await callback.message.edit(result)
    except Exception:
        await callback.message.reply(result)



async def apply_leave_decision(decider_id: int, leave_id: int, new_status: str, note_admin: str = None):
    status, extra = await run_db(
        db.try_set_status, leave_id, new_status, decider_id, note_admin
    )
    leave = await run_db(db.get_leave, leave_id)

    if status == "not_found" or leave is None:
        return "این درخواست دیگر معتبر نیست (احتمالاً لغو شده)."

    if status == "full":
        names = "، ".join(display_name(o) for o in extra) if extra else "-"
        return (
            f"❌ ظرفیت مرخصی برای {jalali.format_jalali(leave['leave_date'])} "
            f"پر است. دارندگان: {names}"
        )

    requester = await run_db(db.get_user, leave["user_id"])
    status_fa_msg = {
        "approved": "✅ تایید شد",
        "reviewing": "🔍 در حال بررسی است",
        "rejected": "❌ رد شد",
    }.get(new_status, new_status)
    note_line = f"\nتوضیح بررسی‌کننده: {note_admin}" if note_admin else ""
    try:
        await client.send_message(
            leave["user_id"],
            f"وضعیت درخواست مرخصیِ {jalali.format_jalali(leave['leave_date'])} شما: "
            f"{status_fa_msg}{note_line}",
        )
    except Exception:
        logger.exception("خطا در اطلاع‌رسانی وضعیت به کاربر %s", leave["user_id"])

    return f"وضعیت به «{status_fa_msg}» تغییر کرد و به کاربر اطلاع داده شد."


# ----------------------------------------------------- گروه‌ها / دعوت‌ها ----

async def cb_bcastopt(callback: CallbackQuery, data: str):
    viewer = await run_db(db.get_user, callback.user.id)
    if not viewer or not viewer.get("is_admin"):
        await _ui_reply(callback, "فقط مدیر.")
        return
    opt = data.split(":")[1]
    if opt == "1":
        await run_db(db.set_setting, "broadcast_with_button", "1")
        states.set_state(callback.user.id, action="broadcast_options", with_btn=True)
        await _ui_reply(
            callback,
            "📢 دکمه «ریست ربات» همراه پیام: فعال\nدر صورت تمایل ادامه را بزنید.",
            kb.broadcast_options_keyboard(True),
        )
        return
    if opt == "0":
        await run_db(db.set_setting, "broadcast_with_button", "0")
        states.set_state(callback.user.id, action="broadcast_options", with_btn=False)
        await _ui_reply(
            callback,
            "📢 دکمه «ریست ربات» همراه پیام: غیرفعال\nدر صورت تمایل ادامه را بزنید.",
            kb.broadcast_options_keyboard(False),
        )
        return
    if opt == "go":
        raw = await run_db(db.get_setting, "broadcast_with_button", "1")
        with_btn = str(raw) not in ("0", "false", "False")
        states.set_state(callback.user.id, action="broadcast_subject", with_btn=with_btn)
        await _ui_reply(callback, "موضوع پیام همگانی را وارد کنید:")
        return
    await _ui_reply(callback, "گزینه نامعتبر.")


async def cb_resetbot(callback: CallbackQuery, data: str):
    viewer = await run_db(db.get_user, callback.user.id)
    if not viewer or not viewer.get("is_admin"):
        await _ui_reply(callback, "فقط مدیر.")
        return
    if data.endswith(":no"):
        await _ui_reply(callback, "ریست لغو شد.")
        return
    # پاک‌سازی حافظه موقت
    try:
        states.PENDING_INPUT.clear()
        states.SELECTION.clear()
    except Exception:
        pass
    try:
        if hasattr(cache, "clear"):
            cache.clear()
        elif hasattr(cache, "store") and hasattr(cache.store, "clear"):
            cache.store.clear()
    except Exception:
        logger.exception("cache clear failed")
    await _ui_reply(
        callback,
        "✅ ریست انجام شد.\nحالت‌های موقت کاربران پاک شد. داده دیتابیس دست نخورده است.\nهمه با /start منوی تازه می‌گیرند.",
    )


async def cb_bcast_reset(callback: CallbackQuery, data: str):
    """ریست سمت کاربر: پاک‌سازی حالت موقت + منوی تازه (پس از آپدیت ربات)."""
    uid = callback.user.id
    try:
        states.clear_state(uid)
        states.clear_selection(uid)
    except Exception:
        pass
    try:
        cache.inv_user(uid)
    except Exception:
        pass
    db_user = await run_db(db.get_user, uid)
    menu = await menu_with_terms(db_user) if db_user else None
    try:
        await callback.message.edit(
            "✅ ریست انجام شد.\nحالت موقت شما پاک شد. از منوی زیر استفاده کنید."
        )
    except Exception:
        pass
    if db_user and db_user.get("profile_complete"):
        await callback.message.reply(
            "منوی به‌روز:",
            components=menu,
        )
    else:
        await callback.message.reply("لطفاً /start را بزنید.")


async def cb_bcast_ack(callback: CallbackQuery, data: str):
    await cb_bcast_reset(callback, data)


async def cb_succvia(callback: CallbackQuery, data: str):

    """succvia:contact|link:replace_admin|transfer_lead"""
    parts = data.split(":")
    if len(parts) < 3:
        await _ui_reply(callback, "گزینه نامعتبر.")
        return
    method, purpose = parts[1], parts[2]
    viewer = await run_db(db.get_user, callback.user.id)
    if purpose in ("replace_admin", "appoint_lead") and not (viewer and viewer.get("is_admin")):
        await _ui_reply(callback, "فقط مدیر.")
        return
    if purpose == "transfer_lead" and not (viewer and viewer.get("is_shift_lead")):
        await _ui_reply(callback, "فقط مسئول شیفت.")
        return
    if method == "list" and purpose == "appoint_lead":
        users = await run_db(db.list_all_active_users) or []
        # فقط کسانی که هنوز مسئول شیفت نیستند
        users = [u for u in users if not u.get("is_shift_lead")]
        mode = await run_db(db.get_calendar_mode)
        cfg = await run_db(db.get_shift_config) if mode == "shift" else None
        letters = shift.shift_letters(cfg["shift_count"]) if cfg else []
        if not users:
            await _ui_reply(callback, "عضو تاییدشده‌ای برای انتصاب نیست.")
            return
        await _ui_reply(
            callback,
            "عضو را از لیست انتخاب کنید:",
            kb.pick_member_keyboard(users, letters, "appoint_sl_pick"),
        )
        return
    if method == "contact":
        states.set_state(callback.user.id, action="awaiting_contact", purpose=purpose)
        await callback.message.reply(
            "مخاطب جانشین را از دفترچه تلفن انتخاب و ارسال کنید.",
            components=kb.contact_request_menu(),
        )
        return
    if method == "link":
        token = secrets.token_urlsafe(8)
        if purpose == "appoint_lead":
            await run_db(
                db.create_invite, token, "lead", None, callback.user.id,
                None, 0, None, 1, [],
            )
            await run_db(db.set_setting, f"invite_purpose_{token}", "appoint_lead")
            try:
                me = await client.get_me()
                uname = getattr(me, "username", None) or "BOT"
                link = f"https://ble.ir/{uname}?start={token}"
            except Exception:
                link = f"/start {token}"
            await _ui_reply(
                callback,
                f"🔗 لینک دعوت مسئول شیفت:\n{link}\n\nپس از عضویت، مناطق را از تنظیمات مسئولان شیفت تخصیص دهید.",
            )
            return
        if purpose == "transfer_lead":
            rids = await run_db(db.list_shift_lead_region_ids, callback.user.id) or []
            await run_db(
                db.create_invite, token, "lead", None, callback.user.id,
                None, 0, viewer.get("shift_index") if viewer else None, 1, rids,
            )
            await run_db(db.set_setting, f"pending_lead_transfer_by_{callback.user.id}", token)
            await run_db(db.set_setting, f"invite_purpose_{token}", "transfer_lead")
        else:
            # جانشین مدیر — نقش عادی؛ با فلگ در settings هنگام /start اعمال می‌شود
            await run_db(
                db.create_invite, token, None, None, callback.user.id,
                None, 0, None, 0, None,
            )
            await run_db(db.set_setting, f"pending_admin_successor_by_{callback.user.id}", token)
            await run_db(db.set_setting, f"invite_purpose_{token}", "replace_admin")
        try:
            me = await client.get_me()
            uname = getattr(me, "username", None) or "BOT"
            link = f"https://ble.ir/{uname}?start={token}"
        except Exception:
            link = f"/start {token}"
        label = "جایگزینی مدیر" if purpose == "replace_admin" else "واگذاری مسئول شیفت"
        await _ui_reply(
            callback,
            f"🔗 لینک {label}:\n{link}\n\nاین لینک را فقط برای فرد مورد نظر بفرستید. پس از باز کردن لینک و تکمیل ثبت‌نام، نقش منتقل می‌شود.",
        )
        return
    await _ui_reply(callback, "گزینه نامعتبر.")


async def cb_addvia(callback: CallbackQuery, data: str):

    """انتخاب روش افزودن: لینک یا دفترچه تلفن."""
    method = data.split(":", 1)[1]
    viewer = await run_db(db.get_user, callback.user.id)
    roles = allowed_add_roles(viewer)
    if not roles:
        await _ui_reply(callback, "شما اجازه‌ی افزودن عضو ندارید.")
        return
    if method == "link":
        await _ui_reply(
            callback,
            "نقش فرد دعوت‌شونده را انتخاب کنید:",
            kb.role_select_keyboard("invrole", allowed_roles=roles),
        )
        return
    if method == "contact":
        states.set_state(callback.user.id, action="awaiting_contact", purpose="add")
        try:
            menu = kb.contact_request_menu()
        except Exception:
            logger.exception("contact_request_menu failed")
            menu = None
        text_msg = (
            "دفترچه تلفن را باز کنید و مخاطب را انتخاب کنید.\n"
            "روی دکمه «انتخاب و ارسال مخاطب» بزنید."
        )
        try:
            if menu is not None:
                await callback.message.reply(text_msg, components=menu)
            else:
                await callback.message.reply(text_msg)
        except Exception:
            logger.exception("reply contact menu failed")
            await callback.message.reply("لطفاً یک مخاطب از دفترچه تلفن برای ربات ارسال کنید.")
        return
    await _ui_reply(callback, "گزینه نامعتبر.")


async def cb_invite_role(callback: CallbackQuery, data: str):
    role_code = data.split(":", 1)[1]
    viewer = await run_db(db.get_user, callback.user.id)
    is_snr_only = (
        viewer
        and (viewer.get("is_senior") or viewer.get("role") == "snr")
        and not viewer.get("is_admin")
        and not viewer.get("is_shift_lead")
    )
    if is_snr_only:
        shift_code = (
            str(viewer["shift_index"])
            if viewer.get("shift_index") is not None
            else "none"
        )
        await _show_invite_region_picker(callback, role_code, shift_code)
        return
    mode = await run_db(db.get_calendar_mode)
    cfg = await run_db(db.get_shift_config) if mode == "shift" else None
    if cfg:
        await callback.message.edit(
            "شیفتِ افرادی که با این لینک عضو می‌شوند را انتخاب کنید:",
            components=kb.shift_letter_keyboard(cfg["shift_count"], f"invshift:{role_code}"),
        )
        return
    await _show_invite_region_picker(callback, role_code, "none")


async def cb_invite_shift(callback: CallbackQuery, data: str):
    _, role_code, shift_idx = data.split(":")
    await _show_invite_region_picker(callback, role_code, shift_idx)


async def _show_invite_region_picker(callback: CallbackQuery, role_code, shift_code):
    viewer = await run_db(db.get_user, callback.user.id)
    if viewer and viewer.get("is_admin"):
        regions = await run_db(db.list_regions)
    else:
        allowed = await run_db(db.managed_region_ids, callback.user.id)
        all_regions = await run_db(db.list_regions)
        regions = [r for r in all_regions if r["id"] in allowed]
    if not regions:
        await callback.message.edit(
            "هیچ منطقه‌ای در محدوده‌ی دسترسی شما نیست. ابتدا یک منطقه بسازید.",
            components=kb.nav_row_keyboard(back_callback="nav_back_admin"),
        )
        return

    # مسئول شیفت: انتخاب چندمنطقه‌ای (سرگروه مناطق)
    if role_code == "lead":
        states.set_state(
            callback.user.id,
            action="inv_lead_regions",
            role_code=role_code,
            shift_code=str(shift_code),
            selected=[],
        )
        await callback.message.edit(
            "مناطق تحت مدیریت این مسئول شیفت را انتخاب کنید (می‌توانید چند مورد را تیک بزنید):",
            components=kb.multi_region_toggle_keyboard(regions, set(), "invlead_tog", done_callback="invlead_done"),
        )
        return

    await callback.message.edit(
        "منطقه‌ی کاریِ افرادی که با این لینک عضو می‌شوند را انتخاب کنید:",
        components=kb.region_select_keyboard(regions, f"invregion:{role_code}:{shift_code}"),
    )


async def cb_invite_region(callback: CallbackQuery, data: str):
    _, role_code, shift_code, region_code = data.split(":")
    if region_code == "none":
        await callback.message.edit("باید یک منطقه انتخاب کنید.")
        return
    region_id = int(region_code)
    viewer = await run_db(db.get_user, callback.user.id)
    if not (viewer and viewer.get("is_admin")) and not await run_db(
        db.can_manage_region, callback.user.id, region_id
    ):
        await callback.message.edit("❌ این منطقه خارج از محدوده‌ی دسترسی شماست.")
        return
    groups = await run_db(db.list_member_groups, region_id)
    if not groups:
        await callback.message.edit(
            "این منطقه هنوز گروهی ندارد. ابتدا یک گروه بسازید.",
            components=kb.nav_row_keyboard(back_callback="nav_back_admin"),
        )
        return
    await callback.message.edit(
        "گروهِ کاریِ افرادی که با این لینک عضو می‌شوند را انتخاب کنید:",
        components=kb.group_select_keyboard(groups, f"invgroup:{role_code}:{shift_code}", allow_none=False),
    )



async def cb_invlead_toggle(callback: CallbackQuery, data: str):
    """تیک زدن/برداشتن منطقه برای دعوت مسئول شیفت."""
    rid = int(data.split(":")[1])
    st = states.get_state(callback.user.id) or {}
    if st.get("action") != "inv_lead_regions":
        await callback.message.reply("جلسه منقضی شده؛ دوباره از لینک دعوت شروع کنید.")
        return
    selected = set(st.get("selected") or [])
    if rid in selected:
        selected.discard(rid)
    else:
        selected.add(rid)
    states.set_state(
        callback.user.id,
        action="inv_lead_regions",
        role_code=st.get("role_code", "lead"),
        shift_code=st.get("shift_code", "none"),
        selected=list(selected),
    )
    regions = await run_db(db.list_regions)
    await callback.message.edit(
        "مناطق تحت مدیریت این مسئول شیفت را انتخاب کنید:",
        components=kb.multi_region_toggle_keyboard(regions, selected, "invlead_tog", done_callback="invlead_done"),
    )


async def cb_invlead_done(callback: CallbackQuery, data: str):
    """ساخت لینک دعوت مسئول شیفت با مناطق انتخاب‌شده."""
    st = states.get_state(callback.user.id) or {}
    if st.get("action") != "inv_lead_regions":
        await callback.message.reply("جلسه منقضی شده؛ دوباره از لینک دعوت شروع کنید.")
        return
    selected = st.get("selected") or []
    if not selected:
        await callback.message.reply("حداقل یک منطقه انتخاب کنید.")
        return
    shift_code = st.get("shift_code", "none")
    shift_index = None if shift_code == "none" else int(shift_code)
    token = secrets.token_urlsafe(6)
    await run_db(
        db.create_invite, token, "lead", None, callback.user.id,
        None, 0, shift_index, 1, selected,
    )
    states.clear_state(callback.user.id)

    names = []
    for rid in selected:
        r = await run_db(db.get_region, rid)
        if r:
            names.append(r["name"])
    shift_txt = "بدون شیفت مشخص"
    if shift_index is not None:
        cfg = await run_db(db.get_shift_config)
        if cfg:
            letters = shift.shift_letters(cfg["shift_count"])
            if shift_index < len(letters):
                shift_txt = f"شیفت {letters[shift_index]}"
    username = _bot_username["value"]
    link_line = f"https://ble.ir/{username}?start={token}\n" if username else ""
    await callback.message.edit(
        "✅ لینک دعوت مسئول شیفت ساخته شد.\n"
        f"نقش: مسئول شیفت (سرگروه مناطق)\n{shift_txt}\n"
        f"مناطق: {', '.join(names) or '-'}\n\n"
        f"{link_line}"
        f"کد دعوت: `{token}`\n\n"
        "فرد جدید با /start و این کد، به‌صورت خودکار مسئول شیفت مناطق بالا می‌شود.",
        components=kb.after_action_keyboard(
            add_callback="nav_invite_again",
            add_label="🔗 ساخت لینک دیگر",
            back_callback="nav_back_admin",
            show_main=False,
        ),
    )


async def cb_invite_group(callback: CallbackQuery, data: str):
    _, role_code, shift_code, group_code = data.split(":")
    role = None if role_code == "none" else role_code
    shift_index = None if shift_code == "none" else int(shift_code)
    group_id = None if group_code == "none" else int(group_code)

    viewer = await run_db(db.get_user, callback.user.id)
    if group_id and not (viewer and viewer.get("is_admin")):
        group = await run_db(db.get_group, group_id)
        if not group or not await run_db(db.can_manage_region, callback.user.id, group.get("region_id")):
            await callback.message.edit("❌ این گروه خارج از محدوده‌ی دسترسی شماست.")
            return

    token = secrets.token_urlsafe(6)
    await run_db(db.create_invite, token, role, group_id, callback.user.id, None, 0, shift_index)

    role_label = config.ROLE_LABELS.get(role, "بدون نقش")
    group = await run_db(db.get_group, group_id) if group_id else None
    group_name = group["name"] if group else "بدون گروه"
    region = await run_db(db.get_region, group["region_id"]) if group and group.get("region_id") else None
    region_name = region["name"] if region else "-"
    shift_txt = "بدون شیفت مشخص"
    if shift_index is not None:
        cfg = await run_db(db.get_shift_config)
        if cfg:
            letters = shift.shift_letters(cfg["shift_count"])
            if shift_index < len(letters):
                shift_txt = f"شیفت {letters[shift_index]}"

    username = _bot_username["value"]
    link_line = f"https://ble.ir/{username}?start={token}\n" if username else ""
    await callback.message.edit(
        "✅ لینک دعوت ساخته شد؛ فردی که از این لینک عضو شود به‌طور خودکار با این مشخصات تایید می‌شود:\n"
        f"نقش: {role_label}\n{shift_txt}\nمنطقه: {region_name}\nگروه: {group_name}\n\n"
        f"{link_line}"
        f"کد دعوت: `{token}`\n\n"
        "اگر با کلیک روی لینک بالا، ربات به‌طور خودکار شروع نشد، از فرد جدید بخواهید "
        "ابتدا ربات را باز کند و پیام زیر را برایش ارسال کند:\n"
        f"/start {token}"
    )


async def cb_edit_capacity(callback: CallbackQuery, data: str):
    group_id = int(data.split(":", 1)[1])
    group = await run_db(db.get_group, group_id)
    if not group:
        return
    states.set_state(callback.user.id, action="edit_capacity", group_id=group_id)
    await callback.message.reply(
        f"ظرفیت جدید هم‌زمان برای گروه «{group['name']}» را به‌صورت عدد وارد کنید (فعلی: {group['max_concurrent']}):"
    )


# ------------------------------------------------------ تایید کاربر جدید ---

async def cb_approve_start(callback: CallbackQuery, data: str):
    user_id = int(data.split(":", 1)[1])
    viewer = await run_db(db.get_user, callback.user.id)
    allowed = await run_db(db.managed_region_ids, callback.user.id)
    if not (viewer and (viewer.get("is_admin") or allowed)):
        await callback.message.reply("شما اجازه‌ی تایید عضو جدید را ندارید.")
        return
    await callback.message.edit("نقش این فرد را انتخاب کنید:", components=kb.role_select_keyboard(f"setrole:{user_id}", allowed_roles=allowed_add_roles(await run_db(db.get_user, callback.user.id))))


async def cb_set_role(callback: CallbackQuery, data: str):
    """بعد از نقش: شیفت فقط برای مدیر/مسئول شیفت؛ ارشد شیفت خودش را به ارث می‌برد."""
    _, user_id, role_code = data.split(":")
    viewer = await run_db(db.get_user, callback.user.id)
    mode = await run_db(db.get_calendar_mode)
    cfg = await run_db(db.get_shift_config) if mode == "shift" else None

    # تکنسین ارشد: بدون پرسش شیفت — از شیفت خودش استفاده می‌شود
    is_snr_only = (
        viewer
        and (viewer.get("is_senior") or viewer.get("role") == "snr")
        and not viewer.get("is_admin")
        and not viewer.get("is_shift_lead")
    )
    if is_snr_only:
        shift_code = (
            str(viewer["shift_index"])
            if viewer.get("shift_index") is not None
            else "none"
        )
        await _show_region_picker(callback, user_id, role_code, shift_code)
        return

    if cfg:
        await callback.message.edit(
            "شیفتِ این فرد را انتخاب کنید:",
            components=kb.shift_letter_keyboard(cfg["shift_count"], f"aprv_shift:{user_id}:{role_code}"),
        )
        return
    await _show_region_picker(callback, user_id, role_code, "none")


async def cb_aprv_shift(callback: CallbackQuery, data: str):
    _, user_id, role_code, shift_idx = data.split(":")
    await _show_region_picker(callback, user_id, role_code, shift_idx)


async def _show_region_picker(callback: CallbackQuery, user_id, role_code, shift_code):
    viewer = await run_db(db.get_user, callback.user.id)
    if viewer and viewer.get("is_admin"):
        regions = await run_db(db.list_regions)
    else:
        allowed = await run_db(db.managed_region_ids, callback.user.id)
        all_regions = await run_db(db.list_regions)
        regions = [r for r in all_regions if r["id"] in allowed]
    if not regions:
        term_r = await _term_region()
        await callback.message.edit(f"هیچ {term_r} در محدوده‌ی دسترسی شما نیست. ابتدا یکی بسازید.")
        return
    # مسئول شیفت: چندمنطقه‌ای
    if role_code == "lead":
        states.set_state(
            callback.user.id,
            action="aprv_lead_regions",
            target_uid=int(user_id),
            role_code=role_code,
            shift_code=str(shift_code),
            selected=[],
        )
        term_r = await _term_region()
        await callback.message.edit(
            f"{term_r} تحت مدیریت این مسئول شیفت را انتخاب کنید (چندتایی):",
            components=kb.multi_region_toggle_keyboard(
                regions, set(), "aprvlead_tog", done_callback="aprvlead_done"
            ),
        )
        return
    term_r = await _term_region()
    await callback.message.edit(
        f"{term_r} این فرد را انتخاب کنید:",
        components=kb.region_select_keyboard(regions, f"aprv_region:{user_id}:{role_code}:{shift_code}"),
    )


async def cb_aprv_region(callback: CallbackQuery, data: str):
    _, user_id, role_code, shift_code, region_code = data.split(":")
    if region_code == "none":
        await callback.message.edit("باید یک منطقه انتخاب کنید.")
        return
    region_id = int(region_code)
    viewer = await run_db(db.get_user, callback.user.id)
    if not (viewer and viewer.get("is_admin")) and not await run_db(
        db.can_manage_region, callback.user.id, region_id
    ):
        await callback.message.edit("❌ این منطقه خارج از محدوده‌ی دسترسی شماست.")
        return
    groups = await run_db(db.list_member_groups, region_id)
    if not groups:
        term_g = await _term_group()
        await callback.message.edit(
            f"این منطقه هنوز {term_g} ندارد. ابتدا یکی بسازید."
        )
        return
    term_g = await _term_group()
    await callback.message.edit(
        f"{term_g} این فرد را انتخاب کنید:",
        components=kb.group_select_keyboard(
            groups, f"aprv_group:{user_id}:{role_code}:{shift_code}", allow_none=False
        ),
    )



async def cb_aprvlead_toggle(callback: CallbackQuery, data: str):
    rid = int(data.split(":")[1])
    st = states.get_state(callback.user.id) or {}
    if st.get("action") != "aprv_lead_regions":
        await callback.message.reply("جلسه منقضی شده؛ دوباره از تایید اعضا شروع کنید.")
        return
    selected = set(st.get("selected") or [])
    if rid in selected:
        selected.discard(rid)
    else:
        selected.add(rid)
    states.set_state(
        callback.user.id,
        action="aprv_lead_regions",
        target_uid=st.get("target_uid"),
        role_code=st.get("role_code", "lead"),
        shift_code=st.get("shift_code", "none"),
        selected=list(selected),
    )
    regions = await run_db(db.list_regions)
    await callback.message.edit(
        "مناطق تحت مدیریت را انتخاب کنید:",
        components=kb.multi_region_toggle_keyboard(
            regions, selected, "aprvlead_tog", done_callback="aprvlead_done"
        ),
    )


async def cb_aprvlead_done(callback: CallbackQuery, data: str):
    st = states.get_state(callback.user.id) or {}
    if st.get("action") != "aprv_lead_regions":
        await callback.message.reply("جلسه منقضی شده.")
        return
    selected = st.get("selected") or []
    if not selected:
        await callback.message.reply("حداقل یک منطقه انتخاب کنید.")
        return
    uid = int(st["target_uid"])
    shift_code = st.get("shift_code", "none")
    shift_index = None if shift_code == "none" else int(shift_code)
    try:
        await run_db(db.appoint_shift_lead, uid, selected, shift_index)
    except ValueError as e:
        await callback.message.reply(fa_error(e))
        return
    states.clear_state(callback.user.id)
    names = []
    for rid in selected:
        r = await run_db(db.get_region, rid)
        if r:
            names.append(r["name"])
    shift_txt = ""
    if shift_index is not None:
        cfg = await run_db(db.get_shift_config)
        if cfg:
            letters = shift.shift_letters(cfg["shift_count"])
            if shift_index < len(letters):
                shift_txt = f"\nشیفت: {letters[shift_index]}"
    await callback.message.edit(
        f"✅ مسئول شیفت تایید شد.{shift_txt}\nمناطق: {', '.join(names)}"
    )
    try:
        u = await run_db(db.get_user, uid)
        await client.send_message(
            uid,
            f"✅ شما به‌عنوان مسئول شیفت تایید شدید.{shift_txt}\nمناطق: {', '.join(names)}",
            components=await menu_with_terms(u) if u else kb.shift_lead_menu(),
        )
    except Exception:
        logger.exception("notify lead approve failed")


async def cb_aprv_group(callback: CallbackQuery, data: str):
    _, user_id, role_code, shift_code, group_code = data.split(":")
    role = None if role_code == "none" else role_code
    shift_index = None if shift_code == "none" else int(shift_code)
    group_id = None if group_code == "none" else int(group_code)
    await finalize_approve(callback, int(user_id), role, group_id, shift_index)


async def finalize_approve(callback: CallbackQuery, user_id: int, role, group_id, shift_index):
    viewer = await run_db(db.get_user, callback.user.id)
    if group_id and not (viewer and viewer.get("is_admin")):
        group = await run_db(db.get_group, group_id)
        if not group or not await run_db(db.can_manage_region, callback.user.id, group.get("region_id")):
            await callback.message.edit("❌ این گروه خارج از محدوده‌ی دسترسی شماست.")
            return
    is_senior = 1 if role == "snr" else 0
    await run_db(db.approve_user, user_id, role, group_id, shift_index, None, is_senior)
    role_label = config.ROLE_LABELS.get(role, "بدون نقش")
    group = await run_db(db.get_group, group_id) if group_id else None
    group_name = group["name"] if group else "بدون گروه"
    region = await run_db(db.get_region, group["region_id"]) if group and group.get("region_id") else None
    region_name = region["name"] if region else "-"
    shift_txt = ""
    if shift_index is not None:
        cfg = await run_db(db.get_shift_config)
        if cfg:
            letters = shift.shift_letters(cfg["shift_count"])
            if shift_index < len(letters):
                shift_txt = f"\nشیفت: {letters[shift_index]}"
    await callback.message.edit(
        f"✅ کاربر تایید شد.\nنقش: {role_label}{shift_txt}\nمنطقه: {region_name}\nگروه: {group_name}"
    )
    try:
        approved_user = await run_db(db.get_user, user_id)
        await client.send_message(
            user_id,
            f"✅ شما توسط مدیر تایید شدید.\nنقش: {role_label}{shift_txt}\nمنطقه: {region_name}\nگروه: {group_name}",
            components=await menu_with_terms(approved_user) if approved_user else kb.user_menu(),
        )
    except Exception:
        logger.exception("خطا در اطلاع‌رسانی تایید به کاربر %s", user_id)


async def cb_set_member_shift_start(callback: CallbackQuery, data: str):
    user_id = int(data.split(":", 1)[1])
    cfg = await run_db(db.get_shift_config)
    if not cfg:
        await callback.message.reply("ابتدا باید چرخه‌ی شیفت را در «⚙️ تنظیمات» پیکربندی کنید.")
        return
    await callback.message.edit(
        "شیفت این عضو را انتخاب کنید:",
        components=kb.shift_letter_keyboard(cfg["shift_count"], f"applymembershift:{user_id}"),
    )


async def cb_apply_member_shift(callback: CallbackQuery, data: str):
    _, user_id, idx = data.split(":")
    user_id, idx = int(user_id), int(idx)
    await run_db(db.set_user_shift, user_id, idx)
    cfg = await run_db(db.get_shift_config)
    letters = shift.shift_letters(cfg["shift_count"]) if cfg else []
    letter = letters[idx] if idx < len(letters) else str(idx)
    await callback.message.edit(f"✅ شیفت این عضو روی «{letter}» تنظیم شد.")


# ----------------------------------------------------- تنظیمات / شیفت -----

async def cb_set_mode(callback: CallbackQuery, data: str):
    _, mode = data.split(":")
    await run_db(db.set_setting, "calendar_mode", mode)
    if mode == "workday":
        await callback.message.edit("✅ نوع تقویم روی «روزکار» تنظیم شد.", components=kb.settings_keyboard("workday"))
    else:
        await callback.message.edit(
            "✅ نوع تقویم روی «شیفتی» تنظیم شد.\nحالا باید چرخه‌ی شیفت را پیکربندی کنید.\n"
            "تعداد شیفت‌ها را وارد کنید (بین ۲ تا ۲۶)، مثلاً 4:"
        )
        states.set_state(callback.user.id, action="cfg_shift_count")


async def cb_cfgshift_start(callback: CallbackQuery, data: str):
    await callback.message.edit("تعداد شیفت‌ها را وارد کنید (بین ۲ تا ۲۶)، مثلاً 4:")
    states.set_state(callback.user.id, action="cfg_shift_count")


async def cb_cfgshift_own(callback: CallbackQuery, data: str):
    _, idx = data.split(":")
    idx = int(idx)
    state = states.get_state(callback.user.id)
    if not state or state.get("action") != "cfg_own_shift":
        return
    states.set_state(
        callback.user.id, action="cfg_ref_slot", shift_count=state["shift_count"],
        cycle_length=state["cycle_length"], labels=state["labels"], own_shift_index=idx,
    )
    await callback.message.edit(
        "امروز برای شیفتِ شما کدام ردیف از سیکل است؟ انتخاب کنید:",
        components=kb.slot_select_keyboard(state["labels"], "cfgshift_ref"),
    )




async def cb_nav_main(callback: CallbackQuery):
    """بازگشت به منوی اصلی نقش کاربر."""
    states.clear_state(callback.user.id)
    db_user = await run_db(db.get_user, callback.user.id)
    await _ui_reply(callback, "🏠 منوی اصلی", await menu_with_terms(db_user))


async def open_admin_settings(callback: CallbackQuery):
    """نمایش منوی تنظیمات مدیر (یک سطح بالاتر از زیرمنوها)."""
    mode = await run_db(db.get_calendar_mode)
    cfg = await run_db(db.get_shift_config) if mode == "shift" else None
    sc = cfg["shift_count"] if cfg else None
    await _ui_reply(
        callback,
        "⚙️ تنظیمات ربات",
        kb.settings_keyboard(mode, is_admin=True, shift_count=sc),
    )


async def cb_nav_back_admin(callback: CallbackQuery):
    """یک سطح عقب: از زیرمنوی تنظیمات → خود تنظیمات (نه منوی اصلی)."""
    st = states.get_state(callback.user.id) or {}
    stack = list(st.get("nav_stack") or [])
    # پاک کردن اکشن جاری ولی نگه‌داشتن stack در صورت نیاز
    if stack:
        target = stack.pop()
        states.set_state(callback.user.id, nav_stack=stack)
        if target == "settings":
            await open_admin_settings(callback)
            return
        if target == "settings_shifts":
            await cb_settings_shifts(callback)
            return
        if isinstance(target, str) and target.startswith("shmgmt:"):
            await cb_shift_mgmt(callback, target)
            return
        if target == "settings_regions":
            regions = await run_db(db.list_regions)
            await _ui_reply(callback, "مناطق:", kb.regions_manage_keyboard(regions))
            return
        if target == "settings_shiftleads":
            leads = await run_db(db.list_shift_leads)
            cap = await run_db(db.get_max_shift_leads)
            await _ui_reply(
                callback,
                f"مسئولان شیفت ({len(leads)} از سقف {cap}):",
                kb.shift_leads_manage_keyboard(leads),
            )
            return
    # پیش‌فرض: برگشت به تنظیمات
    states.clear_state(callback.user.id)
    await open_admin_settings(callback)


def nav_push(uid: int, screen: str):
    """ثبت صفحهٔ فعلی در پشتهٔ بازگشت."""
    st = states.get_state(uid) or {}
    stack = list(st.get("nav_stack") or [])
    if not stack or stack[-1] != screen:
        stack.append(screen)
    # حفظ بقیهٔ state
    kwargs = {k: v for k, v in st.items() if k != "nav_stack"}
    states.set_state(uid, nav_stack=stack, **kwargs)


async def _safe_edit_or_reply(callback: CallbackQuery, text: str, components=None):
    """ویرایش پیام؛ اگر شکست خورد، پیام جدید می‌فرستد تا کاربر بلاک نشود."""
    try:
        if components is not None:
            await callback.message.edit(text, components=components)
        else:
            await callback.message.edit(text)
    except Exception:
        logger.exception("edit failed; falling back to reply")
        if components is not None:
            await callback.message.reply(text, components=components)
        else:
            await callback.message.reply(text)


async def cb_cfg_first_day(callback: CallbackQuery, data: str):
    """اولین روز کاری هر شیفت — وضعیت از دیتابیس + state خوانده می‌شود تا از دست نرود."""
    parts = data.split(":")
    # انتظار: cfgfirst:{shift_index}:{slot_index}
    if len(parts) < 3:
        await callback.message.reply("دکمه نامعتبر است. دوباره از تنظیمات شیفت شروع کنید.")
        return
    try:
        shift_index = int(parts[1])
        slot_idx = int(parts[2])
    except ValueError:
        await callback.message.reply("داده نامعتبر بود. لطفاً دوباره تلاش کنید.")
        return

    cfg = await run_db(db.get_shift_config)
    if not cfg:
        await callback.message.reply("چرخهٔ شیفت هنوز پیکربندی نشده. از تنظیمات شروع کنید.")
        return

    labels = cfg.get("labels") or []
    shift_count = int(cfg["shift_count"])
    if not labels or slot_idx < 0 or slot_idx >= len(labels):
        await callback.message.reply("ردیف انتخاب‌شده معتبر نیست.")
        return
    if shift_index < 0 or shift_index >= shift_count:
        await callback.message.reply("شماره شیفت نامعتبر است.")
        return

    # ذخیره در دیتابیس (منبع حقیقت)
    await run_db(db.set_shift_override, shift_index, today_str(), slot_idx)

    # پیشرفت را از overrides دیتابیس بساز
    overrides = cfg.get("overrides") or {}
    overrides = dict(overrides)
    overrides[str(shift_index)] = {"ref_date": today_str(), "ref_slot_index": slot_idx}

    state = states.get_state(callback.user.id) or {}
    first_days = dict(state.get("first_days") or {})
    # همگام‌سازی با overrides
    for k, v in overrides.items():
        if isinstance(v, dict) and "ref_slot_index" in v:
            first_days[str(k)] = int(v["ref_slot_index"])
    first_days[str(shift_index)] = slot_idx

    letters = shift.shift_letters(shift_count)
    letter = letters[shift_index] if shift_index < len(letters) else str(shift_index)
    slot_name = labels[slot_idx]["name"]

    # شیفت بعدی که هنوز تنظیم نشده
    next_i = None
    for i in range(shift_count):
        if str(i) not in first_days:
            next_i = i
            break

    if next_i is not None:
        states.set_state(
            callback.user.id,
            action="cfg_first_day",
            shift_count=shift_count,
            labels=labels,
            first_days=first_days,
            first_day_index=next_i,
        )
        nxt_letter = letters[next_i] if next_i < len(letters) else str(next_i)
        await _safe_edit_or_reply(
            callback,
            f"✅ شیفت {letter}: اولین روز کاری = «{slot_name}»\n"
            f"پیشرفت: {len(first_days)} از {shift_count}\n\n"
            f"شیفت {nxt_letter} — ردیفِ اولین روز کاری را انتخاب کنید:",
            components=kb.slot_select_keyboard(labels, f"cfgfirst:{next_i}"),
        )
        return

    # همه انجام شد
    states.set_state(
        callback.user.id,
        action="cfg_leads_per_shift",
        shift_count=shift_count,
        first_days=first_days,
    )
    await _safe_edit_or_reply(
        callback,
        f"✅ شیفت {letter}: اولین روز کاری = «{slot_name}»\n"
        f"✅ اولین روز کاری همهٔ {shift_count} شیفت ثبت شد.\n\n"
        f"حالا «تعداد مسئول شیفت» را وارد کنید "
        f"(سقف کل، مثلاً {shift_count}):",
    )


async def cb_cfgshift_ref(callback: CallbackQuery, data: str):
    _, idx = data.split(":")
    idx = int(idx)
    state = states.get_state(callback.user.id)
    if not state or state.get("action") != "cfg_ref_slot":
        return
    ref_date = today_str()
    await run_db(
        db.save_shift_config, state["shift_count"], state["cycle_length"], state["labels"],
        ref_date, state["own_shift_index"], idx,
    )
    # اورراید اختصاصی برای شیفت خود مدیر هم ذخیره شود
    await run_db(db.set_shift_override, state["own_shift_index"], ref_date, idx)
    letters = shift.shift_letters(state["shift_count"])
    first_days = {str(state["own_shift_index"]): idx}
    start_i = 0
    while start_i < state["shift_count"] and str(start_i) in first_days:
        start_i += 1
    if start_i >= state["shift_count"]:
        # همه از قبل پر است (نادر) → مستقیم سقف مسئول
        states.set_state(
            callback.user.id,
            action="cfg_leads_per_shift",
            shift_count=state["shift_count"],
            first_days=first_days,
        )
        await callback.message.edit(
            "✅ چرخه‌ی شیفت و اولین روز کاری همهٔ شیفت‌ها ثبت شد.\n\n"
            "حالا «تعداد مسئول شیفت» را وارد کنید (سقف کل، مثلاً "
            f"{state['shift_count']}):"
        )
        return
    states.set_state(
        callback.user.id,
        action="cfg_first_day",
        shift_count=state["shift_count"],
        cycle_length=state["cycle_length"],
        labels=state["labels"],
        first_day_index=start_i,
        first_days=first_days,
        own_shift_index=state["own_shift_index"],
        own_ref_slot=idx,
    )
    await callback.message.edit(
        "✅ چرخه‌ی شیفت ذخیره شد.\n"
        f"شیفت‌ها: {', '.join(letters)} | طول سیکل: {state['cycle_length']} روز\n\n"
        "مرحلهٔ بعد: «اولین روز کاری» هر شیفت را مشخص کنید "
        "(کدام ردیف سیکل، روز اولِ کاریِ آن شیفت است).\n\n"
        f"شیفت {letters[start_i]} — ردیفِ اولین روز کاری را انتخاب کنید:",
        components=kb.slot_select_keyboard(state["labels"], f"cfgfirst:{start_i}"),
    )


async def cb_shift_own_settings(callback: CallbackQuery, data: str):
    """تعیین روز کاری یک شیفت (از پنل تنظیمات شیفت‌ها)."""
    try:
        shift_index = int(data.split(":")[1])
    except (IndexError, ValueError):
        await _ui_reply(callback, "دکمه نامعتبر است.")
        return
    cfg = await run_db(db.get_shift_config)
    if not cfg:
        await _ui_reply(callback, "ابتدا باید چرخه‌ی شیفت را پیکربندی کنید.")
        return
    letters = shift.shift_letters(cfg["shift_count"])
    letter = letters[shift_index] if shift_index < len(letters) else str(shift_index)
    try:
        cur_idx = _shift_slot_index(cfg, shift_index, today_str())
        cur_name = cfg["labels"][cur_idx]["name"]
    except Exception:
        cur_name = "?"
    await _ui_reply(
        callback,
        f"تنظیمات شیفت {letter}\n"
        f"امروز طبق محاسبه روی «{cur_name}» است.\n"
        "ردیف درستِ امروز را برای همین شیفت انتخاب کنید:",
        kb.slot_select_keyboard(cfg["labels"], f"sslot:{shift_index}"),
    )


async def cb_shift_own_settings_slot(callback: CallbackQuery, data: str):
    """ثبت روز کاری یک شیفت."""
    parts = data.split(":")
    if len(parts) < 3:
        await _ui_reply(callback, "دکمه نامعتبر است.")
        return
    try:
        shift_index = int(parts[1])
        slot_idx = int(parts[2])
    except ValueError:
        await _ui_reply(callback, "داده نامعتبر.")
        return
    cfg = await run_db(db.get_shift_config)
    if not cfg:
        await _ui_reply(callback, "چرخه پیکربندی نشده.")
        return
    labels = cfg.get("labels") or []
    if slot_idx < 0 or slot_idx >= len(labels):
        await _ui_reply(callback, "ردیف نامعتبر.")
        return
    try:
        await run_db(db.set_shift_override, shift_index, today_str(), slot_idx)
    except Exception as e:
        logger.exception("set_shift_override")
        await _ui_reply(callback, f"خطا در ذخیره: {e}")
        return
    letters = shift.shift_letters(cfg["shift_count"])
    letter = letters[shift_index] if shift_index < len(letters) else str(shift_index)
    slot_name = labels[slot_idx]["name"]
    from bale import InlineKeyboardMarkup, InlineKeyboardButton
    kb_i = InlineKeyboardMarkup()
    kb_i.add(InlineKeyboardButton(text="↩️ بازگشت به پنل شیفت", callback_data=f"shmgmt:{shift_index}"), row=1)
    kb_i.add(InlineKeyboardButton(text="⚙️ لیست شیفت‌ها", callback_data="settings_shifts"), row=2)
    await _ui_reply(
        callback,
        f"✅ شیفت {letter}: امروز روی «{slot_name}» تنظیم شد.",
        kb_i,
    )



async def cb_dayinfo(callback: CallbackQuery, data: str):
    """نمایش مرخصی‌های روز در منطقه + اکشن‌های ارشد + ثبت سریع برای خود."""
    date_str = data.split(":", 1)[1]
    viewer = await run_db(db.get_user, callback.user.id)
    if not viewer:
        return

    region_id = None
    if viewer.get("is_admin"):
        region_id = None  # همه
    elif viewer.get("is_shift_lead"):
        region_id = None  # فیلتر بعدی
    else:
        region_id = viewer.get("region_id")

    rows = await run_db(db.leaves_on_date_for_viewer, date_str, region_id)

    if viewer.get("is_shift_lead") and not viewer.get("is_admin"):
        allowed = set(await run_db(db.list_shift_lead_region_ids, viewer["user_id"]) or [])
        filtered = []
        for r in rows:
            u = await run_db(db.get_user, r["user_id"])
            if u and u.get("region_id") in allowed:
                filtered.append(r)
        rows = filtered
    elif not viewer.get("is_admin") and region_id is not None:
        # همه اعضا فقط منطقه خودشان (قبلاً در کوئری فیلتر شده)
        pass

    date_fa = jalali.format_jalali(date_str)
    if not rows:
        text = f"در {date_fa} مرخصی فعالی در محدودهٔ دید شما نیست."
    else:
        lines = [
            f"• {display_name(r)} — {r.get('group_name') or '-'} — {STATUS_FA.get(r['status'], r['status'])}"
            for r in rows
        ]
        text = f"📋 مرخصی‌های {date_fa}:\n" + "\n".join(lines)

    from bale import InlineKeyboardMarkup, InlineKeyboardButton
    kb_i = InlineKeyboardMarkup()
    row_i = 1

    # ارشد: لغو/رد مرخصی تکنسین و اپراتور همان منطقه
    is_snr = bool(viewer.get("is_senior") or viewer.get("role") == "snr")
    if is_snr and viewer.get("region_id"):
        for r in rows:
            u = await run_db(db.get_user, r["user_id"])
            if not u:
                continue
            if u.get("region_id") != viewer.get("region_id"):
                continue
            if u.get("is_shift_lead") or u.get("is_admin") or u.get("is_senior") or u.get("role") == "snr":
                continue  # فقط تکنسین و اپراتور
            if r.get("status") not in ("pending", "reviewing", "approved"):
                continue
            name = display_name(r) or str(r["user_id"])
            kb_i.add(
                InlineKeyboardButton(
                    text=f"❌ لغو «{name[:16]}»",
                    callback_data=f"snr_cancel:{r['id']}",
                ),
                row=row_i,
            )
            row_i += 1
            if r.get("status") in ("pending", "reviewing"):
                kb_i.add(
                    InlineKeyboardButton(
                        text=f"✅ تایید «{name[:16]}»",
                        callback_data=f"decide:{r['id']}:approved",
                    ),
                    row=row_i,
                )
                row_i += 1

    # ثبت مرخصی خود در همین روز (اگر هنوز ندارد)
    own = next((r for r in rows if r.get("user_id") == viewer["user_id"]), None)
    is_snr = bool(viewer.get("is_senior") or viewer.get("role") == "snr")
    can_self = (
        viewer.get("approved")
        and not viewer.get("is_admin")
        and date_str >= today_str()
        and (viewer.get("group_id") or is_snr)
    )
    if can_self and not own:
        if is_snr and viewer.get("shift_index") is not None:
            cap = await run_db(db.senior_leave_capacity_on_date, int(viewer["shift_index"]), date_str)
            label = "ظرفیت ارشدهای هم‌شیفت"
        elif viewer.get("group_id"):
            cap = await run_db(db.group_capacity_on_date, viewer["group_id"], date_str)
            label = "ظرفیت گروه شما"
        else:
            cap = {"max": 0, "used": 0, "remaining": 0}
            label = "ظرفیت"
        rem = cap.get("remaining", 0)
        mx = cap.get("max", 0)
        if mx == 0 and is_snr:
            # نامحدود
            kb_i.add(
                InlineKeyboardButton(
                    text="📝 ثبت مرخصی من",
                    callback_data=f"quick_leave:{date_str}",
                ),
                row=row_i,
            )
            row_i += 1
            text += f"\n\n{label}: بدون سقف"
        elif rem > 0:
            kb_i.add(
                InlineKeyboardButton(
                    text=f"📝 ثبت مرخصی من (باقی: {rem})",
                    callback_data=f"quick_leave:{date_str}",
                ),
                row=row_i,
            )
            row_i += 1
            text += f"\n\n{label}: {cap['used']}/{mx}"
        else:
            text += f"\n\n{label} پر است ({cap['used']}/{mx})."
    elif own:
        text += f"\n\nوضعیت مرخصی شما: {STATUS_FA.get(own.get('status'), own.get('status'))}"

    if row_i == 1:
        await callback.message.reply(text)
    else:
        await callback.message.reply(text, components=kb_i)


async def cb_snr_cancel_leave(callback: CallbackQuery, data: str):
    """ارشد: لغو مرخصی تکنسین/اپراتور همان منطقه."""
    leave_id = int(data.split(":")[1])
    viewer = await run_db(db.get_user, callback.user.id)
    leave = await run_db(db.get_leave, leave_id)
    if not viewer or not leave:
        await _ui_reply(callback, "درخواست پیدا نشد.")
        return
    if not (viewer.get("is_senior") or viewer.get("role") == "snr"):
        await _ui_reply(callback, "فقط تکنسین ارشد مجاز است.")
        return
    requester = await run_db(db.get_user, leave["user_id"])
    if not requester or requester.get("region_id") != viewer.get("region_id"):
        await _ui_reply(callback, "این مرخصی در منطقه شما نیست.")
        return
    if requester.get("is_senior") or requester.get("is_shift_lead") or requester.get("is_admin"):
        await _ui_reply(callback, "لغو مرخصی این نقش در اختیار شما نیست.")
        return
    ok = await run_db(db.cancel_leave, leave_id)
    if not ok:
        await _ui_reply(callback, "لغو انجام نشد.")
        return
    try:
        await client.send_message(
            leave["user_id"],
            f"❌ مرخصی {jalali.format_jalali(leave['leave_date'])} شما توسط تکنسین ارشد لغو شد.",
        )
    except Exception:
        logger.exception("notify cancel")
    await _ui_reply(
        callback,
        f"✅ مرخصی {display_name(requester)} در {jalali.format_jalali(leave['leave_date'])} لغو شد.",
    )


async def cb_quick_leave(callback: CallbackQuery, data: str):
    """ثبت سریع مرخصی برای خود در یک روز از dayinfo."""
    date_str = data.split(":", 1)[1]
    uid = callback.user.id
    viewer = await run_db(db.get_user, uid)
    if not viewer:
        return
    if date_str < today_str():
        await _ui_reply(callback, "برای روز گذشته نمی‌توان ثبت کرد.")
        return
    is_snr = bool(viewer.get("is_senior") or viewer.get("role") == "snr")
    if is_snr and viewer.get("shift_index") is not None:
        cap = await run_db(db.senior_leave_capacity_on_date, int(viewer["shift_index"]), date_str)
        if cap.get("max", 0) > 0 and cap.get("remaining", 0) <= 0:
            names = "، ".join(
                f"{o.get('first_name') or ''} {o.get('last_name') or ''}".strip() or str(o["user_id"])
                for o in (cap.get("owners") or [])
            )
            await _ui_reply(
                callback,
                f"ظرفیت مرخصی ارشدهای این شیفت پر است ({cap['used']}/{cap['max']}).\nدارندگان: {names or '-'}",
            )
            return
    else:
        if not viewer.get("group_id"):
            await _ui_reply(callback, "گروه کاری شما مشخص نیست.")
            return
        cap = await run_db(db.group_capacity_on_date, viewer["group_id"], date_str)
        if cap.get("remaining", 0) <= 0:
            await _ui_reply(callback, f"ظرفیت گروه پر است ({cap['used']}/{cap['max']}).")
            return
    status, extra = await run_db(db.request_leave, uid, date_str, None, None)
    if status == "full":
        names = "، ".join(display_name(o) for o in (extra or [])) if extra else "-"
        await _ui_reply(
            callback,
            f"❌ ظرفیت تکمیل است.\nدارنده(های) مرخصی: {names}\n"
            "درخواستی ثبت نشد و برای مسئول ارسال نشد.",
        )
        return
    if status != "created":
        await _ui_reply(callback, "قبلاً برای این روز ثبت شده است.")
        return
    await notify_admin_new_leave_request(viewer, date_str, extra)
    await _ui_reply(
        callback,
        f"📝 درخواست مرخصی {jalali.format_jalali(date_str)} ثبت شد "
        f"(ظرفیت گروه پس از تایید: {cap['used']}/{cap['max']}).",
    )



async def cb_batch_decide(callback: CallbackQuery, data: str):
    """تغییر وضعیت یک روز از پیام چندروزه: bdecide:{leave_id}:{status}"""
    _, leave_id, new_status = data.split(":")
    leave_id = int(leave_id)
    status, extra = await run_db(db.try_set_status, leave_id, new_status, callback.user.id)
    if status == "full":
        names = "، ".join(display_name(o) for o in extra)
        await callback.message.reply(f"ظرفیت گروه پر است. دارندگان مرخصی: {names}")
        return
    if status == "not_found":
        await callback.message.reply("درخواست پیدا نشد.")
        return
    await callback.message.reply(
        f"وضعیت روز به «{STATUS_FA.get(new_status, new_status)}» تغییر کرد. "
        f"در پایان «ثبت نهایی» را بزنید."
    )


async def cb_batch_commit(callback: CallbackQuery):
    """پس از تصمیم‌های چندروزه، خلاصه را برای کاربر ارسال می‌کند."""
    await callback.message.reply(
        "✅ تصمیم‌ها ثبت شد. وضعیت هر روز برای درخواست‌دهنده ارسال می‌شود "
        "(در نسخه فعلی هر تصمیم بلافاصله ذخیره شده است)."
    )


# appoint shift lead via text state
# (handled in handle_stateful_text — ensure action exists)


if __name__ == "__main__":
    client.run()


# ========================================================= مدیریت شیفت‌ها (تنظیمات)

