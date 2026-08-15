from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from auto_alpha.platform.artifacts import storage
from auto_alpha.platform.artifacts.storage import (
    canonical_hash,
    publish_prepared_generation,
    validate_generation,
)


SCHEMA = "prepared_generation_test_v1"
MANIFEST = "prepared_manifest.json"


def test_prepared_generation_is_frozen_and_validated_before_pointer_publish(
    tmp_path: Path,
) -> None:
    prepared = _prepared_generation(tmp_path / "prepared", payload="one")
    observed_modes: list[dict[str, int]] = []

    def validator(path: Path) -> dict[str, object]:
        root = path.parent
        observed_modes.append(
            {
                item.relative_to(root).as_posix(): item.stat().st_mode & 0o777
                for item in (root, *sorted(root.rglob("*")))
            }
        )
        return validate_generation(path, schema=SCHEMA, manifest_name=MANIFEST)

    result = publish_prepared_generation(
        tmp_path / "published",
        prepared_directory=prepared,
        manifest_name=MANIFEST,
        validator=validator,
        pointer_schema="prepared_pointer_v1",
        pointer_fields={"mode": "test"},
    )

    assert result["cache_hit"] is False
    assert observed_modes
    assert all(not mode & 0o222 for mode in observed_modes[0].values())
    target = Path(result["manifest_path"]).parent
    assert not target.stat().st_mode & 0o222
    pointer = json.loads((tmp_path / "published" / "current.json").read_text())
    assert pointer == {
        "schema_version": "prepared_pointer_v1",
        "generation_id": result["generation_id"],
        "content_hash": result["content_hash"],
        "manifest": f"generations/{result['generation_id']}/{MANIFEST}",
        "mode": "test",
    }


def test_validator_failure_publishes_neither_generation_nor_pointer(
    tmp_path: Path,
) -> None:
    prepared = _prepared_generation(tmp_path / "prepared", payload="invalid")
    output = tmp_path / "published"

    def rejected(_path: Path) -> dict[str, object]:
        raise ValueError("validator rejected prepared generation")

    with pytest.raises(ValueError, match="validator rejected"):
        publish_prepared_generation(
            output,
            prepared_directory=prepared,
            manifest_name=MANIFEST,
            validator=rejected,
            pointer_schema="prepared_pointer_v1",
        )

    assert prepared.is_dir()
    assert not prepared.stat().st_mode & 0o222
    assert not (output / "current.json").exists()
    assert not (output / "generations").exists()


@pytest.mark.parametrize("symlink_position", ("leaf", "ancestor"))
def test_prepared_path_symlink_is_rejected_without_moving_its_target(
    tmp_path: Path,
    symlink_position: str,
) -> None:
    real_parent = tmp_path / "real_parent"
    real = _prepared_generation(real_parent, payload="must-stay-put")
    real_target = real.resolve()
    closure = _file_closure(real)
    alias_parent = tmp_path / "alias_parent"
    if symlink_position == "leaf":
        alias_parent.mkdir()
        prepared_alias = alias_parent / real.name
        prepared_alias.symlink_to(real, target_is_directory=True)
        lexical_symlink = prepared_alias
    else:
        alias_parent.symlink_to(real_parent, target_is_directory=True)
        prepared_alias = alias_parent / real.name
        lexical_symlink = alias_parent
    output = tmp_path / "published"

    with pytest.raises(
        ValueError,
        match="prepared_generation_directory_symlink_forbidden",
    ):
        publish_prepared_generation(
            output,
            prepared_directory=prepared_alias,
            manifest_name=MANIFEST,
            validator=_validator,
            pointer_schema="prepared_pointer_v1",
        )

    assert lexical_symlink.is_symlink()
    assert prepared_alias.resolve() == real_target
    assert real.is_dir()
    assert _file_closure(real) == closure
    assert not output.exists()


def test_generation_namespace_symlink_is_rejected_without_external_write(
    tmp_path: Path,
) -> None:
    output = tmp_path / "published"
    output.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (output / "generations").symlink_to(external, target_is_directory=True)
    prepared = _prepared_generation(tmp_path / "prepared", payload="must-stay-local")

    with pytest.raises(ValueError, match="generation_namespace_symlink_forbidden"):
        publish_prepared_generation(
            output,
            prepared_directory=prepared,
            manifest_name=MANIFEST,
            validator=_validator,
            pointer_schema="prepared_pointer_v1",
        )

    assert not tuple(external.iterdir())
    assert not (output / "current.json").exists()


def test_published_target_cannot_be_reused_as_its_own_prepared_directory(
    tmp_path: Path,
) -> None:
    output = tmp_path / "published"
    prepared = _prepared_generation(tmp_path / "prepared", payload="stable")
    first = publish_prepared_generation(
        output,
        prepared_directory=prepared,
        manifest_name=MANIFEST,
        validator=_validator,
        pointer_schema="prepared_pointer_v1",
    )
    target = Path(first["manifest_path"]).parent
    closure = _file_closure(target)

    with pytest.raises(ValueError, match="prepared_generation_output_overlap"):
        publish_prepared_generation(
            output,
            prepared_directory=target,
            manifest_name=MANIFEST,
            validator=_validator,
            pointer_schema="prepared_pointer_v1",
        )

    assert target.is_dir()
    assert _file_closure(target) == closure


