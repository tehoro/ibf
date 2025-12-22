import json

from ibf.llm import costs


def test_llm_costs_override(tmp_path, monkeypatch) -> None:
    override = {
        "gemini-3-flash-preview": {
            "input_per_million": 0.9,
            "cached_input_per_million": 0.4,
            "output_per_million": 3.3,
        }
    }
    path = tmp_path / "llm_costs.json"
    path.write_text(json.dumps(override), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    costs._load_external_costs.cache_clear()

    entry = costs.get_model_cost("gemini-3-flash-preview")
    assert entry is not None
    assert entry.input_per_million == 0.9
    assert entry.cached_input_per_million == 0.4
    assert entry.output_per_million == 3.3
