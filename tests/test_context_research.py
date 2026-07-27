from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import requests

from ibf.api.context_research import (
    BraveContextResearchProvider,
    ContextResearchError,
    ResearchLocation,
)


def _brave_payload(
    url: str,
    title: str,
    passage: str = "Official Otaki evidence with a 50 mm rainfall threshold.",
) -> dict:
    return {
        "grounding": {
            "generic": [
                {
                    "url": url,
                    "title": title,
                    "snippets": [passage],
                }
            ],
            "map": [],
        },
        "sources": {
            url: {
                "title": title,
                "hostname": "council.govt.nz",
                "age": ["Sunday, July 26, 2026", "2026-07-26", "today"],
            }
        },
    }


def test_brave_research_uses_split_cadences_and_private_sidecars(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 7, 26, 14, 0, tzinfo=ZoneInfo("Pacific/Auckland"))
    current_time = [now]
    monkeypatch.setattr(
        "ibf.api.context_research.get_local_now", lambda _timezone: current_time[0]
    )
    provider = BraveContextResearchProvider("brave-key", cache_dir=tmp_path)
    requests_seen: list[tuple[str, str | None]] = []

    def fake_request(query, locations, *, freshness=None):
        requests_seen.append((query, freshness))
        passage = "Official Otaki evidence with a 50 mm rainfall threshold."
        if "current flood" in query:
            passage = "Current flooding affects Ōtaki roads and infrastructure."
        elif "major festival" in query:
            passage = "The major Ōtaki festival will be held on 30 July 2026."
        return _brave_payload(
            f"https://council.govt.nz/otaki-source-{len(requests_seen)}",
            f"Otaki Source {len(requests_seen)}",
            passage,
        )

    monkeypatch.setattr(provider, "_request", fake_request)
    locations = [
        ResearchLocation(
            name="Ōtaki, New Zealand",
            latitude=-40.75,
            longitude=175.15,
            country_code="NZ",
            country_name="New Zealand",
            admin1="Wellington Region",
            admin2="Kapiti Coast District",
        )
    ]

    first = provider.research(
        "Otaki, New Zealand",
        context_type="location",
        timezone_name="Pacific/Auckland",
        representative_locations=locations,
    )
    second = provider.research(
        "Otaki, New Zealand",
        context_type="location",
        timezone_name="Pacific/Auckland",
        representative_locations=locations,
    )
    current_time[0] = datetime(2026, 7, 27, 14, 0, tzinfo=ZoneInfo("Pacific/Auckland"))
    third = provider.research(
        "Otaki, New Zealand",
        context_type="location",
        timezone_name="Pacific/Auckland",
        representative_locations=locations,
    )

    assert first.request_count == 4
    assert first.estimated_cost_cents == 2.0
    assert len(first.evidence) == 4
    assert second.request_count == 0
    assert third.request_count == 1
    assert len(requests_seen) == 5
    assert all(len(query.split()) <= 50 for query, _freshness in requests_seen)
    assert all('"Ōtaki"' in query for query, _freshness in requests_seen)
    assert '"Kapiti Coast"' in requests_seen[0][0]
    assert '"Kapiti Coast"' not in requests_seen[2][0]  # thresholds use locality only
    assert requests_seen[0][1] == "pw"
    assert all(freshness is None for _query, freshness in requests_seen[1:4])
    assert {path.name.split("__")[-1] for path in first.evidence_paths} == {
        "current.json",
        "events.json",
        "thresholds.json",
        "exposure.json",
    }
    sidecar = json.loads(first.evidence_paths[0].read_text(encoding="utf-8"))
    assert sidecar["query"] in [query for query, _freshness in requests_seen]
    assert sidecar["evidence"][0]["url"].startswith("https://council.govt.nz/otaki")
    assert sidecar["evidence"][0]["published_date"] == "2026-07-26"


