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

## Proposals

- [`proposals/PORTABLE_GROUP_MANIFESTS.md`](proposals/PORTABLE_GROUP_MANIFESTS.md)

## Audits

- [`DOCS_SNIPPET_AUDIT_2026-05-06.md`](DOCS_SNIPPET_AUDIT_2026-05-06.md) —
  Phase 3C docs-snippet close-out audit.

## Frozen history (not in the repo)

`docs/archive/` holds scitoolkit-era phase-completion summaries. It is
gitignored — kept on disk as local history, not shipped. (`PLATFORM_DECISIONS.md`
was promoted out of the archive into `docs/` because it's a live, still-cited
spec rather than frozen history.)
