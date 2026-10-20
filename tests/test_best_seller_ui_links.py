"""Таблица результатов поиска подкатегорий: ссылки на Amazon, объяснение отказа по К1 и подсказка про целевой BSR.
Чистые функции, без Streamlit, сети и базы."""

import best_seller
import best_seller_ui as ui

TARGET = 425_109


def verdict(leader_bsr, target=TARGET):
    return best_seller.evaluate(target, leader_bsr)


def sub(leader_bsr, name="Men's Undershirt Tees", node="121178005011", **extra):
    return ui.Subcategory(name, "", node, leader_bsr, **extra)


# --- ссылки ---------------------------------------------------------------------------------------------------

def test_node_url_is_built_from_domain_dept_and_node():
    assert ui.node_url("ca", "apparel", "121178005011") == "https://www.amazon.ca/gp/bestsellers/apparel/121178005011"


def test_node_url_is_none_without_data_or_with_suspicious_parts():
    assert ui.node_url(None, "apparel", "1") is None
    assert ui.node_url("ca", None, "1") is None
    assert ui.node_url("ca", "apparel", None) is None
    assert ui.node_url("ca", 'x"><script>', "1") is None
    assert ui.node_url("ca", "apparel", "12a") is None


def test_asin_url_accepts_only_real_asins():
    assert ui.asin_url("ca", "B07WNVSKJ9") == "https://www.amazon.ca/dp/B07WNVSKJ9"
    assert ui.asin_url("ca", "b07wnvskj9") is None
    assert ui.asin_url("ca", "B07WNVSKJ9<") is None
    assert ui.asin_url(None, "B07WNVSKJ9") is None


def test_link_escapes_text_and_opens_in_a_new_tab():
    html = ui._link("A & B", "https://www.amazon.ca/dp/B07WNVSKJ9")
    assert 'href="https://www.amazon.ca/dp/B07WNVSKJ9"' in html
    assert 'target="_blank"' in html and "noopener" in html
    assert "A &amp; B" in html
    assert ui._link("A & B", None) == "A &amp; B"


# --- причина отказа -------------------------------------------------------------------------------------------

def test_leader_stronger_than_the_floor_never_passes_whatever_the_target():
    text = ui.explain_rejection(verdict(6_487), 6_487, TARGET)
    assert "6 487" in text and "50 000" in text and "при любой цели" in text


def test_leader_above_the_floor_but_below_the_target_says_what_target_would_pass():
    text = ui.explain_rejection(verdict(80_000), 80_000, 100_000)
    assert "80 000" in text and "100 000" in text and "ниже 80 000" in text
    # и правда: при цели ниже BSR лидера К1 проходит
    assert best_seller.evaluate(79_999, 80_000).k1 is True


def test_other_rejections_keep_the_rules_text():
    k2 = best_seller.evaluate(60_000, 428_399, 40)
    assert ui.explain_rejection(k2, 428_399, 60_000) == k2.reason == "отклонена по К2"


def test_without_a_target_the_rules_text_is_used():
    v = verdict(6_487)
    assert ui.explain_rejection(v, 6_487, None) == v.reason


# --- подсказка при нуле подходящих -----------------------------------------------------------------------------

def evaluated(*leaders, target=TARGET):
    return [(sub(bsr, node=str(i)), best_seller.evaluate(target, bsr)) for i, bsr in enumerate(leaders)]


def test_hint_when_no_leader_is_weak_enough_for_any_target():
    hint = ui.no_match_hint(evaluated(6_487, 1_443))
    assert "при любом целевом BSR" in hint and "50 000" in hint


def test_hint_names_the_target_that_would_let_the_weakest_leader_pass():
    hint = ui.no_match_hint(evaluated(6_487, 90_000, 70_000, target=100_000))
    assert "ниже 90 000" in hint
    assert best_seller.evaluate(89_999, 90_000).k1 is True


def test_no_hint_when_something_is_suitable_pending_or_rejected_for_another_reason():
    suitable = best_seller.evaluate(60_000, 428_399, 100, best_seller.RELEVANT, True)
    pending = best_seller.evaluate(60_000, 428_399)
    k2 = best_seller.evaluate(60_000, 428_399, 40)
    assert ui.no_match_hint([(sub(1), suitable)]) is None
    assert ui.no_match_hint([(sub(1), pending)]) is None
    assert ui.no_match_hint([(sub(1), k2)]) is None
    assert ui.no_match_hint([(sub(6_487), verdict(6_487)), (sub(428_399), k2)]) is None
    assert ui.no_match_hint([]) is None
