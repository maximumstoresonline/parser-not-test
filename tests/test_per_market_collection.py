"""Один ASIN на нескольких рынках собирается на каждом отдельно, и строка страны получает данные своей
страны. Раньше ASIN из пар DE и FR запрашивался один раз, и BSR с ценой одной страны расходились по
строкам обеих (проверка 19.10.2026: 295 из 622 пар). Без настоящих ScrapingDog и Google Sheets."""

import scraping
import sheets
from db_pairs import product_keys_from_pairs
from product_keys import asin_of, domain_of, product_key, split_key

SHARED, OUR_DE, OUR_FR = "B00L9OB0PA", "B0OURDE001", "B0OURFR001"


def pair(market, our, comp):
    return {"marketplace": market, "our_product": "", "our_asin": our, "competitor": "", "comp_asin": comp}


PAIRS = [pair("DE", OUR_DE, SHARED), pair("FR", OUR_FR, SHARED)]


def test_key_round_trip_and_domain():
    key = product_key("de", SHARED.lower())
    assert key == "DE:B00L9OB0PA"
    assert split_key(key) == ("DE", SHARED) and asin_of(key) == SHARED and domain_of(key) == "de"
    assert domain_of(product_key("UK", SHARED)) == "co.uk"
    assert split_key(SHARED) == (None, SHARED) and domain_of(SHARED) is None
    assert product_key("", SHARED) == SHARED


def test_a_shared_asin_gives_one_key_per_market():
    assert product_keys_from_pairs(PAIRS) == ["DE:B0OURDE001", "DE:B00L9OB0PA", "FR:B0OURFR001", "FR:B00L9OB0PA"]
    assert product_keys_from_pairs(PAIRS + PAIRS) == product_keys_from_pairs(PAIRS)


def test_scope_is_decided_per_market():
    # SHARED — наш в UK и конкурент в DE: в UK он в «наших», в DE — в «конкурентах».
    pairs = [pair("UK", SHARED, "B0COMPUK01"), pair("DE", OUR_DE, SHARED)]
    assert product_keys_from_pairs(pairs, scope="ours") == ["UK:B00L9OB0PA", "DE:B0OURDE001"]
    assert product_keys_from_pairs(pairs, scope="competitors") == ["UK:B0COMPUK01", "DE:B00L9OB0PA"]


def test_each_key_is_requested_on_its_own_domain_and_kept_apart(monkeypatch):
    calls = []

    def fetch(asin, domain=None, token=None, session=None):
        calls.append((asin, domain))
        if domain == "fr":
            return None
        return {"title": f"title {domain}", "price": "€10.00" if domain == "de" else "€20.00",
                "product_information": {"Best Sellers Rank": "#111 in Fashion" if domain == "de" else "#222 in Fashion"}}

    monkeypatch.setattr(scraping, "fetch_product", fetch)
    keys = ["DE:" + SHARED, "FR:" + SHARED, "IT:" + SHARED]
    _, by_key, failed = scraping.fetch_products_concurrent(keys, token="t", max_workers=1)

    assert sorted(calls) == sorted([(SHARED, "de"), (SHARED, "it")] + [(SHARED, "fr")] * 3)
    assert set(by_key) == {"DE:" + SHARED, "IT:" + SHARED} and failed == ["FR:" + SHARED]
    assert by_key["DE:" + SHARED].asin == SHARED  # в данных товара — голый ASIN, без страны
    assert by_key["DE:" + SHARED].price == 10.0 and by_key["IT:" + SHARED].price == 20.0


def test_a_plain_asin_still_uses_the_domain_map(monkeypatch):
    calls = []
    monkeypatch.setattr(scraping, "fetch_product", lambda asin, domain=None, token=None, session=None: calls.append(domain) or {"title": "x"})
    _, by_key, _ = scraping.fetch_products_concurrent([SHARED], token="t", asin_domains={SHARED: "ca"}, max_workers=1)
    assert calls == ["ca"] and SHARED in by_key


HEADERS = ["snapshot_date", "marketplace", "our_asin", "our_bsr", "our_price", "our_bsr_delta_24h",
           "comp_asin", "comp_bsr", "comp_price", "comp_bsr_delta_24h", "price_diff_pct", "updated_at"]


