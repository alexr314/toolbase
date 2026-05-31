"""
Pydantic schemas for the Tier-1 declarative ``config:`` block.

A toolkit author declares config in ``toolkit.yaml``:

```yaml
config:
  - name: api_key
    description: "..."
    type: secret
    required: true
  - name: data_path
    type: path
    default: ~/.toolbase/data/my-toolkit
```

This module turns that list-of-dicts into validated ``ConfigField``
instances and exposes one ``ConfigSchema`` for the whole block.

Per the manager's Item 3 sketch sign-off (2026-05-06):

- **List-of-objects form** (not dict-keyed). Order matters because the
  prompt order at install is what the user sees.
- **Seven supported types:** string, secret, path, integer, float,
  boolean, choice. Per-type validators applied at parse time so a
  bad ``toolkit.yaml`` is rejected by ``toolbase validate`` rather
  than at install or serve.
- **Sentinel for "filled in later":** ``<NEEDS VALUE>`` — a literal
  string written into the YAML by the install-time runner when the
  user skips a required field. The serve-time resolver treats this
  the same as a missing required field.

Pydantic's discriminated unions don't fit cleanly here (we'd need a
non-trivial schema-per-type) so we use one model with conditional
validators keyed off ``type``. Slightly less elegant, much easier to
explain to a tool author reading a validation error.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


# Literal sentinel written to the YAML when the user skips a required
# field at install time. The serve-time resolver detects this string
# and skips the toolkit with the same UX as "field missing entirely."
# Human-readable in `toolbase config show` output, which is its main
# argument over null/empty-string.
NEEDS_VALUE_SENTINEL = "<NEEDS VALUE>"


_VALID_TYPES = {
    "string", "secret", "path", "integer", "float", "boolean", "choice",
}


# ── Template variables for serve-time default expansion ───────────────
#
# A toolkit author can write ``default: ${CWD}`` (or ``${PROJECT_ROOT}``)
# in their ``config:`` block. The literal template string is stored;
# expansion happens lazily in ``load_state_config`` so the value
# reflects the orchestrator's process state at the moment of serve
# (= the harness's launch directory, = where the agent thinks it's
# working). See ``_expand_default_template`` in
# ``setup/declarative.py`` for the resolution logic.
#
# Composition is supported: ``${CWD}/scratch`` is fine. Mixing two
# templates in one value is syntactically allowed but rarely useful.
# Unknown template names (``${BANANA}``) are rejected at schema parse
# time so toolkit authors get a clear error in ``tb validate`` rather
# than a silent serve-time fallback.
_TEMPLATE_VARS = frozenset({"CWD", "PROJECT_ROOT"})
_TEMPLATE_PATTERN = re.compile(r"\$\{([A-Z_]+)\}")


def value_has_template(value: Any) -> bool:
    """True if ``value`` is a string containing at least one ``${VAR}`` token."""
    return isinstance(value, str) and bool(_TEMPLATE_PATTERN.search(value))


def template_vars_in(value: str) -> List[str]:
    """Return the list of ``${VAR}`` names present in ``value``."""
    return _TEMPLATE_PATTERN.findall(value)


# Pydantic-reserved attribute names — using these as field names would
# collide with the generated Pydantic model and produce confusing
# errors. Catch them at parse time with a clear message.
_RESERVED_NAMES = {
    "model_config", "model_fields", "model_validate", "model_dump",
    "dict", "json", "fields", "schema",
}


class ConfigError(ValueError):
    """Raised when a ``config:`` block is malformed.

    Distinct from generic ``ValidationError`` so callers (validate,
    install, serve) can distinguish "user's toolkit.yaml is bad" from
    "user's stored config value doesn't match the declared schema."
    """


class ConfigField(BaseModel):
    """One entry in a toolkit's ``config:`` block.

    See the module docstring for the YAML shape; this models a single
    parsed entry. Type-specific options (``min``, ``max``, ``options``,
    ``must_exist``) are checked by ``model_validator`` against the
    chosen ``type`` so a tool author who declares ``type: integer`` and
    forgets ``min``/``max`` works fine, while one who declares
    ``type: choice`` without ``options`` gets a clear error.
    """

    model_config: ClassVar[dict] = {"extra": "forbid"}

    name: str = Field(
        ..., description=(
            "Field name. Becomes the YAML key in the saved config file "
            "and the state-field name on tools that declare it via "
            "``@define_tool(state=[...])``."
        ),
    )
    description: Optional[str] = Field(
        None, description="Human-readable description shown at install prompt.",
    )
    type: str = Field(
        ..., description=(
            "One of: string, secret, path, integer, float, boolean, choice."
        ),
    )
    required: bool = Field(
        False, description=(
            "If True, install-time prompts can't be skipped without "
            "leaving the literal NEEDS_VALUE_SENTINEL behind. ``serve`` "
            "will refuse to launch the toolkit until the value is filled."
        ),
    )
    default: Optional[Any] = Field(
        None, description="Default value used in --no-prompt and skip-on-Esc paths.",
    )
    # Per-type optional knobs. Declared at the top level (not nested
    # under each type's submodel) for ergonomics. Cross-validated below.
    min: Optional[Union[int, float]] = Field(
        None, description="Lower bound (integer/float types only).",
    )
    max: Optional[Union[int, float]] = Field(
        None, description="Upper bound (integer/float types only).",
    )
    options: Optional[List[str]] = Field(
        None, description="Allowed values (choice type only). At least 2 required.",
    )
    must_exist: Optional[bool] = Field(
        None, description=(
            "Path type only. If True, the path must exist at the time it "
            "is filled in (validated at config-write time, not at "
            "install time — a path the user is *about* to create is fine)."
        ),
    )

    # ── validators ─────────────────────────────────────────────────

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not v:
            raise ValueError("config field 'name' cannot be empty")
        if v != v.strip():
            raise ValueError(
                f"config field name {v!r} has leading/trailing whitespace"
            )
        if v in _RESERVED_NAMES:
            raise ValueError(
                f"config field name {v!r} collides with a Pydantic-reserved "
                "attribute. Rename to something else."
            )
        # Names must be valid Python identifiers because Orchestral's
        # state-field injection writes them onto the tool instance via
        # ``setattr(tool, name, value)``. Non-identifier names would
        # create attributes only reachable via getattr, defeating the
        # purpose.
        if not v.isidentifier():
            raise ValueError(
                f"config field name {v!r} must be a valid Python identifier "
                "(alphanumeric + underscore, must start with a letter or _)."
            )
        return v

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in _VALID_TYPES:
            raise ValueError(
                f"unknown config field type {v!r}. "
                f"Valid types: {sorted(_VALID_TYPES)}"
            )
        return v

    @model_validator(mode="after")
    def _cross_validate_per_type(self) -> "ConfigField":
        t = self.type

        # min/max only make sense for numeric types.
        if (self.min is not None or self.max is not None) and t not in (
            "integer", "float",
        ):
            raise ValueError(
                f"config field {self.name!r}: 'min'/'max' only apply to "
                f"integer/float types (got type={t!r})"
            )
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(
                f"config field {self.name!r}: min ({self.min}) > max ({self.max})"
            )

        # options must be set for choice and only for choice.
        if t == "choice":
            if not self.options:
                raise ValueError(
                    f"config field {self.name!r}: type=choice requires "
                    "an 'options' list"
                )
            if len(self.options) < 2:
                raise ValueError(
                    f"config field {self.name!r}: type=choice requires "
                    "at least 2 options (a 1-option choice has no choice)"
                )
            seen = set()
            for opt in self.options:
                if opt in seen:
                    raise ValueError(
                        f"config field {self.name!r}: duplicate option {opt!r}"
                    )
                seen.add(opt)
        elif self.options is not None:
            raise ValueError(
                f"config field {self.name!r}: 'options' only applies to "
                f"type=choice (got type={t!r})"
            )

        # must_exist only applies to path.
        if self.must_exist is not None and t != "path":
            raise ValueError(
                f"config field {self.name!r}: 'must_exist' only applies to "
                f"type=path (got type={t!r})"
            )

        # Validate default (if any) against the declared type.
        # Templated defaults (e.g. ``${CWD}``, ``${PROJECT_ROOT}``) are
        # not coerced here — they're literal placeholders the orchestrator
        # expands at serve time. We DO validate that any templates used
        # reference known variables so authors get a clear error from
        # ``tb validate`` rather than a silent miss at serve time.
        # Restricted to path / string types: expanding a template into a
        # bool/integer/choice/secret field would never make sense.
        if self.default is not None:
            if value_has_template(self.default):
                if t not in ("path", "string"):
                    raise ValueError(
                        f"config field {self.name!r}: template defaults "
                        f"are only supported for type=path or type=string "
                        f"(got type={t!r})"
                    )
                unknown = [
                    v for v in template_vars_in(self.default)
                    if v not in _TEMPLATE_VARS
                ]
                if unknown:
                    valid = ", ".join(sorted(_TEMPLATE_VARS))
                    raise ValueError(
                        f"config field {self.name!r}: default uses unknown "
                        f"template variable(s) {unknown!r}. Valid "
                        f"variables: {valid}."
                    )
            else:
                try:
                    _coerce_value(self, self.default)
                except ConfigError as e:
                    raise ValueError(
                        f"config field {self.name!r}: default value rejected: {e}"
                    )

        return self


class ConfigSchema(BaseModel):
    """The full ``config:`` block: an ordered list of fields.

    Order is preserved because install-time prompts walk the list in
    order, and that order is what the user reads.
    """

    fields: List[ConfigField] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_names(self) -> "ConfigSchema":
        seen: set = set()
        for f in self.fields:
            if f.name in seen:
                raise ValueError(
                    f"duplicate config field name: {f.name!r}"
                )
            seen.add(f.name)
        return self

    def required_fields(self) -> List[ConfigField]:
        return [f for f in self.fields if f.required]

    def field_by_name(self, name: str) -> Optional[ConfigField]:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def __iter__(self):  # type: ignore[override]
        return iter(self.fields)


# ── public parser used by validation.py and the install/serve flows ──


def parse_config_block(raw: Any) -> ConfigSchema:
    """Turn a raw value from ``toolkit.yaml`` into a ``ConfigSchema``.

    Accepts:
        - ``None`` → empty schema
        - empty list → empty schema
        - list of dicts → one ``ConfigField`` per dict

    Raises ``ConfigError`` with a clear message on any malformation.
    """
    if raw is None:
        return ConfigSchema(fields=[])
    if not isinstance(raw, list):
        raise ConfigError(
            "toolkit.yaml 'config:' must be a list of field definitions, "
            f"got {type(raw).__name__}"
        )
    parsed: List[ConfigField] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ConfigError(
                f"toolkit.yaml 'config:' entry #{i + 1} must be a mapping, "
                f"got {type(entry).__name__}"
            )
        try:
            parsed.append(ConfigField(**entry))
        except Exception as e:
            raise ConfigError(
                f"toolkit.yaml 'config:' entry #{i + 1} "
                f"({entry.get('name', '<unnamed>')!r}): {e}"
            ) from e
    try:
        return ConfigSchema(fields=parsed)
    except Exception as e:
        raise ConfigError(str(e)) from e


# ── value coercion / validation ───────────────────────────────────────


def _coerce_value(field: ConfigField, raw: Any) -> Any:
    """Convert a raw user-supplied value into the field's declared type.

    Returns the parsed value on success. Raises ``ConfigError`` with a
    helpful message on failure. Used by the prompt runner (after raw
    text input) and by the schema validator on default values.

    Idempotent for values already in the right type — passing an int
    to an integer field returns the int unchanged.

    NOTE: ``NEEDS_VALUE_SENTINEL`` is *not* a valid value for any type.
    The runner writes it directly to the YAML; this function would
    reject it. That's intentional: at the moment a user enters a value,
    that value should be a real one, not the sentinel.
    """
    t = field.type

    if raw == NEEDS_VALUE_SENTINEL:
        raise ConfigError(
            f"config field {field.name!r}: refusing to accept the "
            f"placeholder {NEEDS_VALUE_SENTINEL!r} as a real value"
        )

    if t in ("string", "secret"):
        if not isinstance(raw, str):
            raise ConfigError(
                f"config field {field.name!r}: expected string, got "
                f"{type(raw).__name__}"
            )
        if field.required and not raw:
            raise ConfigError(
                f"config field {field.name!r}: required, cannot be empty"
            )
        return raw

    if t == "path":
        if isinstance(raw, Path):
            p = raw
        elif isinstance(raw, str):
            if not raw:
                if field.required:
                    raise ConfigError(
                        f"config field {field.name!r}: required, "
                        "cannot be empty"
                    )
                return ""
            p = Path(raw).expanduser()
        else:
            raise ConfigError(
                f"config field {field.name!r}: expected path string, "
                f"got {type(raw).__name__}"
            )
        if field.must_exist and not p.exists():
            raise ConfigError(
                f"config field {field.name!r}: path {p} does not exist "
                "(must_exist=true)"
            )
        return str(p)

    if t == "integer":
        if isinstance(raw, bool):
            # bool is an int subclass in Python; reject explicitly to
            # avoid 'true' silently parsing as 1.
            raise ConfigError(
                f"config field {field.name!r}: expected integer, got bool"
            )
        if isinstance(raw, int):
            v = raw
        elif isinstance(raw, str):
            try:
                v = int(raw.strip())
            except (ValueError, AttributeError):
                raise ConfigError(
                    f"config field {field.name!r}: cannot parse {raw!r} "
                    "as integer"
                )
        else:
            raise ConfigError(
                f"config field {field.name!r}: expected integer, got "
                f"{type(raw).__name__}"
            )
        if field.min is not None and v < field.min:
            raise ConfigError(
                f"config field {field.name!r}: {v} below min ({field.min})"
            )
        if field.max is not None and v > field.max:
            raise ConfigError(
                f"config field {field.name!r}: {v} above max ({field.max})"
            )
        return v

    if t == "float":
        if isinstance(raw, bool):
            raise ConfigError(
                f"config field {field.name!r}: expected float, got bool"
            )
        if isinstance(raw, (int, float)):
            v_f = float(raw)
        elif isinstance(raw, str):
            try:
                v_f = float(raw.strip())
            except (ValueError, AttributeError):
                raise ConfigError(
                    f"config field {field.name!r}: cannot parse {raw!r} "
                    "as float"
                )
        else:
            raise ConfigError(
                f"config field {field.name!r}: expected float, got "
                f"{type(raw).__name__}"
            )
        if field.min is not None and v_f < float(field.min):
            raise ConfigError(
                f"config field {field.name!r}: {v_f} below min ({field.min})"
            )
        if field.max is not None and v_f > float(field.max):
            raise ConfigError(
                f"config field {field.name!r}: {v_f} above max ({field.max})"
            )
        return v_f

    if t == "boolean":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            s = raw.strip().lower()
            if s in ("y", "yes", "true", "1"):
                return True
            if s in ("n", "no", "false", "0"):
                return False
        raise ConfigError(
            f"config field {field.name!r}: cannot parse {raw!r} as boolean"
        )

    if t == "choice":
        if not isinstance(raw, str):
            raise ConfigError(
                f"config field {field.name!r}: expected one of {field.options}, "
                f"got {type(raw).__name__}"
            )
        if raw not in (field.options or []):
            raise ConfigError(
                f"config field {field.name!r}: {raw!r} is not in "
                f"{field.options}"
            )
        return raw

    raise ConfigError(
        f"config field {field.name!r}: unknown type {t!r} (internal error)"
    )


def coerce_value(field: ConfigField, raw: Any) -> Any:
    """Public alias for ``_coerce_value`` so tests don't import the
    underscore-prefixed name."""
    return _coerce_value(field, raw)
