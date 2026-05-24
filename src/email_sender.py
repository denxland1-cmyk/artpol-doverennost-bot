"""Отправка письма поставщику через Gmail SMTP."""

from __future__ import annotations
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from .pdf_processor import DoverennostData
from .suppliers import Supplier

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587  # STARTTLS (на cloud-провайдерах чаще проходит чем 465/SSL)


def _today_msk() -> str:
    """Сегодняшняя дата в МСК в формате DD.MM.YYYY."""
    return datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y")


def build_subject() -> str:
    return f'Заявка АРТПОЛ на {_today_msk()}'


def build_body(data: DoverennostData, signature_name: str, signature_phone: str) -> str:
    """Тело письма по образцу: «Кулемин 15 Цемент ЦЕМ I 42.5Н по 50 кг»."""
    surname = data.driver_surname or "—"
    qty = data.item_qty_int if data.item_qty_int is not None else (data.item_qty_propisi or "—")
    item = data.item_name or "—"
    main_line = f"{surname} {qty} {item}"
    return (
        f"{main_line}\n"
        f"\n"
        f"Компания ООО «АРТПОЛ»\n"
        f"{signature_name}\n"
        f"{signature_phone}\n"
    )


def build_attachment_name(data: DoverennostData) -> str:
    """«Доверенность_705_22.05.2026.pdf» (или fallback если поля не распарсились)."""
    num = data.number or "X"
    date = data.date_str or _today_msk()
    return f"Доверенность_{num}_{date}.pdf"


def send(
    *,
    to_supplier: Supplier,
    data: DoverennostData,
    pdf_path: Path,
    gmail_user: str | None = None,
    gmail_password: str | None = None,
    signature_name: str | None = None,
    signature_phone: str | None = None,
) -> dict:
    """Отправляет письмо. Возвращает dict с метаданными для журнала.

    Бросает исключение при сбое SMTP — caller ловит и шлёт админу.
    """
    gmail_user = gmail_user or os.environ["GMAIL_USER"]
    gmail_password = gmail_password or os.environ["GMAIL_APP_PASSWORD"]
    signature_name = signature_name or os.environ.get("SIGNATURE_NAME", "Менеджер")
    signature_phone = signature_phone or os.environ.get("SIGNATURE_PHONE", "")

    subject = build_subject()
    body = build_body(data, signature_name, signature_phone)
    attach_name = build_attachment_name(data)

    msg = EmailMessage()
    msg["From"] = gmail_user
    msg["To"] = to_supplier.email
    msg["Subject"] = subject
    msg.set_content(body)

    with open(pdf_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="pdf",
            filename=attach_name,
        )

    # Gmail App Password может быть с пробелами ("xxxx yyyy zzzz wwww") —
    # SMTP требует без; strip пробелов на всякий случай.
    gmail_password = gmail_password.replace(" ", "")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(gmail_user, gmail_password)
        smtp.send_message(msg)

    return {
        "to_email": to_supplier.email,
        "supplier_key": to_supplier.key,
        "subject": subject,
        "attach_name": attach_name,
        "doverennost_number": data.number,
        "doverennost_date": data.date_str,
    }
