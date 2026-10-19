"""Движок поиска подкатегорий Best Seller: разбор ответов ScrapingDog и порядок проверок с ранней остановкой.
Без сети: страницы и карточки — короткие копии настоящей разметки (разведка 16.10.2026)."""

import json
from datetime import datetime, timedelta, timezone

import best_seller
import best_seller_search as s

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
OURS = "B0H9LK9T39"


def bs_page(name, items, siblings=(), parents=()):
    """Страница Best Sellers: места в data-client-recs-list, заголовки карточек, меню (родители и соседи)."""
    recs = json.dumps([{"id": asin, "metadataMap": {"render.zg.rank": str(rank)}} for rank, asin in items])
    cards = "".join(f'<div id="gridItemRoot"><div data-asin="{asin}"><div class="_cDEzb_p13n-sc-css-line-clamp-3_g3dy1">'
                    f"Title of {asin}</div></div></div>" for _, asin in items)
    nav = "".join(f'<a href="/Best/zgbs/fashion/{n}/ref=zg_bs_unv_fashion_2_1">{label}</a>' for n, label in parents)
    nav += "".join(f'<a href="/Best/zgbs/fashion/{n}/ref=zg_bs_nav_fashion_4_1">{label}</a>' for n, label in siblings)
    return (f"<html><title>Amazon.com Best Sellers: Best {name}</title><body>{nav}"
            f'<div data-client-recs-list="{recs.replace(chr(34), "&quot;")}"></div>{cards}</body></html>')


def leader_page(bsr, badge_text=""):
    return (f'<div id="zeitgeistBadge_feature_div" class="celwidget"><span>{badge_text}</span></div>'
            f'<div id="unifiedPrice_feature_div"></div><table><tr><th>Best Sellers Rank</th>'
            f"<td>#{bsr:,} in Clothing, Shoes &amp; Jewelry #1 in Sub</td></tr></table>")


PRODUCT = {
    "title": "Merino Sun Hat",
    "best_sellers_rank": [
        {"rank": 90000, "category": "Clothing, Shoes & Jewelry", "link": "https://www.amazon.com/gp/bestsellers/fashion/ref=x"},
        {"rank": 8, "category": "Men's Novelty Sun Hats", "link": "https://www.amazon.com/gp/bestsellers/fashion/9057034011/ref=x"},
        {"rank": 12, "category": "Women's Novelty Sun Hats", "link": "https://www.amazon.com/gp/bestsellers/fashion/9056981011/ref=x"},
    ],
    "categories": [{"node": "7141123011"}, {"node": "9056985011"}],
}


class Fake:
    def __init__(self, pages, product=PRODUCT):
        self.pages, self.product, self.urls = pages, product, []

    def fetch_product(self, asin, domain):
        self.urls.append(f"product {asin} {domain}")
        return self.product

    def fetch_page(self, url):
        self.urls.append(url)
        return self.pages.get(url)


def top(prefix, n, start=1):
    return [(r, f"B0{prefix}{r:06d}") for r in range(start, start + n)]


URL = "https://www.amazon.com/gp/bestsellers/fashion/"


def test_parse_bestsellers_reads_ranks_titles_and_only_sibling_links():
    page = s.parse_bestsellers(bs_page("Men's Novelty Sun Hats", top("AA", 50), siblings=[("111", "Visors")],
                                       parents=[("7147440011", "Women")]))
    assert page.name == "Men's Novelty Sun Hats" and len(page.ranked) == 50 and page.ranked[0] == (1, "B0AA000001")
    assert page.titles["B0AA000001"] == "Title of B0AA000001"
    assert [(x.node, x.name) for x in page.linked] == [("111", "Visors")]  # родитель «Women» не кандидат


def test_parse_leader_badge_only_from_its_own_block():
    assert s.parse_leader(leader_page(994, "#1 Best Seller in Women's Skullies")) == (994, True)
    assert s.parse_leader(leader_page(994, "#1 New Release in Women's Novelty Sun Hats")) == (994, False)
    assert s.parse_leader(leader_page(994, "")) == (994, False)
    assert s.parse_leader('<p>#1 Best Seller</p><table><tr><th>Best Sellers Rank</th><td>#5 in X</td></tr></table>') \
        == (5, None)  # блока бейджа нет — не знаем, а не «нет»
    assert s.parse_leader("Amazon Bestseller-Rang: Nr. 1.531 in Fashion")[0] == 1531


