"""Раздел Best Seller, режим «Один ASIN» с настоящим поиском: запуск, показ сохранённого результата, причины,
по которым кнопка неактивна. Поиск и база подменены — без сети."""

from datetime import datetime
from html import unescape

from streamlit.testing.v1 import AppTest

import best_seller_store as store
import best_seller_ui as ui

SCRIPT = """
import best_seller_ui
best_seller_ui.render(connect=(lambda: None), actor="Anna", can_edit=CAN_EDIT, token=TOKEN)
"""
FOUND = {
    "id": 7, "market": "US", "asin": "B0H9LK9T39", "title": "Classic Straw Hat", "our_bsr": 859857,
    "target_bsr": 60000, "status": "done", "error": "", "requests": 13, "actor": "Anna",
    "finished_at": datetime(2026, 10, 16, 18, 30),
    "own": [{"node": "9057034011", "name": "Men's Novelty Sun Hats", "rank": 8},
            {"node": "9056981011", "name": "Women's Novelty Sun Hats", "rank": 12}],
    "results": [
        {"node": "9057034011", "name": "Men's Novelty Sun Hats", "leader_bsr": 428399, "k1": True, "k2": True,
         "k3": True, "k4": True, "relevance": "relevant", "status": "suitable", "priority": "высокий", "reason": "подходит"},
        {"node": "9057030011", "name": "Men's Novelty Cowboy Hats", "leader_bsr": 420000, "k1": True, "k2": True,
         "k3": None, "k4": None, "relevance": None, "status": "pending", "priority": None,
         "reason": "нужна оценка релевантности"},
        {"node": "9056958011", "name": "Women's Novelty Accessories", "leader_bsr": 222, "k1": False, "k2": None,
         "k3": None, "k4": None, "relevance": None, "status": "rejected", "priority": None, "reason": "отклонена по К1"},
    ],
}


def page_text(at):
    return unescape(" ".join([m.value for m in at.markdown] + [c.value for c in at.caption] + [i.value for i in at.info]))


def start(monkeypatch, *, can_edit=True, token="T", found=FOUND, tables=True):
    runs = []
    monkeypatch.setattr(store, "search_tables_exist", lambda c: tables)
    monkeypatch.setattr(store, "latest_search", lambda c, m, a: found)
    monkeypatch.setattr(ui, "_run_search", lambda c, m, a, target, actor, tok: runs.append((m, a, target, actor, tok)))
    at = AppTest.from_string(SCRIPT.replace("CAN_EDIT", str(can_edit)).replace("TOKEN", repr(token)))
    return at.run(timeout=30), runs


def test_without_search_tables_the_mockup_example_stays():
    import pytest

    mp = pytest.MonkeyPatch()
    try:
        at, _ = start(mp, tables=False)
        assert "примените best_seller_db/001_watchlist.sql и 002_search.sql" in page_text(at)
        assert "×7,7" in page_text(at)  # пример из макета
    finally:
        mp.undo()


def test_search_runs_with_the_link_country_and_target(monkeypatch):
    at, runs = start(monkeypatch)
    at.text_input(key="bs_link").set_value("https://www.amazon.com/dp/B0H9LK9T39").run(timeout=30)
    at.number_input(key="bs_target").set_value(60000).run(timeout=30)
    at.button(key="bs_find").click().run(timeout=30)
    assert not at.exception
    assert runs == [("US", "B0H9LK9T39", 60000, "Anna", "T")]


def test_target_zero_means_the_current_bsr(monkeypatch):
    at, runs = start(monkeypatch)
    at.text_input(key="bs_link").set_value("https://www.amazon.de/dp/B07WVYMY7Y").run(timeout=30)
    at.button(key="bs_find").click().run(timeout=30)
    assert runs == [("DE", "B07WVYMY7Y", None, "Anna", "T")]


