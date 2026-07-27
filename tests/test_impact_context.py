from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from ibf.api.context_research import (
    ContextResearchError,
    EvidenceItem,
    ResearchBatch,
    ResearchLocation,
    ResearchResult,
)
from ibf.api.impact import (
    _build_hosted_context_prompt,
    _cache_path,
    _extract_gemini_grounding_audit,
    _generate_context,
    _generate_context_brave,
    _generate_context_gemini_search,
    _generate_context_openai_web_search,
    _filter_invalid_upcoming_event_bullets,
    _repair_brave_synthesis_structure,
    _store_hosted_search_sidecar,
    _strip_private_source_markers,
    _validate_brave_synthesis,
    fetch_impact_context,
)
from ibf.config.models import ForecastConfig
from ibf.config.settings import Secrets
from ibf.llm.settings import LLMSettings


def _research_result(tmp_path: Path) -> ResearchResult:
    retrieved = datetime.now(timezone.utc).isoformat()
    evidence = EvidenceItem(
        bucket="dynamic",
        query="controlled query",
        url="https://council.govt.nz/flood-plan",
        title="District flood plan",
        hostname="council.govt.nz",
        published_date="2026-07-20",
        source_age=["2026-07-20"],
        retrieved_at=retrieved,
        passages=["The river road closes when water reaches the marked flood level."],
    )
    return ResearchResult(
        name="Otaki",
        context_type="location",
        batches=[
            ResearchBatch(
                bucket="dynamic",
                query="controlled query",
                retrieved_at=retrieved,
                local_date="2026-07-26",
                evidence=[evidence],
                request_count=1,
                cache_path=tmp_path / "dynamic.json",
            )
        ],
    )


def _valid_synthesis() -> str:
    return """### Existing Vulnerabilities
• River Road is vulnerable to flooding. [S1]

### Weather Impact Thresholds
• No relevant items found.

### Exposed Populations and Assets
• River Road is an exposed transport route. [S1]

### Upcoming Events
• No relevant items found."""


@pytest.mark.parametrize(
    ("choice", "provider"),
    [
        ("lms:local-context-model", "lmstudio"),
        ("or:google/gemini-3-flash-preview", "openrouter"),
        ("gpt-5-mini", "openai"),
    ],
)
def test_brave_evidence_can_be_synthesized_by_local_or_cloud_llm(
    tmp_path,
    monkeypatch,
    choice,
    provider,
) -> None:
    research = _research_result(tmp_path)
    fake_provider = SimpleNamespace(research=lambda *args, **kwargs: research)
    monkeypatch.setattr(
        "ibf.api.impact.BraveContextResearchProvider",
        lambda api_key: fake_provider,
    )
    resolved = LLMSettings(
        model="resolved-model",
        api_key="key",
        provider=provider,
        base_url="http://localhost:1234/v1" if provider == "lmstudio" else None,
    )
    seen = {}

    def fake_resolve(config, override):
        seen["override"] = override
        return resolved

    monkeypatch.setattr("ibf.api.impact.resolve_llm_settings", fake_resolve)
    monkeypatch.setattr("ibf.api.impact.generate_forecast_text", lambda *args, **kwargs: _valid_synthesis())
    monkeypatch.setattr("ibf.api.impact.consume_last_cost_cents", lambda: 0.25)
    monkeypatch.setattr("ibf.api.impact._store_brave_synthesis_sidecar", lambda *args, **kwargs: None)

    text, cost = _generate_context_brave(
        "location",
        "Otaki",
        4,
        "Pacific/Auckland",
        Secrets(BRAVE_SEARCH_API_KEY="brave-key"),
        context_llm=choice,
        extra_context=None,
        llm_config=ForecastConfig(context_provider="brave", context_llm=choice),
        representative_locations=(),
    )

    assert seen["override"] == choice
    assert "[S1]" not in text
    assert "River Road" in text
    assert cost == pytest.approx(0.75)  # 0.25c synthesis + one 0.5c Brave request.