def test_brave_request_uses_country_and_coordinate_headers(tmp_path, monkeypatch) -> None:
    provider = BraveContextResearchProvider("brave-key", cache_dir=tmp_path)
    captured = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return _brave_payload("https://example.nz/evidence", "Evidence")

    def fake_post(url, *, headers, json, timeout):
        captured.update({"url": url, "headers": headers, "body": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr("ibf.api.context_research.requests.post", fake_post)
    provider._request(
        "Otaki official flood plan",
        (
            ResearchLocation("Otaki", -40.75, 175.15, "NZ"),
            ResearchLocation("Waikanae", -40.88, 175.07, "NZ"),
        ),
        freshness="pm",
    )

    assert captured["headers"]["X-Subscription-Token"] == "brave-key"
    assert captured["headers"]["X-Loc-Country"] == "NZ"
    assert captured["body"]["country"] == "NZ"
    assert captured["body"]["freshness"] == "pm"
    assert float(captured["headers"]["X-Loc-Lat"]) == (-40.75 - 40.88) / 2
    assert captured["timeout"] == 30


def test_brave_request_uses_global_market_for_unsupported_territory(
    tmp_path, monkeypatch
) -> None:
    provider = BraveContextResearchProvider("brave-key", cache_dir=tmp_path)
    captured = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return _brave_payload("https://example.vg/evidence", "Evidence")

    def fake_post(url, *, headers, json, timeout):
        captured.update({"headers": headers, "body": json})
        return Response()

    monkeypatch.setattr("ibf.api.context_research.requests.post", fake_post)
    provider._request(
        "Road Town official flood plan",
        (ResearchLocation("Road Town", 18.4286, -64.6185, "VG"),),
    )

    assert captured["headers"]["X-Loc-Country"] == "VG"
    assert captured["body"]["country"] == "ALL"


def test_brave_error_includes_structured_api_detail(tmp_path, monkeypatch) -> None:
    provider = BraveContextResearchProvider("brave-key", cache_dir=tmp_path)
    response = requests.Response()
    response.status_code = 400
    response.url = "https://api.search.brave.com/res/v1/llm/context"
    response._content = json.dumps(
        {
            "error": {
                "code": "OPTION_NOT_IN_PLAN",
                "detail": "The option is not subscribed in the plan.",
            }
        }
    ).encode()
    response.request = requests.Request("POST", response.url).prepare()

    monkeypatch.setattr("ibf.api.context_research.requests.post", lambda *args, **kwargs: response)

    with pytest.raises(ContextResearchError) as exc_info:
        provider._request("Otaki official flood plan", ())

    message = str(exc_info.value)
    assert "OPTION_NOT_IN_PLAN" in message
    assert "not subscribed in the plan" in message


def test_brave_rejects_wrong_place_evidence_and_records_it(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 7, 26, 14, 0, tzinfo=ZoneInfo("Pacific/Auckland"))
    monkeypatch.setattr("ibf.api.context_research.get_local_now", lambda _timezone: now)
    provider = BraveContextResearchProvider("brave-key", cache_dir=tmp_path)

    def fake_request(query, locations, *, freshness=None):
        if "current" in query:
            return _brave_payload(
                "https://waitaki.govt.nz/flood",
                "Waitaki flooding in Oamaru",
                "Waitaki District declared an emergency after flooding in Oamaru, Otago.",
            )
        return _brave_payload(
            f"https://kapiticoast.govt.nz/{len(query)}",
            "Ōtaki flood information",
            (
                "Kapiti Coast District lists a major Ōtaki event on 30 July 2026."
                if "major festival" in query
                else "Kapiti Coast District provides official flood information for Ōtaki."
            ),
        )

    monkeypatch.setattr(provider, "_request", fake_request)
    locations = (
        ResearchLocation(
            "Ōtaki, New Zealand",
            -40.75,
            175.15,
            "NZ",
            "New Zealand",
            "Wellington Region",
            "Kapiti Coast District",
        ),
    )

    result = provider.research(
        "Otaki, New Zealand",
        context_type="location",
        timezone_name="Pacific/Auckland",
        representative_locations=locations,
    )

    current = next(batch for batch in result.batches if batch.bucket == "current")
    assert current.evidence == []
    assert len(current.rejected_evidence) == 2  # initial search plus the one bounded retry
    sidecar = json.loads(current.cache_path.read_text(encoding="utf-8"))
    assert sidecar["rejected_evidence"][0]["rejection_reason"] == (
        "failed place, source-quality, freshness, or event-window validation"
    )
    assert all("Waitaki" not in item.title for item in result.evidence)
