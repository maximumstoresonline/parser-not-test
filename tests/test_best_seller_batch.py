"""Поиск по списку ASIN через очередь: запросы хранилища, воркер, запуск workflow и экран. Без сети и базы."""

import pytest
from streamlit.testing.v1 import AppTest

import access
import best_seller_batch as worker
import best_seller_store as store
import best_seller_ui as ui
import github_dispatch
from test_best_seller_store import FakeDb


class Result:
    status, error, title, our_bsr, target_bsr, requests, own, candidates = "done", "", "T", 9, 9, 3, [], []
    market, asin = "US", "B0GNBSDL65"


# --- хранилище -------------------------------------------------------------------------------------------------

def test_enqueue_needs_editor_and_counts_new_rows():
    db = FakeDb([(1,), (2,)])
    assert store.enqueue_searches(db.connect, actor_role=access.ROLE_EDITOR, actor="Anna") == 2
    sql, params = db.executed[0]
    assert "'queued'" in sql and params == ("Anna", True)
    with pytest.raises(access.AccessDenied):
        store.enqueue_searches(db.connect, actor_role=None, actor="x")


def test_enqueue_all_passes_only_new_false():
    db = FakeDb([])
    store.enqueue_searches(db.connect, actor_role=access.ROLE_EDITOR, actor="Anna", only_new=False)
    assert db.executed[0][1] == ("Anna", False)


def test_claim_next_takes_one_row_without_clashing_with_other_workers():
    db = FakeDb([(5, "US", "B0GNBSDL65", "Anna")])
    assert store.claim_next(db.connect) == {"id": 5, "market": "US", "asin": "B0GNBSDL65", "actor": "Anna"}
    assert "SKIP LOCKED" in db.executed[0][0]
    assert store.claim_next(FakeDb([]).connect) is None


def test_finish_updates_the_queued_row_and_adds_results():
    db = FakeDb([])
    store.finish_search(db.connect, 5, Result())
    assert "UPDATE best_seller.searches" in db.executed[0][0] and db.executed[0][1][-1] == 5
    assert "INSERT INTO best_seller.search_results" in db.executed[1][0]


# --- воркер ----------------------------------------------------------------------------------------------------

def test_worker_processes_the_queue_up_to_the_limit(monkeypatch):
    jobs = [{"id": i, "market": "US", "asin": "B0GNBSDL65", "actor": "A"} for i in range(1, 6)]
    finished = []
    monkeypatch.setattr(store, "recover_stale", lambda c: 0)
    monkeypatch.setattr(store, "claim_next", lambda c: jobs.pop(0) if jobs else None)
    monkeypatch.setattr(store, "finish_search", lambda c, i, r: finished.append(i))
    monkeypatch.setattr(store, "DbNodeCache", lambda c: object())
    counts = worker.run(None, search=lambda *a, **k: Result(), fetch_page=lambda u: None, limit=3, log=lambda s: None)
    assert finished == [1, 2, 3] and counts == {"done": 3} and len(jobs) == 2


def test_one_failing_search_is_marked_and_does_not_stop_the_rest(monkeypatch):
    jobs = [{"id": 1, "market": "US", "asin": "B0GNBSDL65", "actor": "A"},
            {"id": 2, "market": "US", "asin": "B0DG2B7KJN", "actor": "A"}]
    failed, finished = [], []
    monkeypatch.setattr(store, "recover_stale", lambda c: 0)
    monkeypatch.setattr(store, "claim_next", lambda c: jobs.pop(0) if jobs else None)
    monkeypatch.setattr(store, "finish_search", lambda c, i, r: finished.append(i))
    monkeypatch.setattr(store, "fail_search", lambda c, i, m: failed.append((i, m)))
    monkeypatch.setattr(store, "DbNodeCache", lambda c: object())

    def search(asin, market, **kw):
        if asin == "B0GNBSDL65":
            raise RuntimeError("password=secret")
        return Result()

    counts = worker.run(None, search=search, fetch_page=lambda u: None, log=lambda s: None)
    assert counts == {"error": 1, "done": 1} and finished == [2]
    assert failed == [(1, "Сбой поиска (RuntimeError)")]  # текст исключения не попадает в базу


def test_limit_from_env():
    assert worker.limit_from_env("7") == 7
    assert worker.limit_from_env("") == worker.limit_from_env("0") == worker.limit_from_env("x") == worker.DEFAULT_LIMIT


# --- запуск workflow -------------------------------------------------------------------------------------------

def test_batch_dispatch_calls_its_own_workflow_without_inputs():
    calls = []

    class R:
        status_code = 204

    result = github_dispatch.dispatch_batch_search("tok", post=lambda url, **kw: calls.append((url, kw)) or R())
    assert result.ok
    url, kw = calls[0]
    assert url.endswith("/actions/workflows/best_seller_batch.yml/dispatches") and kw["json"] == {"ref": "main"}
    assert not github_dispatch.dispatch_batch_search("").ok
    assert not github_dispatch.dispatch_batch_search("tok", "bad repo").ok