def test_brave_retries_when_accepted_event_evidence_is_omitted(tmp_path, monkeypatch) -> None:
    retrieved = datetime.now(timezone.utc).isoformat()
    event_evidence = EvidenceItem(
        bucket="events",
        query="Halifax major events",
        url="https://natalday.org/events.php",
        title="Natal Day Festival Halifax-Dartmouth",
        hostname="natalday.org",
        published_date="2026-07-02",
        source_age=["2026-07-02"],
        retrieved_at=retrieved,
        passages=["The Buskers Festival runs from July 30 to August 3, 2026."],
    )
    research = ResearchResult(
        name="Halifax, Nova Scotia",
        context_type="location",
        batches=[
            ResearchBatch(
                bucket="events",
                query="Halifax major events",
                retrieved_at=retrieved,
                local_date="2026-07-26",
                evidence=[event_evidence],
                cache_path=tmp_path / "events.json",
            )
        ],
    )
    fake_provider = SimpleNamespace(research=lambda *args, **kwargs: research)
    monkeypatch.setattr(
        "ibf.api.impact.BraveContextResearchProvider", lambda api_key: fake_provider
    )
    monkeypatch.setattr(
        "ibf.api.impact.resolve_llm_settings",
        lambda *args, **kwargs: LLMSettings(
            model="gemini-3-flash-preview", api_key="key", provider="gemini"
        ),
    )
    monkeypatch.setattr(
        "ibf.api.impact.get_local_now",
        lambda _timezone: datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc),
    )
    drafts = iter(
        [
            """### Existing Vulnerabilities
• No relevant items found.
### Weather Impact Thresholds
• No relevant items found.
### Exposed Populations and Assets
• The Halifax waterfront hosts major public gatherings. [S1]""",
            """### Existing Vulnerabilities
• No relevant items found.
### Weather Impact Thresholds
• No relevant items found.
### Exposed Populations and Assets
• The Halifax waterfront hosts major public gatherings. [S1]
### Upcoming Events
• July 30-August 3, 2026: Halifax Buskers Festival. [S1]""",
        ]
    )
    call_count = 0

    def fake_generate(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return next(drafts)

    monkeypatch.setattr("ibf.api.impact.generate_forecast_text", fake_generate)
    monkeypatch.setattr("ibf.api.impact.consume_last_cost_cents", lambda: 0.0)
    monkeypatch.setattr(
        "ibf.api.impact._store_brave_synthesis_sidecar", lambda *args, **kwargs: None
    )

    text, _cost = _generate_context_brave(
        "location",
        "Halifax, Nova Scotia",
        4,
        "UTC",
        Secrets(BRAVE_SEARCH_API_KEY="brave-key"),
        context_llm="gemini-3-flash-preview",
        extra_context=None,
        llm_config=ForecastConfig(
            context_provider="brave", context_llm="gemini-3-flash-preview"
        ),
        representative_locations=(),
    )

    assert call_count == 2
    assert "Halifax Buskers Festival" in text


def test_brave_synthesis_validation_requires_citations_and_exact_event_dates() -> None:
    invalid = """### Existing Vulnerabilities
• Unsupported vulnerability.
### Weather Impact Thresholds
• 50 mm in 24 hours. [S2]
### Exposed Populations and Assets
• No relevant items found.
### Upcoming Events
• Major festival next week. [S1]"""

    errors = _validate_brave_synthesis(invalid, source_count=1)

    assert "uncited bullet in Existing Vulnerabilities" in errors
    assert "invalid source marker S2" in errors
    assert "upcoming event without an exact date" in errors


def test_brave_synthesis_validation_rejects_event_outside_window() -> None:
    text = _valid_synthesis().rsplit("• No relevant items found.", 1)[0] + (
        "• Major festival on 20 August 2026. [S1]"
    )

    errors = _validate_brave_synthesis(
        text,
        source_count=1,
        event_start=datetime(2026, 7, 26, tzinfo=timezone.utc).date(),
        event_end=datetime(2026, 8, 5, tzinfo=timezone.utc).date(),
    )

    assert "upcoming event outside the allowed date window" in errors


def test_invalid_event_bullets_are_dropped_without_losing_valid_context() -> None:
    raw = """### Existing Vulnerabilities
• Boulder has mapped flood hazards. [S5]
### Weather Impact Thresholds
• No relevant items found.
### Exposed Populations and Assets
• Boulder Creek is exposed. [S6]
### Upcoming Events
• July 26, 2026: Medicine Picnic. [S4]
• June 30-August 2, 2026: Shakespeare Festival. [S3]
• August 6-11, 2026: County Fair. [S3]
• Saturdays through November 21, 2026: Farmers Market. [S3]
• August 1, 2026: Unsupported uncited event.
• May 24 – 27, 2026: Creek Festival. [S5]"""

    filtered, notes = _filter_invalid_upcoming_event_bullets(
        raw,
        event_start=datetime(2026, 7, 26, tzinfo=timezone.utc).date(),
        event_end=datetime(2026, 8, 5, tzinfo=timezone.utc).date(),
        event_source_markers={"S2", "S3", "S4"},
    )

    assert "Medicine Picnic" in filtered
    assert "Shakespeare Festival" in filtered  # Its exact range overlaps the window.
    assert "County Fair" not in filtered
    assert "Farmers Market" not in filtered
    assert "Unsupported uncited event" not in filtered
    assert "Creek Festival" not in filtered
    assert len(notes) == 4
    assert _validate_brave_synthesis(
        filtered,
        source_count=6,
        event_start=datetime(2026, 7, 26, tzinfo=timezone.utc).date(),
        event_end=datetime(2026, 8, 5, tzinfo=timezone.utc).date(),
        event_source_markers={"S2", "S3", "S4"},
    ) == []


def test_brave_synthesis_repairs_missing_empty_section_wrapping_and_marker_lists() -> None:
    raw = """### Existing Vulnerabilities
* Drainage is constrained. [S1].
### Weather Impact Thresholds
* 30 mm in one hour caused road flooding. [S1, S2].
### Exposed Populations and Assets
* Major
road corridors are exposed. [S2].
### Historical Context
* This extra section must be omitted. [S3]
### Upcoming Events
* • No relevant items found."""

    repaired = _repair_brave_synthesis_structure(raw)

    assert "### Historical Context" not in repaired
    assert "• Major road corridors are exposed. [S2]." in repaired
    assert "30 mm in one hour caused road flooding. [S1] [S2]." in repaired
    assert repaired.endswith("### Upcoming Events\n• No relevant items found.")
    assert "• •" not in repaired
    assert _validate_brave_synthesis(repaired, source_count=3) == []


def test_private_source_markers_are_removed_from_public_context() -> None:
    assert _strip_private_source_markers("Flood risk. [S1] [S2]\nLocal note. [LOCAL]") == (
        "Flood risk.\nLocal note."
    )


def test_openai_web_search_failure_does_not_use_ungrounded_chat(monkeypatch) -> None:
    fake_client = SimpleNamespace(
        responses=SimpleNamespace(create=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("search down"))),
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: pytest.fail("chat fallback must not be called")
            )
        ),
    )
    monkeypatch.setattr("ibf.api.impact.OpenAI", lambda **kwargs: fake_client)

    text, cost = _generate_context_openai_web_search(
        "prompt",
        model_name="gpt-5-mini",
        api_key="key",
        name="Otaki",
    )

    assert text == ""
    assert cost == 0.0


