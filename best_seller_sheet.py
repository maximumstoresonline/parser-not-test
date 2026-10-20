"""Запись найденных подкатегорий в Google-таблицу гипотез «Получение bs за счет смены под категории» (SOP-AMZ-001,
раздел 7). Пишем на отдельный лист TAB, а не на основной лист Артура: структура из 9 столбцов ещё не утверждена
(пробел П3 в SOP), основной лист остаётся как есть. Без Streamlit; доступ к Google подменяется в тестах."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Mapping, Optional, Sequence, Tuple

import best_seller
import pairs_store

SPREADSHEET_ID = "1_v8TWbUK0zfjzX6O6hP9L9n8QndhnhSgGNg_Az5Wal8"  # в SOP распознан с кадра, сверен 21.10.2026
TAB = "Best Seller (авто)"
HEADERS = ["Ссылка на подкатегорию", "Товар-кандидат", "ASIN", "Родительская категория", "Релевантность",
           "BSR лидера", "Наш целевой BSR", "Приоритет", "Дата проверки", "Подкатегория", "Страна"]


class SheetError(RuntimeError):
    """Запись не удалась; текст безопасен для показа (без деталей драйвера и ключей)."""


@dataclass(frozen=True)
class WriteResult:
    written: int
    duplicates: int
    no_link: int


def _text(value: object) -> str:
    """Строка для ячейки: значения, похожие на формулу, не должны выполняться как формулы."""
    text = str(value or "").strip()
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def subcategory_url(market: str, dept: Optional[str], node: str) -> Optional[str]:
    domain = pairs_store.DOMAIN_BY_MARKET.get(market)
    if not (domain and dept and str(node).isdigit() and dept.replace("-", "").isalnum()):
        return None
    return f"https://www.amazon.{domain}/gp/bestsellers/{dept}/{node}"


def build_rows(items: Sequence[Mapping], now: Optional[datetime] = None) -> Tuple[List[list], int]:
    """Строки листа и число подкатегорий без ссылки (нет раздела Best Sellers в кэше): их не пишем."""
    from zoneinfo import ZoneInfo

    day = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Europe/Kyiv")).strftime("%d.%m.%Y")
    rows, no_link = [], 0
    for item in items:
        link = subcategory_url(item["market"], item.get("dept"), item["node"])
        if not link:
            no_link += 1
            continue
        rows.append([link, _text(item.get("title"))[:120], item["asin"], _text(item.get("path")),
                     best_seller.RELEVANCE_LABELS.get(item.get("relevance"), ""), item.get("leader_bsr") or "",
                     item.get("target_bsr") or "", item.get("priority") or "", day, _text(item.get("name")),
                     item["market"]])
    return rows, no_link


def write_rows(spreadsheet, rows: Sequence[list]) -> Tuple[int, int]:
    """Добавляет строки на лист TAB (создаёт его с шапкой). Пара (ASIN, ссылка) уже на листе — пропускается.
    Возвращает (записано, дубликатов)."""
    import gspread

    try:
        sheet = spreadsheet.worksheet(TAB)
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=TAB, rows=1000, cols=len(HEADERS))
    existing = sheet.get_all_values()
    if not existing:
        sheet.append_row(HEADERS, value_input_option="RAW")
        existing = [HEADERS]
    have = {(r[2], r[0]) for r in existing[1:] if len(r) > 2}
    fresh = []
    for row in rows:
        key = (row[2], row[0])
        if key not in have:
            have.add(key)
            fresh.append(row)
    if fresh:
        sheet.append_rows(fresh, value_input_option="USER_ENTERED")
    return len(fresh), len(rows) - len(fresh)


def make_writer(info: Mapping, sheet_id: str = SPREADSHEET_ID,
                client_factory: Optional[Callable] = None) -> Callable[[Sequence[Mapping]], WriteResult]:
    """Функция записи для дашборда. info — ключ сервисного аккаунта Google из секретов (в код и логи не попадает)."""

    def write(items: Sequence[Mapping]) -> WriteResult:
        rows, no_link = build_rows(items)
        if not rows:
            return WriteResult(0, 0, no_link)
        try:
            import gspread

            client = (client_factory or gspread.service_account_from_dict)(dict(info))
            written, duplicates = write_rows(client.open_by_key(sheet_id), rows)
        except SheetError:
            raise
        except Exception as exc:  # noqa: BLE001 — текст ошибки драйвера не показываем
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (403, 404):
                raise SheetError("Нет доступа к таблице: добавьте сервисный аккаунт Google в редакторы таблицы "
                                 "«Получение bs за счёт смены под категории».") from None
            raise SheetError(f"Не удалось записать в Google-таблицу ({type(exc).__name__}).") from None
        return WriteResult(written, duplicates, no_link)

    return write
