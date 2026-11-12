# Data Available via EdgarTools (`edgartools` v6.0.0)

Complete reference of all SEC EDGAR data extractable with `edgartools`, organized by data domain with exact Python/CLI commands and explanations.

---

## 1. Setup & Prerequisites

Every SEC EDGAR request requires a User-Agent identity (`Name email@domain.com`).

```bash
# Core package (Python >= 3.11)
pip install edgartools

# Optional extras
pip install "edgartools[ai]"          # MCP server & AI token/HTTP dependencies
pip install "edgartools[data]"        # DuckDB & Zstandard support for bulk datasets
pip install "edgartools[all-cloud]"   # fsspec + S3, GCS, and Azure Blob storage
```

```python
from edgar import set_identity, get_identity, configure_http, diagnose_ssl

set_identity("Your Name your.email@example.com")  # Or set env var EDGAR_IDENTITY
get_identity()                                    # Inspect active SEC identity
diagnose_ssl()                                    # Troubleshoot SSL/certificate issues
```

---

## 2. Company, Entity & Filer Metadata

Extract legal identity, CIK, tickers, exchanges, SIC industry classification, addresses, SEC filer status, and cover-page share/float metrics.

| Data Extracted | Exact Command | Explanation |
| :--- | :--- | :--- |
| Company / Entity lookup | `Company("AAPL")` / `Entity(320193)` | Resolves ticker, CIK, or name (`Company` adds financial shortcuts) |
| Universal smart lookup | `find("AAPL")` / `find("0000320193-23-000106")` | Auto-routes tickers, CIKs, accession numbers, fund IDs, or names |
| Fuzzy company search | `find_company("Apple")` | Searches SEC company directory by name |
| Bulk ticker directory | `get_company_tickers()` | Full SEC ticker-to-CIK mapping table |
| All public companies | `from edgar.entity import public_companies; public_companies()` | Iterates/returns public company records |
| Core company profile | `company.cik`, `company.name`, `company.tickers`, `company.get_exchanges()` | Legal name, CIK, ticker symbols, and listed exchanges |
| Industry & fiscal year | `company.sic`, `company.industry`, `company.fiscal_year_end` | SEC SIC code, industry label, and FYE (`MMDD`) |
| Addresses | `company.mailing_address()`, `company.business_address()` | Registered mailing and business street addresses |
| Filer classification | `company.filer_category()`, `company.filer_type()`, `company.business_category()` | Accelerated/SRC/EGC status, domestic/foreign, business type (`REIT`, `BDC`, etc.) |
| Filer boolean flags | `company.is_large_accelerated_filer`, `company.is_smaller_reporting_company`, `company.is_emerging_growth_company`, `company.is_foreign`, `company.is_fund` | SEC status flags |
| Predecessor entity | `company.predecessor()` | Resolves predecessor filer history when applicable |
| Cover-page DEI metrics | `company.shares_outstanding`, `company.public_float` | Latest DEI shares outstanding and public float |
| Company icon | `company.get_icon()` / `get_icon_from_ticker("AAPL")` | Company logo/icon bytes |
| LLM-ready summary | `company.to_context(detail="standard")` | Token-budgeted structured company summary |

```python
from edgar import Company, find, find_company

company = Company("AAPL")
print(company.cik, company.name, company.sic, company.industry)
print(company.business_address())
print(company.shares_outstanding, company.public_float)

# Search companies by partial name
results = find_company("Nvidia")
```

---

## 3. Filing Discovery, Real-Time Feeds & Full-Text Search

Query SEC filing indexes by date/form/quarter, monitor the live SEC RSS/Atom feed, or search inside filing text via EDGAR EFTS.

