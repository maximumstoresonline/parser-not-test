"""Таблица гипотез внутри дашборда: добавление подкатегорий, запрос, показ и выгрузка. Без базы."""

from datetime import datetime, timezone

import pytest
from streamlit.testing.v1 import AppTest

import access
import best_seller_store as store
import best_seller_ui as ui
from test_best_seller_batch import FakeDb

ROW = {"market": "US", "asin": "B0GNBSDL65", "title": "Merino Beanie", "node": "9057034011",
       "name": "Men's Novelty Sun Hats", "path": "Clothing › Novelty & More", "dept": "fashion", "leader_bsr": 460779,
       "target_bsr": 60000, "relevance": "relevant", "priority": "высокий",
       "checked": datetime(2026, 10, 20, 17, 10, tzinfo=timezone.utc), "actor": "Anna"}


# --- хранилище -------------------------------------------------------------------------------------------------

def test_adding_needs_the_editor_role_and_sets_the_flag():
    db = FakeDb([("9057034011",)])
    assert store.mark_written(db.connect, 7, ["9057034011"], actor_role=access.ROLE_EDITOR) == 1
    assert "written_to_sheet = TRUE" in db.executed[0][0]
    with pytest.raises(access.AccessDenied):
        store.mark_written(db.connect, 7, ["1"], actor_role=None)


def test_hypotheses_query_takes_the_latest_search_per_pair():
    db = FakeDb([("US", "B0GNBSDL65", "T", "1", "n", "p", "fashion", 5, 6, "relevant", "высокий", None, "A")])
    rows = store.hypotheses(db.connect)
    assert rows[0]["asin"] == "B0GNBSDL65" and "DISTINCT ON (s.marketplace, s.asin, r.node)" in db.executed[0][0]
    assert "WHERE r.written_to_sheet" in db.executed[0][0]


# --- таблица -----------------------------------------------------------------------------------------------------

def test_frame_has_the_sop_columns_and_a_working_link():
    frame = ui.hypothesis_frame([ROW])
    assert list(frame.columns) == ui.HYPO_COLUMNS
    row = frame.iloc[0]
    assert row["Ссылка"] == "https://www.amazon.com/gp/bestsellers/fashion/9057034011"
    assert (row["ASIN"], row["Релевантность"], row["Приоритет"], row["Проверено"]) == (
        "B0GNBSDL65", "релевантная", "высокий", "20.10 20:10")


def test_without_a_section_the_link_is_empty():
    assert ui.hypothesis_frame([{**ROW, "dept": None}]).iloc[0]["Ссылка"] is None


SCRIPT = """
import best_seller_ui
best_seller_ui.render(connect=(lambda: None), actor="Anna", can_edit=True)
"""


def open_hypotheses(monkeypatch, rows):
    monkeypatch.setattr(store, "search_tables_exist", lambda c: True)
    monkeypatch.setattr(store, "hypotheses", lambda c: rows)
    at = AppTest.from_string(SCRIPT)
    at.session_state["bs_mode"] = ui.MODES[2]
    return at.run(timeout=30)


def test_the_tab_shows_rows_and_filters(monkeypatch):
    at = open_hypotheses(monkeypatch, [ROW, {**ROW, "asin": "B0DG2B7KJN", "priority": "низкий"}])
    assert not at.exception and at.dataframe[0].value.shape[0] == 2
    at.text_input(key="bs_hypo_query").set_value("B0DG").run(timeout=30)
    assert at.dataframe[0].value.shape[0] == 1
    at.text_input(key="bs_hypo_query").set_value("").run(timeout=30)
    at.selectbox(key="bs_hypo_priority").select("высокий").run(timeout=30)
    assert at.dataframe[0].value.shape[0] == 1


def test_empty_table_says_so(monkeypatch):
    at = open_hypotheses(monkeypatch, [])
    assert any("пока пусто" in i.value for i in at.info)


# --- кнопки «Добавить» -------------------------------------------------------------------------------------------

