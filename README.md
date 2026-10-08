# Telegram News Bot

ربات جمع‌آوری، پردازش و انتشار خودکار خبر برای کانال تلگرام.

## معماری

RSS/Atom → جمع‌آوری امن → فقط TechCrunch/The Verge/Engadget → حذف تبلیغات → حذف تکراری → Gemini → ترجمه/خلاصه → outbox پایدار → Telegram

GitHub Actions هر ۳۰ دقیقه (دقیقه‌های ۸ و ۳۸) اجرا می‌شود و timeout اجرای Workflow به ۷۵ دقیقه افزایش یافته است. ربات خبرهای ۹۰ دقیقه اخیر را بررسی می‌کند. پنجره عمداً بزرگ‌تر از فاصله اجراست چون cron گیت‌هاب گاهی دیر اجرا می‌شود یا یک اجرا را جا می‌اندازد.

جمع‌آوری URL با بررسی scheme، host/IP، redirect و اندازه پاسخ محدود شده است. تعداد candidateها و زمان کل پردازش نیز سقف دارند.

## تضمین‌های انتشار

- سیستم **at-least-once** است، نه exactly-once. API تلگرام برای sendMessage یک کلید idempotency ارائه نمی‌کند، بنابراین crash دقیقاً بین تأیید موفق Telegram و persist نهایی state از نظر تئوری می‌تواند duplicate ایجاد کند.
- برای کاهش این پنجره، هر پیام قبل از ارسال در outbox به‌صورت atomic ثبت می‌شود. اگر runner قبل از ارسال متوقف شود، اجرای بعدی outbox را بازیابی می‌کند و پیام از دست نمی‌رود.
- پیام موفق پس از ارسال به‌عنوان sent در state پایدار ثبت می‌شود و سپس story در deduplication state قرار می‌گیرد.
- خطای 5xx، timeout و خطاهای مبهم Telegram باعث fallback خودکار به متن ساده نمی‌شوند، چون ممکن است درخواست در سمت Telegram پذیرفته شده باشد.
- fallback فقط برای rejectionهای قطعی و قابل‌اعتماد انجام می‌شود.
- state خراب یا نامعتبر **fatal** است و bot با state ناشناخته خبر منتشر نمی‌کند.
- خبرها از قدیمی به جدید منتشر می‌شوند.
- state ربات روی branch مستقل bot-state نگهداری می‌شود تا Branch Protection روی main دور زده نشود.
- انتشار خبر و persistence state در دو job جدا اجرا می‌شوند؛ job انتشار مجوز نوشتن روی repository ندارد.

## امنیت و کنترل مصرف

- جلوگیری از SSRF و دسترسی به IPهای private/local/link-local.
- محدودیت حجم پاسخ‌های RSS و مقاله.
- محدودیت redirect.
- حداکثر **۳۰ candidate** در هر اجرا.
- deadline داخلی ۱۰ دقیقه، کمتر از timeout پانزده‌دقیقه‌ای workflow.
- اعتبارسنجی خروجی Gemini و category به‌صورت enum.
- فقط سه منبع مجاز هستند: TechCrunch، The Verge و Engadget.
- هیچ فیلتر موضوعی تکنولوژی وجود ندارد؛ تمام خبرهای این سه منبع پذیرفته می‌شوند، به‌جز تبلیغات، محتوای sponsored/paid/promoted و advertorial.
- حذف تکراری هم بر اساس URL و هم تطبیق رویداد بین بازنویسی‌های متفاوت و زبان انگلیسی/فارسی انجام می‌شود.
- timeout درخواست Gemini برابر ۹۰ ثانیه است.
- workflowهای CI با SHA pin شده‌اند و مجوزها حداقلی شده‌اند.

## Secrets

در Settings → Secrets and variables → Actions این سه Secret را اضافه کنید:
- GEMINI_API_KEY
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID

## اجرا

    pip install -r requirements.txt pytest
    pytest -q
    python -m app.main

منابع RSS در data/sources.json قابل تغییر هستند.