| Data Extracted | Exact Command | Explanation |
| :--- | :--- | :--- |
| Historical filing index | `get_filings(year=2025, quarter=1, form="10-K")` | Queries quarterly SEC master index (`Filings` collection) |
| Company-specific filings | `company.get_filings(form="10-K", amendments=False)` | Lists all filings for one entity, optionally excluding `/A` |
| Filter & slice filings | `filings.filter(form=["10-K", "10-Q"], filing_date="2024-01-01:")` | Subsets by form, date range, CIK, or ticker |
| Latest / head / tail | `filings.latest(5)` / `filings.head(10)` / `filings.tail(10)` | Returns newest or slice of `Filing` objects |
| Lookup by accession | `get_by_accession_number("0000320193-23-000106")` | Direct retrieval of a single SEC filing |
| Export index table | `filings.to_pandas()` | Converts filing metadata index to a pandas DataFrame |
| Real-time SEC feed | `get_current_filings(form="8-K")` | Live recent SEC filings page (`CurrentFilings`) |
| Full day/feed scan | `get_all_current_filings(form="4")` / `iter_current_filings_pages()` | Paginates all entries in the current SEC feed |
| Pre-filtered form feeds | `get_insider_transaction_filings()`, `get_portfolio_holding_filings()`, `get_fund_portfolio_filings()`, `get_money_market_filings()`, `get_restricted_stock_filings()` | Shortcuts for Forms 3/4/5, 13F, NPORT, N-MFP, and Form 144 |
| Full-text search (EFTS) | `search_filings("artificial intelligence", forms=["10-K"], start_date="2024-01-01")` | Searches text inside SEC filings via `efts.sec.gov` (up to 10,000 hits) |
| Faceted EFTS search | `EFTSSearch("tariff impact", forms=["10-K", "8-K"])` | Exposes `.aggregations` (entity, SIC, state, form counts) |

```python
from edgar import get_filings, get_current_filings, search_filings, get_by_accession_number

# Historical 10-K index for Q1 2025
filings = get_filings(year=2025, quarter=1, form="10-K")
df = filings.to_pandas()

# Live 8-K filings arriving now
latest_8ks = get_current_filings(form="8-K")

# Full-text search across filing contents
hits = search_filings("cybersecurity incident", forms=["8-K"], start_date="2025-01-01")
first_filing = hits[0].get_filing()
```

---

## 4. Raw Filing Documents, Attachments, SGML & Text Search

Extract raw HTML/XML, clean plain text, Markdown, parsed document trees, exhibits, SGML containers, and regex matches from any `Filing`.

| Data Extracted | Exact Command | Explanation |
| :--- | :--- | :--- |
| Primary HTML / XML | `filing.html()` / `filing.xml()` | Raw primary document HTML or XML string |
| Plain text / Markdown | `filing.text()` / `filing.markdown()` | Clean readable text or Markdown conversion |
| Parsed document AST | `doc = filing.parse()` | Returns `edgar.documents.Document` (sections, tables, headings) |
| Terminal / browser view | `filing.view()` / `filing.open()` / `filing.open_homepage()` | Rich terminal preview or opens SEC URL in browser |
| Filing attachments | `filing.attachments` | Lists all exhibits/documents (`EX-10.1`, `EX-21`, XBRL, images) |
| SGML bundle & header | `filing.sgml()` / `filing.header()` | Full submission SGML archive and parsed SEC header metadata |
| Document sections | `filing.sections()` | Detected section names inside the filing |
| Search / grep inside filing | `filing.search("revenue")` / `filing.grep(r"Item\s+1A", regex=True)` | BM25 search or exact/regex line matches across filing documents |
| Related filings & SEC letters | `filing.related_filings()` / `filing.correspondence()` | Linked filings by file number or SEC `CORRESP`/`UPLOAD` thread |
| URLs | `filing.filing_url`, `filing.homepage_url`, `filing.text_url` | Direct SEC.gov URLs for document, index page, and `.txt` bundle |

```python
filing = Company("AAPL").get_filings(form="10-K").latest()
md = filing.markdown()
matches = filing.grep(r"supply chain", regex=True)
exhibits = filing.attachments
```

---

## 5. Financial Statements, XBRL & Multi-Year Comparisons

Extract standardized and as-reported income statements, balance sheets, cash flows, equity statements, notes, XBRL fact tables, calculation trees, and multi-period stitched statements.

### High-Level Financials (`Financials` & `MultiFinancials`)