def test_own_subcategories_from_product_api():
    main, subs = s.own_subcategories(PRODUCT)
    assert main == 90000
    assert [(x.node, x.dept, x.rank) for x in subs] == [("9057034011", "fashion", 8), ("9056981011", "fashion", 12)]


def test_already_in_a_top_three_needs_no_search_and_costs_one_request():
    product = {**PRODUCT, "best_sellers_rank": PRODUCT["best_sellers_rank"][:1] + [
        {"rank": 3, "category": "X", "link": "https://www.amazon.com/gp/bestsellers/fashion/1/ref=x"}]}
    fake = Fake({}, product)
    result = s.search(OURS, "US", fetch_product=fake.fetch_product, fetch_page=fake.fetch_page, cache=s.MemoryCache())
    assert result.status == "not_needed" and result.requests == 1 and fake.urls == [f"product {OURS} com"]


def test_full_search_with_early_stop_on_k1_and_relevance_only_when_needed():
    weak, strong = "9057034011", "9056981011"
    pages = {
        URL + weak: bs_page("Men's Novelty Sun Hats", top("AA", 50), siblings=[("222", "Visors")]),
        URL + weak + "?pg=2": bs_page("Men's Novelty Sun Hats", top("AA", 50, 51)),
        "https://www.amazon.com/dp/B0AA000001": leader_page(460779, "#1 Best Seller in Men's Novelty Sun Hats"),
        URL + strong: bs_page("Women's Novelty Sun Hats", top("BB", 50)),
        "https://www.amazon.com/dp/B0BB000001": leader_page(358, "#1 Best Seller in X"),
        URL + "222": bs_page("Visors", top("CC", 50)),
        "https://www.amazon.com/dp/B0CC000001": leader_page(35598),
    }
    fake, asked = Fake(pages), []

    def relevance(our_title, titles):
        asked.append((our_title, len(titles)))
        return best_seller.RELEVANT

    result = s.search(OURS, "US", fetch_product=fake.fetch_product, fetch_page=fake.fetch_page,
                      cache=s.MemoryCache(), target_bsr=60000, relevance=relevance, now=NOW)
    assert result.status == "done" and result.our_bsr == 90000 and result.target_bsr == 60000
    by_node = {c.sub.node: c for c in result.candidates}
    assert set(by_node) == {weak, strong, "222"}  # свои подкатегории + сосед из меню
    ok = by_node[weak].verdict
    assert (ok.k1, ok.k2, ok.k3, ok.k4, ok.status, ok.priority) == (True, True, True, True, best_seller.SUITABLE, best_seller.HIGH)
    assert by_node[weak].info.item_count == 100
    assert by_node[strong].verdict.reason == "отклонена по К1" and by_node["222"].verdict.reason == "отклонена по К1"
    assert result.candidates[0].sub.node == weak  # подходящие сверху
    # ?pg=2 запрошен только для прошедшей К1, ИИ спрошен только для неё; итого 1 + 3×2 + 1 = 8 запросов.
    assert [u for u in fake.urls if "pg=2" in u] == [URL + weak + "?pg=2"]
    assert asked == [("Merino Sun Hat", 100)]
    assert result.requests == 8


def test_fresh_cache_saves_requests_and_old_cache_is_refreshed():
    weak = "9057034011"
    pages = {URL + weak: bs_page("Men's Novelty Sun Hats", top("AA", 50)),
             "https://www.amazon.com/dp/B0AA000001": leader_page(30000)}
    product = {**PRODUCT, "best_sellers_rank": PRODUCT["best_sellers_rank"][:2]}
    cache = s.MemoryCache()
    kw = dict(cache=cache, target_bsr=60000, now=NOW)
    first = Fake(pages, product)
    s.search(OURS, "US", fetch_product=first.fetch_product, fetch_page=first.fetch_page, **kw)
    again = Fake(pages, product)
    result = s.search(OURS, "US", fetch_product=again.fetch_product, fetch_page=again.fetch_page, **kw)
    assert result.requests == 1 and again.urls == [f"product {OURS} com"]  # подкатегория из кэша
    later = Fake(pages, product)
    s.search(OURS, "US", fetch_product=later.fetch_product, fetch_page=later.fetch_page, cache=cache,
             target_bsr=60000, now=NOW + timedelta(days=s.CACHE_DAYS, minutes=1))
    assert URL + weak in later.urls  # неделя прошла — топ запрошен заново


