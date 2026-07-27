"""Bounded, auditable impact-context research providers."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

import requests

from ..util import ensure_directory, format_request_exception, get_local_now, write_text_file

logger = logging.getLogger(__name__)

BRAVE_LLM_CONTEXT_URL = "https://api.search.brave.com/res/v1/llm/context"
BRAVE_REQUEST_COST_CENTS = 0.5  # $5 per 1,000 Search/LLM Context requests, checked 2026-07-26.
EVIDENCE_CACHE_DIR = ensure_directory("ibf_cache/impact/evidence")
STATIC_EVIDENCE_MAX_AGE_DAYS = 60
EVENT_EVIDENCE_MAX_AGE_DAYS = 3
EVENT_LOOKAHEAD_DAYS = 10
BRAVE_RESEARCH_VERSION = 2
_CACHE_SCHEMA_VERSION = BRAVE_RESEARCH_VERSION
_MAX_GAP_SEARCHES = 1
_UNSUITABLE_EVIDENCE_HOST_SUFFIXES = ("substack.com", "wikipedia.org")
_BRAVE_SEARCH_COUNTRIES = {
    "AR",
    "AT",
    "AU",
    "BE",
    "BR",
    "CA",
    "CH",
    "CL",
    "CN",
    "DE",
    "DK",
    "ES",
    "FI",
    "FR",
    "GB",
    "GR",
    "HK",
    "ID",
    "IN",
    "IT",
    "JP",
    "KR",
    "MX",
    "MY",
    "NL",
    "NO",
    "NZ",
    "PH",
    "PL",
    "PT",
    "RU",
    "SA",
    "SE",
    "TR",
    "TW",
    "US",
    "ZA",
}


class ContextResearchError(RuntimeError):
    """Raised when research or synthesis cannot return usable context."""

    def __init__(self, message: str, *, cost_cents: float = 0.0) -> None:
        super().__init__(message)
        self.cost_cents = max(float(cost_cents), 0.0)


@dataclass(frozen=True)
class ResearchLocation:
    """A representative point used to spatially ground context research."""

    name: str
    latitude: float
    longitude: float
    country_code: Optional[str] = None
    country_name: Optional[str] = None
    admin1: Optional[str] = None
    admin2: Optional[str] = None


@dataclass(frozen=True)
class EvidenceItem:
    """One source and the passages Brave extracted for a controlled query."""

    bucket: str
    query: str
    url: str
    title: str
    hostname: str
    published_date: Optional[str]
    source_age: list[str]
    retrieved_at: str
    passages: list[str]


@dataclass
class ResearchBatch:
    """Evidence returned by one category search or its cached equivalent."""

    bucket: str
    query: str
    retrieved_at: str
    local_date: str
    evidence: list[EvidenceItem]
    rejected_evidence: list[EvidenceItem] = field(default_factory=list)
    request_count: int = 0
    from_cache: bool = False
    cache_path: Optional[Path] = None


@dataclass
class ResearchResult:
    """Combined focused evidence batches for one forecast entity."""

    name: str
    context_type: str
    batches: list[ResearchBatch]

    @property
    def evidence(self) -> list[EvidenceItem]:
        """Return all evidence items in their focused search-bucket order."""
        return [item for batch in self.batches for item in batch.evidence]

    @property
    def request_count(self) -> int:
        """Return the number of billable Brave requests made during this call."""
        return sum(batch.request_count for batch in self.batches)

    @property
    def estimated_cost_cents(self) -> float:
        """Return the current list-price estimate for requests made during this call."""
        return self.request_count * BRAVE_REQUEST_COST_CENTS

    @property
    def evidence_paths(self) -> list[Path]:
        """Return the private cache/sidecar files that contain the evidence."""
        return [batch.cache_path for batch in self.batches if batch.cache_path is not None]


@dataclass(frozen=True)
class SearchSpec:
    """A focused Brave search and its independent refresh policy."""

    bucket: str
    query: str
    freshness: Optional[str]
    max_age_days: int
    same_local_date: bool = False


class BraveContextResearchProvider:
    """Run category-specific Brave LLM Context searches with bounded gap filling."""

    def __init__(self, api_key: str, *, cache_dir: Path = EVIDENCE_CACHE_DIR) -> None:
        if not api_key or not api_key.strip():
            raise ContextResearchError(
                "BRAVE_SEARCH_API_KEY is required when context_provider = 'brave'."
            )
        self.api_key = api_key.strip()
        self.cache_dir = ensure_directory(cache_dir)

    def research(
        self,
        name: str,
        *,
        context_type: str,
        timezone_name: str,
        representative_locations: Iterable[ResearchLocation] = (),
    ) -> ResearchResult:
        """Return focused, independently cached evidence for the four context classes."""
        locations = tuple(representative_locations)
        local_now = get_local_now(timezone_name)
        entity_key = _entity_key(name, context_type, locations)
        specs = _build_search_specs(name, context_type, locations, local_now)
        remaining_gap_searches = _MAX_GAP_SEARCHES
        batches: list[ResearchBatch] = []

        for spec in specs:
            batch = self._load_batch(entity_key, spec, local_now)
            if batch is not None:
                batches.append(batch)
                continue
            batch = self._search_batch(
                spec.bucket,
                spec.query,
                local_now,
                locations,
                name=name,
                context_type=context_type,
                freshness=spec.freshness,
            )
            # An empty search can validly mean that no current condition or event
            # exists. Only spend the single gap request when Brave did return
            # material but every source failed evidence validation.
            if not batch.evidence and batch.rejected_evidence and remaining_gap_searches:
                remaining_gap_searches -= 1
                batch = self._gap_fill(
                    batch,
                    _gap_query(spec, name, context_type, locations, local_now),
                    local_now,
                    locations,
                    name=name,
                    context_type=context_type,
                    freshness=spec.freshness,
                )
            try:
                self._store_batch(entity_key, batch)
            except OSError as exc:
                raise ContextResearchError(
                    f"Unable to store the private Brave {spec.bucket} evidence sidecar: {exc}",
                    cost_cents=(sum(item.request_count for item in batches) + batch.request_count)
                    * BRAVE_REQUEST_COST_CENTS,
                ) from exc
            batches.append(batch)

        result = ResearchResult(name=name, context_type=context_type, batches=batches)
        if not result.evidence:
            raise ContextResearchError(f"Brave returned no usable impact evidence for {name}.")
        logger.info(
            "Brave impact research v%d for %s returned %d accepted source(s) using %d new "
            "request(s); buckets=%s; sidecars=%s",
            BRAVE_RESEARCH_VERSION,
            name,
            len(result.evidence),
            result.request_count,
            ", ".join(
                f"{batch.bucket}:{len(batch.evidence)} accepted/{len(batch.rejected_evidence)} rejected"
                for batch in result.batches
            ),
            ", ".join(str(path) for path in result.evidence_paths),
        )
        return result

    def _load_batch(
        self,
        entity_key: str,
        spec: SearchSpec,
        local_now: datetime,
    ) -> Optional[ResearchBatch]:
        path = self.cache_dir / f"{entity_key}__{spec.bucket}.json"
        batch = _read_batch(path)
        if not batch:
            return None
        if spec.same_local_date and batch.local_date != local_now.date().isoformat():
            return None
        try:
            retrieved = datetime.fromisoformat(batch.retrieved_at)
            if retrieved.tzinfo is None:
                retrieved = retrieved.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        reference_now = local_now
        if reference_now.tzinfo is None:
            reference_now = reference_now.replace(tzinfo=timezone.utc)
        if reference_now.astimezone(timezone.utc) - retrieved.astimezone(timezone.utc) > timedelta(
            days=spec.max_age_days
        ):
            return None
        batch.from_cache = True
        batch.request_count = 0
        batch.cache_path = path
        return batch

    def _search_batch(
        self,
        bucket: str,
        query: str,
        local_now: datetime,
        locations: tuple[ResearchLocation, ...],
        *,
        name: str,
        context_type: str,
        freshness: Optional[str],
    ) -> ResearchBatch:
        payload = self._request(query, locations, freshness=freshness)
        retrieved_at = datetime.now(timezone.utc).isoformat()
        extracted = _extract_evidence(payload, bucket, query, retrieved_at)
        evidence, rejected = _filter_usable_evidence(
            extracted,
            name=name,
            context_type=context_type,
            locations=locations,
        )
        if bucket == "events":
            event_end = local_now.date() + timedelta(days=EVENT_LOOKAHEAD_DAYS)
            in_window = [
                item
                for item in evidence
                if _evidence_has_date_in_window(item, local_now.date(), event_end)
            ]
            rejected.extend(item for item in evidence if item not in in_window)
            evidence = in_window
        elif bucket == "current":
            current_evidence = [item for item in evidence if _has_current_hazard_signal(item)]
            rejected.extend(item for item in evidence if item not in current_evidence)
            evidence = current_evidence
        if rejected:
            logger.warning(
                "Rejected %d Brave source(s) failing evidence validation for %s (%s): %s",
                len(rejected),
                name,
                bucket,
                "; ".join(item.title for item in rejected[:5]),
            )
        return ResearchBatch(
            bucket=bucket,
            query=query,
            retrieved_at=retrieved_at,
            local_date=local_now.date().isoformat(),
            evidence=evidence,
            rejected_evidence=rejected,
            request_count=1,
        )

    def _gap_fill(
        self,
        initial: ResearchBatch,
        query: str,
        local_now: datetime,
        locations: tuple[ResearchLocation, ...],
        *,
        name: str,
        context_type: str,
        freshness: Optional[str],
    ) -> ResearchBatch:
        logger.warning(
            "Brave returned no evidence for the %s query; running one bounded gap-fill search.",
            initial.bucket,
        )
        gap = self._search_batch(
            initial.bucket,
            query,
            local_now,
            locations,
            name=name,
            context_type=context_type,
            freshness=freshness,
        )
        gap.query = f"{initial.query}\nGAP FILL: {gap.query}"
        gap.rejected_evidence = initial.rejected_evidence + gap.rejected_evidence
        gap.request_count += initial.request_count
        return gap

    def _request(
        self,
        query: str,
        locations: tuple[ResearchLocation, ...],
        *,
        freshness: Optional[str] = None,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/json",
            "X-Subscription-Token": self.api_key,
        }
        if locations:
            latitude = sum(location.latitude for location in locations) / len(locations)
            longitude = sum(location.longitude for location in locations) / len(locations)
            headers["X-Loc-Lat"] = f"{latitude:.6f}"
            headers["X-Loc-Long"] = f"{longitude:.6f}"
            countries = {
                location.country_code.strip().upper()
                for location in locations
                if location.country_code and location.country_code.strip()
            }
            if len(countries) == 1:
                headers["X-Loc-Country"] = next(iter(countries))

        body: dict[str, Any] = {
            "q": _bounded_query(query),
            "search_lang": "en",
            "count": 20,
            "maximum_number_of_urls": 8,
            "maximum_number_of_tokens": 4096,
            "maximum_number_of_snippets": 24,
            "maximum_number_of_tokens_per_url": 1400,
            "maximum_number_of_snippets_per_url": 6,
            "context_threshold_mode": "balanced",
        }
        country = headers.get("X-Loc-Country")
        if country:
            # Brave accepts all ISO 3166-1 alpha-2 codes in the location header,
            # but its search-market parameter supports a much smaller enum. Use
            # the global market for territories such as VG rather than sending an
            # invalid market or silently defaulting their results to the US.
            body["country"] = country if country in _BRAVE_SEARCH_COUNTRIES else "ALL"
        if freshness:
            body["freshness"] = freshness

        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                response = requests.post(
                    BRAVE_LLM_CONTEXT_URL,
                    headers=headers,
                    json=body,
                    timeout=30,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Brave response was not a JSON object")
                return payload
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < 3 and _is_transient_brave_error(exc):
                    logger.warning(
                        "Brave request failed transiently (attempt %d/3): %s",
                        attempt,
                        _format_brave_error(exc),
                    )
                    time.sleep(2 ** (attempt - 1))
                    continue
                break
        detail = _format_brave_error(last_error) if last_error else "unknown error"
        raise ContextResearchError(f"Brave LLM Context request failed: {detail}") from last_error

    def _store_batch(self, entity_key: str, batch: ResearchBatch) -> None:
        path = self.cache_dir / f"{entity_key}__{batch.bucket}.json"
        payload = {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "bucket": batch.bucket,
            "query": batch.query,
            "retrieved_at": batch.retrieved_at,
            "local_date": batch.local_date,
            "evidence": [asdict(item) for item in batch.evidence],
            "rejected_evidence": [
                {
                    **asdict(item),
                    "rejection_reason": (
                        "failed place, source-quality, freshness, or event-window validation"
                    ),
                }
                for item in batch.rejected_evidence
            ],
        }
        write_text_file(path, json.dumps(payload, indent=2, ensure_ascii=False))
        batch.cache_path = path


def _build_search_specs(
    name: str,
    context_type: str,
    locations: tuple[ResearchLocation, ...],
    local_now: datetime,
) -> tuple[SearchSpec, ...]:
    """Build four focused searches with evidence-specific refresh policies."""
    descriptor = _entity_descriptor(name, context_type, locations)
    threshold_descriptor = _entity_descriptor(
        name, context_type, locations, include_admin=False
    )
    today = local_now.date().isoformat()
    events_end = (local_now + timedelta(days=EVENT_LOOKAHEAD_DAYS)).date().isoformat()
    return (
        SearchSpec(
            bucket="current",
            query=_bounded_query(
                f"{descriptor} current flood road closure power water infrastructure official"
            ),
            freshness="pw",
            max_age_days=1,
            same_local_date=True,
        ),
        SearchSpec(
            bucket="events",
            query=_bounded_query(
                f"{descriptor} major festival concert sport public event exact dates {today} to {events_end}"
            ),
            freshness=None,
            max_age_days=EVENT_EVIDENCE_MAX_AGE_DAYS,
        ),
        SearchSpec(
            bucket="thresholds",
            query=_bounded_query(
                f"{threshold_descriptor} flood hydrology rainfall wind impact thresholds official"
            ),
            freshness=None,
            max_age_days=STATIC_EVIDENCE_MAX_AGE_DAYS,
        ),
        SearchSpec(
            bucket="exposure",
            query=_bounded_query(
                f"{descriptor} official flood-prone communities critical infrastructure vulnerable "
                "populations stormwater wastewater coastal hazard maps"
            ),
            freshness=None,
            max_age_days=STATIC_EVIDENCE_MAX_AGE_DAYS,
        ),
    )


def _gap_query(
    spec: SearchSpec,
    name: str,
    context_type: str,
    locations: tuple[ResearchLocation, ...],
    local_now: datetime,
) -> str:
    """Build the single permitted retry after geographic rejection."""
    descriptor = _entity_descriptor(name, context_type, locations)
    topic = {
        "current": "current official council emergency management disruption status",
        "events": (
            "major events exact dates "
            f"{local_now.date().isoformat()} to "
            f"{(local_now + timedelta(days=EVENT_LOOKAHEAD_DAYS)).date().isoformat()}"
        ),
        "thresholds": "government flood plan quantitative rainfall wind impact threshold",
        "exposure": "government hazard map vulnerable infrastructure communities",
    }[spec.bucket]
    return _bounded_query(
        f"{descriptor} {topic}"
    )


def _entity_descriptor(
    name: str,
    context_type: str,
    locations: tuple[ResearchLocation, ...],
    *,
    include_admin: bool = True,
) -> str:
    if context_type == "location" and locations:
        return _location_search_identity(locations[0], include_admin=include_admin)
    quoted_name = _quoted(name)
    if context_type not in {"area", "regional"} or not locations:
        return quoted_name
    representative_names = " OR ".join(
        _quoted(_primary_place_name(location.name)) for location in locations[:3]
    )
    label = "region" if context_type == "regional" else "area"
    return f"{quoted_name} {label} including ({representative_names})"


def _location_search_identity(
    location: ResearchLocation,
    *,
    include_admin: bool = True,
) -> str:
    """Return automatically geocoded locality and administrative terms for search."""
    primary = _primary_place_name(location.name)
    terms: list[Optional[str]] = [primary]
    if include_admin:
        admin2 = _short_admin_name(location.admin2)
        admin1 = _short_admin_name(location.admin1)
        if admin2 and len(admin2.split()) >= 2 and _normalize_place_text(admin2) != _normalize_place_text(primary):
            terms.append(admin2)
        elif admin1 and _normalize_place_text(admin1) != _normalize_place_text(primary):
            terms.append(admin1)
        elif location.country_name:
            terms.append(location.country_name)
        else:
            terms.extend(part.strip() for part in location.name.split(",")[1:])
    unique: list[str] = []
    normalized_seen: set[str] = set()
    for term in terms:
        if not term or not term.strip():
            continue
        normalized = _normalize_place_text(term)
        if not normalized or normalized in normalized_seen:
            continue
        normalized_seen.add(normalized)
        unique.append(term.strip())
    return " ".join(_quoted(term) for term in unique[:4])


def _short_admin_name(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    shortened = re.sub(
        r"\s+(?:district|region|county|regional municipality|municipality|province|state)$",
        "",
        value.strip(),
        flags=re.IGNORECASE,
    ).strip()
    return shortened or value.strip()


def _primary_place_name(name: str) -> str:
    return name.split(",", 1)[0].strip() or name.strip()


def _quoted(value: str) -> str:
    return f'"{value.replace(chr(34), "").strip()}"'


def _bounded_query(query: str) -> str:
    """Respect Brave's 400-character and 50-word query limits."""
    normalized = re.sub(r"\s+", " ", query).strip()
    words = normalized.split(" ")[:50]
    return " ".join(words)[:400].rstrip()


