"""Список ASIN раздела «Best Seller подкатегории» — своя схема best_seller в базе дашборда, отдельно от таблиц
мониторинга BSR (bsr_radar): раздел независимый, но отдельную базу не заводим (решения владельца 16.10.2026).
Схема — best_seller_db/001_watchlist.sql.

Ключ строки — (страна, ASIN). Удаление — отключение, его можно вернуть. Каждое изменение пишется в журнал
best_seller.asin_changes в той же транзакции. Без Streamlit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import access
import dbutil
from dbutil import Connect

MAX_BATCH = 500
_WHAT = "Операция со списком ASIN Best Seller"
Key = Tuple[str, str]  # (страна, ASIN)


class BestSellerStoreError(RuntimeError):
    """База Best Seller недоступна или запись не подтверждена (без деталей драйвера)."""


@dataclass
class AddPlan:
    to_add: List[Key] = field(default_factory=list)
    to_enable: List[Key] = field(default_factory=list)
    already: List[Key] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def changes(self) -> int:
        return len(self.to_add) + len(self.to_enable)


def _run(connect: Connect, sql: str, params: tuple = (), *, fetch: bool = False):
    return dbutil.run_sql(connect, sql, params, fetch=fetch, error=BestSellerStoreError, what=_WHAT)


def table_exists(connect: Connect) -> bool:
    return bool(_run(connect, "SELECT to_regclass('best_seller.asins') IS NOT NULL;", fetch=True)[0][0])


def list_asins(connect: Connect) -> List[dict]:
    rows = _run(connect, "SELECT marketplace, asin, active, added_by, updated_at FROM best_seller.asins "
                         "ORDER BY marketplace, asin;", fetch=True)
    return [{"marketplace": m, "asin": a, "active": act, "added_by": by, "updated_at": at} for m, a, act, by, at in rows]


def plan_add(existing: Sequence[dict], keys: Sequence[Key]) -> AddPlan:
    """Что сделает «Добавить»: новые — добавить, отключённые — вернуть, активные — уже в списке."""
    plan = AddPlan()
    unique = list(dict.fromkeys(keys))
    if not unique:
        plan.errors.append("Вставьте ссылки на товары Amazon.")
        return plan
    if len(unique) > MAX_BATCH:
        plan.errors.append(f"За раз можно добавить не больше {MAX_BATCH} ASIN.")
        return plan
    state: Dict[Key, bool] = {(row["marketplace"], row["asin"]): row["active"] for row in existing}
    for key in unique:
        if key not in state:
            plan.to_add.append(key)
        elif state[key]:
            plan.already.append(key)
        else:
            plan.to_enable.append(key)
    return plan


_UPSERT_SQL = """
WITH input AS (
    SELECT * FROM unnest(%s::text[], %s::text[]) AS t(marketplace, asin)
), changed AS (
    INSERT INTO best_seller.asins AS a (marketplace, asin, active, added_by)
    SELECT marketplace, asin, TRUE, %s FROM input
    ON CONFLICT (marketplace, asin) DO UPDATE SET active = TRUE, updated_at = now()
    WHERE NOT a.active
    RETURNING a.marketplace, a.asin, (xmax = 0) AS inserted
)
INSERT INTO best_seller.asin_changes (actor, action, marketplace, asin)
SELECT %s, CASE WHEN inserted THEN 'add' ELSE 'enable' END, marketplace, asin FROM changed
RETURNING action;
"""

_SET_ACTIVE_SQL = """
WITH input AS (
    SELECT * FROM unnest(%s::text[], %s::text[]) AS t(marketplace, asin)
), changed AS (
    UPDATE best_seller.asins AS a SET active = %s, updated_at = now()
    FROM input i
    WHERE a.marketplace = i.marketplace AND a.asin = i.asin AND a.active IS DISTINCT FROM %s
    RETURNING a.marketplace, a.asin
)
INSERT INTO best_seller.asin_changes (actor, action, marketplace, asin)
SELECT %s, %s, marketplace, asin FROM changed
RETURNING 1;
"""


def add(connect: Connect, keys: Sequence[Key], *, actor_role: Optional[str], actor: str) -> Dict[str, int]:
    access.require_role(actor_role, access.ROLE_EDITOR)
    unique = list(dict.fromkeys(keys))
    if not unique:
        return {"add": 0, "enable": 0}
    if len(unique) > MAX_BATCH:
        raise ValueError(f"За раз можно добавить не больше {MAX_BATCH} ASIN.")
    rows = _run(connect, _UPSERT_SQL, ([m for m, _ in unique], [a for _, a in unique], actor, actor), fetch=True)
    return {"add": sum(1 for (action,) in rows if action == "add"),
            "enable": sum(1 for (action,) in rows if action == "enable")}


def set_active(connect: Connect, keys: Sequence[Key], active: bool, *, actor_role: Optional[str], actor: str) -> int:
    """Отключает (False) или возвращает (True) ASIN по ключам (страна, ASIN). Возвращает число изменённых."""
    access.require_role(actor_role, access.ROLE_EDITOR)
    unique = list(dict.fromkeys(tuple(key) for key in keys))
    if not unique:
        return 0
    if len(unique) > MAX_BATCH:
        raise ValueError(f"За раз можно изменить не больше {MAX_BATCH} ASIN.")
    action = "enable" if active else "disable"
    rows = _run(connect, _SET_ACTIVE_SQL,
                ([m for m, _ in unique], [a for _, a in unique], active, active, actor, action), fetch=True)
    return len(rows)


# --- поиск подкатегорий (best_seller_db/002_search.sql) ------------------------------------------------------

def search_tables_exist(connect: Connect) -> bool:
    return bool(_run(connect, "SELECT to_regclass('best_seller.searches') IS NOT NULL "
                              "AND to_regclass('best_seller.node_cache') IS NOT NULL;", fetch=True)[0][0])


class DbNodeCache:
    """Кэш подкатегорий на неделю в best_seller.node_cache (тот же интерфейс, что best_seller_search.MemoryCache)."""

    def __init__(self, connect: Connect) -> None:
        self.connect = connect

    def get(self, market: str, node: str):
        import best_seller_search as bss

        rows = _run(self.connect, "SELECT node, dept, name, leader_asin, leader_bsr, leader_badge, item_count, top, "
                                  "linked, fetched_at FROM best_seller.node_cache WHERE marketplace = %s AND node = %s;",
                    (market, node), fetch=True)
        if not rows:
            return None
        node, dept, name, leader, bsr, badge, count, top, linked, fetched = rows[0]
        return bss.NodeInfo(node=node, dept=dept, name=name, leader_asin=leader, leader_bsr=bsr, leader_badge=badge,
                            item_count=count, top=list(top or []), fetched_at=fetched,
                            linked=[bss.Subcategory(x["node"], x.get("dept", ""), x.get("name", ""))
                                    for x in (linked or []) if isinstance(x, dict) and x.get("node")])

    def put(self, market: str, info) -> None:
        linked = [{"node": x.node, "dept": x.dept, "name": x.name} for x in info.linked]
        _run(self.connect, """
            INSERT INTO best_seller.node_cache AS c (marketplace, node, dept, name, leader_asin, leader_bsr,
                                                    leader_badge, item_count, top, linked, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
            ON CONFLICT (marketplace, node) DO UPDATE SET dept = EXCLUDED.dept, name = EXCLUDED.name,
                leader_asin = EXCLUDED.leader_asin, leader_bsr = EXCLUDED.leader_bsr,
                leader_badge = EXCLUDED.leader_badge, item_count = EXCLUDED.item_count, top = EXCLUDED.top,
                linked = EXCLUDED.linked, fetched_at = EXCLUDED.fetched_at;
            """, (market, info.node, info.dept, info.name, info.leader_asin, info.leader_bsr, info.leader_badge,
                  info.item_count, json.dumps(info.top, ensure_ascii=False), json.dumps(linked, ensure_ascii=False),
                  info.fetched_at))


_SAVE_SEARCH_SQL = """
WITH s AS (
    INSERT INTO best_seller.searches (marketplace, asin, actor, title, our_bsr, target_bsr, status, error, own,
                                      requests, finished_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, now())
    RETURNING id
), r AS (
    INSERT INTO best_seller.search_results (search_id, node, name, path, leader_bsr, k1, k2, k3, k4, relevance,
                                            status, priority, reason)
    SELECT s.id, t.* FROM s, unnest(%s::text[], %s::text[], %s::text[], %s::int[], %s::bool[], %s::bool[],
                                    %s::bool[], %s::bool[], %s::text[], %s::text[], %s::text[], %s::text[]) AS t
)
SELECT id FROM s;
"""


def _result_columns(result) -> tuple:
    cs = result.candidates
    return (
        [c.sub.node for c in cs], [c.sub.name[:300] for c in cs], [c.sub.path[:500] for c in cs],
        [c.info.leader_bsr if c.info else None for c in cs],
        [c.verdict.k1 for c in cs], [c.verdict.k2 for c in cs], [c.verdict.k3 for c in cs], [c.verdict.k4 for c in cs],
        [c.relevance for c in cs], [c.verdict.status for c in cs], [c.verdict.priority for c in cs],
        [c.verdict.reason for c in cs],
    )


def _own_json(result) -> str:
    return json.dumps([{"node": x.node, "name": x.name, "rank": x.rank} for x in result.own], ensure_ascii=False)


def save_search(connect: Connect, result, *, actor_role: Optional[str], actor: str) -> int:
    """Поиск и решения по подкатегориям — одним запросом (одна транзакция). Возвращает id поиска."""
    access.require_role(actor_role, access.ROLE_EDITOR)
    rows = _run(connect, _SAVE_SEARCH_SQL,
                (result.market, result.asin, actor, result.title[:500], result.our_bsr, result.target_bsr,
                 result.status, result.error[:500], _own_json(result), result.requests, *_result_columns(result)),
                fetch=True)
    return rows[0][0]


# --- очередь поиска по списку (выполняет best_seller_batch.py в GitHub Actions) ------------------------------------

_ENQUEUE_SQL = """
INSERT INTO best_seller.searches (marketplace, asin, actor, status, target_bsr)
SELECT a.marketplace, a.asin, %s, 'queued', a.target_bsr FROM best_seller.asins a
WHERE a.active
  AND NOT EXISTS (SELECT 1 FROM best_seller.searches s WHERE s.marketplace = a.marketplace AND s.asin = a.asin
                  AND s.status IN ('queued', 'running'))
  AND (NOT %s OR NOT EXISTS (SELECT 1 FROM best_seller.searches s WHERE s.marketplace = a.marketplace
                             AND s.asin = a.asin AND s.status IN ('done', 'not_needed')))
RETURNING id;
"""


MAX_TARGET = 10_000_000


def ensure_target_column(connect: Connect) -> None:
    """Столбец цели у списка ASIN (best_seller_db/003_target.sql) — применяется сам, повторно безопасно."""
    _run(connect, "ALTER TABLE best_seller.asins ADD COLUMN IF NOT EXISTS target_bsr INTEGER "
                  "CHECK (target_bsr IS NULL OR target_bsr > 0);")


def set_targets(connect: Connect, targets: Dict[Key, Optional[int]], *, actor_role: Optional[str]) -> int:
    """Целевой BSR по (страна, ASIN); None — снять цель (будет текущий BSR товара). Возвращает число строк."""
    access.require_role(actor_role, access.ROLE_EDITOR)
    for value in targets.values():
        if value is not None and not (0 < int(value) <= MAX_TARGET):
            raise BestSellerStoreError(f"Целевой BSR должен быть от 1 до {MAX_TARGET:,}.".replace(",", " "))
    if not targets:
        return 0
    keys = list(targets)
    rows = _run(connect, """
        UPDATE best_seller.asins a SET target_bsr = t.target
        FROM unnest(%s::text[], %s::text[], %s::int[]) AS t(market, asin, target)
        WHERE a.marketplace = t.market AND a.asin = t.asin AND a.target_bsr IS DISTINCT FROM t.target
        RETURNING a.asin;
        """, ([k[0] for k in keys], [k[1] for k in keys], [targets[k] for k in keys]), fetch=True)
    return len(rows)


def enqueue_searches(connect: Connect, *, actor_role: Optional[str], actor: str, only_new: bool = True) -> int:
    """Ставит в очередь поиск по активным ASIN списка. Уже стоящие в очереди или идущие не дублируются;
    only_new — пропустить те, по которым уже есть готовый результат (не тратить кредиты повторно)."""
    access.require_role(actor_role, access.ROLE_EDITOR)
    return len(_run(connect, _ENQUEUE_SQL, (actor, only_new), fetch=True))


def claim_next(connect: Connect) -> Optional[dict]:
    """Берёт следующий поиск из очереди (FOR UPDATE SKIP LOCKED: два воркера не возьмут один и тот же)."""
    rows = _run(connect, """
        UPDATE best_seller.searches SET status = 'running', started_at = now()
        WHERE id = (SELECT id FROM best_seller.searches WHERE status = 'queued' ORDER BY id
                    FOR UPDATE SKIP LOCKED LIMIT 1)
        RETURNING id, marketplace, asin, actor, target_bsr;
        """, fetch=True)
    if not rows:
        return None
    sid, market, asin, actor, target = rows[0]
    return {"id": sid, "market": market, "asin": asin, "actor": actor, "target": target}


def recover_stale(connect: Connect, minutes: int = 90) -> int:
    """Поиски, застрявшие в «идёт» (воркер оборвался), помечаются ошибкой, чтобы их можно было повторить."""
    return len(_run(connect, """
        UPDATE best_seller.searches SET status = 'error', error = 'Прервано: поиск не завершился', finished_at = now()
        WHERE status = 'running' AND started_at < now() - make_interval(mins => %s) RETURNING id;
        """, (minutes,), fetch=True))


def finish_search(connect: Connect, search_id: int, result) -> None:
    """Записывает итог поиска из очереди: строка searches обновляется, решения по подкатегориям добавляются."""
    dbutil.run_many(connect, [
        ("""UPDATE best_seller.searches SET title = %s, our_bsr = %s, target_bsr = %s, status = %s, error = %s,
                own = %s::jsonb, requests = %s, finished_at = now() WHERE id = %s;""",
         (result.title[:500], result.our_bsr, result.target_bsr, result.status, result.error[:500],
          _own_json(result), result.requests, search_id), False),
        ("""INSERT INTO best_seller.search_results (search_id, node, name, path, leader_bsr, k1, k2, k3, k4,
                                                   relevance, status, priority, reason)
            SELECT %s, t.* FROM unnest(%s::text[], %s::text[], %s::text[], %s::int[], %s::bool[], %s::bool[],
                                       %s::bool[], %s::bool[], %s::text[], %s::text[], %s::text[], %s::text[]) AS t
            ON CONFLICT (search_id, node) DO NOTHING;""",
         (search_id, *_result_columns(result)), False),
    ], error=BestSellerStoreError, what=_WHAT)


def fail_search(connect: Connect, search_id: int, message: str) -> None:
    _run(connect, "UPDATE best_seller.searches SET status = 'error', error = %s, finished_at = now() WHERE id = %s;",
         (message[:500], search_id))


def recent_searches(connect: Connect, limit: int = 10) -> List[dict]:
    """История: последние поиски, по одному (последнему) на страну и ASIN, свежие сверху."""
    rows = _run(connect, """
        SELECT marketplace, asin, title, status, our_bsr, started_at, finished_at, suitable, pending FROM (
            SELECT DISTINCT ON (s.marketplace, s.asin) s.marketplace, s.asin, s.title, s.status, s.our_bsr,
                   s.started_at, s.finished_at, s.id,
                   (SELECT count(*) FROM best_seller.search_results r WHERE r.search_id = s.id
                    AND r.status = 'suitable') AS suitable,
                   (SELECT count(*) FROM best_seller.search_results r WHERE r.search_id = s.id
                    AND r.status = 'pending') AS pending
            FROM best_seller.searches s ORDER BY s.marketplace, s.asin, s.started_at DESC, s.id DESC
        ) t ORDER BY COALESCE(finished_at, started_at) DESC, id DESC LIMIT %s;
        """, (max(1, min(int(limit), 50)),), fetch=True)
    keys = ("market", "asin", "title", "status", "our_bsr", "started_at", "finished_at", "suitable", "pending")
    return [dict(zip(keys, row)) for row in rows]


def batch_rows(connect: Connect) -> List[dict]:
    """Активные ASIN списка с последним поиском по каждому и лучшей подходящей подкатегорией."""
    rows = _run(connect, """
        SELECT a.marketplace, a.asin, s.status, s.our_bsr, s.title, s.error, s.own,
               (SELECT count(*) FROM best_seller.search_results r WHERE r.search_id = s.id AND r.status = 'suitable'),
               (SELECT count(*) FROM best_seller.search_results r WHERE r.search_id = s.id AND r.status = 'pending'),
               b.name, b.leader_bsr, b.priority, s.started_at, s.finished_at, s.requests, s.actor, a.target_bsr
        FROM best_seller.asins a
        LEFT JOIN LATERAL (SELECT * FROM best_seller.searches x WHERE x.marketplace = a.marketplace AND x.asin = a.asin
                           ORDER BY x.started_at DESC, x.id DESC LIMIT 1) s ON TRUE
        LEFT JOIN LATERAL (SELECT r.name, r.leader_bsr, r.priority FROM best_seller.search_results r
                           WHERE r.search_id = s.id AND r.status = 'suitable'
                           ORDER BY CASE r.priority WHEN 'высокий' THEN 0 WHEN 'низкий' THEN 1 ELSE 2 END,
                                    r.leader_bsr DESC NULLS LAST LIMIT 1) b ON TRUE
        WHERE a.active ORDER BY a.marketplace, a.asin;
        """, fetch=True)
    keys = ("market", "asin", "status", "our_bsr", "title", "error", "own", "suitable", "pending", "best", "leader_bsr",
            "priority", "started_at", "finished_at", "requests", "actor", "target")
    return [dict(zip(keys, row)) for row in rows]


def latest_search(connect: Connect, market: str, asin: str) -> Optional[dict]:
    """Последний поиск по стране и ASIN с решениями по подкатегориям (подходящие сверху) или None."""
    rows = _run(connect, """
        SELECT id, title, our_bsr, target_bsr, status, error, own, requests, actor, finished_at
        FROM best_seller.searches WHERE marketplace = %s AND asin = %s ORDER BY started_at DESC, id DESC LIMIT 1;
        """, (market, asin), fetch=True)
    if not rows:
        return None
    sid, title, our, target, status, error, own, requests, actor, finished = rows[0]
    # top — заголовки топа подкатегории из недельного кэша: по ним сотрудник оценивает К3 (SOP: «посмотреть, что
    # стоит в топе»). Берутся только там, где К3 вообще оценивается (К1 и К2 пройдены).
    results = _run(connect, """
        SELECT r.node, r.name, r.path, r.leader_bsr, r.k1, r.k2, r.k3, r.k4, r.relevance, r.status, r.priority,
               r.reason, CASE WHEN r.k1 AND r.k2 THEN c.top END, c.dept, c.leader_asin, r.written_to_sheet
        FROM best_seller.search_results r
        LEFT JOIN best_seller.node_cache c ON c.marketplace = %s AND c.node = r.node
        WHERE r.search_id = %s
        ORDER BY CASE r.status WHEN 'suitable' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END, r.leader_bsr DESC NULLS LAST;
        """, (market, sid), fetch=True)
    keys = ("node", "name", "path", "leader_bsr", "k1", "k2", "k3", "k4", "relevance", "status", "priority", "reason",
            "top", "dept", "leader_asin", "written")
    return {"id": sid, "market": market, "asin": asin, "title": title, "our_bsr": our, "target_bsr": target,
            "status": status, "error": error, "own": list(own or []), "requests": requests, "actor": actor,
            "finished_at": finished, "results": [dict(zip(keys, row)) for row in results]}


def mark_written(connect: Connect, search_id: int, nodes: Sequence[str], *, actor_role: Optional[str]) -> int:
    """Добавляет подкатегории в таблицу гипотез раздела (флаг written_to_sheet)."""
    access.require_role(actor_role, access.ROLE_EDITOR)
    return len(_run(connect, """
        UPDATE best_seller.search_results SET written_to_sheet = TRUE
        WHERE search_id = %s AND node = ANY(%s::text[]) RETURNING node;
        """, (search_id, list(nodes)), fetch=True))


def hypotheses(connect: Connect) -> List[dict]:
    """Таблица гипотез: подкатегории, добавленные в неё; по каждой паре (страна, ASIN, подкатегория) — из самого
    свежего поиска. Свежие сверху."""
    rows = _run(connect, """
        SELECT market, asin, title, node, name, path, dept, leader_bsr, target_bsr, relevance, priority, checked, actor
        FROM (
            SELECT DISTINCT ON (s.marketplace, s.asin, r.node) s.marketplace AS market, s.asin, s.title, r.node,
                   r.name, r.path, c.dept, r.leader_bsr, s.target_bsr, r.relevance, r.priority,
                   COALESCE(s.finished_at, s.started_at) AS checked, s.actor, s.id
            FROM best_seller.search_results r
            JOIN best_seller.searches s ON s.id = r.search_id
            LEFT JOIN best_seller.node_cache c ON c.marketplace = s.marketplace AND c.node = r.node
            WHERE r.written_to_sheet
            ORDER BY s.marketplace, s.asin, r.node, s.started_at DESC, s.id DESC
        ) t ORDER BY checked DESC, id DESC;
        """, fetch=True)
    keys = ("market", "asin", "title", "node", "name", "path", "dept", "leader_bsr", "target_bsr", "relevance",
            "priority", "checked", "actor")
    return [dict(zip(keys, row)) for row in rows]


def unwritten_high(connect: Connect) -> List[dict]:
    """Подходящие подкатегории с высоким приоритетом из последнего готового поиска по каждому активному ASIN,
    ещё не записанные в таблицу гипотез."""
    rows = _run(connect, """
        WITH latest AS (
            SELECT DISTINCT ON (marketplace, asin) * FROM best_seller.searches WHERE status = 'done'
            ORDER BY marketplace, asin, started_at DESC, id DESC)
        SELECT s.id, s.marketplace, s.asin, s.title, s.target_bsr, r.node, r.name, r.path, c.dept, r.leader_bsr,
               r.relevance, r.priority
        FROM latest s
        JOIN best_seller.asins a ON a.marketplace = s.marketplace AND a.asin = s.asin AND a.active
        JOIN best_seller.search_results r ON r.search_id = s.id AND r.status = 'suitable'
             AND r.priority = 'высокий' AND NOT r.written_to_sheet
        LEFT JOIN best_seller.node_cache c ON c.marketplace = s.marketplace AND c.node = r.node
        ORDER BY s.marketplace, s.asin, r.leader_bsr DESC NULLS LAST;
        """, fetch=True)
    keys = ("search_id", "market", "asin", "title", "target_bsr", "node", "name", "path", "dept", "leader_bsr",
            "relevance", "priority")
    return [dict(zip(keys, row)) for row in rows]


def set_relevance(connect: Connect, search_id: int, node: str, relevance: str, *,
                  actor_role: Optional[str]) -> "best_seller.Verdict":
    """Оценка К3 сотрудником (SOP, раздел 5: посмотреть, что стоит в топе) — без ИИ. Решение по подкатегории
    пересчитывается теми же правилами best_seller.evaluate: дальше проверяется К4 по бейджу лидера из кэша."""
    import best_seller

    access.require_role(actor_role, access.ROLE_EDITOR)
    if relevance not in best_seller.RELEVANCE_LABELS:
        raise BestSellerStoreError("Неизвестная оценка релевантности.")
    rows = _run(connect, """
        SELECT s.target_bsr, r.leader_bsr, r.k1, r.k2, c.leader_badge
        FROM best_seller.search_results r
        JOIN best_seller.searches s ON s.id = r.search_id
        LEFT JOIN best_seller.node_cache c ON c.marketplace = s.marketplace AND c.node = r.node
        WHERE r.search_id = %s AND r.node = %s;
        """, (search_id, node), fetch=True)
    if not rows:
        raise BestSellerStoreError("Подкатегория не найдена в этом поиске — обновите страницу.")
    target, leader_bsr, k1, k2, badge = rows[0]
    if not (k1 and k2 and target):
        raise BestSellerStoreError("Релевантность оценивается только после К1 и К2.")
    verdict = best_seller.evaluate(target, leader_bsr, best_seller.K2_REQUIRED_ITEMS, relevance, badge)
    updated = _run(connect, """
        UPDATE best_seller.search_results SET relevance = %s, k3 = %s, k4 = %s, status = %s, priority = %s, reason = %s
        WHERE search_id = %s AND node = %s RETURNING node;
        """, (relevance, verdict.k3, verdict.k4, verdict.status, verdict.priority, verdict.reason, search_id, node),
                   fetch=True)
    if not updated:
        raise BestSellerStoreError("Оценка не сохранилась — попробуйте ещё раз.")
    return verdict
