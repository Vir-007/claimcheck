"""Syndication detection is the project's differentiator, so it gets the most tests."""

from claimcheck.syndication import cluster, norm_text, pair_link, registrable_domain, similarity

from helpers import src

PTI = "Seven killed, 19 injured as truck hits pickup van in Maharashtra's Amravati"


def wire_story():
    # Same PTI copy, lightly re-headlined, published within hours.
    return [
        src(PTI, "https://www.thehindu.com/news/a1", "2026-10-02T16:23:39", outlet="The Hindu"),
        src("7 killed, 19 injured as truck hits pickup van in Maharashtras Amravati",
            "https://www.tribuneindia.com/news/a2", "2026-10-02T06:20:40", outlet="The Tribune"),
        src("Maharashtra: Seven killed, 19 injured as truck hits pickup van on Samruddhi Highway",
            "https://www.dailypioneer.com/a3", "2026-10-02T07:00:14", outlet="Pioneer Daily"),
        src("7 killed, 19 injured as truck hits pickup van in Maharashtra",
            "https://timesofindia.indiatimes.com/a4", "2026-10-02T06:08:00", outlet="The Times of India"),
    ]


def test_norm_text_maps_number_words_and_strips_possessives():
    assert norm_text("Seven killed in Maharashtra's Amravati") == norm_text("7 killed in Maharashtra Amravati")


def test_norm_text_strips_outlet_suffix():
    assert norm_text("Big news story - The Hindu", "The Hindu") == norm_text("Big news story")


def test_containment_matches_shorter_headline():
    a = norm_text("7 killed, 19 injured as truck hits pickup van in Maharashtra")
    b = norm_text(PTI)
    assert similarity(a, b) >= 0.6


def test_short_titles_do_not_match_by_containment():
    assert similarity(norm_text("National anthem"), norm_text("National anthem of India declared best")) < 0.5


def test_wire_copy_collapses_to_one_cluster():
    clusters = cluster(wire_story())
    assert len(clusters) == 1
    assert clusters[0].size == 4
    assert clusters[0].rule == "wire"
    # origin = earliest published member (TOI 06:08)
    assert clusters[0].members[0] == 3


def test_same_headline_far_apart_in_time_is_not_wire():
    a = src(PTI, "https://a.com/1", "2026-10-02T06:00:00")
    b = src(PTI.replace("Seven", "Six").replace("19", "12"), "https://b.com/2", "2026-12-25T06:00:00")
    assert pair_link(a, b) is None


def test_independent_reporting_stays_separate():
    a = src(PTI, "https://a.com/1", "2026-10-02T06:00:00")
    b = src("Samruddhi expressway crash kills 7, injures 20", "https://b.com/2", "2026-10-02T08:00:00")
    c = src("Maharashtra: 7 killed, 20 injured as truck rams Bolero on Samruddhi Mahamarg | Mumbai news",
            "https://c.com/3", "2026-10-02T09:00:00")
    assert len(cluster([a, b, c])) == 3


def test_same_wire_byline_lowers_threshold():
    a = src("Truck rams pickup van in Amravati, seven dead", "https://a.com/1", "2026-10-02T06:00:00",
            snippet="Amravati (PTI) A speeding truck rammed a pickup van carrying devotees on the expressway")
    b = src("Seven dead after truck hits pickup in Amravati district", "https://b.com/2", "2026-10-02T07:00:00",
            snippet="Amravati, Oct 2 (PTI) A speeding truck rammed a pickup van carrying devotees on Friday")
    assert a.wire == b.wire == "PTI"
    lk = pair_link(a, b)
    assert lk is not None and lk.rule == "wire"


def test_copy_paste_forward_links_regardless_of_date():
    msg = ('Congratulations to all of us. Our national anthem "Jana Gana Mana" is declared as the BEST ANTHEM '
           'OF THE WORLD by UNESCO. Just few minutes ago. Kindly share this.')
    a = src("Post", "https://www.facebook.com/p/1", "2012-10-06", snippet=msg, engine="google")
    b = src("Another post", "https://www.facebook.com/p/2", "2025-10-03", snippet=msg + " Jai Hind", engine="google")
    lk = pair_link(a, b)
    assert lk is not None and lk.rule == "copy-paste"


def test_publisher_network_links_sister_sites():
    assert registrable_domain("bangaloremirror.indiatimes.com") == registrable_domain("timesofindia.indiatimes.com")
    assert registrable_domain("www.example.co.in") == "example.co.in"
    a = src("Fake alert: No, UNESCO did not declare Indian national anthem best in the world",
            "https://timesofindia.indiatimes.com/x", "2019-08-19")
    b = src("UNESCO did not declare the Indian national anthem the best, fake alert",
            "https://bangaloremirror.indiatimes.com/y", "2019-11-30")
    lk = pair_link(a, b)
    assert lk is not None and lk.rule in ("publisher", "copy-paste")


def test_opposite_stances_never_cluster():
    a = src("UNESCO declared Jana Gana Mana the best national anthem in the world", "https://a.com/1", "2016-01-01")
    b = src("UNESCO declared Jana Gana Mana the best national anthem in the world? No", "https://b.com/2", "2016-01-01")
    assert pair_link(a, b) is not None
    assert pair_link(a, b, "supports", "contradicts") is None
    assert pair_link(a, b, "supports", "partial") is not None


def test_complete_linkage_prevents_chaining():
    # a~b and b~c are each similar enough to link, but a and c are not:
    # single-linkage would chain all three into one "story".
    w = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima".split()
    a = src(" ".join(w[0:8]), "https://a.com/1", "2026-10-02T06:00:00")
    b = src(" ".join(w[2:10]), "https://b.com/2", "2026-10-02T06:30:00")
    c = src(" ".join(w[4:12]), "https://c.com/3", "2026-10-02T07:00:00")
    assert pair_link(a, b) and pair_link(b, c) and not pair_link(a, c)
    assert sorted(cl.size for cl in cluster([a, b, c])) == [1, 2]


def test_undated_requires_higher_similarity():
    a = src("Papaya leaf juice raises platelet count in dengue patients study finds", "https://a.com/1", None)
    b = src("Papaya leaf juice raises platelet count in dengue patients: study", "https://b.com/2", None)
    c = src("Papaya leaf juice and dengue: what doctors say about platelets", "https://c.com/3", None)
    assert pair_link(a, b) is not None
    assert pair_link(a, c) is None
