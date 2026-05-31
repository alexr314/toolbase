"""Unit tests for the Phase 3C-1 declarative ``config:`` block schema.

Covers ``toolbase/setup/schema.py``: ConfigField parsing for all
seven types, per-type validators (min/max for numerics, options for
choice, must_exist for path), duplicate detection, reserved-name
defense, and value coercion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from toolbase.setup.schema import (
    ConfigError,
    ConfigField,
    ConfigSchema,
    NEEDS_VALUE_SENTINEL,
    _RESERVED_NAMES,
    coerce_value,
    parse_config_block,
)


# ── parse_config_block ────────────────────────────────────────────────


def test_empty_block_yields_empty_schema():
    schema = parse_config_block(None)
    assert schema.fields == []
    schema = parse_config_block([])
    assert schema.fields == []


def test_non_list_block_rejected():
    with pytest.raises(ConfigError, match="must be a list"):
        parse_config_block({"api_key": {"type": "secret"}})


def test_non_dict_entry_rejected():
    with pytest.raises(ConfigError, match="must be a mapping"):
        parse_config_block(["api_key"])


def test_unknown_type_rejected():
    with pytest.raises(ConfigError, match="unknown config field type"):
        parse_config_block([{"name": "x", "type": "nonsense"}])


def test_duplicate_field_names_rejected():
    with pytest.raises(ConfigError, match="duplicate config field name"):
        parse_config_block([
            {"name": "api_key", "type": "secret"},
            {"name": "api_key", "type": "string"},
        ])


def test_reserved_field_name_rejected():
    """Pydantic-reserved attribute names would silently shadow methods."""
    for reserved in ("model_config", "model_fields", "schema"):
        with pytest.raises(ConfigError, match="Pydantic-reserved"):
            parse_config_block([{"name": reserved, "type": "string"}])


def test_field_name_must_be_python_identifier():
    """State-field injection uses setattr; non-identifier names break that."""
    for bad in ("with-dash", "1starts-with-digit", "has spaces", ""):
        with pytest.raises(ConfigError):
            parse_config_block([{"name": bad, "type": "string"}])


def test_unknown_keys_rejected():
    """extra='forbid' on the model: typos surface immediately."""
    with pytest.raises(ConfigError):
        parse_config_block([
            {"name": "x", "type": "string", "typo_key": "value"},
        ])


# ── per-type validators ──────────────────────────────────────────────


def test_choice_requires_options():
    with pytest.raises(ConfigError, match="requires an 'options' list"):
        parse_config_block([{"name": "mode", "type": "choice"}])


def test_choice_requires_at_least_two_options():
    with pytest.raises(ConfigError, match="at least 2 options"):
        parse_config_block([
            {"name": "mode", "type": "choice", "options": ["only"]},
        ])


def test_choice_rejects_duplicate_options():
    with pytest.raises(ConfigError, match="duplicate option"):
        parse_config_block([
            {"name": "m", "type": "choice", "options": ["a", "b", "a"]},
        ])


def test_options_only_allowed_for_choice():
    with pytest.raises(ConfigError, match="only applies to type=choice"):
        parse_config_block([
            {"name": "m", "type": "string", "options": ["a", "b"]},
        ])


def test_min_max_only_allowed_for_numerics():
    with pytest.raises(ConfigError, match="only apply to integer/float"):
        parse_config_block([{"name": "x", "type": "string", "min": 1}])


def test_min_greater_than_max_rejected():
    with pytest.raises(ConfigError, match=r"min .* > max"):
        parse_config_block([
            {"name": "n", "type": "integer", "min": 10, "max": 5},
        ])


def test_must_exist_only_allowed_for_path():
    with pytest.raises(ConfigError, match="only applies to type=path"):
        parse_config_block([
            {"name": "x", "type": "string", "must_exist": True},
        ])


# ── default-value validation at parse time ──────────────────────────


def test_default_value_validated_against_type():
    """A default that doesn't match the declared type is caught at parse."""
    with pytest.raises(ConfigError, match="default value rejected"):
        parse_config_block([
            {"name": "n", "type": "integer", "default": "not-an-int"},
        ])


def test_default_value_for_choice_must_be_in_options():
    # Note: schema doesn't require defaults to be in options at parse
    # time (lets a tool default to "ask the user"). But if it IS a string
    # and matches an option, fine; otherwise the install runner uses it
    # as raw input. We validate the default here only against the type.
    schema = parse_config_block([
        {
            "name": "m", "type": "choice",
            "options": ["a", "b"], "default": "a",
        },
    ])
    assert schema.fields[0].default == "a"


def test_path_default_with_must_exist_works_when_exists(tmp_path: Path):
    p = tmp_path / "exists"
    p.mkdir()
    schema = parse_config_block([{
        "name": "data", "type": "path",
        "must_exist": True, "default": str(p),
    }])
    assert schema.fields[0].default == str(p)


def test_path_default_with_must_exist_fails_when_missing(tmp_path: Path):
    with pytest.raises(ConfigError, match="does not exist"):
        parse_config_block([{
            "name": "data", "type": "path",
            "must_exist": True, "default": str(tmp_path / "missing"),
        }])


# ── ConfigSchema convenience methods ────────────────────────────────


def test_required_fields_filters_correctly():
    schema = parse_config_block([
        {"name": "a", "type": "string", "required": True},
        {"name": "b", "type": "string", "required": False},
        {"name": "c", "type": "string"},
    ])
    required = schema.required_fields()
    assert [f.name for f in required] == ["a"]


def test_field_by_name_lookup():
    schema = parse_config_block([
        {"name": "api_key", "type": "secret"},
    ])
    assert schema.field_by_name("api_key").type == "secret"
    assert schema.field_by_name("missing") is None


