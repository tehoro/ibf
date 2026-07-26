from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from ibf.api.context_research import (
    BraveContextResearchProvider,
    ResearchLocation,
)


def _brave_payload(url: str, title: str) -> dict:
    return {
        "grounding": {
            "generic": [
                {
                    "url": url,
                    "title": title,
                    "snippets": ["Official evidence with a 50 mm rainfall threshold."],
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
    monkeypatch.setattr("ibf.api.context_research.get_local_now", lambda _timezone: now)
    provider = BraveContextResearchProvider("brave-key", cache_dir=tmp_path)
    queries: list[str] = []

    def fake_request(query, locations):
        queries.append(query)
        return _brave_payload(
            f"https://council.govt.nz/source-{len(queries)}",
            f"Source {len(queries)}",
        )

    monkeypatch.setattr(provider, "_request", fake_request)
    locations = [
        ResearchLocation(
            name="Otaki",
            latitude=-40.75,
            longitude=175.15,
            country_code="NZ",
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

    assert first.request_count == 2
    assert first.estimated_cost_cents == 1.0
    assert len(first.evidence) == 2
    assert second.request_count == 0
    assert len(queries) == 2
    assert all(len(query.split()) <= 50 for query in queries)
    assert {path.name.split("__")[-1] for path in first.evidence_paths} == {
        "dynamic.json",
        "static.json",
    }
    sidecar = json.loads(first.evidence_paths[0].read_text(encoding="utf-8"))
    assert sidecar["query"] in queries
    assert sidecar["evidence"][0]["url"].startswith("https://council.govt.nz/")
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
    )

    assert captured["headers"]["X-Subscription-Token"] == "brave-key"
    assert captured["headers"]["X-Loc-Country"] == "NZ"
    assert captured["body"]["country"] == "nz"
    assert float(captured["headers"]["X-Loc-Lat"]) == (-40.75 - 40.88) / 2
    assert captured["timeout"] == 30
