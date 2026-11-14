"""bank/public.py without the network: HTML to Markdown, 10-K item spans,
page furniture, tables, attribution, and the committed source list."""
from bank.public import blocks_of, item_span, load_sources, to_markdown

TEN_K = b"""<html><body>
<div style="display:none"><ix:header>hidden XBRL</ix:header></div>
<table><tr><td>Item 1A.</td><td>Risk Factors</td><td>12</td></tr></table>
<p>Item 1A. Risk Factors</p>
<p>Table of Contents</p>
<p>Item 1B. Unresolved Staff Comments</p>
<div><span style="font-weight:700">Item 1A. Risk Factors</span></div>
<div><span style="font-weight:700">Macroeconomic Risks</span></div>
<p>Demand for the products depends on economic conditions in many markets.</p>
<p>Item 1A</p>
<p>Apple Inc. | 2026 Form 10-K | 17</p>
<p>Currency movements can reduce reported sales.</p>
<div><span style="font-weight:700">Item 1B. Unresolved Staff Comments</span></div>
<p>None.</p>
<div><b>Item 7. Management's Discussion and Analysis</b></div>
<table><tr><td>Segment</td><td>2026</td></tr>
<tr><td>Americas</td><td>$</td><td>178,353</td></tr></table>
<p>Net sales grew in every segment.</p>
<div><b>Item 8. Financial Statements</b></div>
<p>Balance sheet.</p>
</body></html>"""


def test_item_spans_skip_the_table_of_contents_and_drop_furniture():
    blocks = blocks_of(TEN_K)
    assert all("hidden XBRL" not in str(v) for _, v in blocks)
    start, end = item_span(blocks, "1A")
    inside = [v for _, v in blocks[start:end]]
    assert ("heading", "Macroeconomic Risks") in blocks[start:end]
    assert "Currency movements can reduce reported sales." in inside
    assert not any(t in inside for t in ("Item 1A", "Apple Inc. | 2026 Form 10-K | 17", "Table of Contents"))


def test_a_10k_becomes_items_1a_and_7_with_attribution_and_tables():
    source = {"id": "x", "kind": "sec_10k", "title": "Apple Inc. Form 10-K", "cik": 320193,
              "accession": "0000320193-26-000026", "document": "aapl-20260926.htm", "filed": "2026-11-03"}
    text = to_markdown(source, TEN_K)
    assert text.startswith("# Apple Inc. Form 10-K\n\nSource: Apple Inc. Form 10-K, filed with the U.S. "
                           "Securities and Exchange Commission on 2026-11-03")
    assert "https://www.sec.gov/Archives/edgar/data/320193/000032019326000026/aapl-20260926.htm" in text
    assert "## Item 1A. Risk Factors" in text and "### Macroeconomic Risks" in text
    assert "## Item 7. Management's Discussion and Analysis" in text
    assert "| Segment | 2026 |\n|---|---|\n| Americas | 178,353 |" in text  # the "$" cell dropped
    assert "Unresolved Staff Comments" not in text and "Balance sheet." not in text


def test_a_cfpb_answer_stops_before_its_link_lists():
    page = b"""<html><body><nav>Menu</nav><main>
    <h1>What is an overdraft?</h1><p>An overdraft occurs when the bank pays anyway.</p>
    <h2>Browse related questions</h2><ul><li>What is a fee?</li></ul></main></body></html>"""
    source = {"id": "c", "kind": "cfpb", "title": "CFPB: What is an overdraft?", "url": "https://example.gov/a"}
    text = to_markdown(source, page)
    assert "An overdraft occurs when the bank pays anyway." in text and "Source: Consumer Financial" in text
    assert "Browse related questions" not in text and "Menu" not in text


def test_every_committed_source_is_complete():
    for source in load_sources():
        assert source["kind"] in {"sec_10k", "sec_prospectus", "cfpb", "investor_gov"}
        if source["kind"].startswith("sec_"):
            assert {"cik", "accession", "document", "filed"} <= set(source), source["id"]
        else:
            assert source["url"].startswith("https://"), source["id"]
