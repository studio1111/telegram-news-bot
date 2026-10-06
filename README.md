# Telegram News Bot

ربات جمع‌آوری، پردازش و انتشار خودکار خبر برای کانال تلگرام.

## معماری
RSS/Atom → جمع‌آوری → حذف تکراری → Gemini → ترجمه/خلاصه/دسته‌بندی → Telegram

GitHub Actions هر ۵ دقیقه اجرا می‌شود و خبرهای منتشرشده در ۵ دقیقه اخیر را بررسی می‌کند؛ وضعیت خبرهای منتشرشده در data/state.json نگه داشته می‌شود.

## Secrets
در Settings → Secrets and variables → Actions این سه Secret را اضافه کنید:
- GEMINI_API_KEY
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID

## اجرا
```bash
pip install -r requirements.txt pytest
pytest -q
python -m app.main
```

منابع RSS در data/sources.json قابل تغییر هستند.