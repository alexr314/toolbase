"""Unit tests for the Phase 3C-1 install-time runner and serve-time resolver.

Covers ``toolbase/setup/declarative.py``:

- ``run_install_setup(name, schema, mode)`` — fills in defaults
  (non-TTY) or prompts (TTY-mocked); writes the config file. Handles
  required-skipped → NEEDS_VALUE_SENTINEL, idempotent re-run, partial
  user input.
- ``load_state_config(name, schema)`` — reads the YAML, validates
  against the schema, returns a flat state-config dict + list of
  missing/invalid fields.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from toolbase import config as toolbase_config
from toolbase.setup import (
    NEEDS_VALUE_SENTINEL,
    parse_config_block,
    run_install_setup,
    load_state_config,
    load_config,
    save_config,
)
from toolbase.setup.prompts import PromptOutcome


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake = tmp_path / "toolbase"
    fake.mkdir()
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    return fake


# ── run_install_setup: skip-mode (non-TTY) ────────────────────────────


def test_skip_mode_writes_defaults(isolated_config: Path):
    schema = parse_config_block([
        {"name": "host", "type": "string", "default": "localhost"},
        {"name": "port", "type": "integer", "default": 8080},
    ])
    result = run_install_setup("demo", schema, mode="skip")
    assert not result.cancelled
    assert set(result.fields_filled) == {"host", "port"}
    data = load_config("demo")
    assert data["host"] == "localhost"
    assert data["port"] == 8080


def test_skip_mode_required_no_default_writes_sentinel(isolated_config: Path):
    schema = parse_config_block([
        {"name": "api_key", "type": "secret", "required": True},
    ])
    result = run_install_setup("demo", schema, mode="skip")
    assert result.fields_skipped_required == ["api_key"]
    assert result.needs_attention
    assert load_config("demo")["api_key"] == NEEDS_VALUE_SENTINEL


def test_skip_mode_optional_no_default_omits_field(isolated_config: Path):
    schema = parse_config_block([
        {"name": "verbose", "type": "boolean"},  # optional, no default
    ])
    result = run_install_setup("demo", schema, mode="skip")
    assert result.fields_skipped_optional == ["verbose"]
    # File written but field absent
    data = load_config("demo")
    assert "verbose" not in data


def test_empty_schema_creates_no_file(isolated_config: Path):
    schema = parse_config_block([])
    result = run_install_setup("demo", schema, mode="skip")
    assert not result.fields_filled
    # Important: don't drop an empty file for toolkits with no config.
    assert not result.config_file.exists()


def test_install_setup_writes_header_comment(isolated_config: Path):
    schema = parse_config_block([
        {"name": "host", "type": "string", "default": "localhost"},
    ])
    run_install_setup("demo", schema, mode="skip")
    text = (isolated_config / "config" / "demo.yaml").read_text()
    assert "Configuration for demo" in text
    assert "toolbase config show demo" in text


# ── run_install_setup: idempotency ────────────────────────────────────


def test_idempotent_keeps_existing_valid_values(isolated_config: Path):
    """Re-running setup must not blow away an already-filled config."""
    schema = parse_config_block([
        {"name": "host", "type": "string", "required": True},
        {"name": "port", "type": "integer", "default": 8080},
    ])
    # Pre-seed.
    save_config("demo", {"host": "myhost", "port": 9090})
    result = run_install_setup("demo", schema, mode="skip")
    assert "host" in result.fields_filled
    data = load_config("demo")
    assert data["host"] == "myhost"
    assert data["port"] == 9090


def test_idempotent_replaces_sentinel_with_default(isolated_config: Path):
    """A previously skipped required field gets re-prompted on re-run."""
    schema = parse_config_block([
        {"name": "host", "type": "string", "required": True, "default": "localhost"},
    ])
    save_config("demo", {"host": NEEDS_VALUE_SENTINEL})
    result = run_install_setup("demo", schema, mode="skip")
    assert load_config("demo")["host"] == "localhost"
    assert "host" in result.fields_filled


def test_idempotent_invalid_stored_value_reprompted(isolated_config: Path):
    """A stored value that doesn't validate gets reprompted (and skipped in non-TTY)."""
    schema = parse_config_block([
        {"name": "n", "type": "integer", "min": 1, "max": 10, "required": True},
    ])
    save_config("demo", {"n": "not-a-number"})
    result = run_install_setup("demo", schema, mode="skip")
    # No default → required-skipped.
    assert "n" in result.fields_skipped_required
    assert load_config("demo")["n"] == NEEDS_VALUE_SENTINEL


# ── run_install_setup: ask-mode with mocked prompts ──────────────────


