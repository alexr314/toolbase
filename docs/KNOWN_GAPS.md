# Known gaps

Doc/code mismatches and missing features surfaced while writing the user docs
(2026-05-28), by checking behavior against the source rather than the prose.

## Behavior gaps

- **`tb install` takes a single `NAME`.** No multi-name install (`tb install a b`
  fails). The v1 plan and README examples that show multiple names are wrong.
- **No manifest-restore / no-arg install.** `tb install` with no args errors;
  there's no `tb sync` or "install everything in `manifest.yaml`" command. A
  teammate reproducing a project installs each pinned toolkit manually
  (`tb install <name>@<version>`). The "clone + one command" reproduce story
  doesn't exist yet — candidate feature.
- **`tb config set` is `TOOLKIT KEY VALUE`** (space-separated), not `key=value`;
  `config` uses `--user`/`--project`/`--layer` (not `-g`/`-l`). Docs corrected.

## Stale prose to fix

- **README (`serve-curation-revamp` branch)** still has the multi-name-install
  and no-arg-reproduce inaccuracies above.
- **CLI `--help` tagline** reads "Toolbase - Scientific agentic tools made
  easy" — capital "T" (brand name should be lowercase `toolbase`) and a
  science-only framing at odds with the general-purpose positioning.

The user-facing docs site already reflects reality; the two items above are
open follow-ups.
