"""Список поставщиков и их email.

Порядок в SUPPLIERS_ORDER задаёт расположение кнопок в Telegram-клавиатуре
(2 кнопки в ряд). Менять только согласовав с Денисом.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Supplier:
    key: str          # короткий ID для callback_data (≤16 байт)
    name: str         # как показывать на кнопке и в превью
    email: str
    inn: str
    note: str = ""    # пометка о товаре, идёт после названия в кнопке


# Все 8 поставщиков
SUPPLIERS = {
    "stroykommerz":  Supplier("stroykommerz",  'ООО "СтройКоммерц"',   "stroykommerz@yandex.ru",   "5261080507", "цемент"),
    "ledystroy":     Supplier("ledystroy",     'ООО "Леди Строй"',     "ledystroy@ledystroy.ru",   "5245004555", "цемент"),
    "goth":          Supplier("goth",          'ООО "ГОТХ"',           "terminal-td@yandex.ru",    "5256003955", "песок"),
    "optstroy":      Supplier("optstroy",      'ООО "Опт Строй НН"',   "k-s-nn@yandex.ru",         "5259121442", "керамзит"),
    "interstroy":    Supplier("interstroy",    'ООО "Интерстрой"',     "2280406@gmail.com",        "5262116770", "сетка"),
    "selena":        Supplier("selena",        'ООО "Селена"',         "info@selenann.ru",         "5258092502", ""),
    "izoflex":       Supplier("izoflex",       'ООО "Изофлекс"',       "ipe@izoflex.org",          "5257220007", ""),
    "artstroy":      Supplier("artstroy",      'ООО "АРТ Строй"',      "mitronina_ma@arttn.ru",    "5260409115", "изофлекс"),
}

# Порядок кнопок: 1, 4, 8, 2, 3, 7, 6, 5 (по частоте использования, как просил Денис)
SUPPLIERS_ORDER = [
    "stroykommerz",  # 1 цемент
    "ledystroy",     # 4 цемент
    "goth",          # 8 песок
    "optstroy",      # 2 керамзит
    "interstroy",    # 3 сетка
    "selena",        # 7
    "izoflex",       # 6
    "artstroy",      # 5 изофлекс
]


def get(key: str) -> Supplier | None:
    return SUPPLIERS.get(key)


def button_label(key: str) -> str:
    """Текст кнопки: «СтройКоммерц (цемент)» или «Селена»."""
    s = SUPPLIERS[key]
    short_name = s.name.replace('ООО "', "").rstrip('"')
    return f"{short_name} ({s.note})" if s.note else short_name


def match_by_pdf_supplier(pdf_supplier_text: str) -> str | None:
    """Сопоставить «На получение от ...» из PDF с одним из наших поставщиков.

    Возвращает key или None если не нашли (тогда менеджер выберет вручную).
    """
    if not pdf_supplier_text:
        return None
    norm = pdf_supplier_text.lower().replace('"', "").replace("«", "").replace("»", "")
    for key, s in SUPPLIERS.items():
        s_norm = s.name.lower().replace('"', "").replace("ооо ", "")
        if s_norm in norm:
            return key
    return None