def test_hosted_search_regional_prompt_includes_representative_places(monkeypatch) -> None:
    captured = {}

    def fake_search(prompt, **kwargs):
        captured["prompt"] = prompt
        return "### Existing Vulnerabilities\n• No relevant items found.", 0.0

    monkeypatch.setattr("ibf.api.impact._generate_context_gemini_search", fake_search)

    _generate_context(
        "regional",
        "Kapiti Coast",
        4,
        "Pacific/Auckland",
        Secrets(GEMINI_API_KEY="key"),
        context_llm="gemini-3-flash-preview",
        representative_locations=(
            ResearchLocation("Otaki", -40.75, 175.15, "NZ"),
            ResearchLocation("Waikanae", -40.88, 175.07, "NZ"),
        ),
    )

    assert "Otaki, Waikanae" in captured["prompt"]
    assert "not only for the area name" in captured["prompt"]


def test_hosted_prompt_uses_canonical_identity_and_evidence_classes() -> None:
    prompt = _build_hosted_context_prompt(
        "location",
        "Otaki, New Zealand",
        4,
        local_now=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
        extra_context=None,
        representative_locations=(
            ResearchLocation(
                "Ōtaki, New Zealand",
                -40.7583,
                175.15,
                "NZ",
                "New Zealand",
                "Wellington Region",
                "Kapiti Coast District",
            ),
        ),
    )

    assert "Ōtaki, New Zealand, Kapiti Coast District, Wellington Region, NZ" in prompt
    assert "reject a similarly named place" in prompt
    assert "Official criterion" in prompt
    assert "national meteorological" in prompt
    assert "Design/hazard reference" in prompt
    assert "Do NOT search for or summarise weather forecasts" in prompt
    assert "official municipal, tourism, venue, sports and organiser calendars" in prompt
    assert "25mm" not in prompt  # Do not seed plausible numbers into the model.


