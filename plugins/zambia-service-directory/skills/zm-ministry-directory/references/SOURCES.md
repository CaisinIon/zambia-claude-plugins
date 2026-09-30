# Source Catalogue and Search Recipes (Zambia)

Use the sources in tier order. Record the tier of every source you cite (`field_sources[].tier`).
**Tier 5 is lead-only.** It helps you *find* a service or an official link. It never backs a field value on its own.
If only tier 5 supports a value, write `Not published` and note the lead in `source_limitations`.

| Tier | Source type | Examples | Use for |
|---|---|---|---|
| 1 | Zambia eServices API | `scripts/eservices.py` (never scrape the web page) | primary source for services listed there |
| 2 | Agency's own official website and documents | service pages, application forms (PDF/Word), fee schedules, citizen / service charters, annual reports listing licences issued | additional services; fees; processing times; corrections |
| 3 | Legislation | Acts, statutory instruments (fees are often in an SI schedule), Government Gazette notices | legal references; fees fixed by law; who may apply |
| 4 | Oversight and other government sources | the ministry website, the regulator, Cabinet Office, other `.gov.zm` portals, ZamPortal, official verified agency social-media pages (dated posts) | existence of services; the list of agencies under a ministry; recent fee announcements |
| 5 | Leads only | LinkedIn (agency pages, staff posts), news (ZNBC, Zambia Daily Mail, Times of Zambia, Lusaka Times, Diggers), Wikipedia, directories, blogs | finding services, new agency names, dead links, official documents to fetch |

## Known starting points

Check each URL on first use. Record in `searched_sources` which ones you opened.

| What | Where |
|---|---|
| eServices directory (data) | `https://zigsapi.eservices.gov.zm/` via `eservices.py` · public page `https://eservices.gov.zm/#/service/<ID>` |
| Acts of Parliament (PDF) | `https://www.parliament.gov.zm/acts-of-parliament`; PDFs under `https://www.parliament.gov.zm/sites/default/files/documents/acts/` |
| Acts, SIs and case law | ZambiaLII `https://zambialii.org` (search by Act title or SI number) |
| Statutory instruments (copies) | `https://www.enotices.co.zm/` (eServices itself links here; cite the SI title/number, and the link as its location) |
| Ministerial portfolios and agencies under each ministry | the Cabinet Office site, and the latest Gazette notice on ministerial portfolios / statutory functions (search ZambiaLII + news for "Gazette Notice" + "portfolio" + year) |
| Government portal | ZamPortal `https://zamportal.gov.zm/` |
| Ministry websites | usually `https://www.<acronym>.gov.zm` (e.g. Ministry of Tourism `https://www.mot.gov.zm`) |
| Old or dead pages | Internet Archive `https://web.archive.org/web/*/<url>`. Cite the archived copy at the original's tier, and flag it as archived with its date |

Many `.gov.zm` sites have broken TLS chains. `fetch_source.py` retries without verification and records `tls_verified: false`.
This is fine for evidence, but note it.

## Tools, in order of preference

1. `scripts/eservices.py` for anything on eServices.
2. `WebSearch` to discover pages, forms, Acts and news leads.
3. `scripts/fetch_source.py <url> --out <run>/evidence/<slug> --tier N`. It saves the raw file, `text.txt` and sha256, and prints `text_path`. Then use `Grep`/`Read` on `text.txt`.
   Use it for every page or PDF you cite, so the verifier can re-read the exact copy.
4. `WebFetch` for a quick look at a page you will not cite.
5. Chrome DevTools MCP when `fetch_source.py` exits 3 (a JavaScript-rendered page):
   `new_page`/`navigate_page` → `wait_for` → `take_snapshot`. Save the snapshot text to a file, then
   `fetch_source.py <url> --out … --tier N --from-text <file>` so it becomes evidence.
6. LinkedIn usually needs a login. Use search-result snippets as leads only, and never log in.

## Search recipes

Replace `<A>` with the official agency name, `<a>` with its acronym, `<d>` with its domain.

| Goal | Queries |
|---|---|
| Official site | `"<A>" site:gov.zm` · `"<A>" official website Zambia` · `"<a>" Zambia` |
| Services on the site | `site:<d> apply OR application OR licence OR permit OR registration OR certificate` |
| Forms and fees | `site:<d> filetype:pdf form` · `site:<d> fees OR tariff OR charges` · `"<A>" fees schedule pdf` |
| Service charter | `"<A>" service charter` · `site:<d> charter` |
| Governing law | `"<A>" Act No. of` · `site:zambialii.org "<A>"` · `site:parliament.gov.zm "<A>" Act` |
| Fee SI | `"<A>" fees regulations statutory instrument` · `site:zambialii.org "<A>" fees` |
| New or renamed agency | `"<A>" formerly` · `"<A>" renamed` · `"<a>" Zambia 2025 OR 2026` (news = lead) |
| LinkedIn lead | `site:linkedin.com "<A>" Zambia` (snippet only) |
| Recent fee change | `"<A>" new fees 2025 OR 2026` (news = lead → find the SI or official notice) |

Search by the official name **and** the acronym. Also try the ministry's site: many departments publish their forms there,
e.g. `https://www.mot.gov.zm/?page_id=709` for casino forms.

## Evidence rules

- Quote the smallest excerpt that proves the value (≤ 300 characters) in `field_sources[].excerpt`.
- A fee from a document dated more than 2 years before the newest fee source, or superseded by a later SI, is a
  **historical fee**. Label it (see TERMINOLOGY.md), set `historical_fee: true`, and add it to `historical_fee_warnings`.
- Conflicting official sources: prefer eServices for services listed there, unless a newer SI or official notice
  changes the value. Record `conflicts[]` with both values, both sources and the reason for your choice.
- Never infer a service from a similar agency ("ZTA licenses tour operators, so NHCC must too" is not evidence).