def _extract_evidence(
    payload: dict[str, Any],
    bucket: str,
    query: str,
    retrieved_at: str,
) -> list[EvidenceItem]:
    grounding = payload.get("grounding")
    if not isinstance(grounding, dict):
        return []
    raw_items = grounding.get("generic")
    if not isinstance(raw_items, list):
        return []
    sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    evidence: list[EvidenceItem] = []
    seen_urls: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        url = str(raw_item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen_urls:
            continue
        snippets = raw_item.get("snippets")
        if not isinstance(snippets, list):
            continue
        passages = [
            re.sub(r"\s+", " ", str(snippet)).strip()[:4000]
            for snippet in snippets
            if str(snippet).strip()
        ][:8]
        if not passages:
            continue
        seen_urls.add(url)
        metadata = sources.get(url) if isinstance(sources, dict) else None
        if not isinstance(metadata, dict):
            metadata = {}
        raw_age = metadata.get("age")
        source_age = [str(value) for value in raw_age] if isinstance(raw_age, list) else []
        title = str(raw_item.get("title") or metadata.get("title") or url).strip()
        hostname = str(metadata.get("hostname") or urlsplit(url).hostname or "").strip()
        evidence.append(
            EvidenceItem(
                bucket=bucket,
                query=query,
                url=url,
                title=title,
                hostname=hostname,
                published_date=_published_date(source_age),
                source_age=source_age,
                retrieved_at=retrieved_at,
                passages=passages,
            )
        )
    return evidence


def _filter_usable_evidence(
    evidence: list[EvidenceItem],
    *,
    name: str,
    context_type: str,
    locations: tuple[ResearchLocation, ...],
) -> tuple[list[EvidenceItem], list[EvidenceItem]]:
    """Reject sources that do not mention the geocoded locality or its local region."""
    aliases = _geographic_aliases(name, context_type, locations)
    if not aliases:
        return evidence, []
    accepted: list[EvidenceItem] = []
    rejected: list[EvidenceItem] = []
    for item in evidence:
        hostname = item.hostname.lower().strip(".")
        if any(
            hostname == suffix or hostname.endswith(f".{suffix}")
            for suffix in _UNSUITABLE_EVIDENCE_HOST_SUFFIXES
        ):
            rejected.append(item)
            continue
        haystack = _normalize_place_text(
            " ".join([item.title, item.url, item.hostname, *item.passages])
        )
        if any(_contains_place_phrase(haystack, alias) for alias in aliases):
            accepted.append(item)
        else:
            rejected.append(item)
    return accepted, rejected


def _geographic_aliases(
    name: str,
    context_type: str,
    locations: tuple[ResearchLocation, ...],
) -> tuple[str, ...]:
    raw_aliases: list[str] = []
    if context_type in {"area", "regional"}:
        raw_aliases.append(name)
        raw_aliases.extend(
            token
            for token in re.findall(r"[\wÀ-ÿĀ-ž]+", name, flags=re.UNICODE)
            if len(_normalize_place_text(token)) >= 5
            and _normalize_place_text(token) not in {"region", "regional"}
        )
    else:
        raw_aliases.append(_primary_place_name(name))
    for location in locations:
        raw_aliases.append(_primary_place_name(location.name))
        if location.admin2:
            raw_aliases.append(location.admin2)
        elif location.admin1:
            raw_aliases.append(location.admin1)
    aliases: list[str] = []
    for value in raw_aliases:
        normalized = _normalize_place_text(value)
        if len(normalized) >= 3 and normalized not in aliases:
            aliases.append(normalized)
    return tuple(aliases)


def _normalize_place_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    ascii_text = "".join(character for character in decomposed if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).strip()


def _contains_place_phrase(haystack: str, alias: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", haystack))


def _evidence_has_date_in_window(item: EvidenceItem, start: date, end: date) -> bool:
    """Require event evidence itself to contain an exact date in the allowed window."""
    text = " ".join([item.title, *item.passages])
    months = (
        "January|February|March|April|May|June|July|August|September|October|November|December"
    )
    candidates: list[tuple[str, tuple[str, ...]]] = [
        (r"\b\d{4}-\d{2}-\d{2}\b", ("%Y-%m-%d",)),
        (
            rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{months})\s+\d{{4}}\b",
            ("%d %B %Y",),
        ),
        (
            rf"\b(?:{months})\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b",
            ("%B %d, %Y", "%B %d %Y"),
        ),
    ]
    for pattern, formats in candidates:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            raw = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", match.group(0), flags=re.IGNORECASE)
            for format_string in formats:
                try:
                    value = datetime.strptime(raw, format_string).date()
                except ValueError:
                    continue
                if start <= value <= end:
                    return True
    return False


def _has_current_hazard_signal(item: EvidenceItem) -> bool:
    """Exclude fresh but unrelated local pages from the current-disruption bucket."""
    text = _normalize_place_text(" ".join([item.title, *item.passages]))
    pattern = (
        r"\b(?:flood(?:ing|ed)?|landslide|landslip|drought|wildfire|bushfire|storm|"
        r"cyclone|hurricane|tornado|heatwave|emergency|evacuat(?:e|ed|ion)|"
        r"road closure|power outage|water (?:outage|shortage|restriction|contamination)|"
        r"damaged infrastructure|disease outbreak)\b"
    )
    return bool(re.search(pattern, text))


def _published_date(source_age: list[str]) -> Optional[str]:
    for value in source_age:
        match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", value)
        if match:
            return match.group(0)
    return None


def _entity_key(
    name: str,
    context_type: str,
    locations: tuple[ResearchLocation, ...],
) -> str:
    payload = {
        "schema_version": _CACHE_SCHEMA_VERSION,
        "name": name,
        "context_type": context_type,
        "locations": [asdict(location) for location in locations],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]
    safe_name = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:50] or "context"
    return f"{context_type}_{safe_name}_{digest}"