def test_cached_context_prunes_past_events_without_new_research(monkeypatch, tmp_path) -> None:
    cached = """### Existing Vulnerabilities
• Enduring mapped flood exposure.
### Weather Impact Thresholds
• No relevant items found.
### Exposed Populations and Assets
• River Road is exposed.
### Upcoming Events
• July 26, 2026: Past festival.
• July 30-August 3, 2026: Current festival."""
    monkeypatch.setattr(
        "ibf.api.impact._load_recent_cache",
        lambda *args, **kwargs: (cached, tmp_path / "context.json"),
    )
    monkeypatch.setattr("ibf.api.impact.cleanup_impact_cache", lambda: None)
    monkeypatch.setattr(
        "ibf.api.impact.get_local_now",
        lambda _timezone: datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "ibf.api.impact._generate_context",
        lambda *args, **kwargs: pytest.fail("cached context must not trigger research"),
    )

    result = fetch_impact_context(
        "Halifax, Nova Scotia",
        timezone_name="UTC",
        context_provider="llm-search",
        context_llm="gemini-3-flash-preview",
    )

    assert result.from_cache is True
    assert "Past festival" not in result.content
    assert "Current festival" in result.content


def test_gemini_grounding_metadata_is_extracted_and_stored_privately(
    monkeypatch, tmp_path
) -> None:
    metadata = SimpleNamespace(
        web_search_queries=["official Otaki rainfall warning criteria"],
        grounding_chunks=[
            SimpleNamespace(
                web=SimpleNamespace(
                    uri="https://www.metservice.com/warnings/home",
                    title="MetService warning criteria",
                    domain=None,
                )
            )
        ],
        grounding_supports=[
            SimpleNamespace(
                segment=SimpleNamespace(
                    text="MetService publishes official heavy-rain criteria.",
                    start_index=0,
                    end_index=52,
                ),
                grounding_chunk_indices=[0],
                confidence_scores=[0.98],
            )
        ],
    )
    response = SimpleNamespace(
        response_id="response-1",
        model_version="gemini-test",
        candidates=[
            SimpleNamespace(
                finish_reason=SimpleNamespace(value="STOP"),
                grounding_metadata=metadata,
                citation_metadata=None,
            )
        ],
    )

    audit = _extract_gemini_grounding_audit(
        response,
        call_number=1,
        request_text="research prompt",
        response_text="grounded answer",
    )

    assert audit["web_search_queries"] == ["official Otaki rainfall warning criteria"]
    assert audit["sources"][0]["title"] == "MetService warning criteria"
    assert audit["supports"][0]["chunk_indices"] == [0]
    monkeypatch.setattr("ibf.api.impact.CACHE_DIR", tmp_path)
    path = _store_hosted_search_sidecar(
        name="Otaki, New Zealand",
        context_type="location",
        model_name="gemini-test",
        prompt="research prompt",
        final_text="grounded answer",
        call_audits=[audit],
    )

    assert path is not None
    sidecar = json.loads(path.read_text(encoding="utf-8"))
    assert sidecar["provider"] == "gemini-google-search"
    assert sidecar["calls"][0]["sources"][0]["url"].startswith("https://")


