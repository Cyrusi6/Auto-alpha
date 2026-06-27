import json

from data_pipeline.ashare.state import PipelineSyncState, load_pipeline_state, save_pipeline_state


def test_load_pipeline_state_missing_file_returns_empty(tmp_path):
    state = load_pipeline_state(tmp_path / "missing.json")

    assert state.datasets == {}
    assert state.updated_at is None


def test_save_and_load_pipeline_state_roundtrip(tmp_path):
    path = tmp_path / "pipeline_state.json"
    state = PipelineSyncState()
    state.update_dataset(
        dataset="daily_bars",
        records=6,
        start_date="20240101",
        end_date="20240103",
        synced_at="2026-06-27T00:00:00+00:00",
    )

    save_pipeline_state(state, path)
    loaded = load_pipeline_state(path)

    assert loaded.datasets["daily_bars"].records == 6
    assert loaded.datasets["daily_bars"].start_date == "20240101"
    assert loaded.datasets["daily_bars"].end_date == "20240103"


def test_pipeline_state_does_not_store_token_fields(tmp_path):
    path = tmp_path / "pipeline_state.json"
    state = PipelineSyncState()
    state.update_dataset("securities", 3, "20240101", None)

    save_pipeline_state(state, path)
    raw = json.dumps(json.loads(path.read_text(encoding="utf-8"))).lower()

    assert "token" not in raw
    assert "secret" not in raw
