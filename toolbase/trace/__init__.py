"""Provenance trace: an optional, off-by-default record of every toolkit call.

A trace directory holds two append-only JSONL files, each line flushed to the
OS as it is written, so a killed run keeps everything up to the kill:

``agents.jsonl``
    ``{"event": "agent_started", "id", "label", "parent", "model", "engine",
    "harness", "ts"}`` and ``{"event": "agent_ended", "id", "ts"}``.

``calls.jsonl``
    one ``toolbase-call/1`` record per call::

        {"schema": "toolbase-call/1", "id": "c-…", "agent": "ag-…",
         "ts_start": "…Z", "ts_end": "…Z", "toolkit": "heptapod",
         "version": "3.14.1", "tool": "MadGraphFromRunCard", "args": {…},
         "ok": true, "error": null,
         "result": {"sha256": "…", "head": "first 2048 chars"},
         "files": [{"path": "…", "bytes": 1, "mtime": "…Z", "sha256": "…"}],
         "parent": null}

Turning it on (orchestrator side)::

    from toolbase.trace import Trace
    tr = Trace(run_dir / ".toolbase/traces/session", agent="ag-1")
    tr.register_agent(label="lead", parent=None, model="…", engine="…",
                      harness="conjure")
    orch = Orchestrator(..., trace=tr)        # every call on it is ag-1's
    ...
    tr.end_agent()

Inside a toolkit (the host process), ``current()`` is the call being served,
and ``record_call`` / ``child_call`` add records linked to it by ``parent``;
see ``docs/TRACE.md`` in the toolbase repository.
"""

from .core import (
    AGENTS_FILE,
    CALL_SCHEMA,
    CALLS_FILE,
    DEFAULT_HASH_LIMIT,
    META_KEY,
    Trace,
    append_jsonl,
    bind,
    build_call_record,
    child_call,
    child_env,
    collect_files,
    current,
    end_agent,
    new_agent_id,
    new_call_id,
    now_iso,
    read_jsonl,
    record_call,
    register_agent,
    result_digest,
)

__all__ = [
    "AGENTS_FILE", "CALL_SCHEMA", "CALLS_FILE", "DEFAULT_HASH_LIMIT",
    "META_KEY", "Trace", "append_jsonl", "bind", "build_call_record",
    "child_call", "child_env", "collect_files", "current", "end_agent",
    "new_agent_id", "new_call_id", "now_iso", "read_jsonl", "record_call",
    "register_agent", "result_digest",
]