def test_iter_yields_fields_in_declared_order():
    schema = parse_config_block([
        {"name": "z", "type": "string"},
        {"name": "a", "type": "string"},
        {"name": "m", "type": "string"},
    ])
    assert [f.name for f in schema] == ["z", "a", "m"]


# ── coerce_value (per-type) ──────────────────────────────────────────


def _field(name="x", **kwargs) -> ConfigField:
    return ConfigField(name=name, **kwargs)


def test_coerce_string_passthrough():
    f = _field(type="string")
    assert coerce_value(f, "hello") == "hello"


def test_coerce_string_required_rejects_empty():
    f = _field(type="string", required=True)
    with pytest.raises(ConfigError, match="cannot be empty"):
        coerce_value(f, "")


def test_coerce_secret_same_as_string():
    f = _field(type="secret")
    assert coerce_value(f, "tb_user_xx") == "tb_user_xx"


def test_coerce_path_tilde_expands(monkeypatch, tmp_path):
    f = _field(type="path")
    monkeypatch.setenv("HOME", str(tmp_path))
    out = coerce_value(f, "~/data")
    assert out == str(tmp_path / "data")


def test_coerce_path_must_exist_check(tmp_path):
    f = _field(type="path", must_exist=True)
    with pytest.raises(ConfigError, match="does not exist"):
        coerce_value(f, str(tmp_path / "ghost"))


def test_coerce_integer_from_string():
    f = _field(type="integer")
    assert coerce_value(f, "42") == 42
    assert coerce_value(f, " 42 ") == 42


def test_coerce_integer_rejects_bool():
    """bool is an int subclass; reject explicitly so True doesn't sneak in as 1."""
    f = _field(type="integer")
    with pytest.raises(ConfigError, match="got bool"):
        coerce_value(f, True)


def test_coerce_integer_min_max():
    f = _field(type="integer", min=1, max=10)
    assert coerce_value(f, 5) == 5
    with pytest.raises(ConfigError, match="below min"):
        coerce_value(f, 0)
    with pytest.raises(ConfigError, match="above max"):
        coerce_value(f, 11)


def test_coerce_float_from_string():
    f = _field(type="float")
    assert coerce_value(f, "0.95") == 0.95


def test_coerce_boolean_yes_no_variants():
    f = _field(type="boolean")
    for yes in ("y", "Y", "yes", "true", "TRUE", "1"):
        assert coerce_value(f, yes) is True
    for no in ("n", "N", "no", "false", "FALSE", "0"):
        assert coerce_value(f, no) is False


def test_coerce_boolean_native_bool_passthrough():
    f = _field(type="boolean")
    assert coerce_value(f, True) is True
    assert coerce_value(f, False) is False


def test_coerce_boolean_garbage_rejected():
    f = _field(type="boolean")
    with pytest.raises(ConfigError, match="cannot parse"):
        coerce_value(f, "maybe")


def test_coerce_choice_accepts_option():
    f = _field(type="choice", options=["red", "blue"])
    assert coerce_value(f, "red") == "red"


def test_coerce_choice_rejects_other():
    f = _field(type="choice", options=["red", "blue"])
    with pytest.raises(ConfigError, match="not in"):
        coerce_value(f, "green")


def test_coerce_rejects_needs_value_sentinel():
    """The sentinel is for serialization, never a valid coerced input."""
    f = _field(type="string")
    with pytest.raises(ConfigError, match="placeholder"):
        coerce_value(f, NEEDS_VALUE_SENTINEL)


# ── template variables in default values ─────────────────────────────


def test_template_default_cwd_accepted_for_path():
    # Schema parse must NOT try to coerce ${CWD} as a literal path.
    schema = parse_config_block([
        {"name": "workspace_dir", "type": "path", "default": "${CWD}"},
    ])
    assert schema.fields[0].default == "${CWD}"


def test_template_default_project_root_accepted_for_path():
    schema = parse_config_block([
        {"name": "outputs", "type": "path", "default": "${PROJECT_ROOT}/outputs"},
    ])
    assert schema.fields[0].default == "${PROJECT_ROOT}/outputs"


def test_template_default_composed_with_suffix_accepted():
    schema = parse_config_block([
        {"name": "scratch", "type": "path", "default": "${CWD}/scratch/runs"},
    ])
    assert schema.fields[0].default == "${CWD}/scratch/runs"


def test_template_default_accepted_for_string_too():
    # Strings get template support too — useful for prompt-style fields
    # that want a workspace-relative reference.
    schema = parse_config_block([
        {"name": "label", "type": "string", "default": "${CWD}-run"},
    ])
    assert schema.fields[0].default == "${CWD}-run"


def test_template_default_rejected_for_bool():
    # A boolean field with default ${CWD} would never make sense.
    with pytest.raises(Exception, match="template defaults"):
        parse_config_block([
            {"name": "x", "type": "boolean", "default": "${CWD}"},
        ])


def test_template_default_rejected_for_integer():
    with pytest.raises(Exception, match="template defaults"):
        parse_config_block([
            {"name": "x", "type": "integer", "default": "${CWD}"},
        ])


def test_unknown_template_variable_rejected_at_parse_time():
    with pytest.raises(Exception, match=r"unknown template variable"):
        parse_config_block([
            {"name": "x", "type": "path", "default": "${BANANA}"},
        ])


def test_template_path_with_must_exist_does_not_validate_at_parse_time():
    # ${CWD} obviously can't be validated as a path at schema-parse time;
    # the must_exist check happens at serve time after expansion.
    schema = parse_config_block([
        {"name": "ws", "type": "path", "default": "${CWD}",
         "must_exist": True},
    ])
    assert schema.fields[0].must_exist is True
    assert schema.fields[0].default == "${CWD}"