def test_saved_result_is_shown_instead_of_the_mockup(monkeypatch):
    at, _ = start(monkeypatch)
    at.text_input(key="bs_link").set_value("https://www.amazon.com/dp/B0H9LK9T39").run(timeout=30)
    text = page_text(at)
    for expected in ("Classic Straw Hat", "859 857", "60 000", "#8 · #12", "запросов ScrapingDog: 13",
                     "Подходят: 1", "Ждут проверки: 1", "Отклонены: 1", "×7,1", "сильнее порога К1",
                     "нужна оценка релевантности"):
        assert expected in text, expected
    assert "×7,7" not in text  # не пример из макета
    assert "· 9057034011" not in text and "9057034011" in text  # без пути — без лишней точки


def test_button_is_disabled_without_link_token_or_rights(monkeypatch):
    at, _ = start(monkeypatch)
    assert at.button(key="bs_find").disabled  # нет ссылки
    at, _ = start(monkeypatch, token="")
    at.text_input(key="bs_link").set_value("https://www.amazon.com/dp/B0H9LK9T39").run(timeout=30)
    assert at.button(key="bs_find").disabled and "Нет токена ScrapingDog" in page_text(at)
    at, _ = start(monkeypatch, can_edit=False)
    at.text_input(key="bs_link").set_value("https://www.amazon.com/dp/B0H9LK9T39").run(timeout=30)
    assert at.button(key="bs_find").disabled and "правом редактирования" in page_text(at)


def test_not_needed_and_errors_and_first_time(monkeypatch):
    at, _ = start(monkeypatch, found={**FOUND, "status": "not_needed", "results": []})
    at.text_input(key="bs_link").set_value("https://www.amazon.com/dp/B0H9LK9T39").run(timeout=30)
    assert any("уже в топ-3" in s.value for s in at.success)
    at, _ = start(monkeypatch, found={**FOUND, "status": "error", "error": "ScrapingDog не вернул карточку товара"})
    at.text_input(key="bs_link").set_value("https://www.amazon.com/dp/B0H9LK9T39").run(timeout=30)
    assert any("ScrapingDog не вернул карточку" in e.value for e in at.error)
    at, _ = start(monkeypatch, found=None)
    at.text_input(key="bs_link").set_value("https://www.amazon.com/dp/B0H9LK9T39").run(timeout=30)
    assert "поиска ещё не было" in page_text(at)


TOP = [{"rank": 1, "asin": "B0AAAAAAA1", "title": "Straw Cowboy Hat for Men"},
       {"rank": 2, "asin": "B0AAAAAAA2", "title": "Western Cowboy Hat <Brown>"}]


def with_top(found=FOUND):
    results = [dict(row, top=TOP if row["k1"] and row["k2"] else None, dept="fashion") for row in found["results"]]
    return dict(found, results=results)


def test_relevance_is_rated_by_hand_from_the_top_titles(monkeypatch):
    saved = []
    monkeypatch.setattr(store, "set_relevance", lambda c, sid, node, rel, actor_role: saved.append((sid, node, rel, actor_role)))
    at, _ = start(monkeypatch, found=with_top())
    at.text_input(key="bs_link").set_value("https://www.amazon.com/dp/B0H9LK9T39").run(timeout=30)
    text = page_text(at)
    assert "Оценка релевантности (К3)" in text and "Western Cowboy Hat <Brown>" in text
    assert "https://www.amazon.com/gp/bestsellers/fashion/9057030011" in text
    labels = [e.label for e in at.expander]
    # обе подкатегории с пройденными К1 и К2, неоценённая сверху; отклонённая по К1 — нет
    assert labels == ["Men's Novelty Cowboy Hats — не оценена", "Men's Novelty Sun Hats — релевантная"]
    radio = at.radio(key="bs_rel_7_9057030011")
    assert radio.value is None
    assert at.button(key="bs_rel_save_7_9057030011").disabled
    radio.set_value("semi").run(timeout=30)
    at.button(key="bs_rel_save_7_9057030011").click().run(timeout=30)
    assert not at.exception
    assert saved == [(7, "9057030011", "semi", "editor")]
    assert at.radio(key="bs_rel_7_9057034011").value == "relevant"


