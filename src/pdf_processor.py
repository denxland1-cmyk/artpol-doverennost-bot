"""Парсинг и наложение факсимиле на PDF доверенности (форма М-2).

Два публичных метода:
- parse(pdf_path) -> DoverennostData  — извлекает поля для тела письма
- stamp(src_path, dst_path)           — накладывает печать+подпись по якорям
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber

from .numbers_ru import propisi_to_int

# 1 mm в pt (PDF единицы)
MM2PT = 72 / 25.4

# Пути к факсимиле — из корня проекта
ASSETS = Path(__file__).resolve().parent.parent / "assets"
PECHAT_PATH = str(ASSETS / "pechat.png")
PODPIS_PATH = str(ASSETS / "podpis.png")

# Геометрия наложения (одобрено Денисом 2026-05-22):
# - Печать диаметром 40 мм, центр на 10 мм левее центра «М. П.»
# - Подпись 105×38 pt, нижний край на линии Руководителя, отступ 15pt от слова
PECHAT_DIAMETER_MM = 40.0
PECHAT_OFFSET_LEFT_MM = 10.0
PODPIS_WIDTH_PT = 105.0
PODPIS_HEIGHT_PT = 38.0
PODPIS_GAP_FROM_RUKOVODITEL_PT = 15.0


@dataclass
class DoverennostData:
    """Распарсенные поля доверенности для письма поставщику."""
    number: str | None              # «705»
    date_str: str | None            # «22.05.2026»
    driver_short: str | None        # «Кулемин Е. Н.»
    driver_surname: str | None      # «Кулемин» — для тела письма
    supplier_raw: str | None        # «ООО "СтройКоммерц"» — для авто-выбора кнопки
    item_name: str | None           # «Цемент ЦЕМ I 42.5H по 50 кг»
    item_unit: str | None           # «мешок»
    item_qty_propisi: str | None    # «Шестнадцать»
    item_qty_int: int | None        # 16


# -------------------- ПАРСИНГ --------------------

_RUS_MONTHS = {
    "января": "01", "февраля": "02", "марта": "03", "апреля": "04",
    "мая": "05", "июня": "06", "июля": "07", "августа": "08",
    "сентября": "09", "октября": "10", "ноября": "11", "декабря": "12",
}


def parse(pdf_path: str | Path) -> DoverennostData:
    """Парсит PDF доверенности через pdfplumber."""
    with pdfplumber.open(str(pdf_path)) as pdf:
        # Берём страницу с блоком подписей (обычно последняя)
        page = _find_signature_page_plumber(pdf) or pdf.pages[0]
        text = page.extract_text() or ""

    # № доверенности
    m = re.search(r"ДОВЕРЕННОСТЬ\s*№\s*(\d+)", text)
    number = m.group(1) if m else None

    # Дата выдачи: «Дата выдачи " 22 " мая 2026 г.»
    m = re.search(r'Дата выдачи\s*"\s*(\d{1,2})\s*"\s+(\S+)\s+(\d{4})', text)
    if m:
        day = m.group(1).zfill(2)
        month_word = m.group(2).lower()
        year = m.group(3)
        month = _RUS_MONTHS.get(month_word, "00")
        date_str = f"{day}.{month}.{year}"
    else:
        date_str = None

    # ФИО из таблицы: «705 22.05.26 22.05.27 Кулемин Е. Н.»
    driver_short = None
    driver_surname = None
    m = re.search(
        r"\d{2}\.\d{2}\.\d{2}\s+\d{2}\.\d{2}\.\d{2}\s+([А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.)",
        text,
    )
    if m:
        driver_short = m.group(1).strip()
        driver_surname = driver_short.split()[0]

    # Поставщик: «На получение от ООО "СтройКоммерц"»
    m = re.search(r"На получение от\s+(.+?)(?:\n|$)", text)
    supplier_raw = m.group(1).strip() if m else None

    # Товар: «1 <название> <ед> <кол-во прописью>»
    m = re.search(
        r"^1\s+(.+?)\s+(мешок|мешк[аи]|кг|шт|рулон[аы]?|тонн[аы]?|м[23]?|л|пач?к[аы]?)\s+"
        r"([А-ЯЁ][а-яё]+(?:\s+[а-яё]+)*)\s*$",
        text,
        re.MULTILINE,
    )
    item_name = item_unit = item_qty_propisi = None
    item_qty_int = None
    if m:
        item_name = m.group(1).strip()
        item_unit = m.group(2).strip()
        item_qty_propisi = m.group(3).strip()
        item_qty_int = propisi_to_int(item_qty_propisi)

    return DoverennostData(
        number=number,
        date_str=date_str,
        driver_short=driver_short,
        driver_surname=driver_surname,
        supplier_raw=supplier_raw,
        item_name=item_name,
        item_unit=item_unit,
        item_qty_propisi=item_qty_propisi,
        item_qty_int=item_qty_int,
    )


def _find_signature_page_plumber(pdf) -> object | None:
    """Возвращает страницу pdfplumber где есть «М. П.» (блок подписей)."""
    for page in pdf.pages:
        text = page.extract_text() or ""
        if "М. П." in text or "М.П." in text:
            return page
    return None


# -------------------- НАЛОЖЕНИЕ ФАКСИМИЛЕ --------------------

def stamp(src_path: str | Path, dst_path: str | Path) -> None:
    """Накладывает печать и подпись на PDF, сохраняет в dst_path.

    Якоря находятся динамически через page.search_for() — поэтому при
    любом количестве строк номенклатуры печать встанет в правильное место.
    """
    doc = fitz.open(str(src_path))
    page = _find_signature_page_fitz(doc)
    if page is None:
        raise ValueError("Не найден блок подписей (нет 'М. П.' / 'Руководитель')")

    mp_rects = page.search_for("М. П.")
    ruk_rects = page.search_for("Руководитель")
    if not mp_rects or not ruk_rects:
        raise ValueError("Якоря М.П./Руководитель не найдены на странице с подписями")

    mp = mp_rects[0]
    ruk = ruk_rects[0]

    mp_center_x = mp.x0 + mp.width / 2
    mp_center_y = mp.y0 + mp.height / 2

    # === Печать ===
    diam = PECHAT_DIAMETER_MM * MM2PT
    cx = mp_center_x - PECHAT_OFFSET_LEFT_MM * MM2PT
    cy = mp_center_y
    page.insert_image(
        fitz.Rect(cx - diam / 2, cy - diam / 2, cx + diam / 2, cy + diam / 2),
        filename=PECHAT_PATH,
        keep_proportion=True,
        overlay=True,
    )

    # === Подпись ===
    # Нижний край подписи — на линии подписи (= низ слова «Руководитель» + 1pt)
    ruk_line_y = ruk.y1 + 1
    px = ruk.x1 + PODPIS_GAP_FROM_RUKOVODITEL_PT
    py = ruk_line_y - PODPIS_HEIGHT_PT
    page.insert_image(
        fitz.Rect(px, py, px + PODPIS_WIDTH_PT, py + PODPIS_HEIGHT_PT),
        filename=PODPIS_PATH,
        keep_proportion=True,
        overlay=True,
    )

    # Компрессия: garbage сборка, deflate-сжатие потоков, чистка
    doc.save(str(dst_path), garbage=4, deflate=True, clean=True)
    doc.close()


def _find_signature_page_fitz(doc):
    """Возвращает fitz.Page где есть «М. П.» (обычно последняя)."""
    for page in doc:
        if page.search_for("М. П."):
            return page
    return None
