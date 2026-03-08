import re
import os
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Matches ABA PAY messages with any currency symbol ($, ៛, ฿, etc.)
# Uses [^\d\s] so ANY non-digit non-space prefix (currency symbol) is captured.
ABA_PAY_PATTERN = re.compile(
    r"([^\d\s])([0-9,]+(?:\.[0-9]{1,2})?)\s+paid\s+by\s+(.+?)\s+\((\*\d+)\)"
    r".*?on\s+(\w+\s+\d+[,]?\s*\d+:\d+\s*(?:AM|PM))"
    r".*?Trx\.\s*ID:\s*(\d+)",
    re.IGNORECASE | re.DOTALL,
)


def get_currency(symbol: str) -> str:
    """$ = USD, anything else (₿ ៛ ฿ etc.) = KHR."""
    return "USD" if symbol == "$" else "KHR"


@dataclass
class Payment:
    currency: str       # "USD" or "KHR"
    amount: float
    payer: str
    account: str
    pay_date: date
    time_str: str
    trx_id: str


# In-memory storage per user: {user_id: [Payment]}
user_payments: dict[int, list[Payment]] = {}


def get_payments(user_id: int) -> list[Payment]:
    return user_payments.setdefault(user_id, [])


def parse_date(date_str: str) -> tuple[date, str]:
    """Parse 'Mar 08, 11:16 AM' or 'Mar 08 11:16 AM' → (date object, formatted string)."""
    # Normalise: collapse multiple spaces, remove extra commas
    s = re.sub(r",", "", date_str.strip())   # remove commas → 'Mar 08 11:16 AM'
    s = re.sub(r"\s+", " ", s)               # collapse spaces
    year = datetime.now().year
    for fmt in ("%b %d %I:%M %p",):
        try:
            dt = datetime.strptime(f"{s} {year}", f"{fmt} %Y")
            return dt.date(), dt.strftime("%b %d %I:%M %p")
        except ValueError:
            continue
    return date.today(), date_str.strip()


def parse_aba_message(text: str) -> Payment | None:
    """Extract payment details from an ABA PAY notification message."""
    match = ABA_PAY_PATTERN.search(text)
    if not match:
        print(f"[DEBUG] No match for text: {repr(text[:120])}")
        return None
    symbol, amount_str, payer, account, date_str, trx_id = match.groups()
    print(f"[DEBUG] Matched: symbol={repr(symbol)} amount={amount_str} payer={payer} date={date_str}")
    amount = float(amount_str.replace(",", ""))
    currency = get_currency(symbol)
    pay_date, time_str = parse_date(date_str)
    return Payment(
        currency=currency,
        amount=amount,
        payer=payer.strip(),
        account=account,
        pay_date=pay_date,
        time_str=time_str,
        trx_id=trx_id,
    )


def day_summary(payments: list[Payment], target_date: date) -> tuple[float, int, float, int]:
    """Returns (usd_total, usd_count, khr_total, khr_count) for a given date."""
    usd_total = usd_count = khr_total = khr_count = 0.0
    usd_count = khr_count = 0
    for p in payments:
        if p.pay_date == target_date:
            if p.currency == "USD":
                usd_total += p.amount
                usd_count += 1
            else:
                khr_total += p.amount
                khr_count += 1
    return usd_total, usd_count, khr_total, khr_count


def format_day_block(label: str, day: date, payments: list[Payment]) -> str:
    usd_total, usd_count, khr_total, khr_count = day_summary(payments, day)
    return (
        f"📅 {label} ({day.strftime('%b %d')})\n"
        f"  💵 USD: ${usd_total:,.2f}  ({usd_count} payment{'s' if usd_count != 1 else ''})\n"
        f"  🔴 KHR: ៛{khr_total:,.0f}  ({khr_count} payment{'s' if khr_count != 1 else ''})"
    )


