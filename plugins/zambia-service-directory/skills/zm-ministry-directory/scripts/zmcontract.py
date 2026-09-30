"""Fixed contract for all ministry workbooks: columns, terminology, enums.

This module is the single source of truth. references/TERMINOLOGY.md documents
the same values for agents; tests assert the two stay in sync.
"""
from __future__ import annotations

import re

# Service Passport: JSON field -> exact column header, in sheet order.
PASSPORT_COLUMNS: list[tuple[str, str]] = [
    ("ministry", "Ministry"),
    ("agency", "Agency"),
    ("service_name", "Service Name"),
    ("service_description", "Service Description"),
    ("who_can_apply", "Who Can Apply"),
    ("eligibility_requirements", "Eligibility Requirements"),
    ("fee", "Fee"),
    ("processing_time", "Processing Time"),
    ("validity", "Validity"),
    ("legal_references", "Legal References"),
    ("source_link", "Source / Service Link"),
]
PASSPORT_FIELDS = [f for f, _ in PASSPORT_COLUMNS]
PASSPORT_HEADERS = [h for _, h in PASSPORT_COLUMNS]
# Fields whose value must be backed by a cited source (ministry/agency/link are structural).
SOURCED_FIELDS = [
    "service_name", "service_description", "who_can_apply", "eligibility_requirements",
    "fee", "processing_time", "validity", "legal_references",
]

AGENCIES_HEADERS = [
    "Ministry No.", "Ministry", "Entity", "Entity Type",
    "Digitizable Service Areas", "Status", "Notes", "Source Link",
]
OVERVIEW_KEYS = ["Ministry", "Entities included", "Service passports", "Scope"]
OVERVIEW_TABLE_HEADERS = ["Agency", "Entity Type", "Service Count"]
SHEET_NAMES = ["Overview", "Agencies", "Service Passport"]

WORKBOOK_TITLE = "Zambia National Service Directory – Ministry File"
AGENCIES_TITLE = "Service-Delivering Entities – {ministry}"
SECTION_TITLE = "Service Passports – {agency}"
DEFAULT_SCOPE = (
    "Citizen- and business-facing public services. Educational institutions and "
    "internal policy functions are excluded."
)

# Placeholders and status strings (exact text).
NOT_PUBLISHED = "Not published"
NOT_APPLICABLE = "Not applicable"
HISTORICAL_FEE = "Historical fee — current amount requires confirmation"
ESERVICES_LISTED = "Listed on Zambia eServices"
ESERVICES_NOT_LISTED = "Not listed on Zambia eServices — 0 services"
DEPRECATED_PLACEHOLDERS = {"Not specified": NOT_PUBLISHED, "Not Specified": NOT_PUBLISHED, "N/A": NOT_APPLICABLE}
PLACEHOLDERS = {NOT_PUBLISHED, NOT_APPLICABLE}

AGENCY_STATUS = ["Included", "Excluded"]
ENTITY_TYPES = [
    "Government Department", "Statutory Agency", "Statutory Commission",
    "Statutory Board", "Statutory Council", "Statutory Authority", "Statutory Institute",
]
VERIFICATION = ["Pending", "Verified", "Verified with limitations", "Unresolved"]
WRITABLE_VERIFICATION = {"Verified", "Verified with limitations"}
ORIGINS = ["eservices", "official_other", "imported"]
ACTIONS = ["add", "correct", "remove", "unchanged"]

# Source tiers (see references/SOURCES.md). Tier 5 = lead only, never sufficient alone.
SOURCE_TIERS = {
    1: "Zambia eServices API",
    2: "Agency official website, forms, fee schedules, charters",
    3: "Legislation: Acts, statutory instruments, gazette",
    4: "Ministry, regulator, other government portals, official agency social pages",
    5: "Leads only: LinkedIn, news, Wikipedia, unofficial pages",
}
MAX_CITABLE_TIER = 4

ESERVICES_SERVICE_URL = "https://eservices.gov.zm/#/service/{id}"
ESERVICES_DIRECTORY_URL = "https://eservices.gov.zm/#/service-directory/national"

# Agencies "Notes" sentence patterns.
NOTES_LISTED = "{total} verified services: {eservices} on Zambia eServices and {other} on {other_source}"
NOTES_LISTED_ONLY = "{total} verified services, all on Zambia eServices"
NOTES_NOT_LISTED = "{total} services verified from {other_source}; not listed on Zambia eServices"
NOTES_LISTED_UNDER = "{total} services; listed under {provider} on Zambia eServices"
NOTES_NONE = "0 services verified; {reason}"

LEGAL_REF_PATTERNS = [
    re.compile(r"Act,?\s+No\.?\s*\d+,?\s+(of\s+)?\d{4}", re.I),
    re.compile(r"Cap\.?\s*\d+", re.I),
    re.compile(r"(Statutory Instrument|SI)\s+No\.?\s*\d+\s+of\s+\d{4}", re.I),
    re.compile(r"Act\s+of\s+\d{4}", re.I),
    re.compile(r"Constitution of Zambia", re.I),
]


def workbook_filename(ministry_name: str) -> str:
    """Zambia_National_Service_Directory_<Ministry_Name>.xlsx (spaces -> underscores)."""
    return f"Zambia_National_Service_Directory_{ministry_name.strip().replace(' ', '_')}.xlsx"


def normalise_name(text: str) -> str:
    """Lower-case, strip punctuation and extra spaces, for duplicate matching."""
    text = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def agency_key(name: str) -> str:
    """Agency identity for matching across renames: drops a bracketed acronym, e.g. 'X (DNPW)' -> 'x'."""
    return normalise_name(re.sub(r"\([^)]*\)", " ", name or ""))
