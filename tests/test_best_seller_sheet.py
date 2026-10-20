"""Запись подкатегорий в Google-таблицу гипотез: строки, дубликаты, доступ, кнопки. Без сети и базы."""

from datetime import datetime, timezone

import pytest
from streamlit.testing.v1 import AppTest

import best_seller_sheet as sheet
import best_seller_store as store
import best_seller_ui as ui
from test_best_seller_batch import FakeDb

NOW = datetime(2026, 10, 20, 22, 30, tzinfo=timezone.utc)  # в Киеве уже 21.10
ITEM = {"search_id": 7, "market": "US", "asin": "B0GNBSDL65", "title": "Merino Beanie", "target_bsr": 60000,
        "node": "9057034011", "name": "Men's Novelty Sun Hats", "path": "Clothing › Novelty & More", "dept": "fashion",
        "leader_bsr": 460779, "relevance": "relevant", "priority": "высокий"}


def test_row_has_the_columns_from_the_sop_and_kyiv_date():
    rows, no_link = sheet.build_rows([ITEM], NOW)
    assert no_link == 0 and len(rows[0]) == len(sheet.HEADERS)
    assert rows[0][:9] == ["https://www.amazon.com/gp/bestsellers/fashion/9057034011", "Merino Beanie", "B0GNBSDL65",
                           "Clothing › Novelty & More", "релевантная", 460779, 60000, "высокий", "21.10.2026"]


def test_without_a_section_there_is_no_link_and_the_row_is_skipped():
    rows, no_link = sheet.build_rows([{**ITEM, "dept": None}, {**ITEM, "dept": 'x"><b>'}], NOW)
    assert rows == [] and no_link == 2


def test_formula_like_text_is_not_executed():
    rows, _ = sheet.build_rows([{**ITEM, "title": "=HYPERLINK(\"http://x\")"}], NOW)
    assert rows[0][1].startswith("'=")


class FakeSheet:
    def __init__(self, values):
        self.values, self.appended, self.header = values, [], None

    def get_all_values(self):
        return self.values

    def append_row(self, row, value_input_option=None):
        self.header = row

    def append_rows(self, rows, value_input_option=None):
        self.appended += rows


class FakeSpreadsheet:
    def __init__(self, ws):
        self.ws, self.created = ws, False

    def worksheet(self, title):
        if self.ws is None:
            import gspread
            raise gspread.WorksheetNotFound(title)
        return self.ws

    def add_worksheet(self, title, rows, cols):
        self.created, self.ws = True, FakeSheet([])
        return self.ws


def test_new_sheet_gets_a_header_and_rows():
    rows, _ = sheet.build_rows([ITEM], NOW)
    sp = FakeSpreadsheet(None)
    assert sheet.write_rows(sp, rows) == (1, 0)
    assert sp.created and sp.ws.header == sheet.HEADERS and sp.ws.appended == rows


def test_pair_already_on_the_sheet_is_not_written_twice():
    rows, _ = sheet.build_rows([ITEM, ITEM], NOW)
    ws = FakeSheet([sheet.HEADERS, [rows[0][0], "", "B0DG2B7KJN"]])
    assert sheet.write_rows(FakeSpreadsheet(ws), rows) == (1, 1)  # другой ASIN той же подкатегории — новая строка
    ws2 = FakeSheet([sheet.HEADERS, [rows[0][0], "", "B0GNBSDL65"]])
    assert sheet.write_rows(FakeSpreadsheet(ws2), rows) == (0, 2) and ws2.appended == []


class Boom(Exception):
    def __init__(self, status):
        self.response = type("R", (), {"status_code": status})()


def test_writer_hides_driver_details_and_explains_access():
    class Client:
        def open_by_key(self, key):
            raise Boom(403)

    write = sheet.make_writer({"private_key": "SECRET"}, client_factory=lambda info: Client())
    with pytest.raises(sheet.SheetError) as exc:
        write([ITEM])
    assert "редакторы" in str(exc.value) and "SECRET" not in str(exc.value)

    def broken(info):
        raise RuntimeError("private_key=SECRET")

    with pytest.raises(sheet.SheetError) as exc:
        sheet.make_writer({}, client_factory=broken)([ITEM])
    assert "SECRET" not in str(exc.value) and "RuntimeError" in str(exc.value)