def test_gemini_context_fails_closed_without_grounding_metadata(monkeypatch, tmp_path) -> None:
    from google import genai

    response = SimpleNamespace(
        text=_valid_synthesis(),
        usage_metadata=None,
        candidates=[],
        response_id="response-ungrounded",
        model_version="gemini-test",
    )
    fake_client = SimpleNamespace(
        models=SimpleNamespace(generate_content=lambda **kwargs: response)
    )
    monkeypatch.setattr(genai, "Client", lambda **kwargs: fake_client)
    monkeypatch.setattr("ibf.api.impact.CACHE_DIR", tmp_path)

    text, cost = _generate_context_gemini_search(
        "research prompt",
        model_name="gemini-test",
        api_key="key",
        name="Otaki, New Zealand",
    )

    assert text == ""
    assert cost == 0.0
    assert list((tmp_path / "evidence").glob("hosted_*.json"))


def test_modern_impact_cache_key_includes_forecast_days(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("ibf.api.impact.CACHE_DIR", tmp_path)
    date = datetime(2026, 7, 26, tzinfo=timezone.utc)

    four_days = _cache_path("location", "Otaki", 4, "UTC", date_override=date)
    seven_days = _cache_path("location", "Otaki", 7, "UTC", date_override=date)

    assert four_days != seven_days
    assert "_4" in four_days.name
    assert "_7" in seven_days.name


def test_new_default_context_model_does_not_share_legacy_unsuffixed_cache_key(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("ibf.api.impact.CACHE_DIR", tmp_path)
    date = datetime(2026, 7, 26, tzinfo=timezone.utc)

    current = _cache_path("location", "Otaki", 4, "UTC", date_override=date)
    legacy = _cache_path(
        "location",
        "Otaki",
        4,
        "UTC",
        date_override=date,
        context_llm="gemini-3-flash-preview",
    )

    assert "__gemini_35_flash_lite" in current.name
    assert "__gemini" not in legacy.name
    assert current != legacy


def test_brave_failure_can_fall_back_to_explicit_hosted_search_model(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "ibf.api.impact._load_recent_cache",
        lambda *args, **kwargs: (None, tmp_path / "context.json"),
    )
    monkeypatch.setattr(
        "ibf.api.impact._generate_context_brave",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ContextResearchError("Brave down", cost_cents=0.75)
        ),
    )
    seen = {}

    def fake_hosted(*args, **kwargs):
        seen["model"] = kwargs["context_llm"]
        return "### Existing Vulnerabilities\n• Hosted fallback.", 1.5

    monkeypatch.setattr("ibf.api.impact._generate_context", fake_hosted)
    monkeypatch.setattr("ibf.api.impact.store_impact_context", lambda *args, **kwargs: None)

    result = fetch_impact_context(
        "Otaki",
        context_provider="brave",
        context_llm="lms:local-context-model",
        context_fallback_llm="gemini-3-flash-preview",
        secrets=Secrets(BRAVE_SEARCH_API_KEY="brave-key"),
    )

    assert seen["model"] == "gemini-3-flash-preview"
    assert result.content.endswith("Hosted fallback.")
    assert result.cost_cents == 2.25
