"""Воркер поиска подкатегорий Best Seller по очереди (запускается в GitHub Actions, best_seller_batch.yml).

Берёт из best_seller.searches строки в статусе queued по одной, ищет подкатегории (best_seller_search.search) и пишет
результат в ту же строку. Стоп по лимиту BS_BATCH_LIMIT (по умолчанию 25 ASIN за прогон) — так один запуск не сожжёт
кредиты ScrapingDog; остаток остаётся в очереди, его можно запустить ещё раз. Токены берутся только из окружения и
нигде не печатаются.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Optional

import best_seller_store as store

DEFAULT_LIMIT = 25


def limit_from_env(raw: Optional[str]) -> int:
    return int(raw) if raw and raw.strip().isdigit() and int(raw) > 0 else DEFAULT_LIMIT


def process_one(connect, job: dict, *, search: Callable, fetch_page: Callable) -> str:
    """Один поиск из очереди; любая ошибка превращается в статус error этой строки и не валит остальные."""
    import best_seller_search as bss

    market, asin = job["market"], job["asin"]
    try:
        result = search(
            asin, market, cache=store.DbNodeCache(connect), branches=bss.BRANCHES_BY_MARKET.get(market),
            keep=bss.keyword_filter,
            fetch_product=lambda a, d: bss.product_from_page(fetch_page(bss.product_url(market, a))),
            fetch_page=fetch_page)
        store.finish_search(connect, job["id"], result)
        return result.status
    except Exception as exc:  # noqa: BLE001 — текст ошибки драйвера может содержать параметры подключения
        try:
            store.fail_search(connect, job["id"], f"Сбой поиска ({type(exc).__name__})")
        except Exception:  # noqa: BLE001
            pass
        return "error"


def run(connect, *, search: Callable, fetch_page: Callable, limit: int = DEFAULT_LIMIT, log=print) -> dict:
    counts: dict = {}
    store.recover_stale(connect)
    for _ in range(limit):
        job = store.claim_next(connect)
        if job is None:
            break
        status = process_one(connect, job, search=search, fetch_page=fetch_page)
        counts[status] = counts.get(status, 0) + 1
        log(f"{job['market']} {job['asin']}: {status}")
    log("Итог: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "очередь пуста"))
    return counts


def main() -> int:
    import psycopg2

    import best_seller_search as bss
    import scraping

    token = os.environ.get("SCRAPINGDOG_TOKEN", "")
    url = os.environ.get("DATABASE_URL", "")
    if not token or not url:
        print("Нужны переменные SCRAPINGDOG_TOKEN и DATABASE_URL (секреты репозитория).", file=sys.stderr)
        return 1
    counts = run(lambda: psycopg2.connect(url, connect_timeout=10), search=bss.search,
                 fetch_page=lambda u: scraping.scrape_page(u, token=token),
                 limit=limit_from_env(os.environ.get("BS_BATCH_LIMIT")))
    return 0 if counts.get("error", 0) < sum(counts.values()) or not counts else 1


if __name__ == "__main__":
    raise SystemExit(main())
