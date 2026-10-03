from claimcheck.stance import CONTRADICTS, PARTIAL, SUPPORTS, UNRELATED, Stance
from claimcheck.verdict import score_claim

from helpers import src

CLAIM = "Seven people were killed when a truck hit a pickup van in Amravati, Maharashtra"


def S(label, conf=0.9, rel=0.8):
    return Stance(label, conf, "ev", "test", rel)


def test_syndicated_support_counts_once():
    sources = [
        src("Seven killed, 19 injured as truck hits pickup van in Maharashtra's Amravati", "https://a.com/1",
            "2026-10-02T06:00:00", outlet="A"),
        src("7 killed, 19 injured as truck hits pickup van in Maharashtras Amravati", "https://b.com/2",
            "2026-10-02T07:00:00", outlet="B"),
        src("Seven killed, 19 injured as truck hits pickup van in Amravati", "https://c.com/3",
            "2026-10-02T08:00:00", outlet="C"),
        src("Amravati: police say speeding truck rammed Bolero carrying devotees", "https://d.com/4",
            "2026-10-02T09:00:00", outlet="D"),
        src("Cricket score", "https://e.com/5", "2026-10-02T09:00:00"),
    ]
    stances = [S(SUPPORTS), S(SUPPORTS), S(SUPPORTS), S(SUPPORTS), S(UNRELATED)]
    v = score_claim(CLAIM, sources, stances, [], "test")
    assert v.naive[SUPPORTS] == 4
    assert v.independent[SUPPORTS] == 2  # the wire cluster + D
    assert len(v.syndicated) == 1 and len(v.syndicated[0].items) == 3
    assert v.syndicated[0].origin.source.outlet == "A"
    assert v.label == "Supported"
    assert v.earliest.source.outlet == "A"
    assert v.total_sources == 5 and len(v.evidence) == 4


def test_fact_checkers_drive_false_label_and_dissent():
    sources = [
        src("FAKE: UNESCO did not declare anthem best", "https://www.altnews.in/1", "2017-04-20"),
        src("No, UNESCO never ranked national anthems", "https://www.boomlive.in/2", "2018-01-01"),
        src("UNESCO declared Jana Gana Mana best anthem, rejoice", "https://www.facebook.com/p/3", "2016-01-01",
            engine="google"),
        src("Viral claim about anthem is false says ministry", "https://news.example.com/4", "2019-01-01"),
    ]
    stances = [S(CONTRADICTS), S(CONTRADICTS), S(SUPPORTS), S(CONTRADICTS)]
    v = score_claim("UNESCO declared Jana Gana Mana the best national anthem", sources, stances, [], "test")
    assert v.label.startswith("False")
    assert v.score < -0.5
    assert [d.origin.source.domain for d in v.dissent] == ["facebook.com"]
    assert v.earliest_contradiction.source.domain == "altnews.in"


def test_mixed_evidence_is_contested():
    sources = [src(f"Study {i} on papaya leaf {w}", f"https://s{i}.org/p", None, engine="google_scholar")
               for i, w in enumerate(["helps", "works", "fails", "no effect"])]
    stances = [S(SUPPORTS), S(SUPPORTS), S(CONTRADICTS), S(CONTRADICTS)]
    v = score_claim("Papaya leaf extract increases platelet count", sources, stances, [], "test")
    assert v.label == "Contested / mixed evidence"


def test_insufficient_evidence():
    v = score_claim("x", [src("a", "https://a.com")], [S(PARTIAL)], [], "test")
    assert v.label == "Insufficient evidence"


def test_same_outlet_two_articles_one_voice():
    sources = [
        src("Papaya leaves help platelets, say doctors", "https://timesofindia.indiatimes.com/a", "2023-07-08"),
        src("Can papaya help in dengue? Experts weigh in", "https://timesofindia.indiatimes.com/b", "2026-01-09"),
        src("Other outlet piece", "https://other.com/c", "2024-01-01"),
    ]
    stances = [S(PARTIAL), S(PARTIAL), S(PARTIAL)]
    v = score_claim("Papaya leaf extract increases platelet count", sources, stances, [], "test")
    assert v.independent[PARTIAL] == 2