async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo raw text and show regex match result — for troubleshooting."""
    text = update.message.text or ""
    # Show first 300 chars and their unicode codepoints
    preview = text[:300]
    codepoints = " ".join(f"U+{ord(c):04X}" for c in preview[:30])
    match = ABA_PAY_PATTERN.search(text)
    if match:
        g = match.groups()
        result = (
            f"✅ REGEX MATCHED\n"
            f"Symbol: {repr(g[0])} (U+{ord(g[0]):04X})\n"
            f"Amount: {g[1]}\n"
            f"Payer:  {g[2]}\n"
            f"Acct:   {g[3]}\n"
            f"Date:   {g[4]}\n"
            f"TrxID:  {g[5]}"
        )
    else:
        result = "❌ REGEX DID NOT MATCH"
    await update.message.reply_text(
        f"📨 Raw text ({len(text)} chars):\n{preview}\n\n"
        f"First 30 chars codepoints:\n{codepoints}\n\n"
        f"{result}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "💰 ABA PAY Calculator Bot\n\n"
        "Forward ABA PAY messages — I auto-detect USD ($) and KHR (៛)\n"
        "and track TODAY vs YESTERDAY totals separately.\n\n"
        "Commands:\n"
        "/today      – Today's USD & KHR totals\n"
        "/yesterday  – Yesterday's USD & KHR totals\n"
        "/summary    – Both days at a glance\n"
        "/list       – All recorded payments\n"
        "/clear      – Reset everything\n"
        "/help       – Show this message"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 How to use:\n\n"
        "• Forward any ABA PAY notification (USD or KHR)\n"
        "• The bot reads the amount, payer & date automatically\n"
        "• Or type a plain number to add manually (treated as USD)\n\n"
        "/today      – Today's USD & KHR totals\n"
        "/yesterday  – Yesterday's USD & KHR totals\n"
        "/summary    – Both days at a glance\n"
        "/list       – Full payment history\n"
        "/clear      – Reset"
    )


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payments = get_payments(update.effective_user.id)
    await update.message.reply_text(format_day_block("TODAY", date.today(), payments))


async def yesterday_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payments = get_payments(update.effective_user.id)
    yesterday = date.today() - timedelta(days=1)
    await update.message.reply_text(format_day_block("YESTERDAY", yesterday, payments))


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payments = get_payments(update.effective_user.id)
    today = date.today()
    yesterday = today - timedelta(days=1)
    msg = (
        format_day_block("TODAY", today, payments)
        + "\n\n"
        + format_day_block("YESTERDAY", yesterday, payments)
    )
    await update.message.reply_text(msg)


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payments = get_payments(update.effective_user.id)
    if not payments:
        await update.message.reply_text("No payments recorded yet.")
        return

    lines = []
    for i, p in enumerate(payments, 1):
        sym = "$" if p.currency == "USD" else "៛"
        fmt_amt = f"{p.amount:,.2f}" if p.currency == "USD" else f"{p.amount:,.0f}"
        lines.append(f"{i}. {sym}{fmt_amt} — {p.payer} {p.account}  [{p.time_str}]")

    today = date.today()
    yesterday = today - timedelta(days=1)
    usd_t, uc_t, khr_t, kc_t = day_summary(payments, today)
    usd_y, uc_y, khr_y, kc_y = day_summary(payments, yesterday)

    footer = (
        f"\n━━━━━━━━━━━━━━━━\n"
        f"📅 TODAY ({today.strftime('%b %d')})\n"
        f"  💵 USD: ${usd_t:,.2f}  ({uc_t} payments)\n"
        f"  🔴 KHR: ៛{khr_t:,.0f}  ({kc_t} payments)\n\n"
        f"📅 YESTERDAY ({yesterday.strftime('%b %d')})\n"
        f"  💵 USD: ${usd_y:,.2f}  ({uc_y} payments)\n"
        f"  🔴 KHR: ៛{khr_y:,.0f}  ({kc_y} payments)"
    )

    text = "📋 All Payments:\n\n" + "\n".join(lines) + footer
    if len(text) > 4000:
        text = "📋 All Payments (last 50):\n\n" + "\n".join(lines[-50:]) + footer
    await update.message.reply_text(text)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_payments[update.effective_user.id] = []
    await update.message.reply_text("✅ All payments cleared.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Support both direct text and captions (e.g. photo messages)
    msg = update.message or update.channel_post
    if not msg:
        return
    text = (msg.text or msg.caption or "").strip()
    if not text:
        return
    print(f"[DEBUG] Received message from user {update.effective_user.id}: {repr(text[:120])}")

    # Try ABA PAY parse first — record silently, no reply
    payment = parse_aba_message(text)
    if payment:
        get_payments(update.effective_user.id).append(payment)
        return

    # Fallback: plain number (treated as USD for today) — also silent
    try:
        amount = float(text.replace(",", ""))
        manual = Payment(
            currency="USD", amount=amount, payer="Manual", account="",
            pay_date=date.today(), time_str=datetime.now().strftime("%b %d %I:%M %p"), trx_id="—",
        )
        get_payments(update.effective_user.id).append(manual)
    except ValueError:
        pass  # ignore non-payment text


def main() -> None:
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("debug", debug_command))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("yesterday", yesterday_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot is running...")
    print("NOTE: If in a group, make sure Bot Privacy Mode is OFF in @BotFather")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