| Data Extracted | Exact Command | Explanation |
| :--- | :--- | :--- |
| Latest annual / quarterly statements | `company.get_financials()` / `company.get_quarterly_financials()` | Returns `Financials` from latest 10-K or 10-Q |
| Statement shortcuts on `Company` | `company.income_statement(period="annual")`, `company.balance_sheet()`, `company.cash_flow_statement()` | Supports `period="annual"`, `"quarterly"`, or `"ttm"` (except balance sheet); `as_dataframe=True` |
| Statement methods on `Financials` | `fin.income_statement()`, `fin.balance_sheet()`, `fin.cash_flow_statement()`, `fin.statement_of_equity()`, `fin.comprehensive_income()`, `fin.cover()` | Individual `Statement` objects (`view="standard"`/`"detailed"`/`"summary"`) |
| Built-in financial metrics | `fin.get_revenue()`, `fin.get_net_income()`, `fin.get_operating_income()`, `fin.get_total_assets()`, `fin.get_total_liabilities()`, `fin.get_stockholders_equity()`, `fin.get_operating_cash_flow()`, `fin.get_free_cash_flow()`, `fin.get_capital_expenditures()`, `fin.get_current_assets()`, `fin.get_current_liabilities()`, `fin.get_shares_outstanding_basic()`, `fin.get_shares_outstanding_diluted()`, `fin.get_financial_metrics()` | Normalized scalar metrics (supports `period_offset=0, 1, ...`) |
| Multi-filing stitched statements | `MultiFinancials.extract(filings).income_statement(max_periods=8)` | Combines multiple 10-K/10-Q filings into a single multi-year `StitchedStatement` |

### Deep XBRL Inspection (`filing.xbrl()`)

| Data Extracted | Exact Command | Explanation |
| :--- | :--- | :--- |
| Parse filing XBRL | `xb = filing.xbrl()` | Full XBRL instance + linkbase parser (`XBRL`) |
| All available statements & notes | `xb.statements.list_available()` | DataFrame of every statement, note, and disclosure role |
| Core & note statements | `xb.statements.balance_sheet()`, `xb.statements.schedule_of_investments()`, `xb.statements.notes()`, `xb.statements.disclosures()`, `xb.statements.get(name)` | Retrieve primary statements, BDC/fund schedules, or footnote tables |
| Statement outputs & analytics | `stmt.to_dataframe()`, `stmt.to_markdown()`, `stmt.to_context()`, `stmt.calculate_ratios()`, `stmt.analyze_trends(periods=4)`, `stmt.validate()`, `stmt.search("debt")` | DataFrame export, ratios, trend arrays, and balance validation |
| Raw XBRL fact query | `xb.query(include_dimensions=True).by_concept("Revenue").to_dataframe()` | Fluent `FactQuery` across all tagged facts and dimensions |
| Calculation linkbase | `xb.calculation_linkbase()` | DataFrame of parent-child calculation arcs, weights, and taxonomies |
| Dimensional axes & domains | `xb.axes_for_role(role_uri)`, `xb.domains_for_role(role_uri)`, `xb.reporting_periods` | Role-scoped XBRL dimensions and reporting periods |

```python
from edgar import Company, MultiFinancials

company = Company("MSFT")
fin = company.get_financials()
income_df = fin.income_statement().to_dataframe()
metrics = fin.get_financial_metrics()

# Stitch 5 years of 10-K balance sheets
tenks = company.get_filings(form="10-K").head(5)
multi_bs = MultiFinancials.extract(tenks).balance_sheet().to_dataframe()

# Query dimensional XBRL facts
xb = tenks[0].xbrl()
rev_segments = xb.query(include_dimensions=True).by_concept("Revenue").to_dataframe()
```

---

## 6. SEC Company Facts, Time Series & TTM / Split Normalization

Access SEC's bulk `companyfacts` JSON API (`data.sec.gov/api/xbrl/companyfacts`) for multi-year history, Trailing Twelve Months (TTM) calculations with Q4 derivation, and stock-split adjustments.

