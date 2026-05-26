"""Telegram-бот для отправки доверенностей поставщикам.

Сценарий:
1. Менеджер кидает PDF доверенности (из БИЗНЕС-ПАК)
2. Бот парсит PDF, накладывает печать+подпись на копию
3. Менеджер выбирает поставщика (8 inline-кнопок)
4. Бот шлёт превью обработанного PDF + кнопки [Отправить] [Отмена]
5. По «Отправить» — SMTP через Gmail → email поставщика, запись в SQLite
"""

from __future__ import annotations
import asyncio
import logging
import os
import tempfile
import traceback
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)
from telegram.request import HTTPXRequest

from . import db, suppliers
from .email_sender import send as send_email, build_subject, build_body, build_attachment_name
from .pdf_processor import parse as parse_pdf, stamp as stamp_pdf, DoverennostData

load_dotenv()
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
# httpx логирует полный URL Telegram API, а в URL — токен. Глушим до WARNING.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("doverennost-bot")

ADMIN_TG_ID = int(os.environ.get("ADMIN_TG_ID", "0"))

# Префиксы callback_data
CB_SUPPLIER = "sup:"
CB_SEND = "send"
CB_CANCEL = "cancel"


# -------------------- HELPERS --------------------

def _username(update: Update) -> str | None:
    u = update.effective_user
    return f"@{u.username}" if u and u.username else (u.full_name if u else None)


def _supplier_keyboard(pdf_id: str, preselected: str | None = None) -> InlineKeyboardMarkup:
    """8 поставщиков, 2 кнопки в ряд, порядок из suppliers.SUPPLIERS_ORDER."""
    keys = suppliers.SUPPLIERS_ORDER
    rows = []
    for i in range(0, len(keys), 2):
        row = []
        for k in keys[i:i + 2]:
            label = suppliers.button_label(k)
            if preselected == k:
                label = f"✓ {label}"
            row.append(InlineKeyboardButton(label, callback_data=f"{CB_SUPPLIER}{pdf_id}:{k}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _confirm_keyboard(pdf_id: str, supplier_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Отправить", callback_data=f"{CB_SEND}:{pdf_id}:{supplier_key}"),
        InlineKeyboardButton("✖️ Отмена",    callback_data=f"{CB_CANCEL}:{pdf_id}"),
    ]])


def _format_summary(data: DoverennostData, supplier: suppliers.Supplier) -> str:
    """Превью того, что уйдёт в письме."""
    qty = data.item_qty_int if data.item_qty_int is not None else (data.item_qty_propisi or "—")
    main_line = f"{data.driver_surname or '—'} {qty} {data.item_name or '—'}"
    return (
        f"📧 <b>Поставщик:</b> {supplier.name}\n"
        f"📨 <b>Email:</b> <code>{supplier.email}</code>\n"
        f"📝 <b>Тема:</b> {build_subject()}\n\n"
        f"<b>Текст письма:</b>\n"
        f"<code>{main_line}</code>\n\n"
        f"📎 <b>Вложение:</b> <code>{build_attachment_name(data)}</code>\n\n"
        f"Доверенность №{data.number or '—'} от {data.date_str or '—'}\n"
        f"Водитель: {data.driver_short or '—'}"
    )


async def _notify_admin(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if not ADMIN_TG_ID:
        return
    try:
        await context.bot.send_message(ADMIN_TG_ID, text)
    except Exception:
        log.exception("Не смог уведомить админа")


# -------------------- HANDLERS --------------------

async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Я отправляю доверенности поставщикам.\n\n"
        "Просто пришли мне PDF из БИЗНЕС-ПАК — я распарсю его, "
        "наложу печать с подписью и предложу выбрать поставщика. "
        "Перед отправкой покажу превью."
    )


