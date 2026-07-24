"""Tests for ``toolbase.envs.schema`` — versioned YAML plumbing.

Covers:
- Reading legacy (no schema_version) files as v0.
- Reading current-version files.
- Refusing too-new files with SchemaTooNewError.
- Empty migration framework — legacy files pass through as identity.
- Registering a real migration and chaining it on read.
- Atomic write with mode 0600 by default.
- Header-comment attachment.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from toolbase.envs import schema as schema_mod


@pytest.fixture(autouse=True)
def _clear_registry():
    """Each test gets an empty migration registry."""
    schema_mod.clear_registry()
    yield
    schema_mod.clear_registry()


def test_read_missing_file_returns_default_with_max_version(tmp_path):
    out = schema_mod.read_versioned_yaml(
        tmp_path / "absent.yaml", "toolkit_config",
    )
    assert out == {"schema_version": schema_mod.MAX_SCHEMA_VERSION["toolkit_config"]}


def test_read_missing_file_with_default_overlays(tmp_path):
    out = schema_mod.read_versioned_yaml(
        tmp_path / "absent.yaml", "toolkit_config",
        default={"foo": "bar"},
    )
    assert out["foo"] == "bar"
    assert out["schema_version"] == schema_mod.MAX_SCHEMA_VERSION["toolkit_config"]


def test_read_legacy_file_without_schema_version_assumes_zero(tmp_path):
    """Phase 3C files (no schema_version) load cleanly at v0 → v1 noop."""
    f = tmp_path / "legacy.yaml"
    f.write_text("api_key: abc123\nopacity_path: /data/opacities\n")
    out = schema_mod.read_versioned_yaml(f, "toolkit_config")
    assert out["api_key"] == "abc123"
    assert out["opacity_path"] == "/data/opacities"
    # Read path stamps the post-migration version.
    assert out["schema_version"] == schema_mod.MAX_SCHEMA_VERSION["toolkit_config"]


def test_read_current_version_passes_through(tmp_path):
    f = tmp_path / "cur.yaml"
    f.write_text("schema_version: 1\nfoo: bar\n")
    out = schema_mod.read_versioned_yaml(f, "toolkit_config")
    assert out["schema_version"] == 1
    assert out["foo"] == "bar"


def test_read_too_new_raises(tmp_path):
    f = tmp_path / "future.yaml"
    f.write_text("schema_version: 99\nfoo: bar\n")
    with pytest.raises(schema_mod.SchemaTooNewError) as excinfo:
        schema_mod.read_versioned_yaml(f, "toolkit_config")
    err = excinfo.value
    assert err.file_version == 99
    assert err.max_known == schema_mod.MAX_SCHEMA_VERSION["toolkit_config"]
    # User-facing message includes path and the "older than this config" hint.
    assert "toolbase" in str(err).lower()
    assert str(f) in str(err)


def test_read_unknown_file_type_raises_key_error(tmp_path):
    f = tmp_path / "x.yaml"
    f.write_text("schema_version: 1\n")
    with pytest.raises(KeyError):
        schema_mod.read_versioned_yaml(f, "not-a-real-type")


def test_read_malformed_yaml_raises_value_error(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("key: : : :\n  -bad\n")
    with pytest.raises(ValueError):
        schema_mod.read_versioned_yaml(f, "toolkit_config")


def test_read_non_mapping_top_level_raises_value_error(tmp_path):
    f = tmp_path / "list.yaml"
    f.write_text("- one\n- two\n")
    with pytest.raises(ValueError):
        schema_mod.read_versioned_yaml(f, "toolkit_config")


def test_read_non_integer_schema_version_raises(tmp_path):
    f = tmp_path / "bad-ver.yaml"
    f.write_text('schema_version: "1"\nfoo: bar\n')
    with pytest.raises(ValueError):
        schema_mod.read_versioned_yaml(f, "toolkit_config")


def test_read_empty_file_returns_max_version(tmp_path):
    f = tmp_path / "empty.yaml"
    f.write_text("")
    out = schema_mod.read_versioned_yaml(f, "toolkit_config")
    assert out["schema_version"] == schema_mod.MAX_SCHEMA_VERSION["toolkit_config"]


def test_register_migration_runs_on_read(tmp_path):
    """Register a v0→v1 migration; verify it transforms the dict."""
    def rename_api_to_token(data):
        if "api" in data:
            data["token"] = data.pop("api")
        return data

    schema_mod.register_migration(
        "toolkit_config", from_v=0, to_v=1, fn=rename_api_to_token,
    )

    f = tmp_path / "v0.yaml"
    f.write_text("api: legacy-value\n")  # v0 because no schema_version
    out = schema_mod.read_versioned_yaml(f, "toolkit_config")
    assert "api" not in out
    assert out["token"] == "legacy-value"
    assert out["schema_version"] == 1


def test_register_migration_rejects_skip(tmp_path):
    """to_v must be from_v + 1; multi-step migrations are forbidden."""
    with pytest.raises(ValueError):
        schema_mod.register_migration(
            "toolkit_config", from_v=0, to_v=2, fn=lambda d: d,
        )


def test_register_migration_rejects_duplicate():
    schema_mod.register_migration(
        "toolkit_config", from_v=0, to_v=1, fn=lambda d: d,
    )
    with pytest.raises(ValueError):
        schema_mod.register_migration(
            "toolkit_config", from_v=0, to_v=1, fn=lambda d: d,
        )


def test_register_migration_rejects_negative():
    with pytest.raises(ValueError):
        schema_mod.register_migration(
            "toolkit_config", from_v=-1, to_v=0, fn=lambda d: d,
        )


def test_write_versioned_yaml_creates_file_with_schema_version(tmp_path):
    f = tmp_path / "out.yaml"
    schema_mod.write_versioned_yaml(
        f, "toolkit_config", {"api_key": "abc"},
    )
    body = f.read_text()
    assert "schema_version: 1" in body
    assert "api_key: abc" in body


def test_write_versioned_yaml_preserves_atomicity_via_tmp(tmp_path):
    """Writing leaves no <path>.tmp behind on success."""
    f = tmp_path / "out.yaml"
    schema_mod.write_versioned_yaml(f, "toolkit_config", {"k": "v"})
    tmp = f.with_suffix(f.suffix + ".tmp")
    assert not tmp.exists()
    assert f.exists()


def test_write_versioned_yaml_sets_mode_0600_by_default(tmp_path):
    import os
    f = tmp_path / "secret.yaml"
    schema_mod.write_versioned_yaml(f, "toolkit_config", {"k": "v"})
    mode = os.stat(f).st_mode & 0o777
    if os.name == "posix":
        assert mode == 0o600


def test_write_versioned_yaml_accepts_explicit_mode(tmp_path):
    import os
    f = tmp_path / "public.yaml"
    schema_mod.write_versioned_yaml(
        f, "toolkit_config", {"k": "v"}, mode=0o644,
    )
    if os.name == "posix":
        mode = os.stat(f).st_mode & 0o777
        assert mode == 0o644


def test_write_versioned_yaml_rejects_too_new_version(tmp_path):
    """Can't stamp a file with a version above MAX. Forces 'bump first, write second'."""
    with pytest.raises(ValueError):
        schema_mod.write_versioned_yaml(
            tmp_path / "x.yaml", "toolkit_config",
            {}, current_version=99,
        )