| Data Extracted | Exact Command | Explanation |
| :--- | :--- | :--- |
| Full company facts | `facts = company.get_facts()` / `get_company_facts(cik)` | Returns `EntityFacts` across all XBRL filings |
| Concept time series | `facts.time_series("Revenues", periods=20)` | Historical DataFrame for any US-GAAP/IFRS/DEI concept |
| Query facts | `facts.query().by_concept("NetIncomeLoss").to_dataframe()` | Filter by concept, period, form, or unit |
| Concept discovery | `facts.search_concepts("debt")`, `facts.discover_concept_tags("revenue")`, `facts.available_periods()` | Finds exact XBRL tags and available fiscal periods |
| Convenience facts & DEI | `facts.get_revenue()`, `facts.get_net_income()`, `facts.get_total_assets()`, `facts.dei_facts()`, `facts.entity_info()` | Quick metric lookups and DEI history |
| Ratios, peers & anomalies | `facts.calculate_ratios()`, `facts.peer_comparison(peer_ciks)`, `facts.detect_anomalies()` | Built-in financial ratio, peer comparison, and anomaly checks |
| TTM scalar metrics | `company.get_ttm("Revenues", as_of="2024-Q2")`, `company.get_ttm_revenue()`, `company.get_ttm_net_income()` | Rolling 4-quarter `TTMMetric` with automatic Q4 derivation & split adjustment |
| TTM statements | `company.income_statement(period="ttm")`, `company.cash_flow_statement(period="ttm")` | Rolling 4-quarter income and cash flow statements |
| Stock split detection | `from edgar.ttm import detect_splits, apply_split_adjustments` | Detects `StockSplitConversionRatio` and adjusts EPS/share counts |

```python
from edgar import Company

nvda = Company("NVDA")
ttm_rev = nvda.get_ttm_revenue()
print(ttm_rev.value, ttm_rev.as_of_date, ttm_rev.periods, ttm_rev.has_calculated_q4)

ttm_is = nvda.income_statement(period="ttm", as_dataframe=True)
facts = nvda.get_facts()
rev_history = facts.time_series("Revenues", periods=16)
```

---

## 7. Periodic & Current Company Reports (`filing.obj()`)

Calling `filing.obj()` parses a filing into a specialized report object with section accessors, exhibits, and financials.

| Form | Returned Class | Exact Commands / Properties | Extracted Data |
| :--- | :--- | :--- | :--- |
| `10-K` / `10-K/A` | `TenK` | `tenk.business`, `tenk.risk_factors`, `tenk.management_discussion`, `tenk.directors_officers_and_governance`, `tenk["Item 7"]`, `tenk.get_item_with_part("Part II", "Item 7")`, `tenk.subsidiaries`, `tenk.auditor`, `tenk.notes()`, `tenk.reports()`, `tenk.income_statement` | Business, Risk Factors, MD&A, Governance, Exhibit 21 subsidiaries, auditor (name/PCAOB ID/location), XBRL notes & statements |
| `10-Q` / `10-Q/A` | `TenQ` | `tenq.items`, `tenq.sections`, `tenq.get_item_with_part(...)`, `tenq.income_statement`, `tenq.balance_sheet`, `tenq.cash_flow_statement`, `tenq.notes()` | Quarterly items, XBRL statements, and financial notes |
| `8-K` / `8-K/A` | `EightK` | `eightk.items`, `eightk.date_of_report`, `eightk.press_releases`, `eightk.has_earnings`, `eightk.earnings()`, `eightk.get_exhibit("EX-99.1")`, `eightk.income_statement` | Event item codes (e.g., `2.02`, `5.02`), Exhibit 99 press releases, parsed earnings tables |
| `20-F` / `20-F/A` | `TwentyF` | `tf.key_information`, `tf.risk_factors`, `tf.business`, `tf.company_information`, `tf.operating_review`, `tf.management_discussion`, `tf.directors_and_employees`, `tf.major_shareholders`, `tf.financial_information`, `tf.controls_and_procedures` | Foreign private issuer annual report sections & IFRS/US-GAAP XBRL financials |
| `40-F` / `40-F/A` | `FortyF` | `ff.aif_attachment()`, `ff.risk_factors`, `ff.corporate_structure`, `ff.dividends`, `ff.capital_structure`, `ff.directors_and_officers`, `ff.legal_proceedings` | Canadian MJDS Annual Information Form (AIF) sections & financials |
| `6-K` / `6-K/A` | `SixK` | `sk.date_of_report`, `sk.exhibits`, `sk.press_releases`, `sk.financials`, `sk.content_description`, `sk.text()` | Foreign current report metadata, exhibits, press releases, and tables |