def test_a_viewer_sees_the_top_but_cannot_rate(monkeypatch):
    at, _ = start(monkeypatch, can_edit=False, found=with_top())
    at.text_input(key="bs_link").set_value("https://www.amazon.com/dp/B0H9LK9T39").run(timeout=30)
    assert at.radio(key="bs_rel_7_9057030011").disabled
    assert not [b for b in at.button if b.key == "bs_rel_save_7_9057030011"]
    assert "Оценивать может сотрудник с правом редактирования." in page_text(at)


def test_without_saved_titles_the_amazon_link_is_suggested(monkeypatch):
    at, _ = start(monkeypatch, found=dict(FOUND, results=[dict(r, top=None, dept=None) for r in FOUND["results"]]))
    at.text_input(key="bs_link").set_value("https://www.amazon.com/dp/B0H9LK9T39").run(timeout=30)
    assert "Заголовки топа не сохранены" in page_text(at)


# Реальный случай 20.10.2026: B07WNVSKJ9 в Канаде, цель = текущий BSR, у обоих лидеров BSR намного лучше цели.
CA_ALL_REJECTED = {
    **FOUND, "market": "CA", "asin": "B07WNVSKJ9", "our_bsr": 425109, "target_bsr": 425109,
    "results": [
        {"node": "121178006011", "name": "The most popular items in Men's Undershirt Tank Tops", "leader_bsr": 6487,
         "k1": False, "k2": None, "k3": None, "k4": None, "relevance": None, "status": "rejected", "priority": None,
         "reason": "отклонена по К1", "dept": "apparel", "leader_asin": "B0AAAAAAA1"},
        {"node": "121178005011", "name": "The most popular items in Men's Undershirt Tees", "leader_bsr": 1443,
         "k1": False, "k2": None, "k3": None, "k4": None, "relevance": None, "status": "rejected", "priority": None,
         "reason": "отклонена по К1", "dept": "apparel", "leader_asin": "B0AAAAAAA2"},
    ],
}


def test_all_rejected_search_has_links_reasons_and_a_hint_without_empty_columns(monkeypatch):
    at, _ = start(monkeypatch, found=CA_ALL_REJECTED)
    at.text_input(key="bs_link").set_value("https://www.amazon.ca/dp/B07WNVSKJ9").run(timeout=30)
    assert not at.exception
    html = " ".join(m.value for m in at.markdown)
    # подкатегория и лидер — ссылки на Amazon Канады
    assert 'href="https://www.amazon.ca/gp/bestsellers/apparel/121178006011"' in html
    assert 'href="https://www.amazon.ca/dp/B0AAAAAAA1"' in html
    # причина с числами и подсказка про цель
    text = page_text(at)
    assert "лидер на 6 487" in text and "при любой цели не пройдёт" in text
    assert "Подходящих нет" in text and "при любом целевом BSR К1 не пройти" in text
    # пустые колонки К2–К4 и «Релевантность» не рисуются, есть колонка с причиной
    assert '>Почему отклонена</div>' in html
    for header in ("Релевантность", "К2", "К3", "К4", "Приоритет"):
        assert f'>{header}</div>' not in html


def test_search_with_a_suitable_row_keeps_the_full_table_and_has_no_hint(monkeypatch):
    at, _ = start(monkeypatch)
    at.text_input(key="bs_link").set_value("https://www.amazon.com/dp/B0H9LK9T39").run(timeout=30)
    html = " ".join(m.value for m in at.markdown)
    text = page_text(at)
    assert '>Релевантность</div>' in html and '>Приоритет</div>' in html
    assert "Подходящих нет" not in text


def test_queued_search_from_the_list_mode_says_so(monkeypatch):
    for status, word in (("queued", "в очереди"), ("running", "идёт")):
        at, _ = start(monkeypatch, found={**FOUND, "status": status, "results": []})
        at.text_input(key="bs_link").set_value("https://www.amazon.com/dp/B0H9LK9T39").run(timeout=30)
        assert word in page_text(at) and "Подкатегорий для проверки не нашлось" not in page_text(at)