def test_same_identity_concurrent_publish_is_idempotent(tmp_path: Path) -> None:
    first = _prepared_generation(tmp_path / "prepared_a", payload="same")
    second = _prepared_generation(tmp_path / "prepared_b", payload="same")
    output = tmp_path / "published"

    def publish(prepared: Path) -> dict[str, object]:
        return publish_prepared_generation(
            output,
            prepared_directory=prepared,
            manifest_name=MANIFEST,
            validator=_validator,
            pointer_schema="prepared_pointer_v1",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, (first, second)))

    assert {result["cache_hit"] for result in results} == {False, True}
    assert len({result["generation_id"] for result in results}) == 1
    generation_id = str(results[0]["generation_id"])
    assert [path.name for path in (output / "generations").iterdir()] == [generation_id]
    assert _file_closure(output / "generations" / generation_id) == _expected_closure(
        payload="same"
    )


def test_crash_after_generation_publish_before_pointer_is_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _prepared_generation(tmp_path / "prepared_a", payload="recover")
    output = tmp_path / "published"
    real_atomic_json = storage.atomic_json

    def crash_before_pointer(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated pointer crash")

    monkeypatch.setattr(storage, "atomic_json", crash_before_pointer)
    with pytest.raises(RuntimeError, match="simulated pointer crash"):
        publish_prepared_generation(
            output,
            prepared_directory=first,
            manifest_name=MANIFEST,
            validator=_validator,
            pointer_schema="prepared_pointer_v1",
        )
    assert not (output / "current.json").exists()
    published = tuple((output / "generations").iterdir())
    assert len(published) == 1
    assert not published[0].stat().st_mode & 0o222

    monkeypatch.setattr(storage, "atomic_json", real_atomic_json)
    retry = _prepared_generation(tmp_path / "prepared_b", payload="recover")
    recovered = publish_prepared_generation(
        output,
        prepared_directory=retry,
        manifest_name=MANIFEST,
        validator=_validator,
        pointer_schema="prepared_pointer_v1",
    )

    assert recovered["cache_hit"] is True
    assert (output / "current.json").is_file()
    assert Path(recovered["manifest_path"]).parent == published[0]


def test_crash_immediately_after_rename_recovers_writable_root_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _prepared_generation(tmp_path / "prepared_a", payload="rename-crash")
    output = tmp_path / "published"
    real_rename = storage.os.rename

    def rename_then_crash(source: Path, target: Path) -> None:
        real_rename(source, target)
        raise RuntimeError("simulated crash after rename")

    monkeypatch.setattr(storage.os, "rename", rename_then_crash)
    with pytest.raises(RuntimeError, match="simulated crash after rename"):
        publish_prepared_generation(
            output,
            prepared_directory=first,
            manifest_name=MANIFEST,
            validator=_validator,
            pointer_schema="prepared_pointer_v1",
        )
    assert not (output / "current.json").exists()
    target = next((output / "generations").iterdir())
    assert target.stat().st_mode & 0o200

    monkeypatch.setattr(storage.os, "rename", real_rename)
    retry = _prepared_generation(tmp_path / "prepared_b", payload="rename-crash")
    recovered = publish_prepared_generation(
        output,
        prepared_directory=retry,
        manifest_name=MANIFEST,
        validator=_validator,
        pointer_schema="prepared_pointer_v1",
    )

    assert recovered["cache_hit"] is True
    assert not target.stat().st_mode & 0o222
    assert (output / "current.json").is_file()


@pytest.mark.parametrize("collision", ("changed_content", "extra_file"))
def test_existing_identity_requires_exact_file_closure_and_content(
    tmp_path: Path,
    collision: str,
) -> None:
    output = tmp_path / "published"
    first = _prepared_generation(tmp_path / "prepared_a", payload="original")
    publish_prepared_generation(
        output,
        prepared_directory=first,
        manifest_name=MANIFEST,
        validator=_validator,
        pointer_schema="prepared_pointer_v1",
    )
    colliding = _prepared_generation(tmp_path / "prepared_b", payload="original")
    if collision == "changed_content":
        (colliding / "nested" / "payload.bin").write_bytes(b"changed")
    else:
        (colliding / "unregistered.bin").write_bytes(b"extra")

    with pytest.raises(ValueError, match="immutable_generation_collision"):
        publish_prepared_generation(
            output,
            prepared_directory=colliding,
            manifest_name=MANIFEST,
            validator=_validator,
            pointer_schema="prepared_pointer_v1",
        )


def _prepared_generation(parent: Path, *, payload: str) -> Path:
    semantic = {"schema_version": SCHEMA, "payload_name": "nested/payload.bin"}
    content_hash = canonical_hash(semantic)
    generation_id = f"prepared_{content_hash[:24]}"
    root = parent / generation_id
    (root / "nested").mkdir(parents=True)
    (root / "nested" / "payload.bin").write_bytes(payload.encode())
    manifest = semantic | {
        "content_hash": content_hash,
        "generation_id": generation_id,
    }
    (root / MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root


def _validator(path: Path) -> dict[str, object]:
    return validate_generation(path, schema=SCHEMA, manifest_name=MANIFEST)


def _file_closure(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _expected_closure(*, payload: str) -> dict[str, bytes]:
    semantic = {"schema_version": SCHEMA, "payload_name": "nested/payload.bin"}
    content_hash = canonical_hash(semantic)
    generation_id = f"prepared_{content_hash[:24]}"
    manifest = semantic | {
        "content_hash": content_hash,
        "generation_id": generation_id,
    }
    return {
        MANIFEST: (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        "nested/payload.bin": payload.encode(),
    }