def _read_batch(path: Path) -> Optional[ResearchBatch]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None
        raw_evidence = payload.get("evidence")
        if not isinstance(raw_evidence, list):
            return None
        evidence = [EvidenceItem(**item) for item in raw_evidence if isinstance(item, dict)]
        raw_rejected = payload.get("rejected_evidence", [])
        rejected_evidence = []
        if isinstance(raw_rejected, list):
            for item in raw_rejected:
                if not isinstance(item, dict):
                    continue
                normalized = {key: value for key, value in item.items() if key != "rejection_reason"}
                rejected_evidence.append(EvidenceItem(**normalized))
        return ResearchBatch(
            bucket=str(payload["bucket"]),
            query=str(payload["query"]),
            retrieved_at=str(payload["retrieved_at"]),
            local_date=str(payload["local_date"]),
            evidence=evidence,
            rejected_evidence=rejected_evidence,
            cache_path=path,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        logger.warning("Ignoring invalid Brave evidence cache %s.", path)
        return None


def _is_transient_brave_error(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status is None or status == 429 or (isinstance(status, int) and status >= 500)


def _format_brave_error(exc: Exception) -> str:
    """Include Brave's safe structured error code and message in diagnostics."""
    base = format_request_exception(exc)
    response = getattr(exc, "response", None)
    if response is None:
        return base
    try:
        payload = response.json()
    except (requests.JSONDecodeError, ValueError):
        return base
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
        return base
    error = payload["error"]
    code = re.sub(r"\s+", " ", str(error.get("code") or "")).strip()[:100]
    message = re.sub(r"\s+", " ", str(error.get("detail") or "")).strip()[:500]
    if code:
        base += f" code={code}"
    if message:
        base += f" message={message}"
    return base


__all__ = [
    "BRAVE_RESEARCH_VERSION",
    "BraveContextResearchProvider",
    "ContextResearchError",
    "EvidenceItem",
    "ResearchLocation",
    "ResearchResult",
]
