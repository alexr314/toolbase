"""
Tool execution logger for Toolbase.

Provides thread-safe logging of:
- Tool executions (start / output / complete) — used by serve to track
  individual tool calls.
- Orchestrator-level events (subprocess spawn/crash, MCP client connect, etc.)
  — used by ``toolbase serve`` for "what just happened" diagnostics.

Outputs:

- ``~/.toolbase/logs/YYYY-MM-DD.log`` — daily files, all events flow here.
  Rotated after 30 days.
- ``~/.toolbase/logs/tool_calls.jsonl`` — structured per-call records
  (timestamp, toolkit, tool, args, duration, success). Append-only; not rotated.
- ``~/.toolbase/logs/serve.log`` — append-only mirror of orchestrator events
  and tool calls *during a serve session only*. Pruned on startup if it exceeds
  the size limit (~10 MB). Opt-in via ``ToolLogger(serve_log=True)``; off by
  default so install/list/uninstall don't pollute it.

Event vocabulary for ``log_event(event=...)`` (free-form, but conventions
matter for later log queries):

    serve_started, serve_shutting_down,
    toolkit_loaded, toolkit_skipped,
    subprocess_spawned, subprocess_crashed, subprocess_restarting,
    subprocess_failed_permanently,
    mcp_client_connected, mcp_client_disconnected,
    tools_list_changed.

New event names are fine; keep snake_case and stable.
"""

import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict, field
from collections import deque

from ..config import LOGS_DIR


SERVE_LOG_PATH = LOGS_DIR / "serve.log"
SERVE_LOG_MAX_BYTES = 10 * 1024 * 1024  # ~10 MB before tail-prune on startup
SERVE_LOG_TAIL_BYTES = 5 * 1024 * 1024  # keep ~last 5 MB on prune


