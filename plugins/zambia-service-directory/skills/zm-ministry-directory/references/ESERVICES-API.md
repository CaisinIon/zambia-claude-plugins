# Zambia eServices API

The directory at `https://eservices.gov.zm/#/service-directory/national` is an Angular app.
Its data comes from a public JSON API that needs no login. **Always use `scripts/eservices.py`.**
Do not scrape the web page. The script caches results per day, retries, and maps fields to passports.

Base: `https://zigsapi.eservices.gov.zm/` (from `https://eservices.gov.zm/assets/appsettings.json`).
`https://eservices.gov.zm/api/...` only returns the web page's HTML shell. Do not use it.

## Endpoints

| Purpose | Path | Key fields |
|---|---|---|
| Providers (agencies) | `public/data/AvailableAuthorities?page=0&limit=0&start=0` | `ID`, `Name`, `ShortName`, `TypeOfAuthority_ReferenceTitle` |
| All services | `public/data/AvailablePermitsNew?page=0&limit=0&start=0` | `ID`, `Title`, `AuthorityId`, `AuthorityId_ReferenceTitle`, `TypeOfService` (National/Local), `Applicant`, `Payment`, `IsAvailable`, `ExternalUrl` |
| Service detail | `passport/data/PermitInfo/<ID>` | `Title`, `Description`, `FullDescription`, `whoCanApply`, `EligibilityRequirements`, `FeeText`, `TimeToIssueText`, `PeriodOfValidityText`, `ServiceProvider`, `ModifiedOn` |
| Legal references | `passport/data/PermitLex?page=0&start=0&limit=0&filter=[{"value":"<ID>","property":"PermitTypeId"}]` | `Name`, `Link` |
| Required documents | `passport/data/PermitSupportingDocuments?…same filter…` | `SupportingDocumentTypeId_ReferenceTitle` |
| Public page of a service | `https://eservices.gov.zm/#/service/<ID>` | use as `Source / Service Link` |

## Field mapping (done by `eservices.py agency`)

| Passport field | API source | Cleaning |
|---|---|---|
| Service Name | `PermitInfo.Title` | — |
| Service Description | `Description`; `FullDescription` only if Description < 40 chars | login / "Apply for Service" steps removed |
| Who Can Apply | `whoCanApply` (Python-style list text) → fallback `Applicant` | `Individual; Organization` |
| Eligibility Requirements | `EligibilityRequirements` + required documents | HTML unescaped, tags removed. **Researcher must summarise to 1–3 sentences.** |
| Fee | `FeeText` | lines joined with `; ` |
| Processing Time | `TimeToIssueText` | — |
| Validity | `PeriodOfValidityText` | — |
| Legal References | `PermitLex[].Name` | **Researcher must normalise** to `<Title> Act No. N of YYYY` |
| Link | `#/service/<ID>` | — |

An empty field becomes `Not published`. Each draft carries `draft_flags` listing what still needs review.

## Known data quirks

- **Provider mismatch.** A service can list a different provider from the one that delivers it.
  Example: the 5 hotel-manager services (IDs 95, 159, 271, 277, 282) are listed under Department of Tourism, but the Hotels Managers Registration Council (HMRC) delivers them.
  Reassign these per the counting rules in TERMINOLOGY.md.
- **Repealed Acts.** `PermitLex` may cite a repealed Act next to its replacement (Tourism and Hospitality Act 2007 and 2015). Cite only the Act in force.
- **Not legislation.** `PermitLex` sometimes lists guidelines or other documents that are not laws. These are not legal references.
- **Mismatched IDs.** `AuthorityId` in services may not match any `ID` in authorities (e.g. National Registration).
  `eservices.py` also matches on the provider title (`--name`).
- **Acronyms.** `ShortName` is often a ministry code (`MOTA`, `MFL`, `MLNR`), not the agency acronym.
- **No ministry field.** The API has no ministry field. The roster step maps agencies to ministries.
- **Unavailable services.** `IsAvailable=false` services are still published. They count as passports, with a note in the run report.

## Commands

```bash
PY="${CLAUDE_PLUGIN_DATA}/venv/bin/python"; [ -x "$PY" ] || PY="${CLAUDE_PLUGIN_DATA}/venv/Scripts/python.exe"; S="${CLAUDE_PLUGIN_ROOT}/skills/zm-ministry-directory/scripts"
"$PY" -B "$S/eservices.py" catalogue                       # once per run
"$PY" -B "$S/eservices.py" find-agency "Zambia Tourism Agency"
"$PY" -B "$S/eservices.py" agency --authority-id <ID> [--name "<provider title>"] \
    --ministry "Ministry of Tourism" --agency "Zambia Tourism Agency (ZTA)" \
    --evidence <run>/evidence --out <run>/eservices/<slug>.json
"$PY" -B "$S/eservices.py" service 96                      # raw detail for one service
```
