"""Bounded, auditable impact-context research providers."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
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
EVENT_LOOKAHEAD_DAYS = 10
_CACHE_SCHEMA_VERSION = 1
_MAX_GAP_SEARCHES = 1


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
    request_count: int = 0
    from_cache: bool = False
    cache_path: Optional[Path] = None


@dataclass
class ResearchResult:
    """Combined dynamic and static evidence for one forecast entity."""

    name: str
    context_type: str
    batches: list[ResearchBatch]

    @property
    def evidence(self) -> list[EvidenceItem]:
        """Return all evidence items, preserving dynamic-before-static ordering."""
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
        """Return daily dynamic evidence and slower-changing static evidence."""
        locations = tuple(representative_locations)
        local_now = get_local_now(timezone_name)
        entity_key = _entity_key(name, context_type, locations)
        dynamic_query, static_query = _build_queries(name, context_type, locations, local_now)

        dynamic = self._load_dynamic(entity_key, local_now)
        static = self._load_static(entity_key, local_now)
        remaining_gap_searches = _MAX_GAP_SEARCHES

        if dynamic is None:
            dynamic = self._search_batch(
                "dynamic",
                dynamic_query,
                local_now,
                locations,
            )
            if not dynamic.evidence and remaining_gap_searches:
                remaining_gap_searches -= 1
                dynamic = self._gap_fill(
                    dynamic,
                    _dynamic_gap_query(name, local_now),
                    local_now,
                    locations,
                )
            try:
                self._store_batch(entity_key, dynamic)
            except OSError as exc:
                raise ContextResearchError(
                    f"Unable to store the private Brave dynamic evidence sidecar: {exc}",
                    cost_cents=dynamic.request_count * BRAVE_REQUEST_COST_CENTS,
                ) from exc

        if static is None:
            static = self._search_batch(
                "static",
                static_query,
                local_now,
                locations,
            )
            if not static.evidence and remaining_gap_searches:
                static = self._gap_fill(
                    static,
                    _static_gap_query(name),
                    local_now,
                    locations,
                )
            try:
                self._store_batch(entity_key, static)
            except OSError as exc:
                raise ContextResearchError(
                    f"Unable to store the private Brave static evidence sidecar: {exc}",
                    cost_cents=(dynamic.request_count + static.request_count)
                    * BRAVE_REQUEST_COST_CENTS,
                ) from exc

        result = ResearchResult(name=name, context_type=context_type, batches=[dynamic, static])
        if not result.evidence:
            raise ContextResearchError(f"Brave returned no usable impact evidence for {name}.")
        logger.info(
            "Brave impact research for %s returned %d source(s) using %d new request(s); sidecars=%s",
            name,
            len(result.evidence),
            result.request_count,
            ", ".join(str(path) for path in result.evidence_paths),
        )
        return result

    def _load_dynamic(self, entity_key: str, local_now: datetime) -> Optional[ResearchBatch]:
        path = self.cache_dir / f"{entity_key}__dynamic.json"
        batch = _read_batch(path)
        if batch and batch.local_date == local_now.date().isoformat():
            batch.from_cache = True
            batch.request_count = 0
            batch.cache_path = path
            return batch
        return None

    def _load_static(self, entity_key: str, local_now: datetime) -> Optional[ResearchBatch]:
        path = self.cache_dir / f"{entity_key}__static.json"
        batch = _read_batch(path)
        if not batch:
            return None
        # Cache a no-evidence result only for the local day; do not suppress this
        # slower-changing category for the full static-cache lifetime.
        if not batch.evidence and batch.local_date != local_now.date().isoformat():
            return None
        try:
            retrieved = datetime.fromisoformat(batch.retrieved_at)
            if retrieved.tzinfo is None:
                retrieved = retrieved.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        if datetime.now(timezone.utc) - retrieved.astimezone(timezone.utc) > timedelta(
            days=STATIC_EVIDENCE_MAX_AGE_DAYS
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
    ) -> ResearchBatch:
        payload = self._request(query, locations)
        retrieved_at = datetime.now(timezone.utc).isoformat()
        evidence = _extract_evidence(payload, bucket, query, retrieved_at)
        return ResearchBatch(
            bucket=bucket,
            query=query,
            retrieved_at=retrieved_at,
            local_date=local_now.date().isoformat(),
            evidence=evidence,
            request_count=1,
        )

    def _gap_fill(
        self,
        initial: ResearchBatch,
        query: str,
        local_now: datetime,
        locations: tuple[ResearchLocation, ...],
    ) -> ResearchBatch:
        logger.warning(
            "Brave returned no evidence for the %s query; running one bounded gap-fill search.",
            initial.bucket,
        )
        gap = self._search_batch(initial.bucket, query, local_now, locations)
        gap.query = f"{initial.query}\nGAP FILL: {gap.query}"
        gap.request_count += initial.request_count
        return gap

    def _request(
        self,
        query: str,
        locations: tuple[ResearchLocation, ...],
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
            "maximum_number_of_urls": 12,
            "maximum_number_of_tokens": 6144,
            "maximum_number_of_snippets": 36,
            "maximum_number_of_tokens_per_url": 1600,
            "maximum_number_of_snippets_per_url": 8,
            "context_threshold_mode": "balanced",
        }
        country = headers.get("X-Loc-Country")
        if country:
            body["country"] = country.lower()

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
                        format_request_exception(exc),
                    )
                    time.sleep(2 ** (attempt - 1))
                    continue
                break
        detail = format_request_exception(last_error) if last_error else "unknown error"
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
        }
        write_text_file(path, json.dumps(payload, indent=2, ensure_ascii=False))
        batch.cache_path = path


def _build_queries(
    name: str,
    context_type: str,
    locations: tuple[ResearchLocation, ...],
    local_now: datetime,
) -> tuple[str, str]:
    """Build the two controlled searches used by the budget-aware default cadence."""
    descriptor = _entity_descriptor(name, context_type, locations)
    today = local_now.date().isoformat()
    events_end = (local_now + timedelta(days=EVENT_LOOKAHEAD_DAYS)).date().isoformat()
    dynamic = (
        f"{descriptor} current flooding landslides drought damaged infrastructure power water disruptions "
        f"and major public events with exact dates {today} to {events_end} official local sources"
    )
    static = (
        f"{descriptor} official rainfall wind thresholds flood landslide road closure outage damage "
        "flood-prone communities critical infrastructure vulnerable populations planning research reports"
    )
    return _bounded_query(dynamic), _bounded_query(static)


def _dynamic_gap_query(name: str, local_now: datetime) -> str:
    events_end = (local_now + timedelta(days=EVENT_LOOKAHEAD_DAYS)).date().isoformat()
    return _bounded_query(
        f"{name} official council emergency management current disruptions major events exact dates "
        f"through {events_end}"
    )


def _static_gap_query(name: str) -> str:
    return _bounded_query(
        f"{name} government hazard plan quantitative rainfall wind impact thresholds vulnerable assets "
        "flood maps infrastructure"
    )


def _entity_descriptor(
    name: str,
    context_type: str,
    locations: tuple[ResearchLocation, ...],
) -> str:
    if context_type not in {"area", "regional"} or not locations:
        return name
    representative_names = ", ".join(location.name for location in locations[:5])
    label = "region" if context_type == "regional" else "area"
    return f"{name} {label} including {representative_names}"


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
        return ResearchBatch(
            bucket=str(payload["bucket"]),
            query=str(payload["query"]),
            retrieved_at=str(payload["retrieved_at"]),
            local_date=str(payload["local_date"]),
            evidence=evidence,
            cache_path=path,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        logger.warning("Ignoring invalid Brave evidence cache %s.", path)
        return None


def _is_transient_brave_error(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status is None or status == 429 or (isinstance(status, int) and status >= 500)


__all__ = [
    "BraveContextResearchProvider",
    "ContextResearchError",
    "EvidenceItem",
    "ResearchLocation",
    "ResearchResult",
]