def test_without_relevance_the_candidate_waits_for_k3_and_failures_are_reported():
    weak = "9057034011"
    pages = {URL + weak: bs_page("X", top("AA", 50)), URL + weak + "?pg=2": bs_page("X", top("AA", 50, 51)),
             "https://www.amazon.com/dp/B0AA000001": leader_page(460779, "#1 Best Seller in X")}
    product = {**PRODUCT, "best_sellers_rank": PRODUCT["best_sellers_rank"][:3]}
    fake = Fake(pages, product)
    result = s.search(OURS, "US", fetch_product=fake.fetch_product, fetch_page=fake.fetch_page,
                      cache=s.MemoryCache(), target_bsr=60000, now=NOW)
    by_node = {c.sub.node: c.verdict for c in result.candidates}
    assert by_node[weak].status == best_seller.PENDING and by_node[weak].reason == "нужна оценка релевантности"
    assert by_node["9056981011"].reason == "страница подкатегории не получена"
    failed = Fake({}, None)
    bad = s.search(OURS, "US", fetch_product=failed.fetch_product, fetch_page=failed.fetch_page, cache=s.MemoryCache())
    assert bad.status == "error" and bad.error
    assert s.search(OURS, "XX", fetch_product=fake.fetch_product, fetch_page=fake.fetch_page,
                    cache=s.MemoryCache()).status == "error"


def test_max_candidates_caps_spending():
    siblings = [(str(1000 + i), f"S{i}") for i in range(30)]
    pages = {URL + "9057034011": bs_page("X", top("AA", 50), siblings=siblings)}
    product = {**PRODUCT, "best_sellers_rank": PRODUCT["best_sellers_rank"][:2]}
    fake = Fake(pages, product)
    result = s.search(OURS, "US", fetch_product=fake.fetch_product, fetch_page=fake.fetch_page,
                      cache=s.MemoryCache(), target_bsr=60000, now=NOW, max_candidates=5)
    assert len(result.candidates) == 5


def test_a_dropped_request_is_retried():
    calls = []

    def flaky_product(asin, domain):
        calls.append(asin)
        return None if len(calls) < 3 else PRODUCT

    result = s.search(OURS, "US", fetch_product=flaky_product, fetch_page=lambda url: None,
                      cache=s.MemoryCache(), max_candidates=1)
    assert len(calls) == 3 and result.status == "done"
    calls.clear()
    assert s.search(OURS, "US", fetch_product=lambda a, d: calls.append(a), fetch_page=lambda url: None,
                    cache=s.MemoryCache(), attempts=2).status == "error"
    assert len(calls) == 2


def test_our_listing_can_be_read_from_its_product_page():
    page = ('<span id="productTitle"> Classic Straw Hat </span>'
            '<div id="wayfinding-breadcrumbs_feature_div"><ul><a href="/b?node=7141123011">C</a>'
            '<a href="/b?node=9056985011">M</a></ul></div>'
            "<table><tr><th>Best Sellers Rank</th><td>#859,857 in Clothing (See Top 100) <ul>"
            "<li>#8 in <a href='/gp/bestsellers/fashion/9057034011/ref=pd_zg_hrsr_fashion'>Men's Novelty Sun Hats</a></li>"
            "<li>#12 in <a href='/gp/bestsellers/fashion/9056981011/ref=pd_zg_hrsr_fashion'>Women's Novelty Sun Hats</a></li>"
            "</ul></td></tr></table>")
    product = s.product_from_page(page)
    assert product["title"] == "Classic Straw Hat" and product["categories"] == [{"node": "7141123011"}, {"node": "9056985011"}]
    main, subs = s.own_subcategories(product)
    assert main == 859857 and [(x.node, x.name, x.rank) for x in subs] == [
        ("9057034011", "Men's Novelty Sun Hats", 8), ("9056981011", "Women's Novelty Sun Hats", 12)]
    assert s.product_from_page(None) is None and s.product_from_page("<html></html>") is None