def test_writer_with_nothing_to_write_does_not_touch_google():
    result = sheet.make_writer({}, client_factory=lambda i: 1 / 0)([{**ITEM, "dept": None}])
    assert (result.written, result.no_link) == (0, 1)


# --- хранилище -------------------------------------------------------------------------------------------------

def test_mark_written_and_unwritten_queries():
    db = FakeDb([("a",)])
    assert store.mark_written(db.connect, 7, ["9057034011"]) == 1
    assert "written_to_sheet = TRUE" in db.executed[0][0]
    db2 = FakeDb([(7, "US", "B0GNBSDL65", "T", 6, "1", "n", "p", "fashion", 5, "relevant", "высокий")])
    rows = store.unwritten_high(db2.connect)
    assert rows[0]["node"] == "1" and "NOT r.written_to_sheet" in db2.executed[0][0]


# --- экран -----------------------------------------------------------------------------------------------------

FOUND = {
    "id": 7, "market": "US", "asin": "B0GNBSDL65", "title": "Merino Beanie", "our_bsr": 80928, "target_bsr": 60000,
    "status": "done", "error": "", "requests": 13, "actor": "Anna", "finished_at": datetime(2026, 10, 20, 20, 10),
    "own": [], "results": [
        {"node": "9057034011", "name": "Men's Novelty Sun Hats", "path": "", "leader_bsr": 460779, "k1": True,
         "k2": True, "k3": True, "k4": True, "relevance": "relevant", "status": "suitable", "priority": "высокий",
         "reason": "подходит", "top": [], "dept": "fashion", "leader_asin": "B0H9LK9T39", "written": False},
        {"node": "10208070011", "name": "Women's Ice Skating", "path": "", "leader_bsr": 77590, "k1": True,
         "k2": True, "k3": True, "k4": True, "relevance": "semi", "status": "suitable", "priority": "низкий",
         "reason": "подходит", "top": [], "dept": "fashion", "leader_asin": None, "written": True}]}

SCRIPT = """
import best_seller_ui
best_seller_ui.render(connect=(lambda: None), actor="Anna", can_edit=True, token="T", sheet_writer=WRITER)
"""


def start(monkeypatch, writer=True):
    monkeypatch.setattr(store, "search_tables_exist", lambda c: True)
    monkeypatch.setattr(store, "latest_search", lambda c, m, a: FOUND)
    monkeypatch.setattr(store, "recent_searches", lambda c, n: [])
    at = AppTest.from_string(SCRIPT.replace("WRITER", "None" if not writer else "_writer"))
    at.session_state["bs_link"] = "https://www.amazon.com/dp/B0GNBSDL65"
    return at


def test_button_writes_only_the_selected_not_yet_written_and_marks_them(monkeypatch):
    sent, marked = [], []
    import builtins
    builtins._writer = lambda items: sent.append(list(items)) or sheet.WriteResult(len(items), 0, 0)
    try:
        monkeypatch.setattr(store, "mark_written", lambda c, sid, nodes: marked.append((sid, list(nodes))) or len(nodes))
        at = start(monkeypatch).run(timeout=30)
        assert not at.exception
        at.button(key="bs_write_one").click().run(timeout=30)
        assert not at.exception
        assert [i["node"] for i in sent[0]] == ["9057034011"] and sent[0][0]["asin"] == "B0GNBSDL65"
        assert marked == [(7, ["9057034011"])]
        assert any("Записано в таблицу: 1" in s.value for s in at.success)
    finally:
        del builtins._writer


def test_already_written_row_is_marked_and_not_selectable(monkeypatch):
    at = start(monkeypatch, writer=False).run(timeout=30)
    assert at.checkbox(key="bs_pick_US_B0GNBSDL65_10208070011").disabled
    assert any("записана в таблицу" in m.value for m in at.markdown)


def test_without_google_key_the_user_is_told(monkeypatch):
    at = start(monkeypatch, writer=False).run(timeout=30)
    at.button(key="bs_write_one").click().run(timeout=30)
    assert any("Нет ключа Google" in w.value for w in at.warning)
