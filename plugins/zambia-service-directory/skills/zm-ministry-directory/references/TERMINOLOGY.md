# Terminology, Counting and Scope Rules

Every ministry workbook uses exactly these strings. The code copy is in
`scripts/zmcontract.py`; `tests/test_contract.py` checks that the two stay the same.

## Fixed strings

| Use | Exact text |
|---|---|
| Official source checked, value not given | `Not published` |
| Field genuinely does not apply (e.g. Validity of a one-off search) | `Not applicable` |
| Fee found only in an older source, current amount unknown | `Historical fee — current amount requires confirmation` |
| Agency has services on eServices | `Listed on Zambia eServices` |
| Agency has no services on eServices | `Not listed on Zambia eServices — 0 services` |
| Agencies sheet Status | `Included` or `Excluded` |

Deprecated values, which are rewritten when a row is written:

| Old | New |
|---|---|
| `Not specified` | `Not published` |
| `N/A` | `Not applicable` |

Only write `Not published` after the official sources were actually checked for that field.
If a row could not be re-checked, set `verification = "Unresolved"`. An unresolved passport is not written.

Historical fees: put the historical-fee label first, then the old amount and its year,
e.g. `Historical fee — current amount requires confirmation. 2019 schedule: ZMW 150`.

## Entity types

`Government Department`, `Statutory Agency`, `Statutory Commission`, `Statutory Board`,
`Statutory Council`, `Statutory Authority`, `Statutory Institute`.

## Agencies sheet `Notes` patterns

Pick the first pattern that fits. `{other_source}` is a short label, e.g. `the DNPW forms page`.

| Case | Pattern |
|---|---|
| Listed + other sources | `{total} verified services: {eservices} on Zambia eServices and {other} on {other_source}` |
| Listed only | `{total} verified services, all on Zambia eServices` |
| Services on eServices under another provider | `{total} services; listed under {provider} on Zambia eServices` |
| Not listed | `{total} services verified from {other_source}; not listed on Zambia eServices` |
| None found | `0 services verified; {reason}` |

## Counting methodology

1. One eServices service entry (one eServices ID) = one passport.
2. New / renewal / amendment actions shown on one eServices entry = one passport.
   eServices publishes renewal as its own entry (its own ID) → that entry is its own passport.
3. Different payment or submission channels (online, office, mobile, bank) for the same service = one passport. Never duplicate.
4. A service found in other official sources counts only if it has a distinct application
   outcome (a different licence, permit, certificate, registration, approval or booking).
   A second form for the same outcome is not a new service.
5. A service listed on eServices under provider A but delivered by agency B of the same
   ministry is counted under B. Set `reassigned_from` = A, give a reason, and record it
   in A's `eservices.reassigned_out`.
6. Exact eServices count = number of **National** services for the provider's authority IDs
   returned by the API on the run date. Local council services are counted only with `--include-local`.
7. Totals are always computed from the rows actually written. They are never typed in by hand.

## Inclusion / exclusion

Include services delivered to citizens, residents, businesses, professionals, organisations
or other external applicants.

Exclude:
- internal government processes and staff services;
- recruitment, vacancies and procurement or tenders;
- general information pages, news, strategic plans;
- projects or programmes that are not something a person applies for or books.

A page that only publishes information (e.g. a calendar) is not a service unless the public can
request, book, apply or pay for something through it.

Do not assume an agency provides a service because a similar agency does.

## Source-recording method

- `Source / Service Link` is the single most specific official URL:
  eServices `https://eservices.gov.zm/#/service/<ID>` > the service's own page > its form or PDF >
  the Act or SI > a listing page. A homepage is used only with `link_reason`.
- Every field value keeps its evidence in `field_sources` (URL, tier, excerpt, sha256). Evidence is not written to Excel.
- Conflicts go in `conflicts[]`: which sources disagree, and which source was used and why.
  They are also summarised in the run report.
- Legal references: exact title, Act number and year, e.g. `Tourism and Hospitality Act No. 13 of 2015`;
  `Tourism and Hospitality (Casino) Regulations, SI No. 93 of 2016`; Cap numbers where the Act is cited by chapter.
  Separate several references with `; `.

## Field style (matches the reference workbook)

- Who Can Apply: short list separated by `; ` (e.g. `Individual; Organization`).
- Eligibility Requirements: 1–3 sentences summarising documents and conditions. Not a full checklist.
- Fee: amounts as `14,000 ZMW`; several items separated by `; ` with a label for each item.
- Processing Time: as published (e.g. `60 days`, `Instant`).
- A repealed Act can stay in force in part (savings clause). Before treating an Act as repealed, read the repealing Act's savings and transitional provisions, and cite the surviving Part with its saving section.