async def cmd_history(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Админ-команда: последние 20 отправок."""
    if update.effective_user.id != ADMIN_TG_ID:
        return
    rows = db.recent_sent(20)
    if not rows:
        await update.message.reply_text("Журнал пуст.")
        return
    lines = ["📋 Последние отправки:\n"]
    for r in rows:
        lines.append(
            f"{r['ts'][:16]} | {r['tg_username'] or '—'} → "
            f"{r['supplier_key']} | №{r['doverennost_number']} | "
            f"{r['driver_surname']} {r['item_qty']} {r['item_name']}"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_stats(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Админ-команда: уникальные пользователи + breakdown по действиям."""
    if update.effective_user.id != ADMIN_TG_ID:
        return
    users = db.user_stats()
    if not users:
        await update.message.reply_text("Никто ещё не пользовался ботом.")
        return
    lines = [f"👥 Всего уникальных пользователей: <b>{len(users)}</b>\n"]
    for u in users:
        uname = u["tg_username"] or "—"
        lines.append(
            f"<b>{uname}</b> (id <code>{u['tg_user_id']}</code>)\n"
            f"  📅 {u['first_seen'][:16]} → {u['last_seen'][:16]}\n"
            f"  📄 PDF: {u['pdfs']}  ▶️ выбрал: {u['clicks']}  "
            f"✅ отправлено: {u['sent_ok']}  ✖️ отмена: {u['cancels']}\n"
            f"  ⚠️ парс-fail: {u['parse_fails']}  печать-fail: {u['stamp_fails']}  "
            f"SMTP-fail: {u['fails']}"
        )
    await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_whoami(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    await update.message.reply_text(
        f"user_id: <code>{u.id}</code>\nusername: @{u.username}\nname: {u.full_name}",
        parse_mode=ParseMode.HTML,
    )


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Приём PDF доверенности."""
    user = update.effective_user
    doc = update.message.document
    if not doc or not (doc.file_name or "").lower().endswith(".pdf"):
        await update.message.reply_text("Жду PDF. Это что-то другое.")
        return

    db.log_attempt(tg_user_id=user.id, tg_username=_username(update),
                   action="pdf_received", detail=doc.file_name)

    # Скачиваем во временный файл (исходник)
    work_dir = Path(tempfile.mkdtemp(prefix="doverka_"))
    src_pdf = work_dir / "src.pdf"
    stamped_pdf = work_dir / "stamped.pdf"
    file = await doc.get_file()
    await file.download_to_drive(str(src_pdf))

    # Парсим
    try:
        data = parse_pdf(src_pdf)
    except Exception as e:
        log.exception("parse failed")
        db.log_attempt(tg_user_id=user.id, tg_username=_username(update),
                       action="parse_fail", detail=str(e))
        await update.message.reply_text(
            "Не смог разобрать PDF. Это точно доверенность из БИЗНЕС-ПАК (форма М-2)?"
        )
        await _notify_admin(context, f"parse_fail от {_username(update)}: {e}")
        return

    # Накладываем факсимиле
    try:
        stamp_pdf(src_pdf, stamped_pdf)
    except Exception as e:
        log.exception("stamp failed")
        db.log_attempt(tg_user_id=user.id, tg_username=_username(update),
                       action="stamp_fail", detail=str(e))
        await update.message.reply_text(
            "PDF разобран, но не получилось наложить печать (не нашёл «М. П.» / «Руководитель»)."
        )
        await _notify_admin(context, f"stamp_fail от {_username(update)}: {e}")
        return

    # Сохраняем состояние сессии
    pdf_id = stamped_pdf.stem  # уникальный
    context.user_data[pdf_id] = {
        "data": data,
        "work_dir": work_dir,
        "stamped_pdf": stamped_pdf,
        "src_filename": doc.file_name,
    }

    # Авто-выбор поставщика по содержимому PDF
    auto = suppliers.match_by_pdf_supplier(data.supplier_raw or "")

    parsed_summary = (
        f"📄 Распарсил доверенность №{data.number or '?'} от {data.date_str or '?'}\n"
        f"👤 Водитель: <b>{data.driver_short or '?'}</b>\n"
        f"📦 Товар: <b>{data.item_name or '?'}</b> — "
        f"{data.item_qty_int if data.item_qty_int is not None else data.item_qty_propisi or '?'} "
        f"{data.item_unit or ''}\n"
        f"🏢 Из PDF: <i>{data.supplier_raw or '?'}</i>\n\n"
    )
    if auto:
        s = suppliers.get(auto)
        parsed_summary += f"Похоже это <b>{s.name}</b>. Подтверди или выбери другого:"
    else:
        parsed_summary += "Выбери поставщика:"

    await update.message.reply_text(
        parsed_summary,
        parse_mode=ParseMode.HTML,
        reply_markup=_supplier_keyboard(pdf_id, preselected=auto),
    )


async def on_supplier(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Менеджер нажал на кнопку поставщика — шлём превью + Отправить/Отмена."""
    query = update.callback_query
    await query.answer()

    _, payload = query.data.split(CB_SUPPLIER, 1)
    pdf_id, supplier_key = payload.split(":", 1)

    session = context.user_data.get(pdf_id)
    if not session:
        await query.edit_message_text("Сессия истекла. Пришли PDF заново.")
        return

    supplier = suppliers.get(supplier_key)
    if not supplier:
        await query.edit_message_text("Неизвестный поставщик. Странно.")
        return

    data: DoverennostData = session["data"]
    stamped: Path = session["stamped_pdf"]

    db.log_attempt(tg_user_id=update.effective_user.id, tg_username=_username(update),
                   action="supplier_chosen", detail=supplier_key)

    # Шлём PDF с факсимиле как превью
    await query.message.reply_document(
        document=stamped.open("rb"),
        filename=build_attachment_name(data),
        caption=_format_summary(data, supplier),
        parse_mode=ParseMode.HTML,
        reply_markup=_confirm_keyboard(pdf_id, supplier_key),
    )


async def on_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Менеджер подтвердил отправку."""
    query = update.callback_query
    await query.answer("Отправляю...")

    _, pdf_id, supplier_key = query.data.split(":", 2)
    session = context.user_data.get(pdf_id)
    if not session:
        await query.edit_message_caption("Сессия истекла. Пришли PDF заново.")
        return

    data: DoverennostData = session["data"]
    stamped: Path = session["stamped_pdf"]
    supplier = suppliers.get(supplier_key)

    try:
        meta = send_email(to_supplier=supplier, data=data, pdf_path=stamped)
    except Exception as e:
        log.exception("send failed")
        db.log_attempt(tg_user_id=update.effective_user.id, tg_username=_username(update),
                       action="send_fail", detail=f"{supplier_key}: {e}")
        await query.edit_message_caption(
            f"❌ Не отправилось: <code>{e}</code>",
            parse_mode=ParseMode.HTML,
        )
        await _notify_admin(context, f"SMTP fail от {_username(update)} → {supplier.email}: {e}")
        return

    # Лог в БД
    db.log_sent(
        tg_user_id=update.effective_user.id,
        tg_username=_username(update),
        supplier_key=supplier.key,
        to_email=supplier.email,
        doverennost_number=data.number,
        doverennost_date=data.date_str,
        driver_surname=data.driver_surname,
        item_name=data.item_name,
        item_qty=data.item_qty_int,
        subject=meta["subject"],
        attach_name=meta["attach_name"],
    )

    await query.edit_message_caption(
        f"✅ Отправлено: <b>{supplier.name}</b> → <code>{supplier.email}</code>",
        parse_mode=ParseMode.HTML,
    )

    # Чистим временные файлы
    _cleanup(session, context, pdf_id)


async def on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Отменено")
    _, pdf_id = query.data.split(":", 1)
    session = context.user_data.get(pdf_id)
    db.log_attempt(tg_user_id=update.effective_user.id, tg_username=_username(update),
                   action="cancelled", detail=pdf_id)
    await query.edit_message_caption("✖️ Отменено. Если что — пришли PDF заново.")
    if session:
        _cleanup(session, context, pdf_id)


def _cleanup(session: dict, context: ContextTypes.DEFAULT_TYPE, pdf_id: str) -> None:
    """Удаляет временные файлы и убирает сессию из user_data."""
    import shutil
    try:
        shutil.rmtree(session["work_dir"], ignore_errors=True)
    except Exception:
        log.exception("cleanup")
    context.user_data.pop(pdf_id, None)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Exception:", exc_info=context.error)
    tb = "".join(traceback.format_exception(type(context.error), context.error, context.error.__traceback__))
    await _notify_admin(context, f"❗️ Bot exception:\n<pre>{tb[-2000:]}</pre>")


# -------------------- ENTRY POINT --------------------

def main() -> None:
    token = os.environ["TG_TOKEN"]
    db.init()

    # Расширенные таймауты: при медленном канале PDF 1-7 MB иначе не успевает
    request = HTTPXRequest(connect_timeout=20, read_timeout=60, write_timeout=120, pool_timeout=20)
    app = Application.builder().token(token).request(request).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(MessageHandler(filters.Document.PDF, on_document))
    app.add_handler(CallbackQueryHandler(on_supplier, pattern=f"^{CB_SUPPLIER}"))
    app.add_handler(CallbackQueryHandler(on_send,     pattern=f"^{CB_SEND}:"))
    app.add_handler(CallbackQueryHandler(on_cancel,   pattern=f"^{CB_CANCEL}:"))
    app.add_error_handler(on_error)

    log.info("Бот запускается...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
