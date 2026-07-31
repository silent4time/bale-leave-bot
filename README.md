# ربات مدیریت مرخصی بله

تقویم هجری‌شمسی، گردش‌کار تایید سه‌مرحله‌ای، و تقویم شیفتی قابل‌تنظیم — همه در یک ربات بله.

## ویژگی‌ها

- **ادمین پویا**: اولین نفری که فرم ثبت‌نام را کامل کند، خودکار مدیر می‌شود (اتمیک).
- **فرم ثبت‌نام اجباری** برای همه (نام، نام خانوادگی، شماره پرسنلی).
- **گردش‌کار تایید سه‌حالته**: ثبت موقت ← تایید / بررسی / عدم تایید. ظرفیت گروه فقط در لحظهٔ تایید کنترل می‌شود.
- **تقویم چندانتخابی**: تیک زدن چند روز و ثبت/لغو یکجا.
- **تقویم شیفتی اختیاری**: تعداد شیفت، طول سیکل، نام و علامت هر ردیف، نقطهٔ مرجع.
- **گزارش ماهانه** خودکار برای مدیر و ارشد.
- **مناطق / گروه‌ها / مسئول شیفت / ارشد** با سلسله‌مراتب مشخص.

## نصب سریع (با اسکریپت)

### ۱. ساخت ریپو روی گیت‌هاب

1. یک Repository جدید بسازید (مثلاً `bale-leave-bot`).
2. همهٔ فایل‌های این پوشه را داخل آن قرار دهید و Push کنید.
3. در فایل `manage.sh` مقدار `REPO_URL` را به آدرس واقعی ریپوی خودتان تغییر دهید:

```bash
REPO_URL="${REPO_URL:-https://github.com/silent4time/bale-leave-bot.git}"
```

### ۲. نصب روی سرور

```bash
# روش پیشنهادی: کلون و اجرای منو
git clone https://github.com/silent4time/bale-leave-bot.git
cd bale-leave-bot
bash manage.sh
```

یا با یک دستور (بعد از تنظیم REPO_URL):

```bash
curl -fsSL https://raw.githubusercontent.com/silent4time/bale-leave-bot/main/manage.sh | bash
```

بعد از اجرا، منوی زیر ظاهر می‌شود:

```
  1) Install
  2) Update
  3) Uninstall
  4) Edit settings (token / DB path)
  0) Exit
```

| گزینه | کار |
|--------|-----|
| **۱ Install** | کلون (در صورت نیاز)، ساخت venv، نصب وابستگی‌ها، دریافت توکن، ساخت سرویس systemd (اختیاری) |
| **۲ Update** | `git pull` + به‌روزرسانی وابستگی‌ها + ری‌استارت سرویس |
| **۳ Uninstall** | توقف و حذف سرویس، بکاپ دیتابیس (اختیاری)، حذف پوشه |
| **۴ Edit settings** | تغییر توکن یا مسیر دیتابیس |

### ۳. تست

در بله ربات را باز کنید و `/start` بزنید. اولین نفری که ثبت‌نام را کامل کند مدیر می‌شود.

---

## نصب دستی (بدون اسکریپت)

```bash
git clone https://github.com/silent4time/bale-leave-bot.git
cd bale-leave-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# فایل .env را ویرایش و توکن را بگذارید
nano .env
python3 bot.py
```

### سرویس ۲۴ساعته (systemd)

```bash
sudo nano /etc/systemd/system/bale-leave-bot.service
```

محتوای نمونه:

```ini
[Unit]
Description=Bale Leave Management Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/USER/bale-leave-bot
EnvironmentFile=/home/USER/bale-leave-bot/.env
ExecStart=/home/USER/bale-leave-bot/venv/bin/python3 /home/USER/bale-leave-bot/bot.py
Restart=always
RestartSec=5
User=USER

[Install]
WantedBy=multi-user.target
```

سپس:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bale-leave-bot
sudo journalctl -u bale-leave-bot -f
```

---

## آپدیت

از داخل پوشهٔ نصب:

```bash
bash manage.sh
# گزینه ۲ را انتخاب کنید
```

یا دستی:

```bash
cd ~/bale-leave-bot
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart bale-leave-bot
```

---

## حذف کامل

```bash
bash manage.sh
# گزینه ۳
```

یا:

```bash
sudo systemctl stop bale-leave-bot
sudo systemctl disable bale-leave-bot
sudo rm /etc/systemd/system/bale-leave-bot.service
sudo systemctl daemon-reload
# در صورت تمایل کل پوشه را پاک کنید
```

---

## ساختار فایل‌ها

| فایل | نقش |
|------|-----|
| `bot.py` | نقطهٔ ورود و هندلرهای پیام/کال‌بک |
| `database.py` | لایهٔ SQLite (کاربران، گروه‌ها، مرخصی‌ها، تنظیمات) |
| `jalali.py` | تبدیل تاریخ شمسی/میلادی |
| `shift.py` | محاسبهٔ چرخهٔ شیفت |
| `calendar_ui.py` | کیبورد تقویم چندانتخابی |
| `keyboards.py` | منوها و کیبوردهای کمکی |
| `states.py` | وضعیت مکالمات چندمرحله‌ای |
| `config.py` | تنظیمات از متغیرهای محیطی |
| `cache.py` | کش درحافظه‌ای |
| `monthly_report.py` | گزارش ماهانه |
| `manage.sh` | اسکریپت منودار نصب / آپدیت / حذف / تنظیمات |
| `requirements.txt` | وابستگی‌ها (`python-bale-bot`) |
| `.env.example` | نمونهٔ فایل محیط |
| `VERSION` | شمارهٔ نسخه |

---

## متغیرهای محیطی (`.env`)

```bash
BALE_BOT_TOKEN=123456:AAE...     # الزامی
# DB_PATH=/path/to/leave_bot.db  # اختیاری
```

---

## نکات مهم

- پیام‌های ترمینال اسکریپت عمداً انگلیسی هستند تا در Termux و بسیاری از ترمینال‌ها متن مخلوط فارسی/انگلیسی برعکس نشود. خود ربات داخل بله کاملاً فارسی است.
- زمان ثبت درخواست‌ها به‌وقت سیستم سرور ذخیره می‌شود؛ منطقهٔ زمانی سرور را درست تنظیم کنید.
- هیچ داده‌ای (از جمله توکن) به سرور ثالث ارسال نمی‌شود.

---

## لایسنس

همهٔ فایل‌ها تحت اختیار خودتان قرار می‌گیرند. استفاده آزاد برای تیم‌ها و سازمان‌ها.