```python
tenk = Company("AAPL").latest_tenk
print(tenk.auditor)
subs_df = tenk.subsidiaries.to_dataframe() if tenk.subsidiaries else None
mda_text = tenk.management_discussion
```

---

## 8. Insider, Beneficial & Institutional Ownership

Extract Section 16 insider trades, Rule 144 planned sales, Section 13(d)/(g) activist/passive 5%+ stakes, and Section 13(f) institutional portfolios.

| Form | Returned Class | Exact Commands / Properties | Extracted Data |
| :--- | :--- | :--- | :--- |
| `3`, `4`, `5` | `Form3`, `Form4`, `Form5` (`Ownership`) | `f4.insider_name`, `f4.position`, `f4.reporting_owners`, `f4.get_ownership_summary()`, `f4.market_trades()`, `f4.common_stock_purchases`, `f4.common_stock_sales`, `f4.option_exercises`, `f4.derivative_trades()`, `f4.shares_traded()`, `f4.to_dataframe()` | Insider identity/title, open-market buys/sells, option exercises, derivative holdings, post-transaction shares, Rule 10b5-1 flag (`aff10b5_one`) |
| `144` | `Form144` | `f144 = filing.obj(); f144.to_dataframe()` (plus `securities_information`, `securities_to_be_sold`, `securities_sold_past_3_months`) | Proposed restricted/control stock sales, broker details, prior 3-month sales |
| `SC 13D`, `SC 13G` | `Schedule13D`, `Schedule13G` | `sc.issuer_info`, `sc.reporting_persons`, `sc.total_shares`, `sc.items.item4_purpose_of_transaction` (13D) | 5%+ beneficial owners, share counts/percentages, activist Item 4 intent |
| `13F-HR` / `13F-HR/A` | `ThirteenF` | `tf.infotable`, `tf.holdings`, `tf.total_value`, `tf.total_holdings`, `tf.report_period`, `tf.management_company_name`, `tf.holdings_view()`, `tf.compare_holdings()`, `tf.holding_history(periods=3)`, `tf.get_portfolio_managers()`, `tf.previous_holding_report()` | Institutional holdings table (CUSIP, issuer, value, shares, put/call, voting authority), QoQ comparison, multi-quarter history |

```python
from edgar import Company

# Latest Form 4 insider trade
form4 = Company("NVDA").get_filings(form="4").latest().obj()
print(form4.insider_name, form4.position)
print(form4.to_dataframe())

# Institutional 13F portfolio (e.g., Berkshire Hathaway CIK 0001067983)
tf = Company("0001067983").get_filings(form="13F-HR").latest().obj()
print(tf.total_value, tf.total_holdings)
holdings_df = tf.infotable
```

---

## 9. Proxy Statements, Governance & Tender Offers

Extract executive compensation, Pay vs. Performance tables, CEO pay ratio, board/director pay, shareholder proposals, audit fees, proxy contests, and tender offer recommendations.

| Form | Returned Class | Exact Commands / Properties | Extracted Data |
| :--- | :--- | :--- | :--- |
| `DEF 14A` & proxy variants (`PRE 14A`, `DEFA14A`, `DEFM14A`, `DEFC14A`, etc.) | `ProxyStatement` | `proxy.peo_name`, `proxy.peo_total_comp`, `proxy.peo_actually_paid_comp`, `proxy.executive_compensation`, `proxy.summary_compensation_table()`, `proxy.pay_vs_performance()`, `proxy.ceo_pay_ratio()`, `proxy.director_compensation_table()`, `proxy.beneficial_ownership()`, `proxy.voting_proposals()`, `proxy.audit_fees()`, `proxy.named_executives()`, `proxy.insider_trading_policy_adopted()`, `proxy.awards_close_to_mnpi()`, `proxy.season()` | CEO/NEO pay, 5-year Pay vs. Performance, TSR, Summary Compensation Table, CEO pay ratio, director fees, 5%+ owners, proposals, audit fees, MNPI option timing |
| Proxy contests | `ProxyContests` | `from edgar import proxy_contests; proxy_contests(company)` | Contested proxy campaigns (`DEFC14A`, `DFAN14A`, dissident filings) |
| `SC 14D9` / `SC 14D9/A` | `Schedule14D9` | `s14d9.recommendation`, `s14d9.recommendation_text`, `s14d9.is_amendment` | Target board recommendation (`'accept'`, `'reject'`, `'neutral'`, or `None`) and Item 4 text |

