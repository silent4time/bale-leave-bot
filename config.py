# -*- coding: utf-8 -*-
import os

# توکن ربات که از بات‌فادر بله دریافت می‌کنید
BOT_TOKEN = os.getenv("BALE_BOT_TOKEN", "")

# نکته: دیگر نیازی به تعیین دستی ADMIN_IDS نیست — طبق درخواست شما، هر کس اولین
# نفری باشد که فرم ثبت‌نام را کامل کند، به‌صورت خودکار مدیر ربات می‌شود
# (به‌صورت اتمیک در database.try_claim_admin تضمین شده که فقط یک نفر این‌کار را انجام دهد).

# مسیر فایل دیتابیس SQLite
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "leave_bot.db"))

ROLE_LABELS = {
    "op": "اپراتور",
    "tech": "تکنسین",
    "snr": "ارشد",
    "lead": "مسئول شیفت",
}
ROLE_CODES = {v: k for k, v in ROLE_LABELS.items()}
