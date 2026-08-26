
from dotenv import load_dotenv

load_dotenv()
import os
import sqlite3
from datetime import datetime, timedelta
import logging
import json
import tempfile

import telebot
from telebot import types

# -------------------------
# تنظیمات اصلی
# -------------------------

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS = [123456] # آیدی عددی کاربران مجاز برای استفاده از بات
DB_PATH = "/data/periods.db"

bot = telebot.TeleBot(TELEGRAM_TOKEN)
# -------------------------
# دیتابیس (SQLite ساده)
# -------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
    CREATE TABLE IF NOT EXISTS cycles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        start_date TEXT NOT NULL,
        end_date TEXT,
        period_length INTEGER,
        cycle_length INTEGER
    )
    """
    )
    conn.commit()
    conn.close()


def db_execute(query: str, params: tuple = (), fetch: bool = False):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(query, params)
    data = None
    if fetch:
        data = c.fetchall()
    conn.commit()
    conn.close()
    return data

# -------------------------
# ابزارهای کمکی
# -------------------------
def allowed_user_check(user_id: int) -> bool:
    try:
        return int(user_id) in ALLOWED_USER_IDS
    except Exception:
        return False

def now_iso():
    return datetime.utcnow().isoformat()

def parse_iso(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str)

def format_date_short(dt_iso: str) -> str:
    dt = parse_iso(dt_iso)
    return dt.strftime("%Y-%m-%d")

# -------------------------
# منطق چرخه‌ها
# -------------------------
def user_active_cycle(user_id: int):
    q = "SELECT id, start_date FROM cycles WHERE user_id=? AND end_date IS NULL ORDER BY id DESC LIMIT 1"
    res = db_execute(q, (user_id,), fetch=True)
    return res[0] if res else None

def insert_start_cycle(user_id: int, start_dt_iso: str):
    if user_active_cycle(user_id):
        return False, "در حال حاضر یک دوره فعال وجود دارد. نمی‌توانی دوباره شروع کنی."
    db_execute("INSERT INTO cycles (user_id, start_date) VALUES (?, ?)", (user_id, start_dt_iso))
    res = db_execute("SELECT id, start_date FROM cycles WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,), fetch=True)
    if not res:
        return True, "شروع ثبت شد."
    cid, start_iso = res[0]
    prev = db_execute("SELECT start_date FROM cycles WHERE user_id=? AND id<? ORDER BY id DESC LIMIT 1", (user_id, cid), fetch=True)
    if prev:
        prev_start = parse_iso(prev[0][0]).date()
        cur_start = parse_iso(start_iso).date()
        cycle_len = (cur_start - prev_start).days
        if cycle_len > 0:
            db_execute("UPDATE cycles SET cycle_length=? WHERE id=?", (cycle_len, cid))
    return True, "شروع پریود با موفقیت ثبت شد."

def end_active_cycle(user_id: int, end_dt_iso: str):
    active = user_active_cycle(user_id)
    if not active:
        return False, "هیچ دوره فعالی وجود ندارد که بتوان آن را پایان داد."
    cid, start_iso = active
    start_date = parse_iso(start_iso).date()
    end_date = parse_iso(end_dt_iso).date()
    period_len = (end_date - start_date).days + 1
    if period_len <= 0:
        return False, "تاریخ پایان نمی‌تواند قبل از شروع باشد."
    db_execute("UPDATE cycles SET end_date=?, period_length=? WHERE id=?", (end_dt_iso, period_len, cid))
    return True, f"پایان پریود ثبت شد. مدت دوره: {period_len} روز."

def get_cycles(user_id: int, limit: int = 100):
    q = "SELECT id, start_date, end_date, period_length, cycle_length FROM cycles WHERE user_id=? ORDER BY start_date DESC LIMIT ?"
    res = db_execute(q, (user_id, limit), fetch=True)
    return res

# -------------------------
# الگوریتم تخمین پریود بعدی
# -------------------------
def estimate_next_period(user_id: int):
    rows = db_execute("SELECT start_date, cycle_length FROM cycles WHERE user_id=? AND cycle_length IS NOT NULL ORDER BY start_date DESC", (user_id,), fetch=True)
    if not rows or len(rows) < 1:
        return False, "برای تخمین دقیق به حداقل دو دوره کامل نیاز است."
    cycle_lengths = [r[1] for r in rows if r[1] is not None]
    decay = 0.7
    weights = [decay ** i for i in range(len(cycle_lengths))]
    wsum = sum(weights)
    weights = [w / wsum for w in weights]
    weighted_avg = sum(w * cl for w, cl in zip(weights, cycle_lengths))
    avg_days = round(weighted_avg)
    last_row = db_execute("SELECT start_date FROM cycles WHERE user_id=? ORDER BY start_date DESC LIMIT 1", (user_id,), fetch=True)
    last_start_iso = last_row[0][0]
    last_start_date = parse_iso(last_start_iso).date()
    next_est = last_start_date + timedelta(days=avg_days)
    min_date = next_est - timedelta(days=2)
    max_date = next_est + timedelta(days=2)
    msg = (
        f"تخمین تاریخ شروع پریود بعدی (بر اساس میانگین وزنی {avg_days} روز):\n"
        f"احتمالاً بین {min_date.isoformat()} تا {max_date.isoformat()} شروع می‌شود.\n"
        f"(پیش‌بینی بر اساس {len(cycle_lengths)} سیکل اخیر انجام شده.)"
    )
    return True, msg

def export_database():
    tables = db_execute(
        "SELECT name FROM sqlite_master WHERE type='table'",
        fetch=True
    )

    data = {}

    for table in tables:
        table_name = table[0]

        rows = db_execute(
            f"SELECT * FROM {table_name}",
            fetch=True
        )

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute(f"SELECT * FROM {table_name}")

        records = [
            dict(row)
            for row in cur.fetchall()
        ]

        conn.close()

        data[table_name] = records

    return data

# -------------------------
# کیبورد
# -------------------------
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(
        resize_keyboard=True
    )

    markup.row(
        "▶️ شروع پریود",
        "⛔ پایان پریود"
    )

    markup.row(
        "📊 تخمین بعدی",
        "📜 تاریخچه"
    )
    markup.row(
        "📦 استخراج اطلاعات"
    )
    return markup

# -------------------------
# هندلرها
# -------------------------
@bot.message_handler(commands=["start"])
def start_cmd(message):
    user = message.from_user

    if not allowed_user_check(user.id):
        bot.send_message(
            message.chat.id,
            "این بات شخصیه و اجازه دسترسی نداری."
        )
        return

    text = (
        f"سلام {user.first_name} 🌸\n"
        "من دستیار ثبت پریودی‌ت هستم. با دکمه‌ها می‌تونی شروع یا پایان پریود رو ثبت کنی،\n"
        "تخمین دوره بعدی رو ببینی و تاریخچه کامل دوره‌ها رو مشاهده کنی."
    )

    bot.send_message(
        message.chat.id,
        text,
        reply_markup=main_keyboard()
    )


@bot.message_handler(func=lambda message: True)
def message_handler(message):
    user = message.from_user
    text = message.text

    if not allowed_user_check(user.id):
        bot.send_message(
            message.chat.id,
            "این بات شخصیه و اجازه دسترسی نداری."
        )
        return


    if text == "▶️ شروع پریود":

        active = user_active_cycle(user.id)

        if active:
            _, start_iso = active

            bot.send_message(
                message.chat.id,
                f"الان دوره فعالی داری. شروع: {format_date_short(start_iso)}\n"
                "ابتدا باید آن دوره را پایان دهی.",
                reply_markup=main_keyboard()
            )
            return

        ok, msg = insert_start_cycle(
            user.id,
            now_iso()
        )

        bot.send_message(
            message.chat.id,
            msg,
            reply_markup=main_keyboard()
        )


    elif text == "⛔ پایان پریود":

        active = user_active_cycle(user.id)

        if not active:
            bot.send_message(
                message.chat.id,
                "هیچ دوره فعالی وجود ندارد.",
                reply_markup=main_keyboard()
            )
            return


        ok, msg = end_active_cycle(
            user.id,
            now_iso()
        )


        if ok:

            last = db_execute(
                """
                SELECT id, start_date, end_date, period_length, cycle_length
                FROM cycles
                WHERE user_id=?
                ORDER BY start_date DESC
                LIMIT 1
                """,
                (user.id,),
                fetch=True
            )


            if last:

                cid, s_iso, e_iso, p_len, c_len = last[0]

                report = (
                    f"خلاصه‌ی دوره ثبت‌شده:\n"
                    f"- شروع: {format_date_short(s_iso)}\n"
                    f"- پایان: {format_date_short(e_iso)}\n"
                    f"- مدت پریود: {p_len} روز\n"
                )


                if c_len:
                    report += f"- فاصله تا پریود قبلی: {c_len} روز\n"
                else:
                    report += "- فاصله تا پریود قبلی: (داده‌ی کافی وجود ندارد)\n"


                bot.send_message(
                    message.chat.id,
                    msg + "\n\n" + report,
                    reply_markup=main_keyboard()
                )
                return

        bot.send_message(
            message.chat.id,
            msg,
            reply_markup=main_keyboard()
        )


    elif text == "📊 تخمین بعدی":

        ok, msg = estimate_next_period(user.id)

        bot.send_message(
            message.chat.id,
            msg,
            reply_markup=main_keyboard()
        )


    elif text == "📜 تاریخچه":

        rows = get_cycles(
            user.id,
            limit=200
        )


        if not rows:
            bot.send_message(
                message.chat.id,
                "تاریخی ثبت نشده.",
                reply_markup=main_keyboard()
            )
            return


        parts = []

        for idx, row in enumerate(rows, start=1):

            cid, s_iso, e_iso, p_len, c_len = row

            parts.append(
                f"{idx}. شروع: {format_date_short(s_iso)} | پایان: "
                f"{format_date_short(e_iso) if e_iso else '—'} | "
                f"طول پریود: {p_len or '—'} | "
                f"فاصله از قبلی: {c_len or '—'}"
            )


        full = "تاریخچه دوره‌ها:\n" + "\n".join(parts)


        bot.send_message(
            message.chat.id,
            full,
            reply_markup=main_keyboard()
        )

    elif text == "📦 استخراج اطلاعات":

        data = export_database()

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=4
            )

            temp_name = f.name

        with open(temp_name, "rb") as f:
            bot.send_document(
                message.chat.id,
                f,
                caption="📦 Database Export (JSON)"
            )

        os.remove(temp_name)

    else:

        bot.send_message(
            message.chat.id,
            "گزینه نامشخص است 🤍",
            reply_markup=main_keyboard()
        )



@bot.message_handler(commands=["stats"])
def stats_cmd(message):

    user = message.from_user


    if not allowed_user_check(user.id):

        bot.send_message(
            message.chat.id,
            "این بات شخصیه و اجازه دسترسی نداری."
        )
        return


    rows = db_execute(
        """
        SELECT period_length, cycle_length
        FROM cycles
        WHERE user_id=?
        ORDER BY start_date DESC
        """,
        (user.id,),
        fetch=True
    )


    if not rows:

        bot.send_message(
            message.chat.id,
            "هنوز دوره‌ای ثبت نشده."
        )
        return


    period_lengths = [
        r[0] for r in rows
        if r[0] is not None
    ]

    cycle_lengths = [
        r[1] for r in rows
        if r[1] is not None
    ]


    msg = "گزارش کلی:\n"


    if period_lengths:

        avg_period = sum(period_lengths) / len(period_lengths)

        msg += (
            f"- میانگین طول پریود: "
            f"{avg_period:.1f} روز "
            f"(بر اساس {len(period_lengths)} دوره)\n"
        )

    else:

        msg += "- میانگین طول پریود: داده کافی نیست\n"



    if cycle_lengths:

        avg_cycle = sum(cycle_lengths) / len(cycle_lengths)

        msg += (
            f"- میانگین طول سیکل: "
            f"{avg_cycle:.1f} روز "
            f"(بر اساس {len(cycle_lengths)} سیکل)\n"
        )

    else:

        msg += "- میانگین طول سیکل: داده کافی نیست\n"


    bot.send_message(
        message.chat.id,
        msg,
        reply_markup=main_keyboard()
    )

# -------------------------
# main
# -------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
def main():
    init_db()

    bot.delete_webhook()

    bot.infinity_polling(
        skip_pending=True
    )


if __name__ == "__main__":
    main()