```python
proxy = Company("AAPL").get_filings(form="DEF 14A").latest().obj()
print(proxy.peo_name, proxy.peo_total_comp)
exec_comp_df = proxy.executive_compensation
proposals = proxy.voting_proposals()
```

---

## 10. Investment Funds, ETFs, Money Market Funds & BDCs

Extract fund hierarchies, monthly portfolio holdings, money market liquidity/yields, expense ratios, shareholder vote records, and Business Development Company (BDC) investment schedules.

| Data Domain / Form | Class / Entry Point | Exact Commands / Properties | Extracted Data |
| :--- | :--- | :--- | :--- |
| Fund entity hierarchy | `Fund`, `FundCompany`, `FundSeries`, `FundClass` | `f = Fund("VFINX")`, `find_fund("VOO")`, `find_funds("Vanguard")`, `f.list_series()`, `f.list_classes()`, `f.get_portfolio()` | Fund company → series → share class navigation & latest holdings |
| `NPORT-P` / `NPORT-EX` | `FundReport` | `report = filing.obj(); report.investment_data()` | Monthly ETF/mutual fund portfolio positions (stocks, bonds, derivatives, weights) |
| `N-MFP2` / `N-MFP3` | `MoneyMarketFund` | `mmf = filing.obj(); mmf.portfolio_data()` | Money market securities, yields, NAV, and liquidity metrics |
| `N-CEN` | `FundCensus` | `census = filing.obj(); census.series_data()` | Annual registered fund census, series, service providers, ETF info |
| `N-CSR` / `N-CSRS` | `FundShareholderReport` | `sr = filing.obj(); sr.expense_data()`, `sr.performance_data()` | Certified annual/semiannual fund expense ratios & returns |
| `497K` | `Prospectus497K` | `p497 = filing.obj(); p497.fees`, `p497.performance`, `p497.best_quarter` | Summary prospectus fee tables, expense examples, 1/5/10-yr returns |
| `N-PX` | `NPX` | `npx = filing.obj(); npx.votes` | Annual institutional/fund proxy voting records by proposal |
| `24F-2NT` | `FundFeeNotice` | `n = filing.obj(); n.aggregate_sales`, `n.net_sales`, `n.registration_fee`, `n.series`, `n.fiscal_year_end` | Annual fund sales, redemptions, and SEC registration fees |
| BDCs & DERA BDC datasets | `BDCEntity`, `BDCDataset` (`edgar.bdc`) | `get_bdc_list()`, `find_bdc("ARCC")`, `is_bdc_cik(1287750)`, `bdc.portfolio_investments()`, `fetch_bdc_dataset(2024, 3)`, `extract_nonaccrual(...)` | Active/registered BDC directory, Schedule of Investments, non-accrual loans, and SEC DERA bulk BDC tables |

```python
from edgar import Fund
from edgar.bdc import get_bdc_list, fetch_bdc_dataset

fund = Fund("SPY")
portfolio_df = fund.get_portfolio()

bdcs = get_bdc_list()
arcc_investments = bdcs.get_by_ticker("ARCC").portfolio_investments()
bdc_q3 = fetch_bdc_dataset(2024, 3)
```

---

## 11. Offerings, Registrations, ABS, Regulatory & Correspondence Filings

