"""EDGAR client, XBRL normalization, 10-K section extraction and the point-in-time fact store."""
from __future__ import annotations

import httpx
import pytest
from src.filings.edgar import EdgarClient, EdgarError, TokenBucket, cik10, recent_filings
from src.filings.factstore import FactStore, get_fact_store
from src.filings.sections import chunk_text, extract_risk_factors, html_to_text, passage_id
from src.filings.xbrl import Fact, normalize_company_facts, restatements

UA = "Test Runner test@example.com"


# ------------------------------------------------------------------ edgar
class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def test_token_bucket_allows_burst_then_throttles() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate=10, capacity=10, clock=clock.now, sleep=clock.sleep)
    for _ in range(10):
        bucket.acquire()
    assert clock.slept == []
    bucket.acquire()  # 11th request in the same instant must wait ~1/rate
    assert sum(clock.slept) == pytest.approx(0.1)


def _client(handler) -> EdgarClient:
    clock = FakeClock()
    return EdgarClient(user_agent=UA, transport=httpx.MockTransport(handler), max_retries=3,
                       bucket=TokenBucket(1000, 1000, clock=clock.now, sleep=clock.sleep))


def test_client_requires_contact_user_agent() -> None:
    with pytest.raises(EdgarError, match="contact email"):
        EdgarClient(user_agent="no-contact")


def test_client_retries_transient_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.filings.edgar.time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.headers["User-Agent"] == UA
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json={"ok": True})

    assert _client(handler).company_facts(320193) == {"ok": True}
    assert calls["n"] == 3


def test_client_does_not_retry_client_errors() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    with pytest.raises(EdgarError, match="404"):
        _client(handler).submissions(1)
    assert calls["n"] == 1


def test_client_gives_up_after_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.filings.edgar.time.sleep", lambda s: None)
    with pytest.raises(EdgarError, match="4 attempts"):
        _client(lambda r: httpx.Response(429)).company_facts(1)


def test_url_shapes() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "data.sec.gov":
            return httpx.Response(200, json={})
        return httpx.Response(200, text="<html/>")

    c = _client(handler)
    c.company_facts(320193)
    c.filing_document(320193, "0000320193-25-000079", "aapl.htm")
    assert seen == [
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl.htm",
    ]
    assert cik10("320193") == "0000320193"


def test_recent_filings_flattens_and_filters() -> None:
    subs = {"filings": {"recent": {
        "accessionNumber": ["a1", "a2", "a3"], "form": ["10-K", "8-K", "10-Q"],
        "filingDate": ["2025-10-31", "2025-10-01", "2025-08-01"], "reportDate": ["", "", ""],
        "acceptanceDateTime": ["t1", "t2", "t3"], "primaryDocument": ["k.htm", "e.htm", "q.htm"],
    }}}
    assert [f["accessionNumber"] for f in recent_filings(subs)] == ["a1", "a3"]


# ------------------------------------------------------------------- xbrl
def _row(val, start, end, accn, fy, fp, form, filed):
    r = {"val": val, "end": end, "accn": accn, "fy": fy, "fp": fp, "form": form, "filed": filed}
    if start:
        r["start"] = start
    return r


def _payload() -> dict:
    """Two 10-Ks. The FY2025 10-K also carries the FY2024 comparative, tagged
    fy=2025 (the trap). FY2024 revenue was tagged `Revenues` originally and
    `RevenueFromContract...` later; FY2024 net income is restated."""
    k24, k25 = "0000000001-24-000001", "0000000001-25-000001"
    return {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            _row(100, "2023-01-01", "2023-12-31", k24, 2024, "FY", "10-K", "2024-02-01"),  # FY2023 comparative
            _row(110, "2024-01-01", "2024-12-31", k24, 2024, "FY", "10-K", "2024-02-01"),
        ]}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            _row(110, "2024-01-01", "2024-12-31", k25, 2025, "FY", "10-K", "2025-02-01"),
            _row(130, "2025-01-01", "2025-12-31", k25, 2025, "FY", "10-K", "2025-02-01"),
            _row(30, "2025-10-01", "2025-12-31", k25, 2025, "FY", "10-K", "2025-02-01"),  # quarterly in a 10-K = Q4
            _row(77, "2025-01-01", "2025-09-30", k25, 2025, "FY", "10-K", "2025-02-01"),  # 9M YTD: dropped
        ]}},
        "NetIncomeLoss": {"units": {"USD": [
            _row(10, "2024-01-01", "2024-12-31", k24, 2024, "FY", "10-K", "2024-02-01"),
            _row(12, "2024-01-01", "2024-12-31", k25, 2025, "FY", "10-K", "2025-02-01"),  # restated
            _row(15, "2025-01-01", "2025-12-31", k25, 2025, "FY", "10-K", "2025-02-01"),
        ]}},
        "Assets": {"units": {"USD": [_row(500, None, "2025-12-31", k25, 2025, "FY", "10-K", "2025-02-01")]}},
    }}}


def test_fiscal_year_comes_from_the_period_not_the_filing() -> None:
    facts = normalize_company_facts(_payload(), "TST")
    rev = {(f.fiscal_year, f.fiscal_period, f.accession): f.value for f in facts if f.metric == "revenue"}
    assert rev[(2024, "FY", "0000000001-25-000001")] == 110  # comparative in the FY2025 10-K is still FY2024
    assert rev[(2025, "FY", "0000000001-25-000001")] == 130
    assert rev[(2025, "Q4", "0000000001-25-000001")] == 30