class FakeTarget:
    def __init__(self):
        self.updates = []

    def clear(self):
        pass

    def update(self, cell, values, value_input_option=None):
        self.updates.append(values)


class FakeMatrix:
    title = sheets.MATRIX_SHEET_NAME
    spreadsheet = object()

    def get_all_values(self):
        return [["Current"], HEADERS, ["History"], HEADERS]


def refresh(monkeypatch, products, failed=(), previous=None, history=None, pairs=PAIRS):
    target = FakeTarget()
    monkeypatch.setattr(sheets, "_get_previous_bsr_snapshot", lambda spreadsheet: previous or {})
    monkeypatch.setattr(sheets, "get_or_create_worksheet", lambda spreadsheet, name, **kwargs: target)
    monkeypatch.setattr(sheets, "_highlight_failed_asins_in_sheet", lambda *args, **kwargs: None)
    monkeypatch.setattr(sheets, "_append_checked_rows_to_history", lambda spreadsheet, records, failed: 0)
    monkeypatch.setattr(sheets, "mark_failed_asins_in_competitors", lambda *args, **kwargs: None)
    result = sheets._refresh_current_sheet_from_matrix(FakeMatrix(), products, "2026-10-20", list(failed), history, pairs=pairs)
    (values,) = target.updates
    rows = [dict(zip(HEADERS, row)) for row in values[2:]]
    return {row["marketplace"]: row for row in rows}, result["records"]


def test_each_country_row_gets_its_own_countrys_data(monkeypatch):
    products = {
        "DE:" + SHARED: {"bsr": "9822", "price": 33.99},
        "FR:" + SHARED: {"bsr": "38612", "price": 29.99},
        "DE:" + OUR_DE: {"bsr": "100", "price": 30.0},
        "FR:" + OUR_FR: {"bsr": "200", "price": 30.0},
    }
    rows, _ = refresh(monkeypatch, products)
    assert (rows["DE"]["comp_bsr"], rows["DE"]["comp_price"]) == ("9822", 33.99)
    assert (rows["FR"]["comp_bsr"], rows["FR"]["comp_price"]) == ("38612", 29.99)


def test_a_failure_in_one_country_does_not_borrow_the_other_countrys_data(monkeypatch):
    rows, _ = refresh(monkeypatch, {"DE:" + SHARED: {"bsr": "9822", "price": 33.99}}, failed=["FR:" + SHARED])
    assert rows["DE"]["comp_bsr"] == "9822"
    assert rows["FR"]["comp_bsr"] == "@" and rows["FR"]["comp_price"] == "@"


def test_the_24h_delta_is_taken_from_the_same_countrys_previous_row(monkeypatch):
    same_pair = [pair("DE", OUR_DE, SHARED), pair("FR", OUR_DE, SHARED)]
    previous = {
        ("DE", OUR_DE, SHARED): {"comp_bsr": "10000"},
        ("FR", OUR_DE, SHARED): {"comp_bsr": "40000"},
        (OUR_DE, SHARED): {"comp_bsr": "40000"},  # без страны: последняя по порядку строка листа
    }
    products = {"DE:" + SHARED: {"bsr": "9822"}, "FR:" + SHARED: {"bsr": "38612"}}
    rows, _ = refresh(monkeypatch, products, previous=previous, pairs=same_pair)
    assert rows["DE"]["comp_bsr_delta_24h"] == -178
    assert rows["FR"]["comp_bsr_delta_24h"] == -1388


def test_history_takes_only_the_pairs_of_the_current_batch_by_country(monkeypatch):
    products = {"DE:" + SHARED: {"bsr": "9822"}, "FR:" + SHARED: {"bsr": "38612"}}
    _, records = refresh(monkeypatch, products, history={"DE:" + SHARED})
    checked = {record["values"]["marketplace"]: record["checked"] for record in records}
    assert checked == {"DE": True, "FR": False}


def test_plain_asin_products_still_fill_rows(monkeypatch):
    # TEST_ASIN и старый режим через лист Competitors собирают по голому ASIN.
    rows, _ = refresh(monkeypatch, {SHARED: {"bsr": "5"}})
    assert rows["DE"]["comp_bsr"] == "5" and rows["FR"]["comp_bsr"] == "5"
