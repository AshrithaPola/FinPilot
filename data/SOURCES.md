# Data sources and licences

Checked on **2026-11-13** by reading each source's own terms page (or, where
noted, the closest official statement found). The public documents
fetched so far are listed under "Fetched" below (2026-11-14). "Allowed"
means what the terms say, not legal advice. Re-check before
the public deploy (Phase 12).

| Source | Use in FinPilot | Terms checked | What's allowed | Decision |
|---|---|---|---|---|
| **FinPilot Bank corpus** (`data/bank/`) | Bank documents, rate tables, OpenAPI spec | Written for this project | Ours | Use. Drafts pending review. |
| **Synthetic customers** (`backend/bank/synthetic.py`) | All customer data | Generated from a seed | Ours | Use. The only customer data. |
| **IBM TabFormer** (synthetic card transactions) | Considered for amounts and merchant mix | [github.com/IBM/TabFormer](https://github.com/IBM/TabFormer) | The repository is **Apache-2.0**. No separate licence or terms for the dataset under `data/credit_card`; it presumably falls under the repo licence, but that isn't stated. The repo was archived (read-only) on 2026-10-28. | **Not used** (your decision, 2026-11-13): transactions are fully synthetic, with realistic patterns (salary, rent, subscriptions, groceries, occasional overdrafts). |
| **Berka / PKDD'99 Financial** (Czech bank) | Considered | [relational.fel.cvut.cz/dataset/Financial](https://relational.fel.cvut.cz/dataset/Financial); search for any other terms | **No licence or terms of use found** on the hosting page or elsewhere. It was released for the PKDD'99 challenge; citing Berka, *Guide to the Financial Data Set* (PKDD 2000 Discovery Challenge) is the usual practice. | **Not used.** No stated licence. |
| **Alpha Vantage** (price API pick) | Daily closes for the ETFs in the demo, cached in Postgres | [alphavantage.co/terms_of_service](https://www.alphavantage.co/terms_of_service/) | Free key: **personal, non-commercial use**. Use is "commercial" if, among other things, information is provided "as part of any type of commercial activity that allows individuals or entities other than User to access information". Licence is non-sublicensable and non-transferable. Free tier: 25 requests/day. | **Use** (your decision, 2026-11-13): real prices, **shown only to signed-in demo accounts**, cached in the database and **never committed**, always with attribution ("Prices: Alpha Vantage") and the as-of date. |
| **FRED** (St. Louis Fed) | Rates and macro series (e.g. fed funds, CPI, Treasury yields) | [fred.stlouisfed.org/docs/api/terms_of_use.html](https://fred.stlouisfed.org/docs/api/terms_of_use.html) | API key required. The app must show: *"This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis."* No FRED logos or implied endorsement. **Some series are owned by third parties** (marked "Copyright" in their notes) and need the owner's permission beyond personal use. Caching isn't addressed; bandwidth must be reasonable. | **Use**, only series with no third-party copyright note; show the notice in the UI; cache in the database, not the repo. |
| **SEC EDGAR / data.sec.gov** | 10-K Items 1A and 7 for 3–4 companies; 2–3 ETF prospectuses | [sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data); [sec.gov/privacy](https://www.sec.gov/privacy) | Free access. Requests must **declare a User-Agent** with a name and contact email (SEC's sample: "Sample Company Name AdminContact@<sample company domain>.com"). **Max 10 requests/second.** sec.gov information "may be copied or further distributed … without the SEC's permission"; cite the SEC; don't use the SEC seal or logos. The page doesn't address copyright in the filings themselves (written by the companies). | **Use.** User-Agent `FinPilot research project <your name> <your email>`, read from `SEC_USER_AGENT` in `.env` (never committed); the fetcher stays under 10 requests a second. Store extracted sections in the database; commit only the list of filings (CIK, accession number), not their text. |
| **Investor.gov** (SEC Office of Investor Education and Advocacy) | Investor-education articles for the documents agent | Its policies page returned **HTTP 403** to the checker; [sec.gov/privacy](https://www.sec.gov/privacy) covers the SEC's own sites | Investor.gov is run by the SEC, and the SEC states that information it creates "may be copied or further distributed" without permission. Third-party images and linked material are excluded. | **Use** (your decision, 2026-11-13): treated like other SEC-created content. Text only, each chunk cited ("Source: Investor.gov, U.S. Securities and Exchange Commission") with its URL; no SEC seal or logos; third-party material skipped. **Not fetched (2026-11-14):** its CDN (Akamai) answers 403 to non-browser clients, the declared SEC User-Agent included, for pages and PDFs alike; FinPilot doesn't impersonate a browser. |
| **CFPB** (consumerfinance.gov) | Consumer guides; model forms as **structure references only** | [consumerfinance.gov/privacy/website-privacy-policy](https://www.consumerfinance.gov/privacy/website-privacy-policy/) | "Information created by the CFPB is in the public domain and you may reproduce, publish, or otherwise use it" without permission; please cite the Bureau. Third-party material may be copyrighted. The CFPB name and logo are registered trademarks. | **Use** CFPB-created text, cited; no logo. The bank's documents follow the structure of CFPB model forms but copy no text. |
| **FDIC** (fdic.gov, BankFind API) | Possible: deposit insurance explainer; bank reference data | [fdic.gov/policies](https://www.fdic.gov/policies); [catalog.data.gov: FDIC BankFind Suite API](https://catalog.data.gov/dataset/fdic-bankfind-suite-api) | fdic.gov's policies page says only that **external** sites' material may be copyrighted and permission must come from its owner; it makes **no public-domain statement** about FDIC's own content. The FDIC Archive calls government-created material on it public domain. data.gov lists BankFind under a **U.S. public domain** licence. | **BankFind data: use.** fdic.gov text: treat as a U.S. government work and cite it, but confirm first. FinPilot Bank is fictional and must never claim FDIC membership. |
| **FINRA** | Investor-education content | Not checked in detail: FINRA content is copyrighted | — | **Link only, never ingested** (your decision). |

## Rules that follow from this

- Committed: our own documents, the generator, lists of filings and series
  ids. Not committed: fetched prices, fetched filing text, FRED values, any
  third-party dataset.
- Every answer that uses a public source cites it; the UI shows the FRED
  notice wherever FRED data appears.
- Each new source gets a row here before any code fetches it.

## Fetched (2026-11-14)

The list lives in [`data/public/sources.yaml`](public/sources.yaml) (CIK,
accession number, document, filing date, or URL); `python -m bank.public
fetch` downloads it into `data/.cache/public/` (gitignored) and `load`
stores it in the database. The text is never committed. Every document
opens with its attribution line, so every chunk can be traced to its source.

| Document | Source | What is used |
|---|---|---|
| Apple Inc. Form 10-K, FY2026 | SEC EDGAR, CIK 320193, accession 0000320193-26-000026 (filed 2026-11-03) | Items 1A and 7 |
| Microsoft Corporation Form 10-K, FY2026 | SEC EDGAR, CIK 789019, accession 0001193125-26-323660 (filed 2026-07-29) | Items 1A and 7 |
| The Coca-Cola Company Form 10-K, FY2025 | SEC EDGAR, CIK 21344, accession 0001628280-26-010047 (filed 2026-02-20) | Items 1A and 7 |
| Vanguard Total Stock Market ETF (VTI) summary prospectus | SEC EDGAR, Form 497K, CIK 36405, accession 0000036405-26-000197 (filed 2026-04-28) | Whole document |
| Vanguard Total Bond Market ETF (BND) summary prospectus | SEC EDGAR, Form 497K, CIK 794105, accession 0000794105-26-000124 (filed 2026-04-28) | Whole document |
| Schwab U.S. Dividend Equity ETF (SCHD) summary prospectus | SEC EDGAR, Form 497K, CIK 1454889, accession 0001104659-26-020681 (filed 2026-02-27) | Whole document |
| What is an overdraft? | CFPB, consumerfinance.gov/ask-cfpb/what-is-an-overdraft-en-1035/ | The answer, without the related-question links |
| How do I dispute a charge on my credit card bill? | CFPB, consumerfinance.gov/ask-cfpb/how-do-i-dispute-a-charge-on-my-credit-card-bill-en-61/ | Same |
| How do I get my money back after I discover an unauthorized transaction? | CFPB, consumerfinance.gov/ask-cfpb/…-en-1017/ | Same |

SEC requests declared `SEC_USER_AGENT` and were paced at 4 a second (limit
10); CFPB requests one a second with a plain project User-Agent. The ETFs
match three of the four demo holdings (VXUS isn't fetched); the companies
are large holdings of those funds.