def test_aliases_resolve_per_period_in_priority_order() -> None:
    facts = normalize_company_facts(_payload(), "TST")
    by_period = {(f.fiscal_year, f.accession): f.concept for f in facts if f.metric == "revenue" and f.fiscal_period == "FY"}
    # FY2024 from the old filing is only available under the legacy tag...
    assert by_period[(2024, "0000000001-24-000001")] == "Revenues"
    # ...while the newer filing's comparative of the same period uses the preferred tag.
    assert by_period[(2024, "0000000001-25-000001")] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    # A period seen only as a comparative (FY2023) has no filing of its own to
    # label it, so it is left out rather than guessed.
    assert not any(f.fiscal_year == 2023 for f in facts)


def test_ytd_durations_dropped_and_instants_kept() -> None:
    facts = normalize_company_facts(_payload(), "TST")
    assert not any(f.value == 77 for f in facts)
    assets = [f for f in facts if f.metric == "total_assets"]
    assert assets[0].period_type == "instant" and assets[0].fiscal_period == "FY"


def test_restatements_detected() -> None:
    groups = restatements(normalize_company_facts(_payload(), "TST"))
    assert [f.value for f in groups[("net_income", "FY", 2024)]] == [10, 12]


# --------------------------------------------------------------- sections
_HTML = """<html><body>
<div style="display:none"><ix:header><ix:hidden>secret 999</ix:hidden></ix:header></div>
<p>Item 1A. Risk Factors ........ 12</p><p>Item 1B. Unresolved Staff Comments ... 20</p>
<p>Item 1A.Risk Factors</p><p>Our supply chain is concentrated in Asia.</p>
<p>Table of Contents</p><p>14</p>
<p>Export controls may restrict sales of data center products.</p>
<p>Item 1B.Unresolved Staff Comments</p><p>None.</p><script>var x=1;</script>
</body></html>"""


def test_html_to_text_skips_hidden_xbrl_and_scripts() -> None:
    text = html_to_text(_HTML)
    assert "secret 999" not in text and "var x" not in text
    assert "supply chain" in text


def test_extract_risk_factors_takes_body_not_table_of_contents() -> None:
    section = extract_risk_factors(html_to_text(_HTML))
    assert "supply chain" in section and "Export controls" in section
    assert "Unresolved" not in section and "None." not in section


def test_chunking_drops_page_furniture_and_respects_size() -> None:
    chunks = chunk_text(extract_risk_factors(html_to_text(_HTML)))
    joined = "\n".join(chunks)
    assert "Table of Contents" not in joined and "\n14\n" not in f"\n{joined}\n"
    long = "\n".join(f"Paragraph {i} " + "word " * 60 for i in range(30))
    assert all(len(c) <= 1800 for c in chunk_text(long))


def test_passage_id_is_content_addressed() -> None:
    assert passage_id("A", "acc", 1, "x") == passage_id("A", "acc", 1, "x")
    assert passage_id("A", "acc", 1, "x") != passage_id("A", "acc", 1, "y")


# -------------------------------------------------------------- factstore
def _store() -> FactStore:
    s = FactStore()
    s.upsert(normalize_company_facts(_payload(), "TST"))
    return s


def test_point_in_time_ignores_later_filings() -> None:
    s = _store()
    assert s.get("TST", "net_income", 2024).value == 12  # latest (restated)
    assert s.get("TST", "net_income", 2024, as_of="2024-06-30").value == 10  # as originally filed
    assert s.get("TST", "revenue", as_of="2024-06-30").fiscal_year == 2024  # FY2025 not filed yet
    assert s.get("TST", "revenue", as_of="2023-01-01") is None


def test_versions_and_upsert_idempotency() -> None:
    s = _store()
    n, v = s.count(), s.data_version
    s.upsert([s.get("TST", "revenue", 2025)])  # a redelivered message
    assert s.count() == n and s.data_version == v + 1
    assert [x.value for x in s.versions("TST", "net_income", 2024)] == [10, 12]
    assert s.upsert([]) == 0


def test_derived_metrics_carry_lineage() -> None:
    s = _store()
    margin = s.derived("TST", "net_margin", 2025)
    assert margin.value == pytest.approx(15 / 130)
    assert margin.inputs == ("TST:net_income:FY2025:0000000001-25-000001", "TST:revenue:FY2025:0000000001-25-000001")
    growth = s.derived("TST", "revenue_growth_yoy", 2025)
    assert growth.value == pytest.approx(130 / 110 - 1)
    assert s.derived("TST", "debt_to_equity", 2025) is None  # inputs not reported
    with pytest.raises(KeyError):
        s.derived("TST", "made_up", 2025)


def test_implied_q4_uses_reported_q4_when_present() -> None:
    q4 = _store().implied_q4("TST", "revenue", 2025)
    assert q4.value == 30 and q4.formula == "reported"
    assert _store().implied_q4("TST", "total_assets", 2025) is None


def test_fixture_store_matches_sec_ground_truth() -> None:
    """Real data: Apple FY2025 (10-K 0000320193-25-000079) and the split-driven EPS restatement."""
    s = get_fact_store()
    assert s.get("AAPL", "revenue", 2025).value == 416_161_000_000
    assert s.get("AAPL", "eps_diluted", 2019, as_of="2020-06-01").value == 11.89
    assert s.get("AAPL", "eps_diluted", 2019).value == 2.97
    q4 = s.implied_q4("AAPL", "revenue", 2025)
    assert q4.formula == "FY - Q1 - Q2 - Q3" and len(q4.inputs) == 4


def test_fact_round_trip() -> None:
    f = _store().get("TST", "revenue", 2025)
    assert Fact(**{k: v for k, v in f.to_dict().items() if k != "fact_id"}) == f