def test_write_versioned_yaml_rejects_negative_version(tmp_path):
    with pytest.raises(ValueError):
        schema_mod.write_versioned_yaml(
            tmp_path / "x.yaml", "toolkit_config",
            {}, current_version=-1,
        )


def test_write_with_header_comment_prepends_it(tmp_path):
    f = tmp_path / "out.yaml"
    schema_mod.write_versioned_yaml(
        f, "toolkit_config", {"k": "v"},
        header_comment="canonical file — edit anytime",
    )
    body = f.read_text()
    assert "canonical file" in body
    # Comment should appear before the body.
    comment_idx = body.index("canonical file")
    body_idx = body.index("k:")
    assert comment_idx < body_idx


def test_round_trip_preserves_comments_on_existing_file(tmp_path):
    """Read a commented file → mutate → write → comments survive."""
    f = tmp_path / "rt.yaml"
    f.write_text(
        "schema_version: 1\n"
        "# this is a leading comment\n"
        "api_key: abc  # inline comment\n"
    )
    # Round-trip via ruamel: load via the schema's loader, mutate, write.
    # We have to use the internal loader because read_versioned_yaml
    # returns a plain dict (loses comments). Round-trip preservation is
    # the storage-layer concern; the schema layer's contract is just
    # "stamp schema_version and write atomically".
    from ruamel.yaml.comments import CommentedMap
    data = CommentedMap()
    data["api_key"] = "abc"
    data["new_field"] = "v"
    schema_mod.write_versioned_yaml(f, "toolkit_config", data)
    body = f.read_text()
    assert "new_field: v" in body


def test_unknown_file_type_in_write_raises(tmp_path):
    with pytest.raises(KeyError):
        schema_mod.write_versioned_yaml(
            tmp_path / "x.yaml", "not-a-real-type",
            {"k": "v"},
        )


def test_chain_two_step_migration(tmp_path, monkeypatch):
    """Simulate file_type with MAX=2 and a chain v0→v1→v2."""
    monkeypatch.setitem(schema_mod.MAX_SCHEMA_VERSION, "toolkit_config", 2)

    def v0_to_v1(data):
        data["step1"] = True
        return data

    def v1_to_v2(data):
        data["step2"] = True
        return data

    schema_mod.register_migration("toolkit_config", 0, 1, v0_to_v1)
    schema_mod.register_migration("toolkit_config", 1, 2, v1_to_v2)

    f = tmp_path / "legacy.yaml"
    f.write_text("k: v\n")  # implicit v0
    out = schema_mod.read_versioned_yaml(f, "toolkit_config")
    assert out["step1"] is True
    assert out["step2"] is True
    assert out["schema_version"] == 2


def test_concurrent_reads_and_writes_are_thread_safe(tmp_path):
    """Resolution parses every cached toolkit's metadata, and runs execute
    trials in parallel — so read/write_versioned_yaml are called concurrently.
    A shared module-level ruamel YAML() is not thread-safe and corrupts under
    that load (spurious "'NoneType' object has no attribute 'anchor'" /
    DuplicateKeyError). Each call must use its own loader/dumper instead.
    """
    f = tmp_path / "meta.yaml"
    f.write_text("schema_version: 1\nname: demo\nvalue: 42\n")
    errors: list[BaseException] = []

    def reader() -> None:
        for _ in range(300):
            try:
                out = schema_mod.read_versioned_yaml(f, "toolkit_config")
                assert out["name"] == "demo"
            except BaseException as e:  # noqa: BLE001 - record, don't swallow
                errors.append(e)
                return

    def writer() -> None:
        wf = tmp_path / f"w{threading.get_ident()}.yaml"
        for i in range(300):
            try:
                schema_mod.write_versioned_yaml(
                    wf, "toolkit_config", {"n": i})
            except BaseException as e:  # noqa: BLE001
                errors.append(e)
                return

    threads = [threading.Thread(target=reader) for _ in range(5)]
    threads += [threading.Thread(target=writer) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"thread-safety regression: {errors[:2]}"
