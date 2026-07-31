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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("leave_bot")

if not config.BOT_TOKEN:
    raise SystemExit("متغیر محیطی BALE_BOT_TOKEN تنظیم نشده است.")

client = Bot(token=config.BOT_TOKEN)
_bot_username = {"value": None}


async def run_db(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


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
        await handle_start(message, author, text)
        return

    state = states.get_state(author.id)
    if state and state.get("action") == "awaiting_contact" and text == kb.CONTACT_CANCEL_TEXT:
        states.clear_state(author.id)
        db_user = await run_db(db.get_user, author.id)
        await message.reply("لغو شد.", components=kb.menu_for_user(db_user))
        return
    if state:
        consumed = await handle_stateful_text(message, author, text, state)
        if consumed:
            return

    db_user = await run_db(db.get_user, author.id)
    if not db_user or not db_user["profile_complete"]:
        await message.reply("لطفاً ابتدا با دستور /start ثبت‌نام را تکمیل کنید.")
        return

    if text == kb.BTN_ADD_CONTACT:
        assignable = await run_db(db.assignable_group_ids, author.id)
        if not (db_user.get("is_admin") or assignable):
            await message.reply("شما اجازه‌ی افزودن عضو ندارید.")
            return
        states.set_state(author.id, action="awaiting_contact")
        await message.reply(
            "برای افزودن عضو جدید، مخاطبِ او را از دفترچه‌تلفن گوشی‌تان انتخاب و ارسال کنید.\n"
            "اگر آن شخص از قبل با همین شماره در بله ثبت‌نام کرده و حریم خصوصی‌اش اجازه بدهد، "
            "می‌توانید همین‌جا نقش/منطقه/گروهش را تعیین کنید.\n"
            "در غیر این‌صورت، یک لینک دعوت می‌سازم که خودتان برایش ارسال کنید.",
            components=kb.contact_request_menu(),
        )
        return

    if db_user.get("is_admin"):
        await handle_admin_menu(message, author, text)
        return

    if not db_user.get("approved"):
        await message.reply("سلام! هنوز در انتظار تایید مدیر (تعیین نقش و گروه) هستید.")
        return

    if db_user.get("is_shift_lead"):
        await handle_shift_lead_menu(message, author, db_user, text)
        return

    if db_user.get("is_senior"):
        await handle_senior_menu(message, author, db_user, text)
        return

    await handle_user_menu(message, author, db_user, text)


# ==========================================================================
#  ورود/ثبت‌نام (/start)
# ==========================================================================

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
    menu = kb.menu_for_user(db_user)
    if db_user.get("is_admin"):
        await message.reply(f"سلام {name}! به پنل مدیریت خوش برگشتید.", components=menu)
    elif not db_user.get("approved"):
        await message.reply("سلام! هنوز در انتظار تایید مدیر (تعیین نقش و گروه) هستید.")
    elif db_user.get("is_shift_lead"):
        await message.reply(f"سلام {name}! به پنل مسئول شیفت خوش برگشتید.", components=menu)
    elif db_user.get("is_senior"):
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
        if invite and invite["role"] and invite["group_id"]:
            await run_db(db.increment_invite_use, token)
            await run_db(db.approve_user, uid, invite["role"], invite["group_id"], invite.get("shift_index"))
            group = await run_db(db.get_group, invite["group_id"])
            region = await run_db(db.get_region, group["region_id"]) if group.get("region_id") else None
            role_label = config.ROLE_LABELS.get(invite["role"], invite["role"])
            shift_txt = ""
            if invite.get("shift_index") is not None:
                cfg = await run_db(db.get_shift_config)
                if cfg:
                    letters = shift.shift_letters(cfg["shift_count"])
                    if invite["shift_index"] < len(letters):
                        shift_txt = f"\nشیفت: {letters[invite['shift_index']]}"
            await message.reply(
                f"✅ ثبت‌نام شما تکمیل و عضویتتان تایید شد.\nنقش: {role_label}{shift_txt}\n"
                f"منطقه: {region['name'] if region else '-'}\nگروه: {group['name']}",
                components=kb.user_menu(),
            )
            return
        if invite:
            await run_db(db.increment_invite_use, token)

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
    """
    پردازش مخاطبی که مدیر/مسئول شیفت/ارشد از دفترچه‌تلفن گوشی‌اش فرستاده.
    - اگر آن شماره متعلق به یک کاربر شناخته‌شده‌ی بله باشد (contact.user موجود باشد)،
      مستقیم وارد جریان تعیین نقش/گروه می‌شویم (دقیقاً مثل تایید یک کاربر در-انتظار).
    - در غیر این‌صورت (شماره ناشناس یا حریم خصوصی مانع شناسایی شده)، یک لینک دعوت
      پیشنهاد می‌شود تا خودشان برای آن شخص بفرستند.
    """
    states.clear_state(author.id)
    db_user = await run_db(db.get_user, author.id)
    if not db_user or not (db_user.get("is_admin") or await run_db(db.assignable_group_ids, author.id)):
        await message.reply("شما اجازه‌ی افزودن عضو ندارید.")
        return

    target_user = getattr(contact, "user", None)
    shared_first = getattr(contact, "first_name", None) or ""
    shared_last = getattr(contact, "last_name", None) or ""
    name = f"{shared_first} {shared_last}".strip()

    if target_user is not None:
        target_uid = target_user.id
        await run_db(
            db.touch_user_bale_info, target_uid,
            getattr(target_user, "first_name", None) or shared_first,
            getattr(target_user, "username", None),
        )
        existing = await run_db(db.get_user, target_uid)

        if existing and existing.get("is_admin"):
            await message.reply("این شخص از قبل مدیر ربات است.", components=kb.menu_for_user(db_user))
            return
        if existing and existing.get("approved"):
            await message.reply(
                f"«{name or display_name(existing)}» از قبل عضو تایید‌شده‌ی ربات است.",
                components=kb.menu_for_user(db_user),
            )
            return

        await message.reply("✅ دریافت شد؛ این شخص کاربر بله است.", components=kb.menu_for_user(db_user))
        await message.reply(
            f"نقش «{name or target_uid}» را انتخاب کنید:",
            components=kb.role_select_keyboard(f"setrole:{target_uid}"),
        )
        return

    # شماره به کاربر بله شناخته‌شده‌ای وصل نشد (یا حریم خصوصی مانع شد) → لینک دعوت جایگزین
    await message.reply(
        "✅ دریافت شد؛ این شماره را نتوانستیم به یک حساب بله متصل کنیم "
        "(یا حریم خصوصی‌اش اجازه نمی‌دهد).", components=kb.menu_for_user(db_user),
    )
    phone = getattr(contact, "phone_number", None) or "؟"
    await message.reply(
        f"به‌جایش برای «{name or phone}» یک لینک دعوت می‌سازیم — نقش لینک را انتخاب کنید:",
        components=kb.role_select_keyboard("invrole"),
    )


# ==========================================================================
#  ورودی‌های متنی چندمرحله‌ای (فرم ثبت‌نام / ساخت گروه / تنظیمات شیفت)
# ==========================================================================

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
        await message.reply("✅ ظرفیت گروه به‌روزرسانی شد.", components=kb.admin_menu())
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
            "فرمت: نام کامل|علامت کوتاه   (مثال: صبح اول|ص1)\n\nردیف ۱ را ارسال کنید:"
        )
        return True

    if action == "cfg_slot_name":
        if "|" not in text:
            await message.reply("فرمت درست نیست. به این شکل ارسال کنید: نام کامل|علامت کوتاه   (مثال: صبح اول|ص1)")
            return True
        name, short = (p.strip() for p in text.split("|", 1))
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
                components=kb.user_menu(),
            )
        except ValueError as e:
            await message.reply(f"{fa_error(e)}\nدوباره شناسه را وارد کنید یا از منو خارج شوید.")
        return True

    if action == "transfer_shift_lead_uid":
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
                components=kb.user_menu(),
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
            components=kb.admin_menu(),
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
        await message.reply(f"✅ سقف ارشد این منطقه: {cur} نفر", components=kb.admin_menu())
        return True

    if action == "appoint_sl_uid":
        t = text.strip()
        if not t.isdigit():
            await message.reply("شناسه عددی وارد کنید:")
            return True
        target = int(t)
        regions = await run_db(db.list_regions)
        if not regions:
            await message.reply("ابتدا منطقه بسازید.")
            states.clear_state(author.id)
            return True
        states.set_state(
            author.id,
            action="sl_pick_regions",
            target_uid=target,
            selected=[],
            appointing=True,
        )
        await message.reply(
            "مناطق تحت مدیریت این مسئول شیفت را انتخاب کنید:",
            components=kb.multi_region_toggle_keyboard(regions, set(), "sl_tog"),
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
        note = text.strip() or None
        leave_id = state["leave_id"]
        new_status = state["new_status"]
        states.clear_state(author.id)
        result = await apply_leave_decision(author.id, leave_id, new_status, note)
        await message.reply(result)
        return True

    states.clear_state(author.id)
    return False


# ==========================================================================
#  منوی مدیر
# ==========================================================================

async def handle_admin_menu(message: Message, author, text: str):
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
        await message.reply(
            "نقش افرادی که با این لینک عضو می‌شوند را انتخاب کنید:",
            components=kb.role_select_keyboard("invrole"),
        )
        return

    if text == kb.ADMIN_BTN_CALENDAR:
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
        rows = await run_db(db.list_all_future_leaves, today_str())
        if not rows:
            await message.reply("مرخصی فعالی (از امروز به بعد) ثبت نشده است.")
            return
        lines = [
            f"• {jalali.format_jalali(r['leave_date'])} — {display_name(r)} "
            f"({r.get('region_name') or '-'} / {r.get('group_name') or '-'}) "
            f"— {STATUS_FA.get(r['status'], r['status'])} — {jalali.format_datetime_display(r['requested_at'])}"
            for r in rows
        ]
        await message.reply("گزارش مرخصی‌های فعال:\n" + "\n".join(lines))
        return

    if text == kb.ADMIN_BTN_SETTINGS:
        mode = await run_db(db.get_calendar_mode)
        cfg = await run_db(db.get_shift_config) if mode == "shift" else None
        await message.reply(
            "تنظیمات ربات (مناطق، گروه‌ها، مسئولان شیفت، جایگزینی مدیر و پیکربندی‌های دیگر همه اینجاست):",
            components=kb.settings_keyboard(mode, is_admin=True, shift_count=cfg["shift_count"] if cfg else None),
        )
        return

    await message.reply("لطفاً از دکمه‌های منو استفاده کنید.", components=kb.admin_menu())


# ==========================================================================
#  منوی مسئول شیفت
# ==========================================================================

async def handle_shift_lead_menu(message: Message, author, db_user: dict, text: str):
    region_ids = await run_db(db.list_shift_lead_region_ids, author.id)

    if text == kb.LEAD_BTN_QUEUE:
        any_row = False
        for rid in region_ids:
            # صف ارشدهای همان مناطق برای مسئول شیفت (مثل مدیر روی منطقه)
            rows = await run_db(db.list_pending_for_admin)
            rows = [r for r in rows if (await run_db(db.get_user, r["user_id"]) or {}).get("region_id") in region_ids]
            for r in rows:
                any_row = True
                requester = await run_db(db.get_user, r["user_id"])
                txt = format_admin_leave_text(requester, r, r.get("group_name"))
                await message.reply(txt, components=kb.leave_decision_keyboard(r["id"], r["status"]))
        if not any_row:
            await message.reply("صف مرخصی ارشدهای مناطق شما خالی است.")
        return

    if text == kb.LEAD_BTN_GROUPS:
        lines = []
        all_groups = []
        for rid in region_ids:
            region = await run_db(db.get_region, rid)
            groups = await run_db(db.list_groups, rid)
            all_groups.extend(groups)
            rname = region["name"] if region else str(rid)
            for g in groups:
                lines.append(f"• [{rname}] {g['name']} — ظرفیت {g['max_concurrent']}")
        if not lines:
            await message.reply("در مناطق شما گروهی نیست.")
            return
        await message.reply("گروه‌های مناطق شما:\n" + "\n".join(lines),
                            components=kb.groups_edit_keyboard(all_groups) if all_groups else None)
        return

    if text == kb.LEAD_BTN_MEMBERS:
        lines = []
        users_acc = []
        for rid in region_ids:
            users = await run_db(db.list_all_active_users, rid)
            users_acc.extend(users)
            region = await run_db(db.get_region, rid)
            rname = region["name"] if region else str(rid)
            for u in users:
                lines.append(
                    f"• [{rname}] {display_name(u)} — {u.get('group_name') or '-'} "
                    f"{'(ارشد)' if u.get('is_senior') else ''}"
                )
        if not lines:
            await message.reply("عضوی در مناطق شما نیست.")
            return
        mode = await run_db(db.get_calendar_mode)
        await message.reply(
            "اعضای مناطق شما:\n" + "\n".join(lines),
            components=kb.all_users_keyboard(users_acc, mode == "shift"),
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

    if text == kb.LEAD_BTN_CALENDAR:
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

    if text == kb.LEAD_BTN_REPORT:
        rows = await run_db(db.list_all_future_leaves, today_str())
        rows = [r for r in rows if r.get("region_id") in region_ids or (
            (await run_db(db.get_user, r["user_id"]) or {}).get("region_id") in region_ids
        )]
        if not rows:
            await message.reply("مرخصی فعالی در مناطق شما نیست.")
            return
        lines = [
            f"• {jalali.format_jalali(r['leave_date'])} — {display_name(r)} "
            f"({r.get('group_name') or '-'}) — {STATUS_FA.get(r['status'], r['status'])}"
            for r in rows
        ]
        await message.reply("گزارش مناطق شما:\n" + "\n".join(lines))
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
        states.set_state(author.id, action="transfer_shift_lead_uid")
        await message.reply(
            "شناسه عددی (user_id) کاربر جایگزین را وارد کنید.\n"
            "پس از انتقال، شما دیگر مسئول شیفت نخواهید بود."
        )
        return

    if text == kb.LEAD_BTN_SETTINGS:
        await message.reply(
            "تنظیمات عملیاتی مناطق شما:",
            components=kb.lead_settings_keyboard(),
        )
        return

    await message.reply("لطفاً از دکمه‌های منو استفاده کنید.", components=kb.shift_lead_menu())


# ==========================================================================
#  منوی تکنسین ارشد
# ==========================================================================

async def handle_senior_menu(message: Message, author, db_user: dict, text: str):
    region_id = db_user.get("region_id")

    if text == kb.SNR_BTN_QUEUE:
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

    if text == kb.SNR_BTN_MEMBERS:
        if not region_id:
            await message.reply("منطقه کاری شما مشخص نیست.")
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
        await message.reply(
            "اعضای منطقه:\n" + "\n".join(lines),
            components=kb.all_users_keyboard(
                [u for u in users if not u.get("is_senior") or u["user_id"] == author.id],
                mode == "shift",
            ),
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

    if text == kb.SNR_BTN_GROUPS:
        if not region_id:
            await message.reply("منطقه کاری شما مشخص نیست.")
            return
        groups = await run_db(db.list_groups, region_id)
        if not groups:
            await message.reply("گروهی در منطقه نیست.")
            return
        lines = [f"• {g['name']} — ظرفیت {g['max_concurrent']}" for g in groups]
        await message.reply("گروه‌های منطقه:\n" + "\n".join(lines))
        return

    if text == kb.SNR_BTN_CALENDAR:
        await show_calendar(message, db_user)
        return

    if text == kb.SNR_BTN_STATUS:
        await show_status(message, db_user)
        return

    await message.reply("لطفاً از دکمه‌های منو استفاده کنید.", components=kb.senior_menu())


async def show_all_users(message: Message):
    users = await run_db(db.list_all_active_users)
    if not users:
        await message.reply("هنوز عضو فعالی ثبت نشده است.")
        return
    mode = await run_db(db.get_calendar_mode)
    cfg = await run_db(db.get_shift_config) if mode == "shift" else None
    letters = shift.shift_letters(cfg["shift_count"]) if cfg else []
    lines = []
    for u in users:
        role_label = config.ROLE_LABELS.get(u["role"], "-")
        shift_txt = ""
        if mode == "shift":
            if u["shift_index"] is not None and u["shift_index"] < len(letters):
                shift_txt = f" — شیفت: {letters[u['shift_index']]}"
            else:
                shift_txt = " — شیفت: تعیین‌نشده"
        lines.append(
            f"• {display_name(u)} ({u.get('personnel_number') or '-'}) — {role_label} "
            f"— گروه: {u['group_name'] or '-'}{shift_txt}"
        )
    keyboard = kb.all_users_keyboard(users, mode == "shift")
    text = "لیست اعضا:\n" + "\n".join(lines)
    if keyboard:
        await message.reply(text, components=keyboard)
    else:
        await message.reply(text)


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


async def _recipients_for_leave_request(requester: dict) -> list:
    """
    عضو عادی → تکنسین ارشدِ همان گروهِ خودش (نه کل منطقه)
    تکنسین ارشد → مسئولان شیفتی که آن منطقه را دارند
    (مدیر صف ندارد مگر مسئول شیفت باشد — از طریق لیست مسئول شیفت)
    """
    recipients = []
    if requester.get("is_senior"):
        # به مسئولان شیفت منطقه
        leads = await run_db(db.list_shift_leads)
        rid = requester.get("region_id")
        for lead in leads:
            if rid in [r["id"] for r in lead.get("regions") or []]:
                recipients.append(lead["user_id"])
        # اگر مسئولی نبود، به مدیرها اطلاع (فقط اطلاع، نه صف ثابت)
        if not recipients:
            recipients = await run_db(db.list_admin_ids)
    else:
        # به تکنسین ارشدِ همان گروه (هر گروه فقط یک ارشد دارد)
        gid = requester.get("group_id")
        rid = requester.get("region_id")
        if gid:
            users = await run_db(db.list_all_active_users, rid) if rid else []
            for u in users:
                if u.get("is_senior") and u.get("group_id") == gid and u["user_id"] != requester.get("user_id"):
                    recipients.append(u["user_id"])
        if not recipients:
            # fallback: مسئولان شیفت منطقه (اگر گروه هنوز ارشد ندارد)
            leads = await run_db(db.list_shift_leads)
            for lead in leads:
                if rid in [r["id"] for r in lead.get("regions") or []]:
                    recipients.append(lead["user_id"])
    return list(dict.fromkeys(recipients))


async def notify_admin_new_leave_request(requester: dict, date_str: str, leave_id: int):
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
    if text == kb.USER_BTN_CALENDAR:
        await show_calendar(message, db_user)
        return
    if text == kb.USER_BTN_STATUS:
        await show_status(message, db_user)
        return
    await message.reply("لطفاً از دکمه‌های منو استفاده کنید.", components=kb.user_menu())


async def show_status(message: Message, db_user: dict):
    role_label = config.ROLE_LABELS.get(db_user["role"], "هنوز تعیین نشده")
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
    """
    interactive=None → خودکار: عضو/ارشد تعاملی، مدیر/مسئول‌شیفت فقط نمایش مگر عضو هم باشند.
    """
    if interactive is None:
        interactive = not (db_user.get("is_admin") or db_user.get("is_shift_lead")) or bool(
            db_user.get("group_id")
        ) and not db_user.get("is_admin")
        # عضو و ارشد همیشه تعاملی؛ مدیر/مسئول شیفت در حالت مشاهده منطقه معمولاً غیرتعاملی
        if db_user.get("is_senior") and not db_user.get("is_admin"):
            interactive = True
        if not db_user.get("is_admin") and not db_user.get("is_shift_lead"):
            interactive = True
            if not db_user.get("group_id"):
                await message.reply("شما هنوز به هیچ گروهی اختصاص داده نشده‌اید.")
                return

    y, m, _ = jalali.today_jalali()
    await send_fresh_calendar(
        message, db_user, y, m, region_id=region_id, interactive=interactive
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


async def shift_labels_for_month(db_user: dict, year: int, month: int) -> dict:
    mode = await run_db(db.get_calendar_mode)
    if mode != "shift" or db_user.get("shift_index") is None:
        return {}
    cfg = await run_db(db.get_shift_config)
    if not cfg:
        return {}
    ndays = jalali.days_in_jalali_month(year, month)
    out = {}
    for day in range(1, ndays + 1):
        ds = jalali.parse_date_str(year, month, day)
        idx = _shift_slot_index(cfg, db_user["shift_index"], ds)
        out[ds] = cfg["labels"][idx]["short"]
    return out


async def build_calendar_view(
    db_user: dict, year: int, month: int, *, region_id: int = None, interactive: bool = True
):
    own_status = await own_status_for_month(db_user["user_id"], year, month)
    shift_labels = await shift_labels_for_month(db_user, year, month)
    sel = states.get_selection(db_user["user_id"], year, month) if interactive else {"to_submit": set(), "to_cancel": set()}
    others = await approved_others_for_month(db_user, year, month, region_id)
    keyboard = calendar_ui.build_calendar(
        year, month, today_str(), own_status, sel, shift_labels, others,
        interactive=interactive, show_actions=interactive,
    )
    title = "📅 تقویم مرخصی"
    if region_id:
        region = await run_db(db.get_region, region_id)
        if region:
            title += f" — {region['name']}"
    text = title + "\n" + calendar_ui.legend_text()
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
        elif data.startswith("decide:"):
            await cb_decide(callback, data)
        elif data.startswith("bdecide:"):
            await cb_batch_decide(callback, data)
        elif data == "batch_commit":
            await cb_batch_commit(callback)
        elif data == "batch_edit":
            await callback.message.reply("وضعیت‌ها ذخیره شده‌اند. می‌توانید دوباره آیکون‌ها را تغییر دهید.")
        elif data.startswith("invrole:"):
            await cb_invite_role(callback, data)
        elif data.startswith("invshift:"):
            await cb_invite_shift(callback, data)
        elif data.startswith("invregion:"):
            await cb_invite_region(callback, data)
        elif data.startswith("invgroup:"):
            await cb_invite_group(callback, data)
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
        elif data.startswith("shiftcfg:"):
            await cb_shift_own_settings(callback, data)
        elif data.startswith("shiftcfgslot:"):
            await cb_shift_own_settings_slot(callback, data)
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
        elif data == "settings_regions":
            regions = await run_db(db.list_regions)
            await callback.message.reply("مناطق:", components=kb.regions_manage_keyboard(regions))
        elif data == "settings_shiftleads":
            leads = await run_db(db.list_shift_leads)
            cap = await run_db(db.get_max_shift_leads)
            await callback.message.reply(
                f"مسئولان شیفت ({len(leads)} از سقف {cap}):",
                components=kb.shift_leads_manage_keyboard(leads),
            )
        elif data == "settings_replaceadmin":
            states.set_state(callback.user.id, action="replace_admin_uid")
            await callback.message.reply(
                "شناسه عددی (user_id) کاربر جانشین مدیر را وارد کنید.\n"
                "پس از تأیید، شما دیگر مدیر نخواهید بود و او مدیر می‌شود."
            )
        elif data == "settings_max_leads":
            states.set_state(callback.user.id, action="set_max_shift_leads")
            cur = await run_db(db.get_max_shift_leads)
            await callback.message.reply(f"سقف فعلی: {cur}\nعدد جدید را وارد کنید:")
        elif data == "settings_max_seniors":
            states.set_state(callback.user.id, action="set_max_seniors")
            cur = await run_db(db.get_max_seniors_per_region)
            await callback.message.reply(f"سقف فعلیِ تکنسین ارشد در هر منطقه: {cur}\nعدد جدید را وارد کنید:")
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
        elif data == "sl_appoint":
            states.set_state(callback.user.id, action="appoint_sl_uid")
            await callback.message.reply("user_id کاربر برای انتصاب مسئول شیفت را وارد کنید:")
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


# ------------------------------------------------------- تقویم کاربر -------

async def cb_nav(callback: CallbackQuery, data: str):
    _, y, m = data.split(":")
    y, m = int(y), int(m)
    db_user = await run_db(db.get_user, callback.user.id)
    if not db_user:
        return
    interactive = bool(db_user.get("group_id")) and not (
        db_user.get("is_admin") and not db_user.get("is_senior")
    )
    if db_user.get("is_senior") or (
        db_user.get("approved") and not db_user.get("is_admin") and not db_user.get("is_shift_lead")
    ):
        interactive = bool(db_user.get("group_id"))
    await render_calendar_edit(callback, db_user, y, m, interactive=interactive)


async def cb_pick(callback: CallbackQuery, data: str):
    _, y, m, d = data.split(":")
    y, m, d = int(y), int(m), int(d)
    date_str = jalali.parse_date_str(y, m, d)
    if date_str < today_str():
        return
    uid = callback.user.id
    db_user = await run_db(db.get_user, uid)
    if not db_user or not db_user.get("group_id"):
        return
    own_status = await own_status_for_month(uid, y, m)
    if date_str in own_status:
        states.toggle_cancel(uid, y, m, date_str)
    else:
        states.toggle_submit(uid, y, m, date_str)
    await render_calendar_edit(callback, db_user, y, m, interactive=True)


async def cb_confirm_submit(callback: CallbackQuery, data: str):
    """ابتدا توضیح اختیاری می‌پرسد، بعد ثبت واقعی انجام می‌شود."""
    _, y, m = data.split(":")
    y, m = int(y), int(m)
    uid = callback.user.id
    sel = states.get_selection(uid, y, m)
    to_submit = sorted(sel["to_submit"])
    if not to_submit:
        await callback.message.reply("روزی برای ثبت انتخاب نشده بود.")
        return
    states.set_state(
        uid,
        action="leave_note_input",
        year=y,
        month=m,
        dates=to_submit,
    )
    from bale import InlineKeyboardMarkup, InlineKeyboardButton
    skip_kb = InlineKeyboardMarkup()
    skip_kb.add(
        InlineKeyboardButton(text="بدون توضیح — ثبت کن", callback_data=f"leave_note_skip:{y}:{m}"),
        row=1,
    )
    dates_txt = "، ".join(jalali.format_jalali(d) for d in to_submit)
    await callback.message.reply(
        f"توضیح اختیاری برای مرخصی ({dates_txt}) را بنویسید،\n"
        f"یا «بدون توضیح» را بزنید:",
        components=skip_kb,
    )


async def finalize_leave_submit(uid: int, y: int, m: int, dates: list, note: str = None):
    db_user = await run_db(db.get_user, uid)
    if not db_user:
        return
    submitted = []
    batch_id = secrets.token_hex(8) if len(dates) > 1 else None
    for date_str in dates:
        status, lid = await run_db(db.request_leave, uid, date_str, note, batch_id)
        if status == "created":
            submitted.append((date_str, lid))
    sel = states.get_selection(uid, y, m)
    sel["to_submit"].clear()
    states.clear_state(uid)

    if submitted:
        dates_txt = "، ".join(jalali.format_jalali(d) for d, _ in submitted)
        # اطلاع به کاربر از طریق همان چت — caller باید message داشته باشد
        if len(submitted) > 1:
            await notify_leave_batch(db_user, submitted)
        else:
            for date_str, lid in submitted:
                await notify_admin_new_leave_request(db_user, date_str, lid)
        return f"📝 درخواست مرخصیِ روز(های) {dates_txt} ثبت شد و برای بررسی ارسال شد."
    return "روزی برای ثبت انتخاب نشده بود یا از قبل ثبت شده است."


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
    _, y, m = data.split(":")
    y, m = int(y), int(m)
    uid = callback.user.id
    db_user = await run_db(db.get_user, uid)
    if not db_user["group_id"]:
        await callback.message.reply("شما هنوز به هیچ گروهی اختصاص داده نشده‌اید.")
    else:
        rows = await run_db(db.list_group_leaves, db_user["group_id"], today_str())
        if not rows:
            await callback.message.reply("مرخصی فعالی در گروه شما ثبت نشده است.")
        else:
            lines = [
                f"• {jalali.format_jalali(r['leave_date'])} — {display_name(r)} "
                f"({STATUS_FA.get(r['status'], r['status'])})"
                for r in rows
            ]
            await callback.message.reply("مرخصی‌های فعال گروه شما:\n" + "\n".join(lines))
    await send_fresh_calendar(callback.message, db_user, y, m)


# ------------------------------------------------------- تصمیم مدیر -------

async def cb_decide(callback: CallbackQuery, data: str):
    """پس از انتخاب وضعیت، توضیح اختیاری از تصمیم‌گیرنده می‌پرسد."""
    _, leave_id, new_status = data.split(":")
    leave_id = int(leave_id)
    leave = await run_db(db.get_leave, leave_id)
    if leave is None:
        await callback.message.edit("این درخواست دیگر معتبر نیست.")
        return

    states.set_state(
        callback.user.id,
        action="decide_note_input",
        leave_id=leave_id,
        new_status=new_status,
    )
    from bale import InlineKeyboardMarkup, InlineKeyboardButton
    skip_kb = InlineKeyboardMarkup()
    skip_kb.add(
        InlineKeyboardButton(
            text="بدون توضیح — اعمال کن",
            callback_data=f"decide_note_skip:{leave_id}:{new_status}",
        ),
        row=1,
    )
    await callback.message.reply(
        f"توضیح اختیاری برای «{STATUS_FA.get(new_status, new_status)}» "
        f"روز {jalali.format_jalali(leave['leave_date'])} را بنویسید، یا بدون توضیح اعمال کنید:",
        components=skip_kb,
    )


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
            f"❌ ظرفیت گروه برای {jalali.format_jalali(leave['leave_date'])} "
            f"توسط {names} پر است."
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

async def cb_invite_role(callback: CallbackQuery, data: str):
    role_code = data.split(":", 1)[1]
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
        await callback.message.edit("هیچ منطقه‌ای در محدوده‌ی دسترسی شما نیست. ابتدا یک منطقه بسازید.")
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
    groups = await run_db(db.list_groups, region_id)
    if not groups:
        await callback.message.edit("این منطقه هنوز گروهی ندارد. ابتدا یک گروه بسازید.")
        return
    await callback.message.edit(
        "گروهِ کاریِ افرادی که با این لینک عضو می‌شوند را انتخاب کنید:",
        components=kb.group_select_keyboard(groups, f"invgroup:{role_code}:{shift_code}", allow_none=False),
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
    await callback.message.edit("نقش این فرد را انتخاب کنید:", components=kb.role_select_keyboard(f"setrole:{user_id}"))


async def cb_set_role(callback: CallbackQuery, data: str):
    """مرحله‌ی بعد از انتخاب نقش: اگر تقویم شیفتی است، شیفت را می‌پرسیم؛ وگرنه می‌رویم سراغ منطقه."""
    _, user_id, role_code = data.split(":")
    mode = await run_db(db.get_calendar_mode)
    cfg = await run_db(db.get_shift_config) if mode == "shift" else None
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
        await callback.message.edit("هیچ منطقه‌ای در محدوده‌ی دسترسی شما نیست. ابتدا یک منطقه بسازید.")
        return
    await callback.message.edit(
        "منطقه‌ی کاریِ این فرد را انتخاب کنید:",
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
    groups = await run_db(db.list_groups, region_id)
    if not groups:
        await callback.message.edit(
            "این منطقه هنوز گروهی ندارد. ابتدا از «📋 گروه‌های این منطقه» یک گروه بسازید."
        )
        return
    await callback.message.edit(
        "گروهِ کاریِ این فرد را انتخاب کنید:",
        components=kb.group_select_keyboard(
            groups, f"aprv_group:{user_id}:{role_code}:{shift_code}", allow_none=False
        ),
    )


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
    await run_db(db.approve_user, user_id, role, group_id, shift_index)
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
        await client.send_message(
            user_id,
            f"✅ شما توسط مدیر تایید شدید.\nنقش: {role_label}{shift_txt}\nمنطقه: {region_name}\nگروه: {group_name}",
            components=kb.user_menu(),
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
    states.clear_state(callback.user.id)
    db_user = await run_db(db.get_user, callback.user.id)
    await callback.message.reply("منوی اصلی:", components=kb.menu_for_user(db_user))


async def cb_nav_back_admin(callback: CallbackQuery):
    states.clear_state(callback.user.id)
    db_user = await run_db(db.get_user, callback.user.id)
    await callback.message.reply("بازگشت:", components=kb.menu_for_user(db_user))


async def cb_cfg_first_day(callback: CallbackQuery, data: str):
    """اولین روز کاری هر شیفت — سپس سقف مسئول و راهنمای کپی ساختار."""
    parts = data.split(":")
    # cfgfirst:{shift_index}:{slot_index}
    if len(parts) < 3:
        # only cfgfirst:{shift_index} from keyboard prefix misuse
        return
    shift_index = int(parts[1])
    slot_idx = int(parts[2])
    state = states.get_state(callback.user.id)
    if not state or state.get("action") != "cfg_first_day":
        return
    first_days = dict(state.get("first_days") or {})
    first_days[str(shift_index)] = slot_idx
    # ذخیرهٔ override برای این شیفت
    await run_db(db.set_shift_override, shift_index, today_str(), slot_idx)

    letters = shift.shift_letters(state["shift_count"])
    next_i = shift_index + 1
    while next_i < state["shift_count"] and str(next_i) in first_days:
        next_i += 1
    if next_i < state["shift_count"]:
        states.set_state(
            callback.user.id,
            action="cfg_first_day",
            shift_count=state["shift_count"],
            cycle_length=state["cycle_length"],
            labels=state["labels"],
            first_day_index=next_i,
            first_days=first_days,
            own_shift_index=state.get("own_shift_index"),
            own_ref_slot=state.get("own_ref_slot"),
        )
        await callback.message.edit(
            f"✅ شیفت {letters[shift_index]}: اولین روز کاری = «{state['labels'][slot_idx]['name']}»\n\n"
            f"شیفت {letters[next_i]} — ردیفِ اولین روز کاری را انتخاب کنید:",
            components=kb.slot_select_keyboard(state["labels"], f"cfgfirst:{next_i}"),
        )
        return

    # همهٔ شیفت‌ها انجام شد → تعداد مسئول
    states.set_state(
        callback.user.id,
        action="cfg_leads_per_shift",
        shift_count=state["shift_count"],
        first_days=first_days,
    )
    await callback.message.edit(
        f"✅ اولین روز کاری همهٔ شیفت‌ها ثبت شد.\n\n"
        f"حالا «تعداد مسئول شیفت» را وارد کنید "
        f"(سقف کل مسئولانی که می‌توانید منصوب کنید، مثلاً {state['shift_count']}):"
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
    letters = shift.shift_letters(state["shift_count"])
    # شیفت خود مدیر از قبل انتخاب شده
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
    """طبق درخواست: برای هر شیفت یک کلید تنظیمات جدا — اینجا امکان تصحیح نقطه‌ی مرجع
    همان شیفت را می‌دهد (مستقل از بقیه‌ی شیفت‌ها) بدون نیاز به پیکربندی کامل از نو."""
    shift_index = int(data.split(":", 1)[1])
    cfg = await run_db(db.get_shift_config)
    if not cfg:
        await callback.message.reply("ابتدا باید چرخه‌ی شیفت را پیکربندی کنید.")
        return
    letters = shift.shift_letters(cfg["shift_count"])
    letter = letters[shift_index] if shift_index < len(letters) else str(shift_index)
    cur_idx = _shift_slot_index(cfg, shift_index, today_str())
    await callback.message.edit(
        f"تنظیمات شیفت {letter}\n"
        f"امروز طبق محاسبه‌ی فعلی روی «{cfg['labels'][cur_idx]['name']}» است.\n"
        "اگر درست نیست، ردیف درستِ امروز را برای همین شیفت انتخاب کنید تا فقط همین شیفت "
        "تصحیح شود (بقیه‌ی شیفت‌ها دست‌نخورده می‌مانند):",
        components=kb.slot_select_keyboard(cfg["labels"], f"shiftcfgslot:{shift_index}"),
    )


async def cb_shift_own_settings_slot(callback: CallbackQuery, data: str):
    _, shift_index, slot_idx = data.split(":")
    shift_index, slot_idx = int(shift_index), int(slot_idx)
    cfg = await run_db(db.get_shift_config)
    if not cfg:
        return
    await run_db(db.set_shift_override, shift_index, today_str(), slot_idx)
    letters = shift.shift_letters(cfg["shift_count"])
    letter = letters[shift_index] if shift_index < len(letters) else str(shift_index)
    await callback.message.edit(
        f"✅ نقطه‌ی مرجعِ شیفت {letter} تصحیح شد: امروز روی «{cfg['labels'][slot_idx]['name']}» است.\n"
        "بقیه‌ی شیفت‌ها بدون تغییر ماندند."
    )


async def cb_dayinfo(callback: CallbackQuery, data: str):
    """نمایش نام افراد دارای مرخصی در یک روز."""
    date_str = data.split(":", 1)[1]
    viewer = await run_db(db.get_user, callback.user.id)
    if not viewer:
        return
    region_id = None
    if not viewer.get("is_admin"):
        if viewer.get("is_shift_lead"):
            # همه مناطق تحت مدیریت — بدون فیلتر سخت؛ یا region خود
            region_id = None
            # محدود به مناطق تحت مدیریت در کوئری بعدی
        else:
            region_id = viewer.get("region_id")
    rows = await run_db(db.leaves_on_date_for_viewer, date_str, region_id)
    if viewer.get("is_shift_lead") and not viewer.get("is_admin"):
        allowed = set(await run_db(db.list_shift_lead_region_ids, viewer["user_id"]))
        filtered = []
        for r in rows:
            u = await run_db(db.get_user, r["user_id"])
            if u and u.get("region_id") in allowed:
                filtered.append(r)
        rows = filtered
    if not rows:
        await callback.message.reply(f"در {jalali.format_jalali(date_str)} مرخصی فعالی نیست.")
        return
    lines = [
        f"• {display_name(r)} — {r.get('group_name') or '-'} — {STATUS_FA.get(r['status'], r['status'])}"
        for r in rows
    ]
    await callback.message.reply(
        f"📋 مرخصی‌های {jalali.format_jalali(date_str)}:\n" + "\n".join(lines)
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
