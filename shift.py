# -*- coding: utf-8 -*-
"""
محاسبه‌ی «اسلاتِ سیکل» برای هر شیفت در هر تاریخ، بر پایه‌ی یک روزِ مرجع.

فرض مدل (باید توسط مدیر واقعی تایید/تست شود، چون در متن کاربر دقیقاً مشخص
نشده جهت چرخش شیفت‌ها نسبت به هم به چه صورت است):

- شیفت‌ها با حروف بزرگ لاتین شماره‌گذاری می‌شوند: A=0, B=1, C=2, ...
- سیکل از `cycle_length` روز تشکیل شده که هرکدام یک برچسب دارند (مثلاً
  «صبح اول»..«رست دوم»).
- فاصله‌ی چرخشی بین دو شیفت متوالی = cycle_length // shift_count (باید
  cycle_length بر shift_count بخش‌پذیر باشد، مثلاً ۸ روز / ۴ شیفت = ۲ روز).
- مدیر یک نقطه‌ی مرجع می‌دهد: «در تاریخ X، شیفت من (فلان) روی اسلات فلان است».
  بقیه‌ی شیفت‌ها و بقیه‌ی روزها نسبت به همین یک نقطه محاسبه می‌شوند.
"""
import jalali


def jalali_days_between(date_a: str, date_b: str) -> int:
    """تعداد روز بین دو تاریخ شمسی رشته‌ای 'YYYY-MM-DD' (b - a)."""
    ay, am, ad = (int(x) for x in date_a.split("-"))
    by, bm, bd = (int(x) for x in date_b.split("-"))
    ga = jalali.jalali_to_gregorian(ay, am, ad)
    gb = jalali.jalali_to_gregorian(by, bm, bd)
    return (gb - ga).days


def slot_index_for(cycle_length: int, shift_count: int, ref_date: str, ref_shift_index: int,
                    ref_slot_index: int, target_date: str, target_shift_index: int) -> int:
    offset = cycle_length // shift_count
    day_delta = jalali_days_between(ref_date, target_date)
    shift_delta = target_shift_index - ref_shift_index
    idx = (ref_slot_index + shift_delta * offset + day_delta) % cycle_length
    return idx


def shift_letters(shift_count: int):
    return [chr(ord("A") + i) for i in range(shift_count)]
