"""The bundle's viewer: one static ``index.html`` with its CSS, JS and data
inlined, so it opens from disk with no server and no network.

``viewer.html`` is the page, ``viewer.css`` and ``viewer.js`` (plain
JavaScript, no library) are inlined into it, and the bundle's graph and file
index go into a ``<script type="application/json">`` block.
"""

from __future__ import annotations

import html as _html
import json
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent


def _asset(name: str) -> str:
    return (HERE / name).read_text(encoding="utf-8")


def compact_files(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """files.json rows as ``{"dirs": [...], "rows": [[dir index, name, bytes,
    sha256, mtime, origin, generator, basis, node, copied, present,
    before_trace], ...]}``: directories are shared, which halves the size for
    a sandbox of tens of thousands of files."""
    dirs: Dict[str, int] = {}
    out = []
    for r in rows:
        d, _, name = r["path"].rpartition("/")
        i = dirs.setdefault(d, len(dirs))
        gen = (r.get("generated_by") or [None])[0]
        out.append([i, name, r.get("bytes"), r.get("sha256"), r.get("mtime"), r.get("origin"),
                    gen, r.get("basis"), r.get("node"), 1 if r.get("copied") else 0,
                    1 if r.get("present") else 0, 1 if r.get("before_trace") else 0])
    return {"dirs": list(dirs), "rows": out}


def embed_json(data: Any) -> str:
    """JSON safe inside a ``<script>`` element: no ``</`` and no ``<!--``."""
    s = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    return s.replace("<!--", "\\u003c!--").replace("</", "<\\/")


def render(trace_doc: Dict[str, Any], file_rows: List[Dict[str, Any]]) -> str:
    data = {"meta": trace_doc.get("meta", {}), "nodes": trace_doc.get("nodes", []),
            "edges": trace_doc.get("edges", []), "files": compact_files(file_rows)}
    page = _asset("viewer.html")
    title = _html.escape(str(data["meta"].get("session") or ""))
    head, _, rest = page.partition("/*__CSS__*/")
    mid, _, tail = rest.partition("/*__JS__*/")
    pre_data, _, post_data = mid.partition("__DATA__")
    head = head.replace("__TITLE__", title)
    return "".join([head, _asset("viewer.css"), pre_data, embed_json(data), post_data,
                    _asset("viewer.js"), tail])