def test_walk_goes_down_the_branches_to_leaf_subcategories_and_filters_by_name():
    root, hats = "2474954011", "300"
    pages = {
        URL + root: bs_page("Men's Hats & Caps", top("RR", 50), siblings=[(hats, "Hats"), ("303", "Balaclavas")]),
        URL + hats: bs_page("Hats", top("HH", 50), siblings=[("301", "Trapper Hats"), ("302", "Sun Hats")]),
        URL + "301": bs_page("Men's Trapper Hats", top("TT", 50), siblings=[("301", "Trapper Hats"), ("302", "Sun Hats")]),
        URL + "302": bs_page("Men's Sun Hats", top("SS", 50), siblings=[("301", "Trapper Hats"), ("302", "Sun Hats")]),
    }
    fake, asked = Fake(pages), []

    def keep(our_title, names):
        asked.append(list(names))
        return [n for n in names if n != "Balaclavas"]  # как «Balaclavas — пропускать сразу» в SOP

    leaves, used = s.walk_branches("US", [s.Branch("Men › Hats & Caps", "fashion", root)], fetch_page=fake.fetch_page,
                                   cache=s.MemoryCache(), now=NOW, keep=keep, our_title="Merino Beanie")
    assert [(x.node, x.name, x.path) for x in leaves] == [
        ("301", "Men's Trapper Hats", "Men › Hats & Caps › Hats › Trapper Hats"),
        ("302", "Men's Sun Hats", "Men › Hats & Caps › Hats › Sun Hats")]
    assert URL + "303" not in fake.urls and asked[0] == ["Hats", "Balaclavas"]
    assert used == 4


def test_walk_stops_at_the_page_limit():
    kids = [(f"{500 + i}", f"Kid {i}") for i in range(20)]
    pages = {URL + "400": bs_page("Root", top("RR", 5), siblings=kids)}
    pages.update({URL + n: bs_page(name, top("AA", 5), siblings=kids) for n, name in kids})
    fake = Fake(pages)
    leaves, used = s.walk_branches("US", [s.Branch("Root", "fashion", "400")], fetch_page=fake.fetch_page,
                                   cache=s.MemoryCache(), now=NOW, max_pages=6)
    assert used == 6 and len(leaves) == 5


def test_search_with_branches_checks_leaves_and_keeps_the_path():
    root = "2474954011"
    pages = {
        URL + root: bs_page("Men's Hats & Caps", top("RR", 50), siblings=[("302", "Sun Hats"), ("309", "Other")]),
        URL + "302": bs_page("Men's Sun Hats", top("SS", 50), siblings=[("302", "Sun Hats"), ("309", "Other")]),
        "https://www.amazon.com/dp/B0SS000001": leader_page(30000),
    }
    product = {**PRODUCT, "best_sellers_rank": PRODUCT["best_sellers_rank"][:1]}  # своих подкатегорий нет
    fake = Fake(pages, product)
    result = s.search(OURS, "US", fetch_product=fake.fetch_product, fetch_page=fake.fetch_page, cache=s.MemoryCache(),
                      target_bsr=60000, now=NOW, branches=[s.Branch("Men › Hats & Caps", "fashion", root)])
    assert [(c.sub.node, c.sub.path, c.verdict.reason) for c in result.candidates] == [
        ("302", "Men › Hats & Caps › Sun Hats", "отклонена по К1")]
    # страница подкатегории взята при обходе и повторно не запрашивается; «309» не открылась — в кандидаты не попала
    assert fake.urls.count(URL + "302") == 1


def test_keyword_filter_drops_never_relevant_and_beanie_skips_from_the_sop():
    names = ["Belts", "Hand Fans", "Hats & Caps", "Gloves & Mittens", "Keyrings, Keychains & Charms", "Balaclavas",
             "Visors", "Skullies & Beanies", "Sunglasses & Eyewear Accessories"]
    assert s.keyword_filter("Merino Wool Beanie", names) == ["Hats & Caps", "Gloves & Mittens", "Skullies & Beanies"]
    # не шапка — балаклавы и козырьки не выкидываются (для гейтора балаклава релевантна)
    assert "Balaclavas" in s.keyword_filter("Merino Neck Gaiter", names)
    assert "Belts" not in s.keyword_filter("Merino Neck Gaiter", names)


def test_season_sports():
    assert "skiing" in s.season_sports(NOW) and "cycling" not in s.season_sports(NOW)
    july = NOW.replace(month=7)
    assert "cycling" in s.season_sports(july) and "skiing" not in s.season_sports(july)
    assert "running" in s.season_sports(NOW) and "running" in s.season_sports(july)