# --- экран -----------------------------------------------------------------------------------------------------

def db_row(asin, status, **extra):
    row = {"market": "US", "asin": asin, "status": status, "our_bsr": 91233, "title": "", "error": "", "own": [],
           "suitable": 0, "pending": 0, "best": None, "leader_bsr": None, "priority": None}
    return {**row, **extra}


ROWS = [db_row("B0GNBSDL65", "done", suitable=2, best="Men's Novelty Sun Hats", leader_bsr=460779, priority="высокий"),
        db_row("B0DG2B7KJN", "not_needed"), db_row("B0FBGFR4KP", "queued"),
        db_row("B0H9LK9T39", "error", error="ScrapingDog не вернул карточку товара")]


def test_rows_and_counts_from_the_database():
    rows = [ui.batch_row_from_db(r) for r in ROWS]
    assert [r.status for r in rows] == ["готово", "поиск не нужен", "в очереди", "ошибка запроса"]
    assert rows[1].best == "уже в топ-3 своей подкатегории" and rows[0].suitable == 2
    assert ui.batch_counts(rows) == (3, 1, 0, 1, 1)
    assert ui.batch_row_from_db(db_row("B0AAAAAAA1", None)).status == "ещё не искали"


def test_csv_has_header_and_rows():
    rows = [ui.batch_row_from_db(r) for r in ROWS]
    lines = ui.batch_csv(rows).strip().splitlines()
    assert lines[0].startswith("ASIN,Страна") and len(lines) == 5 and "B0GNBSDL65,US,91 233" in lines[1]


SCRIPT = """
import best_seller_ui
best_seller_ui.render(connect=(lambda: None), actor="Anna", can_edit=CAN_EDIT, token="T", dispatch_token="G", repo="o/r")
"""


def start(monkeypatch, can_edit=True, ready=True):
    monkeypatch.setattr(store, "table_exists", lambda c: ready)
    monkeypatch.setattr(store, "search_tables_exist", lambda c: ready)
    monkeypatch.setattr(store, "list_asins", lambda c: [])
    monkeypatch.setattr(store, "batch_rows", lambda c: ROWS)
    at = AppTest.from_string(SCRIPT.replace("CAN_EDIT", str(can_edit)))
    at.session_state["bs_mode"] = ui.MODES[1]
    return at.run(timeout=30)


def test_live_table_progress_and_csv_button(monkeypatch):
    at = start(monkeypatch)
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Обработано 3 из 4" in text and "Men&#x27;s Novelty Sun Hats" in text or "Men's Novelty Sun Hats" in text
    assert "уже в топ-3 своей подкатегории" in text


def test_start_enqueues_then_dispatches(monkeypatch):
    sent = []
    monkeypatch.setattr(store, "enqueue_searches", lambda c, **kw: sent.append(kw) or 3)
    monkeypatch.setattr(github_dispatch, "dispatch_batch_search",
                        lambda token, repo: sent.append((token, repo)) or github_dispatch.DispatchResult(True, "ok"))
    at = start(monkeypatch)
    at.button(key="bs_batch_run").click().run(timeout=30)
    assert not at.exception
    assert sent[0]["only_new"] is True and sent[0]["actor"] == "Anna" and sent[1] == ("G", "o/r")
    assert any("поставлено 3" in s.value for s in at.success)


def test_dispatch_failure_keeps_the_queue_and_tells_how_to_run_manually(monkeypatch):
    monkeypatch.setattr(store, "enqueue_searches", lambda c, **kw: 2)
    monkeypatch.setattr(github_dispatch, "dispatch_batch_search",
                        lambda token, repo: github_dispatch.DispatchResult(False, "нет права"))
    at = start(monkeypatch)
    at.button(key="bs_batch_run").click().run(timeout=30)
    assert any("нет права" in w.value and "Actions" in w.value for w in at.warning)


def test_nothing_to_queue_and_no_rights(monkeypatch):
    monkeypatch.setattr(store, "enqueue_searches", lambda c, **kw: 0)
    at = start(monkeypatch)
    at.button(key="bs_batch_run").click().run(timeout=30)
    assert any("Поставить нечего" in i.value for i in at.info)
    assert start(monkeypatch, can_edit=False).button(key="bs_batch_run").disabled


def test_without_search_tables_the_mockup_stays(monkeypatch):
    at = start(monkeypatch, ready=False)
    assert any("002_search.sql" in i.value for i in at.info)


def test_pending_subcategories_are_not_reported_as_no_match():
    row = ui.batch_row_from_db(db_row("B0GNBSDL65", "done", pending=3))
    assert row.best == "ждут оценки К3: 3" and row.suitable == 0
    assert ui.batch_row_from_db(db_row("B0GNBSDL65", "done")).best == "нет подходящих"
