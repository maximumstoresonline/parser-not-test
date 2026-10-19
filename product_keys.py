"""Ключ товара в сборе: страна + ASIN.

Один ASIN продаётся на нескольких рынках, и BSR, цена и наличие у него на каждом рынке свои. Раньше сбор
хранил товары по голому ASIN: ASIN из DE и FR запрашивался один раз (на рынке, который последним попал в
карту ASIN -> домен), и эти данные расходились по строкам обеих стран. Проверка 19.10.2026: так было у 61
из 76 ASIN, стоящих в нескольких странах, — 295 из 622 пар.

Ключ — строка "DE:B00L9OB0PA". Голый ASIN по-прежнему допустим (TEST_ASIN, старый режим через лист
Competitors): для него домен берётся из карты ASIN -> домен, как раньше.
"""
from typing import Optional, Tuple

SEPARATOR = ":"

MARKETPLACE_DOMAINS = {
    "US": "com",
    "CA": "ca",
    "UK": "co.uk",
    "DE": "de",
    "FR": "fr",
    "ES": "es",
    "IT": "it",
    "MX": "com.mx",
    "JP": "co.jp",
    "AU": "com.au",
}


def product_key(marketplace: Optional[str], asin: str) -> str:
    market = str(marketplace or "").strip().upper()
    asin = str(asin or "").strip().upper()
    return f"{market}{SEPARATOR}{asin}" if market else asin


def split_key(key: str) -> Tuple[Optional[str], str]:
    """("DE", "B00L9OB0PA") для "DE:B00L9OB0PA"; (None, asin) для голого ASIN."""
    text = str(key or "").strip()
    if SEPARATOR in text:
        market, asin = text.split(SEPARATOR, 1)
        return market.strip().upper() or None, asin.strip().upper()
    return None, text.upper()


def asin_of(key: str) -> str:
    return split_key(key)[1]


def domain_of(key: str) -> Optional[str]:
    """Домен amazon из ключа; None — у голого ASIN или неизвестного рынка."""
    market = split_key(key)[0]
    return MARKETPLACE_DOMAINS.get(market) if market else None