FOUND = {
    "id": 7, "market": "US", "asin": "B0GNBSDL65", "title": "Merino Beanie", "our_bsr": 80928, "target_bsr": 60000,
    "status": "done", "error": "", "requests": 13, "actor": "Anna", "finished_at": datetime(2026, 10, 20, 20, 10),
    "own": [], "results": [
        {"node": "9057034011", "name": "Men's Novelty Sun Hats", "path": "", "leader_bsr": 460779, "k1": True,
         "k2": True, "k3": True, "k4": True, "relevance": "relevant", "status": "suitable", "priority": "высокий",
         "reason": "подходит", "top": [], "dept": "fashion", "leader_asin": None, "written": False},
        {"node": "10208070011", "name": "Women's Ice Skating", "path": "", "leader_bsr": 77590, "k1": True,
         "k2": True, "k3": True, "k4": True, "relevance": "semi", "status": "suitable", "priority": "низкий",
         "reason": "подходит", "top": [], "dept": "fashion", "leader_asin": None, "written": True}]}


def open_single(monkeypatch):
    monkeypatch.setattr(store, "search_tables_exist", lambda c: True)
    monkeypatch.setattr(store, "latest_search", lambda c, m, a: FOUND)
    monkeypatch.setattr(store, "recent_searches", lambda c, n: [])
    at = AppTest.from_string(SCRIPT)
    at.session_state["bs_link"] = "https://www.amazon.com/dp/B0GNBSDL65"
    return at.run(timeout=30)


def test_button_adds_only_the_selected_and_not_yet_added(monkeypatch):
    added = []
    monkeypatch.setattr(store, "mark_written",
                        lambda c, sid, nodes, actor_role: added.append((sid, list(nodes), actor_role)) or len(nodes))
    at = open_single(monkeypatch)
    assert not at.exception
    assert at.checkbox(key="bs_pick_US_B0GNBSDL65_10208070011").disabled  # уже в таблице
    at.button(key="bs_write_one").click().run(timeout=30)
    assert not at.exception
    assert added == [(7, ["9057034011"], access.ROLE_EDITOR)]
    assert any("Добавлено в таблицу гипотез: 1" in s.value for s in at.success)


def test_batch_button_adds_all_high_priority(monkeypatch):
    added = []
    row = {"market": "US", "asin": "B0GNBSDL65", "status": "done", "our_bsr": 1, "title": "", "error": "", "own": [],
           "suitable": 1, "pending": 0, "target": None, "best": "x", "leader_bsr": 5, "priority": "высокий", "started_at": None,
           "finished_at": None, "requests": 3, "actor": "A"}
    monkeypatch.setattr(store, "table_exists", lambda c: True)
    monkeypatch.setattr(store, "search_tables_exist", lambda c: True)
    monkeypatch.setattr(store, "list_asins", lambda c: [])
    monkeypatch.setattr(store, "ensure_target_column", lambda c: None)
    monkeypatch.setattr(store, "batch_rows", lambda c: [row])
    monkeypatch.setattr(store, "unwritten_high", lambda c: [{**ROW, "search_id": 7}, {**ROW, "search_id": 8,
                                                                                    "node": "5"}])
    monkeypatch.setattr(store, "mark_written",
                        lambda c, sid, nodes, actor_role: added.append((sid, list(nodes))) or len(nodes))
    at = AppTest.from_string(SCRIPT)
    at.session_state["bs_mode"] = ui.MODES[1]
    at.run(timeout=30)
    at.button(key="bs_batch_write").click().run(timeout=30)
    assert not at.exception
    assert added == [(7, ["9057034011"]), (8, ["5"])]
    assert any("Добавлено в таблицу гипотез: 2" in s.value for s in at.success)


def test_batch_button_says_when_there_is_nothing_to_add(monkeypatch):
    row = {"market": "US", "asin": "B0GNBSDL65", "status": "done", "our_bsr": 1, "title": "", "error": "", "own": [],
           "suitable": 0, "pending": 0, "target": None, "best": None, "leader_bsr": None, "priority": None,
           "started_at": None,
           "finished_at": None, "requests": 0, "actor": ""}
    monkeypatch.setattr(store, "table_exists", lambda c: True)
    monkeypatch.setattr(store, "search_tables_exist", lambda c: True)
    monkeypatch.setattr(store, "list_asins", lambda c: [])
    monkeypatch.setattr(store, "ensure_target_column", lambda c: None)
    monkeypatch.setattr(store, "batch_rows", lambda c: [row])
    monkeypatch.setattr(store, "unwritten_high", lambda c: [])
    at = AppTest.from_string(SCRIPT)
    at.session_state["bs_mode"] = ui.MODES[1]
    at.run(timeout=30)
    at.button(key="bs_batch_write").click().run(timeout=30)
    assert any("Нечего добавлять" in i.value for i in at.info)
