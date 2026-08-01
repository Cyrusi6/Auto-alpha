from pathlib import Path

from dev_tools.repository_layout import LEGACY_PACKAGE_MAP, audit_repository_layout


def test_task_numbered_packages_are_not_architectural_roots():
    audit = audit_repository_layout(Path("."))

    assert audit.status == "passed"
    assert audit.legacy_directory_count == 0
    assert audit.legacy_import_count == 0
    assert audit.legacy_packaging_entry_count == 0
    assert audit.top_level_package_count == 1
    assert audit.source_package_count > 0


def test_canonical_packages_exist_for_every_removed_task_package():
    for canonical in LEGACY_PACKAGE_MAP.values():
        path = Path("src", *canonical.split("."))
        assert (path / "__init__.py").is_file(), canonical
