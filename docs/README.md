# Toolbase Package (CLI) — Docs Index

Implementation notes and design specs specific to the `toolbase` CLI package.
For project-wide forward-state, the binding architecture decisions, and the
backlog, see the parent `../../STATUS.md`; for this package's agent context see
[`../CLAUDE.md`](../CLAUDE.md).

## Design specs (live)

- [`SERVE_ARCHITECTURE.md`](SERVE_ARCHITECTURE.md) — how `toolbase serve` works
  (orchestrator + per-toolkit subprocess). NOTE: carries a "superseded" banner
  for the transport question — the orchestrator↔subprocess wire is persistent
  stdio now, not the HTTP-loopback the body describes.
- [`SETUP_SYSTEM_SPEC.md`](SETUP_SYSTEM_SPEC.md) — toolkit configuration &
  `setup.py` architecture (Phase 3C, file-first).
- [`SETUP_RECIPES.md`](SETUP_RECIPES.md) — copy-paste setup recipes for toolkit
  authors.
- [`ENVIRONMENTS.md`](ENVIRONMENTS.md) — the cache-plus-manifest environment
  model (`~/.toolbase/cache/...` + project `.toolbase/manifest.yaml`).
- [`PLATFORM_DECISIONS.md`](PLATFORM_DECISIONS.md) — platform architecture
  decisions and open questions (incl. the Docker Phase 3B direction). Cited from
  the project `STATUS.md` roadmap.

## Working notes (not in the repo)

Design drafts, proposals, audits, and gap lists are kept on disk and
gitignored rather than shipped — `docs/proposals/`, the snippet audits, and
`KNOWN_GAPS.md` are working documents, not things a reader of the public repo
should have to sort through. Promote one by removing its `.gitignore` entry
once it is finished and meant for an outside audience.

`docs/archive/` holds scitoolkit-era phase-completion summaries on the same
terms. (`PLATFORM_DECISIONS.md` was promoted out of the archive into `docs/`
because it's a live, still-cited spec rather than frozen history.)
