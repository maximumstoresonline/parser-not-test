"""История поисков: запрос, подпись и клик, подставляющий ссылку. Без базы."""

from datetime import datetime, timezone

from streamlit.testing.v1 import AppTest

import best_seller_store as store
import best_seller_ui as ui
from test_best_seller_store import FakeDb

ROW = {"market": "US", "asin": "B0GNBSDL65", "title": "Beanie", "status": "done", "our_bsr": 1,
       "started_at": datetime(2026, 10, 20, 17, 10, tzinfo=timezone.utc), "finished_at": None, "suitable": 0,
       "pending": 2}


def test_recent_searches_query_is_limited_and_one_row_per_asin():
    db = FakeDb([("US", "B0GNBSDL65", "T", "done", 1, None, None, 0, 0)])
    rows = store.recent_searches(db.connect, 10)
    sql, params = db.executed[0]
    assert "DISTINCT ON (s.marketplace, s.asin)" in sql and params == (10,) and rows[0]["asin"] == "B0GNBSDL65"
    store.recent_searches(db.connect, 999)
    assert db.executed[1][1] == (50,)


def test_labels_show_what_is_known():
    assert ui.history_label(ROW) == "B0GNBSDL65 · US · ждут К3: 2 · 20.10 20:10"
    assert "подходят 3" in ui.history_label({**ROW, "suitable": 3})
    assert "нет подходящих" in ui.history_label({**ROW, "pending": 0})
    assert "поиск не нужен" in ui.history_label({**ROW, "status": "not_needed"})


SCRIPT = """
import best_seller_ui
best_seller_ui.render(connect=(lambda: None), actor="Anna", can_edit=True, token="T")
"""


def test_click_puts_the_link_into_the_field(monkeypatch):
    monkeypatch.setattr(store, "search_tables_exist", lambda c: True)
    monkeypatch.setattr(store, "recent_searches", lambda c, n: [ROW])
    monkeypatch.setattr(store, "latest_search", lambda c, m, a: None)
    at = AppTest.from_string(SCRIPT).run(timeout=30)
    assert not at.exception
    at.button(key="bs_hist_0").click().run(timeout=30)
    assert at.text_input(key="bs_link").value == "https://www.amazon.com/dp/B0GNBSDL65"