@dataclass
class LogEntry:
    """Single log entry."""
    timestamp: str
    toolkit: str
    tool: str
    message: str
    level: str  # info, warning, error, success

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class ToolCallRecord:
    """Record of a tool call for structured logging."""
    timestamp: str
    toolkit: str
    tool: str
    args: Dict[str, Any]  # Sanitized arguments
    duration: Optional[float] = None
    success: Optional[bool] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class EventRecord:
    """Record of an orchestrator-level event (not tied to a specific tool call)."""
    timestamp: str
    event: str  # vocabulary in module docstring
    toolkit: Optional[str]  # None for events not scoped to a toolkit
    message: str
    level: str  # info, warn, error
    fields: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ToolLogger:
    """
    Thread-safe logger for tool execution.

    Features:
    - Daily log files (~/.toolbase/logs/YYYY-MM-DD.log)
    - Structured JSONL tool call log (tool_calls.jsonl)
    - In-memory buffer for TUI (last 1000 entries)
    - Auto-rotation (keeps last 30 days)
    """

    def __init__(self, max_memory_logs: int = 1000, *, serve_log: bool = False):
        """
        Initialize logger.

        Args:
            max_memory_logs: Maximum log entries to keep in memory
            serve_log: If True, also write tool calls and events to
                ~/.toolbase/logs/serve.log. Default False so install/list/
                uninstall logging stays out of serve.log.
        """
        self._lock = threading.Lock()
        self._memory_logs = deque(maxlen=max_memory_logs)
        self._active_calls: Dict[str, ToolCallRecord] = {}  # tool_id -> record
        self._serve_log_enabled = serve_log

        # Ensure logs directory exists
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        # Rotate old daily log files on startup
        self._rotate_logs()

        if serve_log:
            self._prune_serve_log_if_oversized()
            self._write_serve_session_marker()

    def enable_serve_log(self) -> None:
        """Turn on serve.log mirroring on an already-constructed logger.

        Needed because the global logger is a singleton: any caller that
        invokes ``get_logger()`` without ``serve_log=True`` before
        ``toolbase serve`` starts (e.g. project-discovery debug
        logging) would otherwise lock the instance into
        ``_serve_log_enabled=False`` for the rest of the process.
        Idempotent: prune + session marker fire only on the first call.
        """
        with self._lock:
            if self._serve_log_enabled:
                return
            self._serve_log_enabled = True
        # Run prune + session marker outside the lock so we don't hold
        # it during file IO. (The flag flip itself is the source of
        # truth; the marker is best-effort.)
        self._prune_serve_log_if_oversized()
        self._write_serve_session_marker()

    def log_tool_start(self, toolkit: str, tool: str, args: Dict[str, Any]) -> str:
        """
        Log the start of a tool execution.

        Args:
            toolkit: Toolkit name
            tool: Tool name
            args: Tool arguments (will be sanitized)

        Returns:
            tool_id: Unique ID for this tool call
        """
        timestamp = datetime.now().isoformat()
        tool_id = f"{toolkit}::{tool}::{timestamp}"

        # Sanitize arguments (remove potential secrets)
        sanitized_args = self._sanitize_args(args)

        # Create record
        record = ToolCallRecord(
            timestamp=timestamp,
            toolkit=toolkit,
            tool=tool,
            args=sanitized_args
        )

        with self._lock:
            self._active_calls[tool_id] = record

            human = f"[{timestamp}] {toolkit}::{tool} - Starting"
            self._write_daily_log(human, level="info")

            entry = LogEntry(
                timestamp=timestamp,
                toolkit=toolkit,
                tool=tool,
                message="Starting",
                level="info"
            )
            self._memory_logs.append(entry)

            if self._serve_log_enabled:
                self._write_serve_log(human + "\n")

        return tool_id

    def log_tool_output(
        self,
        toolkit: str,
        tool: str,
        message: str,
        level: str = "info"
    ):
        """
        Log output from a tool during execution.

        Args:
            toolkit: Toolkit name
            tool: Tool name
            message: Output message
            level: Log level (info, warning, error, success)
        """
        timestamp = datetime.now().isoformat()

        with self._lock:
            human = f"[{timestamp}] {toolkit}::{tool} - {message}"
            self._write_daily_log(human, level=level)

            entry = LogEntry(
                timestamp=timestamp,
                toolkit=toolkit,
                tool=tool,
                message=message,
                level=level
            )
            self._memory_logs.append(entry)

            if self._serve_log_enabled:
                self._write_serve_log(human + "\n")

    def log_tool_complete(
        self,
        tool_id: str,
        duration: float,
        success: bool,
        error: Optional[str] = None
    ):
        """
        Log the completion of a tool execution.

        Args:
            tool_id: Tool ID from log_tool_start()
            duration: Execution duration in seconds
            success: Whether execution succeeded
            error: Error message if failed
        """
        timestamp = datetime.now().isoformat()

        with self._lock:
            # Get the active call record
            if tool_id not in self._active_calls:
                # Tool wasn't started properly, create minimal record
                parts = tool_id.split("::")
                if len(parts) >= 2:
                    record = ToolCallRecord(
                        timestamp=timestamp,
                        toolkit=parts[0],
                        tool=parts[1],
                        args={},
                        duration=duration,
                        success=success,
                        error=error
                    )
                else:
                    return  # Invalid tool_id
            else:
                record = self._active_calls[tool_id]
                record.duration = duration
                record.success = success
                record.error = error
                del self._active_calls[tool_id]

            self._write_tool_call_log(record)

            status = "✓ Completed" if success else "✗ Failed"
            human = f"[{timestamp}] {record.toolkit}::{record.tool} - {status} in {duration:.2f}s"
            if error:
                human += f" - {error}"

            self._write_daily_log(human, level="success" if success else "error")

            entry = LogEntry(
                timestamp=timestamp,
                toolkit=record.toolkit,
                tool=record.tool,
                message=f"{status} in {duration:.2f}s" + (f" - {error}" if error else ""),
                level="success" if success else "error"
            )
            self._memory_logs.append(entry)

            if self._serve_log_enabled:
                self._write_serve_log(human + "\n")

    def get_recent_logs(self, limit: Optional[int] = None) -> List[LogEntry]:
        """
        Get recent log entries from memory.

        Args:
            limit: Maximum number of entries (None = all)

        Returns:
            List of log entries (most recent last)
        """
        with self._lock:
            logs = list(self._memory_logs)
            if limit:
                logs = logs[-limit:]
            return logs

    def _sanitize_args(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize arguments to remove potential secrets.

        Args:
            args: Original arguments

        Returns:
            Sanitized arguments
        """
        sanitized = {}

        # List of keys that might contain secrets
        secret_keys = {
            'password', 'passwd', 'pwd',
            'token', 'api_key', 'apikey', 'key',
            'secret', 'auth', 'credential'
        }

        for key, value in args.items():
            key_lower = key.lower()

            # Check if key name suggests a secret
            if any(secret in key_lower for secret in secret_keys):
                sanitized[key] = "***REDACTED***"
            else:
                # Keep the value but limit size for large objects
                if isinstance(value, (str, int, float, bool, type(None))):
                    sanitized[key] = value
                elif isinstance(value, (list, tuple)):
                    # Truncate long lists
                    if len(value) > 10:
                        sanitized[key] = f"<{type(value).__name__} of {len(value)} items>"
                    else:
                        sanitized[key] = value
                elif isinstance(value, dict):
                    # Recursively sanitize nested dicts
                    sanitized[key] = self._sanitize_args(value)
                else:
                    # Other types: just show type
                    sanitized[key] = f"<{type(value).__name__}>"

        return sanitized

    def _write_daily_log(self, message: str, level: str = "info"):
        """
        Write to daily log file.

        Args:
            message: Log message
            level: Log level
        """
        # Get today's log file
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = LOGS_DIR / f"{today}.log"

        # Write line
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                level_prefix = {
                    'info': '[INFO]',
                    'warning': '[WARN]',
                    'error': '[ERROR]',
                    'success': '[SUCCESS]'
                }.get(level, '[INFO]')

                f.write(f"{level_prefix} {message}\n")
        except Exception:
            # Fail silently - don't break execution over logging
            pass

    def _write_tool_call_log(self, record: ToolCallRecord):
        """
        Write tool call record to structured JSONL log.

        Args:
            record: Tool call record
        """
        log_file = LOGS_DIR / "tool_calls.jsonl"

        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record.to_dict()) + '\n')
        except Exception:
            # Fail silently
            pass

    def _rotate_logs(self):
        """Remove daily log files older than 30 days."""
        try:
            cutoff_date = datetime.now() - timedelta(days=30)

            for log_file in LOGS_DIR.glob("*.log"):
                # Only match daily files of shape YYYY-MM-DD.log; leave
                # serve.log and any other non-dated files untouched.
                try:
                    date_str = log_file.stem
                    file_date = datetime.strptime(date_str, "%Y-%m-%d")

                    if file_date < cutoff_date:
                        log_file.unlink()
                except (ValueError, OSError):
                    continue
        except Exception:
            # Fail silently - rotation is not critical
            pass

    # ── serve.log support ────────────────────────────────────────────────

    def log_event(
        self,
        event: str,
        toolkit: Optional[str] = None,
        message: str = "",
        level: str = "info",
        **fields,
    ) -> None:
        """
        Log an orchestrator-level event.

        Use this for things that aren't tied to a specific tool call:
        subprocess spawn/crash, MCP client connection, toolkit skipped at
        startup, etc. See module docstring for the event vocabulary.

        Args:
            event: short snake_case identifier (e.g. ``subprocess_spawned``)
            toolkit: toolkit name if the event is scoped to one, else None
            message: human-readable detail
            level: ``info``, ``warn``, or ``error``
            **fields: arbitrary structured fields (port, pid, restart_count, ...)
                included in the JSONL payload and rendered as ``key=value`` in
                the human-readable log lines.
        """
        timestamp = datetime.now().isoformat()
        record = EventRecord(
            timestamp=timestamp,
            event=event,
            toolkit=toolkit,
            message=message,
            level=level,
            fields=fields or {},
        )

        with self._lock:
            # Human-readable line for the daily file
            scope = f" {toolkit}" if toolkit else ""
            extra = (
                " " + " ".join(f"{k}={v}" for k, v in record.fields.items())
                if record.fields else ""
            )
            human = f"[{timestamp}] event={event}{scope} - {message}{extra}".rstrip()
            self._write_daily_log(human, level=level)

            # Memory ring (TUI consumes this later; uses LogEntry shape)
            entry = LogEntry(
                timestamp=timestamp,
                toolkit=toolkit or "<orchestrator>",
                tool=event,
                message=message + extra,
                level=level,
            )
            self._memory_logs.append(entry)

            # Also append to serve.log if enabled
            if self._serve_log_enabled:
                self._write_serve_log(human + "\n")
                # And one structured line for jq-friendly parsing
                self._write_serve_log_jsonl(record.to_dict())

    def _write_serve_log(self, line: str) -> None:
        """Append a raw line to serve.log. Fail silently."""
        try:
            with open(SERVE_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def _write_serve_log_jsonl(self, payload: Dict[str, Any]) -> None:
        """Append a JSON line to serve.log (one-per-line, alongside human lines)."""
        try:
            with open(SERVE_LOG_PATH, "a", encoding="utf-8") as f:
                f.write("# " + json.dumps(payload) + "\n")
        except Exception:
            pass

    def _write_serve_session_marker(self) -> None:
        """Write a banner identifying a new serve session."""
        bar = "═" * 63
        marker = (
            f"\n{bar}\n"
            f"serve session started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"pid {os.getpid()}\n"
            f"{bar}\n"
        )
        self._write_serve_log(marker)

    def _prune_serve_log_if_oversized(self) -> None:
        """If serve.log is over the size cap, keep the tail and discard the rest."""
        try:
            if not SERVE_LOG_PATH.exists():
                return
            size = SERVE_LOG_PATH.stat().st_size
            if size <= SERVE_LOG_MAX_BYTES:
                return
            # Read the last SERVE_LOG_TAIL_BYTES and rewrite the file.
            with open(SERVE_LOG_PATH, "rb") as f:
                f.seek(-SERVE_LOG_TAIL_BYTES, os.SEEK_END)
                tail = f.read()
            # Drop anything before the first newline so we don't start mid-line.
            nl = tail.find(b"\n")
            if nl != -1:
                tail = tail[nl + 1:]
            with open(SERVE_LOG_PATH, "wb") as f:
                f.write(b"# --- serve.log pruned to last ~5 MB ---\n")
                f.write(tail)
        except Exception:
            pass


# Global logger instance
_logger: Optional[ToolLogger] = None


def get_logger(*, serve_log: bool = False) -> ToolLogger:
    """
    Get the global logger instance.

    Args:
        serve_log: pass True from inside ``toolbase serve`` to enable
            writing tool calls and orchestrator events to ``serve.log``
            in addition to the daily files. If the singleton already
            exists with ``serve_log`` off, calling with ``serve_log=True``
            upgrades the existing instance (turns mirroring on in place)
            rather than silently dropping the request. That matters
            because callers like ``_log_project_discovered`` instantiate
            the logger off the hot path during serve startup; if their
            no-kwarg ``get_logger()`` wins the race, the orchestrator's
            later ``get_logger(serve_log=True)`` must still take effect.

    Returns:
        ToolLogger instance (singleton)
    """
    global _logger
    if _logger is None:
        _logger = ToolLogger(serve_log=serve_log)
        return _logger
    if serve_log:
        _logger.enable_serve_log()
    return _logger