| Form | Returned Class | Exact Commands / Properties | Extracted Data |
| :--- | :--- | :--- | :--- |
| `S-1`, `S-1/A`, `F-1`, `F-1/A` | `RegistrationS1` | `s1.offering_type`, `s1.total_offering`, `s1.fee_table`, `s1.takedowns`, `s1.effective_date` | IPO/registration offering type, registered dollar amount, Exhibit 107 fee table, cover page |
| `S-3`, `S-3ASR`, `S-3D`, `S-3DPOS`, `F-3`, `F-3ASR` (+ `/A`) | `RegistrationS3` | `s3.offering_type`, `s3.total_offering`, `s3.fee_table`, `s3.takedowns` | Universal/automatic shelf capacity, filer category, linked `424B*` takedowns |
| `S-4`, `F-4` (+ `/A`) | `RegistrationS4` | `s4 = filing.obj()` | Merger / business-combination registration details & cover page |
| `DRS` / `DRS/A` | `DraftRegistrationStatement` | `drs.underlying_form`, `drs.underlying_object`, `drs.registration_number`, `drs.is_amendment` | Confidential draft registration underlying form & delegated `RegistrationS1` |
| `424B1`–`424B5`, `424B7`, `424B8` | `Prospectus424B` | `p.deal.price`, `p.deal.gross_proceeds`, `p.deal.discount_rate` | Shelf takedown/IPO pricing, gross proceeds, underwriting discount, dilution |
| `EFFECT` | `Effect` | `eff.effective_date`, `eff.source_submission_type`, `eff.get_source_filing()` | Registration effectiveness date and link to source filing |
| `D` / `D/A` | `FormD` | `fd.offering`, `fd.recipients` | Regulation D exempt private offering amounts, exemptions, related persons |
| `C`, `C-U`, `C-AR`, `C-TR` | `FormC` | `fc.offering_information`, `fc.annual_report_disclosure` | Regulation CF crowdfunding targets, deadlines, portal, issuer financials |
| `10-D` (CMBS with `EX-102`) | `TenD` (`edgar.abs`) | `td.loans`, `td.properties`, `td.asset_data.summary()` | CMBS loan-level and property-level DataFrames and pool summaries (`AutoLeaseAssetData` for ABS-EE) |
| `MA-I` | `MunicipalAdvisorForm` | `mai = filing.obj(); mai.advisor_name` | Municipal advisor individual registration & disciplinary history |
| `ATS-N`, `ATS-N/MA`, `ATS-N/UA`, `ATS-N/CA`, `ATS-N-W` | `AlternativeTradingSystem`, `AlternativeTradingSystemWithdrawal` | `ats = filing.obj()` | Dark pool / ATS order types, fees, access, matching rules, conflicts |
| `CORRESP`, `UPLOAD` | `Correspondence`, `CorrespondenceThread` | `corr = filing.obj()` / `filing.correspondence()` | SEC comment letters and company responses |
| `X-17A-5`, `TA-1`, `TA-2`, `TA-W`, `MA`, `MA-W`, `CFPORTAL`, `SBSE`, `SBSE-A`, `SBSE-W`, `ATS-N-C` | `XmlFiling` | `xf['brokerDealerName']`, `xf.form_data`, `xf.to_html()` | Deep key lookup, full parsed XML dict, and SEC-rendered HTML for XML regulatory forms |

---

## 12. Bundled Offline Reference Data & Company Universe Datasets

Bundled reference tables in `edgar/reference/data/` work offline with zero network calls; bulk company dataset builders aggregate SEC submissions into Parquet/DuckDB.

