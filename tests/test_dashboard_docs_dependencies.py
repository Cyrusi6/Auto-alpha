from pathlib import Path


DOC_TARGETS = [
    Path("dashboard/config.py"),
    Path("dashboard/data_service.py"),
    Path("dashboard/visualizer.py"),
    Path("dashboard/app.py"),
    Path("README.md"),
    Path("CATREADME.md"),
    Path("pyproject.toml"),
    Path("requirements.txt"),
]

OLD_TERMS = ["crypto", "solana", "meme"]
REMOVED_DEPENDENCIES = [
    "solana",
    "solders",
    "base58",
    "asyncpg",
    "psycopg2-binary",
    "sqlalchemy",
    "aiohttp",
]


def test_dashboard_docs_and_dependency_files_do_not_use_old_terms():
    for path in DOC_TARGETS:
        payload = path.read_text(encoding="utf-8").lower()
        for term in OLD_TERMS:
            assert term not in payload, f"{term} found in {path}"


def test_removed_dependencies_are_absent_from_project_files():
    payload = (Path("pyproject.toml").read_text(encoding="utf-8") + "\n" + Path("requirements.txt").read_text(encoding="utf-8")).lower()

    for dependency in REMOVED_DEPENDENCIES:
        assert dependency not in payload
