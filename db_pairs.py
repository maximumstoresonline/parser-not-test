"""
Чтение пар "наш ASIN - конкурент" напрямую из Postgres (bsr_radar.competitor_pairs)
как альтернатива чтению листа Competitors в Google Sheets.

Используется парсером только когда задана переменная окружения PAIR_SOURCE=database
(по умолчанию парсер по-прежнему читает пары из Sheets - см. run_parser() в
parser_not_test.py). Google Таблица остаётся источником записи результатов
независимо от этого флага - здесь только чтение списка пар для сбора.
"""

from __future__ import annotations

import os
from typing import Dict, List

import psycopg2

from product_keys import product_key
from sheets import _amazon_domain


def _connect():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def load_active_competitor_pairs_from_db() -> List[Dict[str, str]]:
    """Тот же формат, что sheets.load_active_competitor_pairs():
    [{"marketplace", "our_product", "our_asin", "competitor", "comp_asin"}, ...]
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT marketplace, our_asin, our_product, comp_asin, competitor_name
                FROM bsr_radar.competitor_pairs
                WHERE active = TRUE
                ORDER BY marketplace, our_asin
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "marketplace": marketplace,
            "our_asin": our_asin,
            "our_product": our_product or "",
            "competitor": competitor_name or "",
            "comp_asin": comp_asin,
        }
        for marketplace, our_asin, our_product, comp_asin, competitor_name in rows
    ]


def build_asin_domain_map_from_db() -> Dict[str, str]:
    """Тот же формат, что sheets.build_asin_domain_map(): {ASIN: домен amazon}."""
    domain_map: Dict[str, str] = {}
    for pair in load_active_competitor_pairs_from_db():
        domain = _amazon_domain(pair["marketplace"])
        if pair["our_asin"]:
            domain_map[pair["our_asin"]] = domain
        if pair["comp_asin"]:
            domain_map[pair["comp_asin"]] = domain
    return domain_map


def asins_from_pairs(pairs: List[Dict[str, str]], scope: str = "all") -> List[str]:
    """Тот же порядок/дедупликация по смыслу, что sheets.load_asins_from_config().

    scope сужает список для частичного сбора: "ours" — только наши товары, "competitors" —
    только конкуренты, "all" (по умолчанию) — все, как раньше. "Наш" — ASIN, который хотя бы в
    одной активной паре стоит как our_asin. Сбор идёт по product_keys_from_pairs (ASIN отдельно на
    каждом рынке); здесь — голые ASIN без страны.
    """
    if scope not in ("all", "ours", "competitors"):
        scope = "all"
    our_asins = {pair["our_asin"] for pair in pairs if pair["our_asin"]}
    seen: Dict[str, None] = {}
    for pair in pairs:
        for asin in (pair["our_asin"], pair["comp_asin"]):
            if not asin:
                continue
            if scope == "ours" and asin not in our_asins:
                continue
            if scope == "competitors" and asin in our_asins:
                continue
            seen[asin] = None
    return list(seen.keys())


def product_keys_from_pairs(pairs: List[Dict[str, str]], scope: str = "all") -> List[str]:
    """Что собирать: ключи «страна:ASIN» (product_keys) в порядке пар, без повторов.

    В отличие от asins_from_pairs, ASIN из пар разных стран даёт отдельный ключ на каждую страну:
    у него на каждом рынке свои BSR и цена. «Наш» — ключ, который хотя бы в одной активной паре своего
    рынка стоит как our_asin (run_control.positions_summary считает по тому же правилу)."""
    if scope not in ("all", "ours", "competitors"):
        scope = "all"
    ours = {product_key(pair["marketplace"], pair["our_asin"]) for pair in pairs if pair["our_asin"]}
    seen: Dict[str, None] = {}
    for pair in pairs:
        for asin in (pair["our_asin"], pair["comp_asin"]):
            if not asin:
                continue
            key = product_key(pair["marketplace"], asin)
            if scope == "ours" and key not in ours:
                continue
            if scope == "competitors" and key in ours:
                continue
            seen[key] = None
    return list(seen.keys())
