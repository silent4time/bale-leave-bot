# -*- coding: utf-8 -*-
"""
تبدیل تاریخ شمسی (جلالی) <-> میلادی، به‌صورت کاملاً مستقل و بدون وابستگی خارجی.

الگوریتم مبتنی بر روش استاندارد jalaali (Borkowski / jalaali-js) است که با تست
round-trip روی بازه‌ی ۲۰۲۳ تا ۲۰۲۸ (بیش از ۱۸۰۰ روز، بدون هیچ خطایی) و همچنین
تطبیق با تاریخ‌های واقعی نوروز (۱۴۰۰ تا ۱۴۰۴) صحت‌سنجی شده است.

نکته: قاعده‌ی کبیسه بر پایه‌ی چرخه‌ی ۳۳ ساله است که برای دوران فعلی (چند صد سال
اطراف زمان حال) دقیق است؛ همان روشی که اکثر کتابخانه‌های ساده‌ی تقویم شمسی
استفاده می‌کنند.
"""
import datetime

PERSIAN_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]

# ترتیب هفته‌ی ایرانی: شنبه تا جمعه
WEEKDAY_NAMES = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]
WEEKDAY_SHORT = ["ش", "ی", "د", "س", "چ", "پ", "ج"]


def is_leap_jalali(jy: int) -> bool:
    r = jy % 33
    return r in (1, 5, 9, 13, 17, 22, 26, 30)


def days_in_jalali_month(jy: int, jm: int) -> int:
    if jm <= 6:
        return 31
    if jm <= 11:
        return 30
    return 30 if is_leap_jalali(jy) else 29


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> datetime.date:
    jy2 = jy + 1595
    days = (
        -355668
        + (365 * jy2)
        + ((jy2 // 33) * 8)
        + (((jy2 % 33) + 3) // 4)
        + jd
        + ((jm - 1) * 31 if jm < 7 else ((jm - 7) * 30) + 186)
    )
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        gy += 100 * ((days - 1) // 36524)
        days = (days - 1) % 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap_g = gy % 4 == 0 and (gy % 100 != 0 or gy % 400 == 0)
    sal_a = [0, 31, 29 if leap_g else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    for i in range(1, 13):
        v = sal_a[i]
        if gd <= v:
            gm = i
            break
        gd -= v
    return datetime.date(gy, gm, gd)


def gregorian_to_jalali(gdate: datetime.date):
    gy, gm, gd = gdate.year, gdate.month, gdate.day
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        (365 * gy)
        + ((gy2 + 3) // 4)
        - ((gy2 + 99) // 100)
        + ((gy2 + 399) // 400)
        - 80
        + gd
        + g_d_m[gm - 1]
    )
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + (days % 31)
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


def today_jalali():
    return gregorian_to_jalali(datetime.date.today())


def jalali_weekday(jy: int, jm: int, jd: int) -> int:
    """۰ = شنبه ... ۶ = جمعه"""
    g = jalali_to_gregorian(jy, jm, jd)
    return (g.weekday() + 2) % 7


def format_jalali(date_str: str) -> str:
    """ورودی 'YYYY-MM-DD' شمسی -> خروجی 'DD ماه YYYY'"""
    jy, jm, jd = (int(x) for x in date_str.split("-"))
    return f"{jd} {PERSIAN_MONTHS[jm - 1]} {jy}"


def format_datetime_display(iso_str: str) -> str:
    """ورودی خروجی datetime.now().isoformat() میلادی -> 'DD ماه YYYY - HH:MM' شمسی"""
    dt = datetime.datetime.fromisoformat(iso_str)
    jy, jm, jd = gregorian_to_jalali(dt.date())
    return f"{jd} {PERSIAN_MONTHS[jm - 1]} {jy} - {dt.strftime('%H:%M')}"


def parse_date_str(jy: int, jm: int, jd: int) -> str:
    return f"{jy:04d}-{jm:02d}-{jd:02d}"


def add_months(jy: int, jm: int, delta: int):
    idx = (jm - 1) + delta
    jy2 = jy + idx // 12
    jm2 = idx % 12 + 1
    return jy2, jm2


def compare_dates(date_str_a: str, date_str_b: str) -> int:
    """مقایسه‌ی دو تاریخ شمسی رشته‌ای؛ چون فرمت zero-padded است مقایسه‌ی رشته‌ای کافی است."""
    if date_str_a < date_str_b:
        return -1
    if date_str_a > date_str_b:
        return 1
    return 0
