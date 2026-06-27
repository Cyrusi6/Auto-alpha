from research_suite.catalog import load_artifact_catalog, register_artifact, write_artifact_catalog
from research_suite.models import ArtifactCatalog


def test_artifact_catalog_register_write_load(tmp_path):
    catalog = ArtifactCatalog(suite_name="suite", created_at="2026-06-27T00:00:00Z")
    catalog = register_artifact(
        catalog,
        name="manifest",
        path=tmp_path / "data" / "manifest.json",
        kind="json",
        stage="data_sync",
        metadata={"records": 3},
    )

    json_path, md_path = write_artifact_catalog(catalog, tmp_path)
    loaded = load_artifact_catalog(json_path)

    assert json_path.exists()
    assert md_path.exists()
    assert loaded.suite_name == "suite"
    assert loaded.entries[0].metadata["records"] == 3
    assert "manifest" in md_path.read_text(encoding="utf-8")
