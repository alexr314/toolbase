"""Unit tests for the Phase 3C-1 prompt helpers.

Covers ``toolbase/setup/prompts.py``: type-dispatched prompts in
``"ask"`` mode (with simulated stdin), default-fallback in skip /
yes / no modes, and validation retry on bad input.

Click prompts are awkward to drive from CliRunner without a Click
command wrapping them, so we use ``monkeypatch`` to replace
``click.prompt`` with a sequence-driven fake. The fake keeps track of
how many calls were made so we can assert "retried 3 times then
skipped" without TTY plumbing.
"""

from __future__ import annotations

from typing import Any, List

import click
import pytest

from toolbase.setup import prompts
from toolbase.setup.schema import ConfigField


def _field(name="x", **kw) -> ConfigField:
    return ConfigField(name=name, **kw)


@pytest.fixture
def fake_prompt(monkeypatch):
    """Replace ``click.prompt`` with a sequence-driven fake.

    Tests do ``fake_prompt.queue(["alice", "bob"])`` to set up the
    sequence of values the next ``click.prompt`` calls will return.
    """
    class _Fake:
        def __init__(self):
            self.values: List[Any] = []
            self.calls = 0

        def queue(self, values):
            self.values.extend(values)

        def __call__(self, *_args, **_kwargs):
            self.calls += 1
            if not self.values:
                raise click.exceptions.Abort()  # treat empty queue as Ctrl-C
            v = self.values.pop(0)
            if isinstance(v, click.exceptions.Abort):
                raise v
            return v

    fp = _Fake()
    monkeypatch.setattr(prompts.click, "prompt", fp)
    return fp


# ── non-interactive modes ────────────────────────────────────────────


def test_skip_mode_with_default_returns_default():
    f = _field(type="string", default="hello")
    out = prompts.prompt_for_field(f, mode="skip")
    assert out.has_value
    assert out.value == "hello"


def test_skip_mode_no_default_skipped():
    f = _field(type="string")
    out = prompts.prompt_for_field(f, mode="skip")
    assert out.skipped
    assert not out.has_value


def test_yes_mode_treated_as_skip_with_default():
    f = _field(type="integer", default=4)
    out = prompts.prompt_for_field(f, mode="yes")
    assert out.has_value
    assert out.value == 4


def test_no_mode_same_as_skip():
    f = _field(type="string")
    out = prompts.prompt_for_field(f, mode="no")
    assert out.skipped


def test_skip_mode_default_validated_through_coerce(monkeypatch, tmp_path):
    f = _field(type="path", default=str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path))
    out = prompts.prompt_for_field(f, mode="skip")
    assert out.has_value
    # path coercion strips/expands; check the absolute path returned.
    assert str(tmp_path / "data") in out.value


# ── interactive textual prompts ─────────────────────────────────────


def test_string_prompt_accepts_typed_value(fake_prompt):
    fake_prompt.queue(["my-project"])
    f = _field(type="string")
    out = prompts.prompt_for_field(f, mode="ask")
    assert out.value == "my-project"


def test_string_prompt_empty_skips(fake_prompt):
    fake_prompt.queue([""])
    f = _field(type="string")
    out = prompts.prompt_for_field(f, mode="ask")
    assert out.skipped


def test_string_prompt_empty_with_default_uses_default(fake_prompt):
    # Click's prompt(default=X) returns X when user hits Enter; our fake
    # honors what's queued, so we queue the literal default (mimicking
    # click's behavior).
    fake_prompt.queue(["fallback-default"])
    f = _field(type="string", default="fallback-default")
    out = prompts.prompt_for_field(f, mode="ask")
    assert out.value == "fallback-default"


def test_secret_prompt_hides_input(fake_prompt, monkeypatch):
    """Asserts the click.prompt call passed hide_input=True."""
    seen_kwargs = {}
    real = fake_prompt

    def wrapping_prompt(*args, **kwargs):
        seen_kwargs.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(prompts.click, "prompt", wrapping_prompt)
    fake_prompt.queue(["tb_user_secret"])

    f = _field(type="secret")
    out = prompts.prompt_for_field(f, mode="ask")
    assert out.value == "tb_user_secret"
    assert seen_kwargs.get("hide_input") is True


def test_integer_prompt_retries_on_invalid(fake_prompt):
    """Bad input retries up to 3 times before skipping."""
    fake_prompt.queue(["abc", "xyz", "42"])
    f = _field(type="integer")
    out = prompts.prompt_for_field(f, mode="ask")
    assert out.value == 42
    # 3 calls (2 bad + 1 good)
    assert fake_prompt.calls == 3


def test_integer_prompt_gives_up_after_3_failures(fake_prompt):
    fake_prompt.queue(["a", "b", "c"])
    f = _field(type="integer")
    out = prompts.prompt_for_field(f, mode="ask")
    assert out.skipped
    assert fake_prompt.calls == 3


def test_path_prompt_expands_tilde(fake_prompt, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_prompt.queue(["~/aster-data"])
    f = _field(type="path")
    out = prompts.prompt_for_field(f, mode="ask")
    assert out.value == str(tmp_path / "aster-data")


# ── boolean prompt ──────────────────────────────────────────────────


def test_boolean_prompt_yes(fake_prompt):
    fake_prompt.queue(["y"])
    f = _field(type="boolean")
    out = prompts.prompt_for_field(f, mode="ask")
    assert out.value is True


def test_boolean_prompt_no(fake_prompt):
    fake_prompt.queue(["n"])
    f = _field(type="boolean")
    out = prompts.prompt_for_field(f, mode="ask")
    assert out.value is False


def test_boolean_prompt_empty_with_default_uses_default(fake_prompt):
    """Empty input + default → use default."""
    fake_prompt.queue(["y"])
    f = _field(type="boolean", default=True)
    out = prompts.prompt_for_field(f, mode="ask")
    assert out.value is True


# ── choice prompt ──────────────────────────────────────────────────


def test_choice_prompt_by_index(fake_prompt):
    fake_prompt.queue(["2"])
    f = _field(type="choice", options=["red", "green", "blue"])
    out = prompts.prompt_for_field(f, mode="ask")
    assert out.value == "green"


def test_choice_prompt_by_literal(fake_prompt):
    fake_prompt.queue(["blue"])
    f = _field(type="choice", options=["red", "green", "blue"])
    out = prompts.prompt_for_field(f, mode="ask")
    assert out.value == "blue"


def test_choice_prompt_invalid_index_retries(fake_prompt):
    fake_prompt.queue(["99", "1"])
    f = _field(type="choice", options=["red", "green"])
    out = prompts.prompt_for_field(f, mode="ask")
    assert out.value == "red"


def test_choice_prompt_default_when_blank(fake_prompt):
    """An option as default surfaces in the displayed list and is taken
    when the user presses Enter (queued as the literal default index)."""
    fake_prompt.queue(["1"])
    f = _field(
        type="choice", options=["primary", "fallback"], default="primary",
    )
    out = prompts.prompt_for_field(f, mode="ask")
    assert out.value == "primary"


# ── cancellation ────────────────────────────────────────────────────


def test_ctrl_c_cancels(fake_prompt):
    """An Abort from Click (Ctrl-C / Ctrl-D) propagates as cancelled."""
    fake_prompt.values.append(click.exceptions.Abort())
    f = _field(type="string")
    out = prompts.prompt_for_field(f, mode="ask")
    assert out.cancelled
    assert not out.has_value
    assert not out.skipped