def test_ask_mode_uses_prompt_outcomes(isolated_config: Path, monkeypatch):
    """Drive the runner via mocked prompt_for_field."""
    schema = parse_config_block([
        {"name": "api_key", "type": "secret", "required": True},
        {"name": "max_workers", "type": "integer", "default": 4},
    ])

    queued = [
        PromptOutcome(value="tb_user_xx", has_value=True),
        PromptOutcome(value=8, has_value=True),
    ]

    def fake_prompt(field, mode):
        return queued.pop(0)

    monkeypatch.setattr(
        "toolbase.setup.declarative.prompt_for_field", fake_prompt,
    )

    result = run_install_setup("demo", schema, mode="ask")
    assert set(result.fields_filled) == {"api_key", "max_workers"}
    data = load_config("demo")
    assert data["api_key"] == "tb_user_xx"
    assert data["max_workers"] == 8


def test_ask_mode_cancellation_stops_flow(isolated_config: Path, monkeypatch):
    """Ctrl-C on any prompt → result.cancelled=True, partial file written."""
    schema = parse_config_block([
        {"name": "a", "type": "string", "required": True},
        {"name": "b", "type": "string", "required": True},
    ])

    def fake_prompt(field, mode):
        if field.name == "a":
            return PromptOutcome(value="filled", has_value=True)
        return PromptOutcome(cancelled=True)

    monkeypatch.setattr(
        "toolbase.setup.declarative.prompt_for_field", fake_prompt,
    )

    result = run_install_setup("demo", schema, mode="ask")
    assert result.cancelled
    data = load_config("demo")
    assert data["a"] == "filled"
    # b never reached → not in file
    assert "b" not in data


# ── load_state_config: serve-time resolver ──────────────────────────


def test_load_state_config_all_filled_returns_state(isolated_config: Path):
    schema = parse_config_block([
        {"name": "api_key", "type": "secret", "required": True},
        {"name": "max_workers", "type": "integer", "default": 4},
    ])
    save_config("demo", {"api_key": "tb_user_xx", "max_workers": 8})
    res = load_state_config("demo", schema)
    assert res.ok
    assert res.state_config == {"api_key": "tb_user_xx", "max_workers": 8}


def test_load_state_config_missing_required_flagged(isolated_config: Path):
    schema = parse_config_block([
        {"name": "api_key", "type": "secret", "required": True},
    ])
    # No file at all.
    res = load_state_config("demo", schema)
    assert not res.ok
    assert res.missing_required == ["api_key"]
    assert "api_key" not in res.state_config


def test_load_state_config_sentinel_treated_as_missing(isolated_config: Path):
    schema = parse_config_block([
        {"name": "api_key", "type": "secret", "required": True},
    ])
    save_config("demo", {"api_key": NEEDS_VALUE_SENTINEL})
    res = load_state_config("demo", schema)
    assert "api_key" in res.missing_required


def test_load_state_config_invalid_value_flagged(isolated_config: Path):
    schema = parse_config_block([
        {"name": "n", "type": "integer", "min": 1, "max": 10},
    ])
    save_config("demo", {"n": 9999})
    res = load_state_config("demo", schema)
    assert not res.ok
    assert res.invalid
    assert res.invalid[0][0] == "n"


def test_load_state_config_optional_missing_omitted(isolated_config: Path):
    """An optional field with no stored value just isn't in state_config."""
    schema = parse_config_block([
        {"name": "verbose", "type": "boolean"},  # optional
    ])
    save_config("demo", {})
    res = load_state_config("demo", schema)
    assert res.ok
    assert "verbose" not in res.state_config


def test_load_state_config_path_returned_as_string(isolated_config: Path, tmp_path):
    """Paths must be JSON-encodable for --state-config; coerce_value
    already returns strings, just verify nothing leaks a Path through."""
    schema = parse_config_block([
        {"name": "data", "type": "path"},
    ])
    save_config("demo", {"data": str(tmp_path)})
    res = load_state_config("demo", schema)
    assert res.ok
    assert isinstance(res.state_config["data"], str)


def test_load_state_config_skip_reason_is_human_readable(isolated_config: Path):
    schema = parse_config_block([
        {"name": "a", "type": "secret", "required": True},
        {"name": "b", "type": "string", "required": True},
    ])
    res = load_state_config("demo", schema)
    reason = res.skip_reason()
    assert reason is not None
    assert "missing required" in reason
    assert "a" in reason and "b" in reason


def test_load_state_config_malformed_yaml_handled(isolated_config: Path):
    """A broken YAML file should not crash the resolver."""
    cfg = isolated_config / "config" / "demo.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("nope: [unclosed\n")
    schema = parse_config_block([
        {"name": "x", "type": "string", "required": True},
    ])
    res = load_state_config("demo", schema)
    assert not res.ok
    # The required field shows up as missing too, since the file isn't
    # readable.
    assert "x" in res.missing_required or any(
        n == "<file>" for n, _ in res.invalid
    )


def test_load_state_config_no_schema_returns_empty(isolated_config: Path):
    schema = parse_config_block([])
    res = load_state_config("demo", schema)
    assert res.ok
    assert res.state_config == {}