def test_a_seasonal_branch_opens_only_this_seasons_sports_on_its_first_level():
    kids = [("601", "Airsoft"), ("602", "Winter Sports"), ("603", "Cycling"), ("604", "Ice Skating")]
    pages = {URL + "600": bs_page("Sport Specific Clothing", top("RR", 5), siblings=kids)}
    pages.update({URL + n: bs_page(name, top("AA", 5), siblings=kids) for n, name in kids})
    fake = Fake(pages)
    leaves, _ = s.walk_branches("US", [s.Branch("Sport", "fashion", "600", seasonal=True)], fetch_page=fake.fetch_page,
                                cache=s.MemoryCache(), now=NOW)
    assert [x.node for x in leaves] == ["602", "604"]  # сначала зимние, в порядке season_sports
    assert URL + "601" not in fake.urls and URL + "603" not in fake.urls


def test_next_page_link_is_not_a_subcategory():
    page = bs_page("Hats", top("HH", 3), siblings=[("301", "Trapper Hats")]).replace(
        "</body>", '<a href="/Best/zgbs/fashion/300/ref=zg_bs_pg_2_fashion?pg=2">Next page</a></body>')
    assert [x.name for x in s.parse_bestsellers(page).linked] == ["Trapper Hats"]


def test_candidates_are_taken_from_every_branch_in_turn():
    leaves = [s.Subcategory(str(i), "fashion", f"A{i}", path=f"Men › A{i}") for i in range(3)] + \
             [s.Subcategory(str(10 + i), "fashion", f"B{i}", path=f"Novelty › B{i}") for i in range(2)]
    assert [x.node for x in s._interleaved(leaves)] == ["0", "10", "1", "11", "2"]


def test_a_leader_page_without_bsr_is_asked_again():
    pages = {URL + "302": bs_page("Men's Sun Hats", top("SS", 50))}
    answers = iter(['<div id="zeitgeistBadge_feature_div"><span>#1 Best Seller</span></div>', leader_page(70000)])

    def fetch(url):
        return next(answers) if "/dp/" in url else pages.get(url)

    info, used = s._check_node("US", s.Subcategory("302", "fashion", "Sun Hats"), fetch, s.MemoryCache(), 60000, NOW)
    assert info.leader_bsr == 70000 and used == 4  # страница, лидер дважды, ?pg=2


def test_keyword_filter_skips_exotic_apparel_and_kids_for_an_adult_product():
    names = ["Exotic Apparel", "Men", "Women", "Boys", "Girls", "Baby"]
    assert s.keyword_filter("Merino Wool Beanie for Women, Men", names) == ["Men", "Women"]
    assert s.keyword_filter("Girls Ice Skating Pants", names) == ["Men", "Women", "Boys", "Girls", "Baby"]


def test_our_page_without_bsr_is_asked_again():
    answers = iter([{"title": "Merino Sun Hat", "best_sellers_rank": []}, PRODUCT])
    fake = Fake({})
    result = s.search(OURS, "US", fetch_product=lambda a, d: next(answers), fetch_page=fake.fetch_page,
                      cache=s.MemoryCache(), now=NOW, max_candidates=1)
    assert result.our_bsr == 90000 and len(result.own) == 2


def test_seasonal_sports_go_first_and_beanies_skip_garments():
    kids = [("701", "Hiking & Outdoor Recreation"), ("702", "Ice Skating"), ("703", "Running")]
    pages = {URL + "700": bs_page("Sport", top("RR", 5), siblings=kids)}
    pages.update({URL + n: bs_page(name, top("AA", 5), siblings=kids) for n, name in kids})
    leaves, _ = s.walk_branches("US", [s.Branch("Sport", "fashion", "700", seasonal=True)],
                                fetch_page=Fake(pages).fetch_page, cache=s.MemoryCache(), now=NOW)
    assert [x.node for x in leaves] == ["702", "703", "701"]
    assert s.keyword_filter("Merino Beanie", ["Pants", "Hoodies", "Accessories", "Hats"]) == ["Accessories", "Hats"]
    assert "Pants" in s.keyword_filter("Merino Base Layer", ["Pants", "Hoodies"])