| Data Extracted | Exact Command | Explanation |
| :--- | :--- | :--- |
| Ticker ↔ CIK / CUSIP lookup | `from edgar.reference import cusip_ticker_mapping, get_ticker_from_cusip` | Bundled offline ticker, exchange, and CUSIP mappings |
| Companies by exchange | `from edgar.reference import get_companies_by_exchanges; get_companies_by_exchanges(["NYSE", "Nasdaq"])` | DataFrame `[cik, ticker, name, exchange]` |
| Companies by industry / state | `get_companies_by_industry(sic=7372)`, `get_companies_by_state("CA")` | Filter SEC companies by SIC code or US state |
| Industry & curated presets | `get_banking_companies()`, `get_software_companies()`, `get_semiconductor_companies()`, `get_pharmaceutical_companies()`, `get_biotechnology_companies()`, `get_oil_gas_companies()`, `get_real_estate_companies()`, `get_insurance_companies()`, `get_retail_companies()`, `get_investment_companies()`, `get_faang_companies()`, `get_tech_giants()`, `get_popular_companies()`, `get_dow_jones_sample()` | Pre-built company universe subsets (`CompanySubset` supports sampling & set ops) |
| Form & place decoders | `describe_form("10-K")`, `get_place_name(...)`, `is_us_company(...)`, `is_foreign_company(...)`, `is_canadian_company(...)` | Human-readable SEC form titles and state/country/filer-type decoders |
| Bulk Company Dataset (Parquet / DuckDB) | `from edgar.reference import get_company_dataset, build_company_dataset_parquet, build_company_dataset_duckdb, to_duckdb` | Builds/loads Arrow/Parquet/DuckDB tables of SEC company metadata (requires `[data]` for DuckDB) |

---

## 13. Local, Cloud & Datamule Bulk Storage

Download SEC submissions, bulk companyfacts, and filings for offline/high-throughput workloads or mirror to cloud object storage.

| Operation | Exact Command | Explanation |
| :--- | :--- | :--- |
| Download bulk SEC data | `download_edgar_data(submissions=True, facts=True, reference=True)` | Downloads SEC bulk `.zip` archives (`submissions.zip`, `companyfacts.zip`, tickers) |
| Bulk download filings | `download_filings(filing_date="2025-01-01:2025-01-31")` | Downloads raw filings to local storage directory |
| Enable / configure local storage | `use_local_storage(True)` / `use_local_storage("E:/edgar_data", allow_network_fallback=False)` | Reads from local disk (optional strict offline mode) |
| Storage diagnostics & maintenance | `storage_info()`, `analyze_storage()`, `optimize_storage()`, `cleanup_storage()`, `clear_cache()`, `check_filing(filing)` | Inspect disk usage, gzip-compress filings, clean old files, verify local availability |
| Cloud storage (`fsspec`) | `use_cloud_storage("s3://my-bucket/edgar")`, `sync_to_cloud(...)` | Store/read SEC data on S3, Cloudflare R2, MinIO, GCS, or Azure Blob |
| Datamule storage | `use_datamule_storage(...)`, `is_using_datamule_storage()` | Read filings from Datamule tar archives (`zstandard`) |

---

## 14. CLI & MCP Server (AI Agent Integration)

Expose EdgarTools data to AI clients (Claude Desktop, Cursor, etc.) via the Model Context Protocol.

### CLI Commands

```bash
# Start MCP server (stdio transport by default)
edgartools mcp
edgartools-mcp

# Start MCP server over streamable HTTP or run self-test
edgartools mcp --transport streamable-http --host 127.0.0.1 --port 8000
edgartools mcp --test
python -m edgar.ai
```

### Exposed MCP Tools (13 Tools)

| Tool Name | Purpose |
| :--- | :--- |
| `edgar_company` | Company profile, financials, filings, and ownership starting point |
| `edgar_filing` | Structured context for a specific filing (by company + form or accession/URL) |
| `edgar_read` | Extract filing sections (Risk Factors, MD&A, Business, 8-K items) |
| `edgar_notes` | Drill into XBRL financial statement notes and disclosures |
| `edgar_search` | Find companies by name or list filings by form type |
| `edgar_text_search` | Full-text search across SEC filing text (EFTS) |
| `edgar_screen` | Filter companies by industry, exchange, or state |
| `edgar_monitor` | Live feed of recent SEC filings |
| `edgar_trends` | Multi-period financial time series with growth rates |
| `edgar_compare` | Side-by-side multi-company financial comparison |
| `edgar_ownership` | Section 16 insider trades (Form 4) and 13F institutional holdings |
| `edgar_fund` | Mutual fund, ETF, BDC, and money market fund data |
| `edgar_proxy` | Executive compensation, governance, and DEF 14A proxy data |

