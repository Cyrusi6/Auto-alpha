from pathlib import Path
from importlib.util import find_spec

from auto_alpha.cli import COMMANDS, normalize_python_module_command
from dev_tools.repository_layout import (
    DOMAIN_SUBSYSTEMS,
    NESTED_SUBSYSTEMS,
    REMOVED_INTERNAL_PACKAGES,
    audit_repository_layout,
)


def test_repository_has_one_public_package_and_six_domains():
    audit = audit_repository_layout(Path("."))

    assert audit.status == "passed"
    assert audit.legacy_directory_count == 0
    assert audit.legacy_import_count == 0
    assert audit.legacy_packaging_entry_count == 0
    assert audit.top_level_package_count == 1
    assert audit.source_package_count == 1
    assert audit.domain_count == 6
    assert audit.subsystem_count == 25
    assert audit.domain_issues == ()


def test_every_domain_subsystem_is_a_package():
    for domain, subsystems in DOMAIN_SUBSYSTEMS.items():
        for subsystem in subsystems:
            path = Path("src/auto_alpha") / domain / subsystem
            assert (path / "__init__.py").is_file(), path
    for relative, subsystems in NESTED_SUBSYSTEMS.items():
        root = Path("src/auto_alpha") / relative
        actual = {
            path.name
            for path in root.iterdir()
            if path.is_dir() and (path / "__init__.py").is_file()
        }
        assert actual == set(subsystems)


def test_task055_generations_are_not_public_packages_or_tests():
    public = Path("src/auto_alpha/platform/network_authority")
    assert public.is_dir()
    assert not list((public / "_internal").rglob("*.py"))
    for relative in REMOVED_INTERNAL_PACKAGES:
        assert not list((Path("src/auto_alpha") / relative).rglob("*.py"))
    assert not list(Path("src").rglob("live_readiness"))
    assert not list(Path("tests").rglob("test_task_055*.py"))


def test_unified_cli_resolves_every_registered_command():
    assert len(COMMANDS) >= 70
    assert all(find_spec(spec.module) is not None for spec in COMMANDS.values())
    normalized = normalize_python_module_command(
        [
            "python",
            "-m",
            "auto_alpha.data.quality.source_validation.run_smoke",
            "--help",
        ]
    )
    assert normalized == [
        "python",
        "-m",
        "auto_alpha",
        "data",
        "validate",
        "--help",
    ]
