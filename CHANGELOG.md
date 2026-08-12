# Changelog

All notable changes to `toolbase` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

- **Skills are surfaced at the scope you connected.** `skill_target()` took no scope, so every adapter wrote to a user-global directory while the MCP server entry beside it went wherever `-u` / `-p` said. `tb connect codex -p` put the server in this repo's `.codex/config.toml` and the guides for its tools in front of every project's agent — and there was no way to say "these skills belong to this repo."

  All four harnesses read a project skill directory as well as a global one, which is the split `config_path` already models, so `skill_target(scope, project_root)` now takes the same pair:

  | harness | user | project |
  | --- | --- | --- |
  | Claude Code | `~/.claude/skills/` | `<root>/.claude/skills/` |
  | Codex | `$CODEX_HOME/skills/` | `<root>/.codex/skills/` |
  | Antigravity | `~/.gemini/config/skills/` | `<root>/.agents/skills/` |
  | OpenCode | `~/.config/opencode/command/` | `<root>/.opencode/command/` |

  `tb disconnect` clears the scope it unwired (`--all` clears both), and `tb uninstall` reaps a gone toolkit's guides from both. Harnesses read both scopes at once, so `tb connect` now says when the other scope is also holding toolbase skills — otherwise the duplicate only shows up in the harness. Codex's project-scope note gained the detail that its `.codex/skills/` load even before the project is trusted, unlike the `config.toml` next to them.

### Fixed

- **Skill slugs are lowercase-dash, like every other skill in the ecosystem.** The slug is what a harness *displays*, and toolbase's kept underscores — `heptapod__pythia_forward_run_cards` where Codex ships `openai-docs` and `skill-installer`, Claude Code ships `code-review`, and OpenCode documents "lowercase hyphen-separated" outright. `skills/run_cards.md`, `skills/run-cards/`, and `skills/Run Cards.md` now all surface as `<toolkit>__run-cards`.

  Case is deliberately not split on: `PythiaCards` stays one word, because nothing distinguishes it from `ArXiv`, where splitting would be wrong. Name skill files the way you want them to read.

  Both spellings are accepted by `tb activate` / `tb deactivate`, and the canonical one is what gets written, so a loadout can't accumulate two spellings of the same skill. Existing `skills.disabled` entries are matched canonically — a literal comparison would have silently stopped matching after this change and put a deactivated guide back in front of the agent, which is the exact failure the prune was added to end. New in `toolbase.skills`: `normalize_slug()` and `slugs_match()`.

- **A surfaced skill is named `<toolkit>__<skill>` in the harness, not whatever the author wrote.** The `<toolkit>__` namespace existed only as a directory name: harnesses display the frontmatter `name`, which was passed through verbatim. A guide whose author wrote prose there (`name: Writing Pythia 8 run cards for forward production`) showed up as that prose, so the namespace was invisible, two toolkits shipping an `mg5` guide were indistinguishable, and the name on screen was not the name `tb deactivate` accepts. Every harness documents the same convention — `name` matches the folder — so this is also what they expect.

  The author's `description` is never rewritten: it is the trigger text the model reads to decide when a skill applies, and it is synthesized only when absent (a skill without one is filtered out before it reaches the model). Other author keys survive; toolbase's own `bundle:` is dropped rather than leaked into a harness's frontmatter. Long descriptions are emitted on one line instead of YAML-wrapped at 80 columns.

- **A surfaced `SKILL.md` is a real file, not a symlink — Codex could not see one.** The dir layout symlinked a complete-frontmatter source so edits to an editable checkout showed up without re-connecting. Codex's skill scanner does not follow a symlinked `SKILL.md`: the skill was written, the connect reported success, `tb list -v` ticked it, and Codex silently never loaded it. Bisected against `codex app-server`'s `skills/list` — a name that doesn't match the folder, a `.toolbase-managed` marker, and `__` in the folder name are all fine; the symlink alone was fatal. Claude Code, Antigravity, and OpenCode all tolerate it, which is why it went unnoticed.

  Supporting files (`references/`, `scripts/`, assets) were per-child symlinks for the same reason and are now copies too. The cost is that an edit to an editable checkout's guide lands on the next `tb connect` rather than immediately — the same step every other state change already needs.

- **OpenCode skills go to OpenCode's skill directory.** OpenCode grew a skill loader (`**/SKILL.md` under `~/.config/opencode/skills` and a project's `.opencode/skills`, surfaced to the model by `description`), and toolbase was still writing flat `command/` prompt files — user-invoked `/<name>` slash commands the model never learns about, with `name` and `bundle` stripped out. Same shape of bug as the Codex prompts one, and fixed the same way: the real surface, frontmatter intact, supporting files alongside, with `command/` declared as a legacy target so the old files are cleared on the next connect.

  With this, all four harnesses use the dir layout. The `flat` layout survives only to clear the two retired surfaces.

- **A connect is a sync, not an append.** Surfacing only ever added. Everything that *stops* a skill from being surfaced — its toolkit dropping out of the active loadout, a `tb deactivate <toolkit>__<skill>`, a bundle's config gate closing, a new version deleting the guide — left the last-written copy on disk, still read by the harness and contradicting every read command that had stopped listing it. `tb deactivate calculator__old_guide` followed by `tb connect` left the guide exactly where it was; only a full `tb disconnect` ever cleared anything.

  `tb connect` now prunes the toolbase-owned entries it didn't just write, so a surface converges on the current answer instead of accumulating every answer it has ever given, and reports what it removed. Ownership is the same evidence the removal paths already used (an `OWNED_MARKER` file per dir, a manifest entry per flat file), so skills you wrote yourself are untouched. Surfacing stays best-effort per toolkit and a toolkit whose surfacing raised is excluded from the prune, so an unrelated error can't cost it the skills it already had. `--no-skills` now means "don't touch the skill surface" in both directions.

  New in `toolbase.skills`: `prune_skills(target, keep=..., skip_owners=...)` and `owned_slugs(target)`.

- **Codex skills go to Codex's skill directory.** Codex has a native skill concept now — `$CODEX_HOME/skills/<name>/SKILL.md`, the same shape Claude Code reads — and toolbase was still writing to the surface that predated it: flat `$CODEX_HOME/prompts/<toolkit>__<skill>.md` files. That surface is a user-invoked slash-command prompt, so nothing a skill said ever reached the model unless the user typed its name; the frontmatter carrying the description that decides *when* a skill applies was stripped on the way in; and a directory-form skill's `references/`, `scripts/` and assets were dropped silently, leaving guides pointing at files that weren't there.

  `tb connect codex` now surfaces into `$CODEX_HOME/skills/`, frontmatter intact and supporting files alongside. Both paths honour `$CODEX_HOME` (as does the `config.toml` the MCP entry is written to, which previously assumed `~/.codex` regardless).

  The old prompt files are cleared on the next `tb connect codex` / `tb disconnect codex` — Codex still reads them, so leaving them would list every skill twice. Only files toolbase owns per the `.toolbase-managed.json` manifest are removed. Adapters declare a moved-from surface via the new `HarnessAdapter.legacy_skill_targets()`.

- **`tb list -v` groups a bundle-scoped skill under its bundle.** A skill's `bundle:` frontmatter ties it to that bundle's availability exactly as a tool's does — the same gate drops both — but skills were listed in one trailing `[skills]` block whatever they were scoped to. The bundle header's `⚠ needs config:` note read as though it applied only to tools, and the skill's own `needs the X bundle` line sat far from the `[X]` it named.

  ```console
  $ tb list -v
      [mg5]  ⚠ needs config: mg5_path
        ✗ MadGraphFromRunCard
        ✗ ValidateProcess
        ✗ mg5 (skill)
      [skills]
        ✓ getting_started
  ```

  Skills scoped to no bundle keep the trailing block. `tb list --json` is unchanged in shape, but a deactivated skill now reports its `bundle` instead of `null`.

- **A skill scoped to a bundle that was never installed is no longer surfaced.** A subset install (`tb install heptapod[pdg]`) never pip-installs the other bundles' deps, so their tools can't be served whatever the config says — and `tb list -v` marked them accordingly. Skill surfacing checked only the config gate, so a guide to tools that provably weren't there was surfaced with a tick beside it. It is now gated by install scope too, matching the orchestrator and the tool rows.

---

## [0.14.0] — 2026-08-03

### Added

- **Skills are visible in the read commands.** Every command that *changed* a skill knew about them — `tb activate <toolkit>__<skill>`, `tb deactivate`, `tb install --no-skills` — and no command that *showed* anything did. A toolkit's skills were undiscoverable short of listing its `skills/` directory.

  That made `tb deactivate <toolkit>__<skill>` effectively write-only: skills default on and nothing displayed their state, so turning one off left no trace on any surface. The only evidence was a missing file under `~/.claude/skills/`.

  ```console
  $ tb list -v
      [skills]
        ✓ using_symbolic
        ✗ heavy_workflow   (needs the heavy bundle)
        ✗ old_guide        (deactivated — `tb activate calculator__old_guide`)
  ```

  `tb status` grows a `Skills` section for active toolkits, and says when no harness is wired — skills reach an agent by being copied into the harness's own directory at `tb connect` time, not served over MCP, so an active toolkit's skills are not in front of anyone until something is wired. `tb list --json` grows a `skills` key carrying each skill's own state; combine it with `active` for whether a skill is actually surfaced, since collapsing the two would lose the difference between a skill you turned off and a toolkit you never activated.

  A skill's state is computed once and rendered by every surface, applying the same filters `surface_skills` applies in the same order, so a listing shows what `tb connect` would actually write.

### Fixed

- **`tb activate <toolkit>__<skill>` claimed success on a skill that stayed hidden.** A skill scoped to a bundle is withheld while that bundle's config keys are unset, and activating manages a different filter — the per-skill blocklist — so it could report "already active" about something that would never reach an agent. It now names the bundle and the config keys it is waiting on.

- **`tb install --help` described surfacing it does not do.** Step 4 read "Surface the toolkit's skills into `~/.claude/skills/`", and `--no-skills` claimed to prevent that. Install only *reports* which skills a toolkit ships; `tb connect` surfaces them, per harness, and install's `--no-skills` suppresses the note and nothing else.

## [0.13.0] — 2026-08-03

### Added

- **`tb clean` removes installs whose Python is gone.** A virtual environment holds no interpreter of its own — it symlinks the one that built it — and `tb install` builds with whichever Python is running toolbase at the time. Install a toolkit from a conda env, delete that env later, and the toolkit is stranded from every project at once, because the cache holds one copy shared by all of them rather than one per environment.

  Nothing about the install is damaged: the toolkit's files and the whole of its `site-packages` are intact, and only the link to the base Python is gone. `tb clean` removes what can no longer run and prints the exact `tb install` to put each one back — which matters, because removing a slot also clears the pin naming its version, and that pin was the record of what had been there.

  ```console
  $ tb clean
  ✓ removed calculator@1.4.0

  Put them back with:
    tb install calculator@1.4.0
  ```

  Editable installs are reported but never removed: the slot is a symlink to a working copy, and taking one away is a larger decision than this command should make on its own. The `tb install -e <path>` that rebuilds it is printed instead.

- **A missing interpreter is reported before serve trips over it.** `tb status` lists it under `Issues`, `tb list` and serve discovery agree, and all three answer from one rule, so they cannot drift apart. Previously nothing noticed: metadata and toolkit files stay intact when the base Python disappears, so discovery called the slot ready, `tb status` listed it as healthy, and serve tried to spawn it on every startup and failed at connect with `mcp connect failed: [Errno 2] No such file or directory` — a message that names the interpreter and explains nothing. Found on a real machine, where one toolkit had been quietly unservable for weeks.

  Conda-based installs are exempt. They are named rather than pathed, and deciding whether one still exists means shelling out to conda, which is too slow for a listing; they still fail loudly at spawn.

### Fixed

- **`tb connect --help` told you to run a flag that does not exist.** Its examples used `-g`, which 0.12.0 replaced with `-u`; running one exits with `No such option: -g`.

- **`tb list --help` documented an interface that had been replaced** — a `*` legend for "pinned", a `<-` marker for the serving version, and pins living in `.toolbase/manifest.yaml`. Its sample is now copied from real output rather than written from memory, which is how the old one drifted.

  Both were found by sweeping every command's help text for removed flags and stale markers, then checking the sweep rather than trusting it: all 18 documented examples that carry flags were extracted and confirmed to parse.

- **`tb uninstall` could silently leave every pin in place.** The cleanup that clears version records is wrapped in a broad `except`, intended for absent or unreadable manifests, which also swallowed real errors into a dimmed note. A failure there leaves records naming versions that no longer exist, and those make serve skip the toolkit outright. It now reports as a warning and says what the consequence is.

### Changed

- **The end-to-end harnesses all pass, and CI enforces it.** Seven of the thirteen had been failing for months against changes nobody updated them for — none of them product bugs, all stale expectations: hardcoded snake_case tool names where orchestral's MCP layer serves PascalCase, config written from outside a project expecting the user layer when scoped commands default to the project, assertions that `project init` writes a `manifest.yaml` when the directory itself is the marker, and a bundle map treated as one bundle per tool when it is a list.

  One was failing at its own hand: it never left the repository root, and since `.toolbase/` is the project marker, the checkout is a project — so its own leftovers there supplied the config value the test asserts is unset. It graded its own residue, and differently in CI, which starts clean. The harnesses now run from their temporary trees, and CI fails if a run dirties the working copy.

  The unit suite was red on CI while green locally for one assertion, on a phrase Rich wrapped mid-line: where the break lands moves with the length of a temp path, and CI's shorter one split the phrase. Console width is pinned for the suite now — 76 assertions match multi-word phrases against captured output, and every one of them was a wrap away from the same failure.

- **The synthetic toolkit fixture is a calculator.** It stood for a real third-party toolkit, named throughout test data that has nothing to do with it. The flow under test is unchanged.

## [0.12.0] — 2026-08-02

Reworks how toolbase decides **which version of a toolkit serves**, and separates that decision from installing and from exposing. The model is now one rule with no exceptions: `install` places bits, `tb use` chooses a version, `tb activate` exposes tools. `install` previously did fragments of all three depending on flags, which is where most of the defects below came from.

### Changed (breaking)

- **Profiles are now loadouts, and they carry versions.** Two changes that only make sense together.

  `tb profile` is `tb loadout`, `--profile` is `--loadout`, the directory is `loadouts/`, and serve.yaml's key is `default.loadout`. "Profile" is overloaded to near-meaninglessness — shell, browser, AWS, user profiles — and it's already the word toolbench uses for this concept, so the two systems stop needing a translation.

  More substantially, a loadout now records which *build* of each toolkit it means, in a top-level `versions:` block. A loadout previously said which tools an agent got but not which build of them, so it was half a specification: share one and it resolves differently elsewhere, or drifts when someone bumps a toolkit. For a benchmark condition that's a silently invalid result. One file now answers both questions.

  ```yaml
  # .toolbase/loadouts/paper.yaml
  versions:
    heptapod: 2.4.0       # omit to take the fallback (newest installed)
  toolkits:
    heptapod:
      bundles: [pdg, analysis]
  ```

  The two blocks are siblings rather than nested because they layer by different rules. Curation shadows: a project's tool selection replaces the user's whole, because merging two curated sets yields a third that nobody designed. Versions layer per toolkit: a project that says nothing about a toolkit keeps whatever you chose machine-wide. Nesting the version inside its curation entry forces both to share the shadowing rule, and that is precisely what made `tb use` activate a toolkit and `tb deactivate` discard its version.

  `tb use` writes there rather than to a manifest, and `--private` gains a real destination: `<name>.local.yaml`, merged over its committed sibling toolkit by toolkit, field by field. That layer has to exist — an editable pin names a directory only your machine has, and committing one leaves a teammate with a dangling pin — so `tb use <toolkit>@editable` routes there by itself and says so.

  Pre-0.12 state keeps working, unmigrated: loadout discovery falls back to `profiles/` per scope, `default.profile` is read when `default.loadout` is unset, a nested `version:` inside a toolkit entry is still honoured, and manifest pins resolve underneath loadout entries. None of the old names or shapes are ever written, so files convert as they are touched.

- **`tb install` writes no manifest and takes no scope.** It puts a toolkit in the shared cache and stops. Previously every install wrote a pin, so a manifest accumulated bookkeeping nobody chose and you couldn't tell deliberate entries from incidental ones — which is where the orphaned pins, the pins that didn't apply, and the install/uninstall scope mismatch all came from. `tb use` is now the only command that records a version, so every entry is one somebody typed.

  Nothing is needed for the common case: with no recorded version, the newest installed one serves. Installing an older version therefore does not switch to it, and install says so rather than leaving you to notice:

  ```console
  $ tb install calculator@1.2.0
  ✓ Successfully installed calculator v1.2.0
  Note: 1.4.0 is what serves here (highest installed, no pin), not the 1.2.0 you just installed.
    To use it: tb use calculator@1.2.0
  ```

  `-u`, `-p` and `--private` are gone from `install`. `-a/--activate` stays and now follows `tb activate`'s own default (this project); pass `-u` to `tb activate` afterwards for the user-level loadout. `-e` no longer writes a private pin — see below.

- **`tb use` defaults to this project**, like every other scoped command. It defaulted to user scope, which meant that run from inside a repo with its own `.toolbase/` it wrote a version that the repo then ignored in favour of the highest installed — a success message for a no-op. `-u` still writes the user layer explicitly. Because an editable slot names a path only your machine has, `tb use -p <toolkit>@editable` routes to the private layer by itself rather than committing a pin that breaks for everyone else.

- **A project is any directory with a `.toolbase/`,** not one containing `.toolbase/manifest.yaml`. The marker was a file that commands had to fabricate in order to be found later, so whether a directory counted as a project depended on which command you happened to run first. The directory alone is the marker, and `~/.toolbase/` is explicitly excluded so your home directory does not become a project that shadows every other.

- **Editable installs are opt-in, and every surface says so.** An `editable` slot still loses the unpinned fallback to any numbered version — deliberately. The cache is user-wide (one `cache/<name>/editable/` shared by every directory), so if linking a checkout won by default, a single `tb install -e` would change what every agent session on the machine runs, and confining it again would mean pinning numbered versions in every *other* project. Losing by default makes it opt-in instead: `tb use <toolkit>@editable` selects the checkout exactly where you run it. It also keeps `tb install` exceptionless — no install changes what serves.

  What changed is the reporting, since the cost of opt-in is the "my edits do nothing" confusion. All three surfaces that can see it now name the fix: `tb install -e` says so the moment it links a losing checkout, `tb list` marks it on the toolkit, and the serve banner repeats it. The old advice pointed at hand-editing `manifest.local.yaml`; it now points at `tb use <toolkit>@editable`.

- **One scope vocabulary across every command: `--user` / `--project` / `--private`.** The CLI had grown two parallel spellings of the same axis — `-g/--global` and `-l/--local` on install/activate/connect, `--user`/`--project` on config — plus a `--local` that meant *committed project scope* in the first family and *gitignored machine layer* in the second. Same word, opposite answer to "will my teammates get this?"

  The three keys are now the same everywhere: `-u/--user` (`~/.toolbase/`), `-p/--project` (`<repo>/.toolbase/`, committed), and `--private` (`<repo>/.toolbase/*.local.yaml`, gitignored). `--global`, `-g`, `--local` and `-l` are **removed**, not deprecated. `--layer` takes `user|project|private`.

  This closes a gap as well as a redundancy: versions had no flag for the private layer at all — the machine-local file was reachable only implicitly, via `-e` or `tb use …@editable`. `tb use --private` now writes it directly.

  Every scoped command now defaults to this project; `install` takes no scope at all. `tb init`'s `-p` short for `--path` is gone, so `-p` means project everywhere; `--path` still works.

### Added

- **`tb status`** — one place that answers which context applies, what would serve, what can reach it, and what's broken. Run against a real machine during development it immediately surfaced two pins naming toolkits that were never installed; serve skips those silently and nothing in `tb list` said so.

  ```console
  $ tb status
  On project  myrepo/   (.toolbase/ above cwd)
  Loadout     default   (implicit default loadout)

  Active — served to agents
    heptapod               2.4.0      pinned

  Installed, not active
    arxiv-search           0.1.0

  Wired harnesses
    claude-code            .mcp.json

  Issues
    skilltk                0.1.0      pinned, not installed
  ```

  The Active and Wired sections always render, saying `(none)` when empty and naming the command that fills them — an absent section reads as "nothing to say here" when the fact that nothing is wired is usually the answer you needed.

- **`tb use <toolkit>@<version>` switches which installed version serves.** Only `install` could record a version, so moving between two already-installed ones meant re-running `tb install <name>@<version>` — which deletes the cache slot and rebuilds the environment from scratch, and, if you decline the "already installed, reinstall?" prompt, aborts before recording anything at all. There was no way to make the switch without paying for the rebuild. `tb use` writes the loadout's `versions:` block and nothing else: it does not download, rebuild, or activate. Bare `tb use <toolkit>` clears the entry; `-u`/`-p`/`--private` scope it. A private entry that would silently override the layer being written is removed, loudly.

- **`tb list` marks the version that actually serves.** The old `*` marked "pinned", which is silent in exactly the case that confuses people most: nothing is pinned, several versions are installed, and the highest-wins fallback picks one with no sign that a choice was made. A `➤` now sits on the slot `tb serve` would spawn, with the reason beside it, and the source of the versions is printed rather than left implicit. `--json` grows `serving` and `serving_reason` (`active` was already taken, and refers to the loadout, not the version).

- **Continuous integration.** The unit suite runs on Python 3.12 and 3.13 for every push, and the e2e harnesses run on pull requests. There was none before, which is why four separate shipped changes each broke a harness unnoticed. Harnesses known to be stale are listed explicitly and the job fails if one of them starts passing, so the list can't quietly become a place where failures go to be forgotten. A working-tree check fails the build if a test writes into the checkout.

### Changed

- **`tb list -v` groups tools by bundle.** One alphabetical list of 60 tools across 12 bundles said nothing about which capability groups a toolkit offers, and repeated each gating reason on every row of a gated bundle — heptapod printed "needs config: wolframscript_path" seven times. Each gate now sits once on its bundle header, next to the command that clears it, and the per-tool `[bundle: x]` tag is gone (bundle membership is the grouping; only multi-bundle tools keep a cross-reference). Uninstalled bundles collapse to a header plus their tool names, replacing the install-gated collapse threshold.

### Fixed

- **`tb list -v` said nothing at all about a toolkit `serve` would refuse to run.** It filtered discovery on "no skip reason" and returned silently when that matched nothing, so a toolkit with a pin naming an absent slot — an editable install removed outside `tb uninstall`, say — printed its version rows and no tools. It read as "this toolkit has no tools" rather than "this is about to fail". The reason is now printed, in both plain and verbose output.

- **`tb use` activated the toolkit as a side effect, and `tb deactivate` discarded its version.** Both followed from storing the version inside the curation entry: writing a version had to create that entry, which is what "activated" means, and removing the entry took the version with it. Deactivating and reactivating a toolkit silently moved it to the newest installed build. Versions live in their own block now, so the two operations no longer touch each other's state.

- **Creating a project dropped every user-level version choice.** A project loadout shadows the user's, which is right for curation and wrong for versions: running `tb activate` in a plain directory makes it a project, and every toolkit whose version you had chosen machine-wide silently jumped to the newest installed. The choice was still on disk, it had just stopped applying. Versions now layer user-then-project per toolkit, the same chain per-toolkit config already used, so a project overrides only what it actually names.

- **`tb setup` could configure a different version than `tb serve` runs.** It read pins from the committed manifest only, while serve reads the committed and machine-local layers merged, so an editable pin in the private layer sent the two commands at different slots. Both now resolve through one implementation; a version naming an uninstalled slot is an error rather than a silent fallback to another.

- **`tb status` looked for wired harnesses where clients never read.** It resolved the project root the way read-only commands do, landing on the user default, while `tb connect` writes `.mcp.json` into the working directory — so a correctly wired project reported nothing wired.

- **A user-scope version written from inside a project claimed success while changing nothing there.** A cwd inside a project resolves versions from *that* project, so `tb use kit@1.0.0` run from a repo with its own `.toolbase/` recorded a choice the repo then ignored in favour of the highest installed. It printed a plain success line. `tb use` now defaults to the project, and still says so when an explicit `-u` write won't apply where you are.

- **Project-scoped writes ignored `--project-dir`.** They walked up from the working directory regardless, so the documented project-discovery override didn't apply.

- **`tb uninstall` listed installed versions lexicographically** in its "not installed" error, putting `2.10.0` before `2.9.0` — the opposite of `tb list` and `tb use`, which sort numerically.

- **`tb uninstall` left a dangling pin in the user-level manifest.** Install pinned the default-project by default (it no longer pins at all), but `uninstall` only cleaned the *active* project's manifest — different files whenever you're inside a project with its own `.toolbase/`. Installing a toolkit from a repo and then uninstalling it there deleted the binaries while leaving `<name>@<version>` recorded globally, naming a version that no longer existed. Since a version naming an absent slot makes serve skip the toolkit outright, the toolkit then stayed unservable everywhere the default-project applies — including after reinstalling a *different* version, because the stale entry still won. Both roots' committed and private layers are now cleaned, in manifests and loadouts alike, and the message names which file it edited.

## [0.11.0] — 2026-07-30

### Added

- **Skills can now be directories, not just files.** A toolkit ships a skill either as `skills/<name>.md` or as `skills/<name>/SKILL.md` — the second form carrying `references/`, `scripts/` and assets beside the guide, which is how skills are written for Claude Code and Antigravity natively. Previously only the flat file was discovered: a directory-form skill was invisible everywhere (not surfaced, not counted, not validated, not reachable by `tb activate <toolkit>__<skill>`) while still shipping inside the package, so a skill written for Claude Code silently did nothing when dropped into a toolkit.

  Attachments travel to harnesses that take a skill as a directory (Claude Code, Antigravity), symlinked per file so edits to an editable install show up live and toolbase never writes into the author's toolkit. Harnesses that take a single prompt file (Codex, OpenCode) get the guide alone. A directory with no `SKILL.md` isn't a skill; `tb validate` now says so rather than ignoring it.

### Changed

- **The Antigravity project-scope warning now says what the CLI actually does.** 0.10.0 shipped a note claiming the IDE and SDK honor a workspace `.agents/mcp_config.json` while the CLI "discovers and then ignores" it, citing [antigravity-cli#60](https://github.com/google-antigravity/antigravity-cli/issues/60) — a report against `.antigravitycli/mcp_config.json`, a different path. Tested directly: with `.agents/mcp_config.json` wired and the `agy` CLI launched in that workspace, no server starts and its log never mentions MCP; the identical entry in the global root starts one at boot. The printed note, the adapter docs, and this changelog now state that, and attribute the IDE/SDK half to Google's documentation rather than to verification. Guidance is unchanged: on the CLI, connect with `-g`.

### Fixed

- **`tb validate` prints its hints on success.** Warnings — missing README, incomplete skill frontmatter, a skill directory with no `SKILL.md` — were only shown when validation *failed*, so a valid toolkit's hints were computed and discarded. That is exactly when the author can still act on them.

## [0.10.0] — 2026-07-30

### Added

- **`tb connect antigravity` wires Google Antigravity.** One adapter covers the `agy` CLI, the Antigravity IDE, and the SDK, because all three read the same MCP config. Scopes follow Antigravity's two customization roots: `-g` writes the global `~/.gemini/config/mcp_config.json`, project scope writes the workspace `.agents/mcp_config.json`. Skills surface natively — `~/.gemini/config/skills/<toolkit>__<skill>/SKILL.md`, the same directory-plus-frontmatter layout Claude Code uses, so a toolkit's guides are loaded on demand rather than reduced to slash commands.

  Two Antigravity quirks are handled: a zero-byte `mcp_config.json` (which the IDE creates on first launch) is read as an empty config rather than refused as malformed, and project scope prints a warning about workspace configs — see the 0.11.0 entry above, which corrects what that warning claims.

## [0.9.1] — 2026-07-29

### Changed

- **The `tb connect orchestral` scaffold now works in `sandbox/` rather than `workspace/`.** Same directory, new name — it sits beside `.toolbase/` and is where both the served toolkits and the scaffold's own file tools resolve relative paths. `sandbox` is the more common convention among toolkits that ship demo agents, and reads as "scratch space the agent owns" rather than "the project". Re-run `tb connect orchestral --force` to pick it up; an existing `workspace/` is left alone, so move or delete it yourself.

### Fixed

- **`tb connect` now wires the exact toolbase binary by default.** A bare `toolbase` command is resolved against the `PATH` the harness inherits, and that PATH can differ from the shell that ran `connect`: activating an environment, sourcing an rc file, opening a new tab, or launching a GUI app can change it. The result was a clean connection followed by a failed MCP server, fixable only by reconnecting with `--abspath`.

  The absolute path is now the default in **every** scope and for every installation, so the harness launches the same toolbase the user connected. `--portable` explicitly writes bare `toolbase` for a committed config that each teammate's PATH should resolve; `--abspath` remains an explicit spelling of the default. This is orthogonal to scope — the command string says how to find the binary, not which sessions get the tools — so project-scoped wiring and per-project curation are unchanged.

## [0.9.0] — 2026-07-29

### Changed

- **Toolkit hosts now run on MCP SDK 2.x.** The `mcp` dependency moved from `>=1.0,<2` to `>=2` and `orchestral-ai` from `>=1.4` to `>=1.10`, in both toolbase's own dependencies and the `HOST_RUNTIME_REQUIREMENTS` installed into every toolkit venv. toolbase never imports the SDK itself — it reaches it only through `orchestral.mcp` — so the major orchestral targets is the major toolbase needs, and the two bounds only make sense as a pair. orchestral 1.10 is a clean port to the 2.x API with no 1.x compatibility path and refuses to start against an older SDK; conversely orchestral <1.10 is built on the decorator API (`Server.list_tools` / `Server.call_tool`) that 2.0 removed. Since orchestral-ai declares no `mcp` constraint of its own, pinning both ends is what keeps pip off a mismatched pair.

  **Existing toolkit venvs were built against mcp 1.x and need `tb install <toolkit> --rebuild`** to pick up the new pair; until then their hosts fail at startup with `'Server' object has no attribute 'list_tools'`, surfacing as `mcp connect failed: unhandled errors in a TaskGroup`.

- Toolkit validation now names `orchestral-ai>=1.10.0` as the floor a toolkit's `requirements.txt` should declare. The check itself is unchanged (still presence-only, not a version comparison).

## [0.8.1] — 2026-07-27

### Fixed

- **`tb connect --abspath` no longer silently falls back to a bare command.** It resolved the binary with `shutil.which("toolbase")`, which returns `None` exactly when toolbase isn't on PATH at connect time (a venv/conda install, or toolbase invoked by absolute path) — the very case `--abspath` exists for — so it quietly wrote a bare `toolbase` that the harness then couldn't launch. It now resolves the toolbase beside the running interpreter first (`sys.executable`'s dir), falling back to `which` then bare.

### Changed

- **`tb connect` command default is now scope-aware.** User-scope connects (`-g`, a machine-local config that is never committed) default to the **absolute** toolbase path so the harness always finds it — important because a harness process (e.g. a GUI app, or a shell without your env activated) often doesn't have a venv/conda `bin` on PATH. Project-scope connects (git-committed, shared) stay **bare** `toolbase` for portability, and now print a note when toolbase is env-installed that a bare command may not resolve for the harness (suggesting `--abspath`, `-g`, or a login-PATH install). New `--portable` flag forces the bare command in any scope; `--abspath` still forces the absolute path.

## [0.8.0] — 2026-07-27

### Added

- **`tb connect opencode` — OpenCode is now a supported harness.** A new `OpenCodeAdapter` wires toolbase into OpenCode's `mcp` block (`opencode.json` / `opencode.jsonc`, user `~/.config/opencode/` honoring `$XDG_CONFIG_HOME`, or project root), writing a local stdio server in OpenCode's single-`command`-array form (`{"type":"local","command":["toolbase","serve"],"enabled":true}`) with a non-destructive, atomic merge that preserves `$schema` and other servers. An existing comment-free `.jsonc` is edited in place; a `.jsonc` that actually carries comments is refused with guidance rather than clobbered. Registering the adapter also lights up `tb disconnect opencode`, `--harnesses`, and `--list`. OpenCode skills surface as `~/.config/opencode/command/<toolkit>__<skill>.md` slash-command prompts (via the existing `SkillTarget` flat layout), keeping only the `description` frontmatter OpenCode shows in its TUI — enabled by a new `SkillTarget.frontmatter_keys` that narrows the emitted block to named keys.

## [0.7.0] — 2026-07-26

### Added

- **`tb connect` now surfaces skills per-harness, and Codex is supported natively.** A toolkit's `skills/*.md` guides are surfaced into the harness you connect, not at install time against a hardcoded path. `tb connect <harness>` surfaces the *activated* toolkits' skills (the same set whose tools are served) into that harness's location; `tb disconnect` / `tb connect --remove` clears them; `--no-skills` wires the MCP server only. Two layouts, one per adapter: **Claude Code** → `~/.claude/skills/<toolkit>__<skill>/SKILL.md` (frontmatter preserved; auto-surfaced to the model and exposed as a `/<name>` slash command), **Codex** → `~/.codex/prompts/<toolkit>__<skill>.md` (frontmatter stripped to the body; a user-invoked `/<name>` slash-command prompt). Adapters declare their surface via a new `HarnessAdapter.skill_target()` returning a `SkillTarget`; `skills.surface_skills` / `unsurface_skills` / `unsurface_all` generalize the copy/gate/ownership logic across layouts. Flat-layout ownership is tracked by a `.toolbase-managed.json` manifest (a flat file has no dir for the `OWNED_MARKER`), so user-authored prompts with the same prefix are never removed.
- **Skill packs (skills-only toolkits) are now first-class.** A toolkit may ship only `skills/*.md` guides and declare no tools. `tools:` in `toolkit.yaml` is now optional (defaults to empty), and the `orchestral-ai` requirement is waived when a toolkit has no tools (a skill pack runs no Orchestral tool framework). Such a toolkit validates, installs, activates, and surfaces its skills through `tb connect` like any other; the serve orchestrator recognizes it (`_toolkit_is_skills_only`: no declared tools *and* no implicit `tools/__init__.py`) and skips launching a host for it — while keeping it fully discoverable so `tb activate` / `tb connect` still surface its guides. When the *only* active toolkits are skill packs, `tb serve` explains there is nothing to serve over MCP and points at `tb connect`, rather than failing with an opaque error. This pairs with per-skill toggling to make a curated pack of standalone skills a supported unit.
- **Per-skill enable/disable, via the existing activation grammar.** `tb deactivate <toolkit>__<skill>` blocklists a single guide; `tb activate <toolkit>__<skill>` restores it. A `<toolkit>__<name>` item is resolved to a **skill** when it matches a surfaced skill slug and *not* a tool (on a genuine name collision the tool wins and a note is printed, preserving existing tool references). Skills are on by default when the toolkit is active, so a profile records only a per-toolkit blocklist — a new `skills: { disabled: [...] }` block under each toolkit entry (`ToolkitSelection.disabled_skills`). Skill surfacing (`surface_skills`, and hence `tb connect`) subtracts these slugs, the per-skill analog of bundle gating. Install/connect now print each guide by its `<toolkit>__<slug>` toggle name so the slug is discoverable. This makes it practical to ship a toolkit as a bundle of individually toggleable skills.

### Changed

- **Skill surfacing moved off `tb install`.** Installing a toolkit now only *reports* that it ships skills and points at `tb connect`; it no longer writes into `~/.claude/skills/`. Skills follow the harness you connect (and the toolkits you've activated), symmetric with how MCP tools are wired. Uninstalling a toolkit reaps its surfaced skills from every harness target (Claude Code and Codex), not just Claude. Back-compat: `install_skills_for_toolkit` / `uninstall_skills_for_toolkit` remain as Claude-dir wrappers over the new `SkillTarget` API.

## [0.6.1] — 2026-07-24

### Fixed

- `read_versioned_yaml` / `write_versioned_yaml` (`envs/schema.py`) and the setup-storage helpers (`setup/storage.py`) no longer share a single module-level `ruamel.yaml.YAML()` instance across calls. A `YAML()` object holds mutable parse state and is not thread-safe, so concurrent callers (e.g. two trials resolving an environment in parallel, which parses every cached toolkit's `.install_meta.yaml`) intermittently corrupted it into a misleading `'NoneType' object has no attribute 'anchor'` or a spurious `DuplicateKeyError` against a file that parses cleanly single-threaded. Each read and write now constructs its own loader/dumper. ([#39](https://github.com/alexr314/toolbase/pull/39))

## [0.2.1] — 2026-06-06

### Fixed

- `tb --version` and `toolbase.__version__` now report the installed package version instead of a stale hardcoded `0.1.0`. Both are sourced from `importlib.metadata.version("toolbase")`, so future releases stay in sync with `pyproject.toml` automatically.

## [0.2.0] — 2026-06-05

Serve/curation revamp. **Breaking** (v0, clean cutover — no compatibility aliases).

### Added

- `toolbase connect <client>` / `disconnect <client>` — write (or remove) the toolbase MCP entry in an agent client's config, replacing the manual JSON copy-paste. Claude Code in v1 (`~/.claude.json` for user scope, `.mcp.json` for `-l` project scope), via a pluggable adapter so Codex / Orchestral can follow. `--list` shows where toolbase is wired; `--clients` lists targets; `--profile` also sets the active profile; `--abspath` writes an absolute binary path. Non-destructive merge, atomic write.
- `toolbase activate` / `deactivate <toolkit | toolkit/bundle | toolkit__tool>` — expose or hide tools in the active profile. The casual-tier surface; users never need to learn "profiles" to curate.
- **Profiles** — named curated tool sets, one file per profile under `<scope>/.toolbase/profiles/<name>.yaml`. `toolbase profile <list|show|create|edit|delete|set-default|path|tools>` manages them (replaces `toolbase groups`).
- `toolbase install -a/--activate` — install and activate in one step.
- `toolbase list -v` — per-tool served/hidden view with bundle + config-gating annotations; `tb list` now marks each toolkit active/inactive, and `--json` gains an `active` field.
- `toolbase config init <toolkit> [--user | --project] [--force]` — scaffold a commented YAML config file from a toolkit's `config:` schema. Defaults to the project layer (matches `config set` / `unset`); pass `--user` for the user layer. Required fields land as `<NEEDS VALUE>`; optional fields with defaults get their default; optional fields without defaults are commented out so the full schema is visible.
- **Workspace-aware schema defaults.** `path` and `string` fields in a toolkit's `config:` block may use `${CWD}` (the orchestrator's `os.getcwd()` at serve time — i.e. the harness's launch directory, where the agent is working) or `${PROJECT_ROOT}` (the discovered `.toolbase/` parent, or `${CWD}` if there is none). Composition works (`${CWD}/scratch`). Unknown templates are rejected at schema parse time. `tb config show` renders templates alongside their current expansion.
- **Multi-bundle tool membership.** A tool's `bundle:` field now accepts either a single name or a list (`bundle: [a, b]`); a multi-bundle tool is served if **any** of its bundles is available and counts as in-profile if any of its bundles is in the profile's allowlist.
- **Per-bundle dependencies.** A toolkit author can declare `deps: [pip-spec]` on each bundle alongside the existing `requires:` (config-key gate). The toolkit's `requirements.txt` stays the always-installed base; bundle `deps:` add on top when the user installs that bundle.
- **Install-time bundle selection: `tb install <toolkit>[a,b]` (pip-extras style) and `--bundle a` (flag form).** Pip-installs only the selected bundles' `deps:` on top of `requirements.txt` rather than every bundle's deps. Re-installing with new bundles is **additive** (pip-like): pip-installs the new bundles' deps into the existing venv without rebuilding. `--rebuild` forces destructive reinstall. Cache metadata (`.install_meta.yaml`) and project manifest entries record the installed bundle set; serve filters tools whose bundles are entirely outside the installed set, with a one-line summary at startup per toolkit.
- **Subset-install visibility in `tb list`.** Version rows now end with `[subset: a, b]` when only some bundles' deps are installed (`[subset: (base only)]` for an explicit empty subset). `--json` gains an `installed_bundles` field (`null` for a full install, list for a subset). `tb list -v` annotates per-tool why a tool is hidden when its bundle isn't in the install set: `(skipped: bundle X not installed)` — multi-bundle plural `(skipped: bundles a, b not installed)`. Install-scope wins over the existing config-gating annotation since install-scope strips the deps that config-gating would later check. When 6+ tools would be install-gated in a single toolkit (large toolkits with bundle subsets — heptapod's 50-tool/8-bundle case prompted this), they collapse into a single dim summary line `(+N tools in uninstalled bundles: a, b, … — add with tb install <name>[<bundle>])` to keep the verbose output scannable. Config-gated tools stay inline since they're one `tb config set` away rather than a reinstall.
- **Author-controlled tool display names.** A `tools[]` entry in `toolkit.yaml` may now carry an optional `display_name:` field that overrides the agent-visible name on the MCP wire (after the orchestrator's `<toolkit>__` prefix). When absent, the default is the Python class name with the trailing `Tool` suffix stripped, PascalCase preserved — so `InspireSearchTool` advertises as `heptapod__InspireSearch`. Explicit `display_name: search_papers` would advertise as `heptapod__search_papers`. Precedence: yaml `display_name:` > `@define_tool(display_name=...)` in code > derived default. The yaml layer wins because that's what the registry sees and what an author editing the file directly expects to take effect.

### Changed

- **Nothing-active by default.** Installing a toolkit places it in the cache but serves nothing until you `activate` it (conda-style: install ≠ activate). `tb serve` resolves an active profile — there is no "serve everything" fallback.
- `serve.yaml` is now defaults-only: `default.profile` (the active profile) and `default.disabled` (absolute blocklists), with a two-layer user→project merge.
- **Vocabulary:** the author-side intra-toolkit grouping is now a **bundle** (was `tool_groups:` / per-tool `group:`); the user-side curated subset is now a **profile** (was the `groups:` block in `serve.yaml`). The developer unit stays a **toolkit**. `tb serve --enable-bundle` replaces `--enable-group`.
- **Resolved state-config is injected at tool construction time.** Tools declared with required `StateField`s (e.g. a `base_directory` the toolkit author marks `required: true`) no longer fail with a pydantic `ValidationError` on serve startup; values flow from `~/.toolbase/config/<toolkit>.yaml` (and project-layer overrides) into the tool constructor via `_import_explicit_tools`. Required fields with a schema default — literal or template — are now satisfied by the default; previously the default was ignored and the field was flagged missing.
- **Per-tool failures during import / construction now skip just that tool**, emitting a structured `tool_import_skipped` log line to the per-toolkit log. A single misconfigured tool no longer takes down its sibling tools or the whole toolkit host.
- **Agent-visible tool names are now PascalCase by default** (breaking on the wire — old: `heptapod__inspiresearch`, new: `heptapod__InspireSearch`). The toolkit host now sets each instance's `_mcp_display_name` to the class name with the `Tool` suffix stripped (PascalCase preserved), and calls `MCPServer(use_display_names=True)` so MCP advertises it. The previous default — `cls.__name__.removesuffix("Tool").lower()` from `BaseTool.get_name()` — collapsed word boundaries into a single lowercase blob that was both harder for the agent to read and impossible to customise per-tool short of subclassing. Agents that have hard-coded the old lowered form (logs, harness configs, scripts) need updating; agents that read the tool list each turn (Claude Code, Codex) adapt automatically.
- **CLI startup is faster.** `tb --help` / `tb list` and similar no-network commands dropped from ~290 ms to ~50 ms warm by lazy-importing `requests`, `rich.syntax`/`pygments`, `rich.panel` / `table` / `progress`, and dropping a dead `Syntax` import. Heavy modules load only when commands that need them run.
- `config_dir()` and `project_config_dir()` are pure path resolvers; they no longer `mkdir(parents=True, exist_ok=True)` as a side effect. Writers (`save_config` etc.) create parents lazily at write time, so a layered path lookup no longer leaves an empty `<project>/.toolbase/config/` dir behind that looks like a half-done install.

### Fixed

- **Orchestrator's per-tool install-scope and config-gating filter actually fires.** `tb serve` reads each toolkit's `toolkit.yaml` to build a `name_to_bundles` lookup (which bundles each tool belongs to) and consults it for every tool the host advertises. The lookup was keyed by toolkit.yaml's `tools[].name` field — the PascalCase BaseTool subclass name (e.g. `InspireSearchTool`). But the toolkit host calls `orchestral.mcp.MCPServer(..., use_display_names=False)`, which registers each tool under `BaseTool.get_name() = cls.__name__.removesuffix("Tool").lower()` — so MCP advertises `inspiresearch`, not `InspireSearchTool`. Every `name_to_bundles.get(host_advertised_name)` missed → `tool_bundles` came back `[]` → both the install-scope gate and the config-gate short-circuited (they no-op on empty bundle membership). Result: a `tb install heptapod[inspire,pdg]` subset install still surfaced ~30 tools instead of the expected ~14, including tools from bundles whose pip deps weren't installed — those would just blow up at the host's import step with a `tool_import_skipped` log line, but the orchestrator continued to advertise the rest. Normalised `name_to_bundles` keys to match the MCP form so the filter works as documented.
- **`tb config init` scaffold no longer produces unparseable multi-document YAML.** Defaults with non-trivial values — `path` template defaults like `${CWD}`, string/integer/secret defaults — were being rendered via `yaml.safe_dump(scalar)` whose output appends a `\n...` document-end marker that the previous `.strip()` only partially trimmed (trailing newline only, not the marker). The resulting file looked like one document but parsed as two, so the orchestrator dropped the toolkit at serve startup with `config incomplete — invalid: <file> (failed to parse ...: expected a single document in the stream)` and the harness reported `Failed to reconnect to toolbase: -32000` with no obvious cause. Existing broken files don't auto-repair — delete the bare `...` line manually or re-run `tb config init --force` to regenerate.
- **Partial-install cache slots no longer wedge subsequent `tb install` invocations.** A Ctrl-C during a long pip install (heavy bundle deps can take minutes) used to leave the cache slot with source files but no `.install_meta.yaml`. The next install with a bundle subset (`tb install foo[a,b]`) matched the "already installed with all bundles" branch, printed a misleading message about needing `--rebuild`, and exited 0 without doing anything. Two-part fix: (1) the fresh-install pipeline (source → env setup → meta write) is now wrapped in `try/finally` keyed on a success flag, so any interrupt or exception before meta-write removes the slot; (2) the collision check explicitly detects a missing `.install_meta.yaml` and treats it as a corrupted slot — auto-clean and proceed as fresh install rather than no-op.

### Removed

- `toolbase groups` and the `groups:` block in `serve.yaml` (replaced by profiles).
- `tb serve` positional toolkit names and the `--group` / `--enable-tool` / `--disable-tool` one-shot flags (curation now lives in profiles; `--profile` selects one).

---

## [0.1.0] — 2026-05-22

Initial Toolbase release. Toolbase is the community registry and CLI for AI agent toolkits — a **toolkit** is the publishable unit, and each toolkit bundles one or more **tools** that agents call over the [Model Context Protocol](https://modelcontextprotocol.io). You author and ship toolkits, install them into isolated environments, and serve them to coding agents (Claude Code, Codex) or any MCP client.

> Toolbase began as `scitoolkit`. The code is mature — it shipped across nine `scitoolkit` releases (0.1.0–0.6.1) over two weeks — but `toolbase` is a new, general-purpose package on PyPI, not a rename of the published `scitoolkit`. This entry is the cumulative feature set as of the first Toolbase release; the granular pre-rebrand release notes live with the `scitoolkit` project.

### Authoring and publishing

- `toolbase init` — scaffold a toolkit from template (`--with-setup` for toolkits that need a `setup.py`).
- `toolbase ingest` — register tools from existing source. Re-running over a directory that already has a `toolkit.yaml` merges (new tools appended, hand-edited entries preserved byte-for-byte) rather than overwriting; `--prune` removes stale entries, `--force` rebuilds from scratch.
- `toolbase create` — reserve a toolkit name on the registry without uploading code (optional; `publish` auto-registers on first run).
- `toolbase validate` — Pydantic-based pre-publish structural checks.
- `toolbase login` — browser-flow auth that stores a per-user token good for any toolkit you own or collaborate on. Legacy per-toolkit tokens (`toolbase login <toolkit>`) are still accepted but deprecated. `whoami` / `logout` round out auth.
- `toolbase publish [--dry-run]` — package and upload to the registry; auto-registers the name on first run, and blocks "version already exists" / "version decrease" before upload.

### Installing and managing

- `toolbase search` — find toolkits on the registry.
- `toolbase install <name|path>` — download (or build from a local path), extract, and set up an isolated environment (venv or conda, auto-detected). Scope flags: `-g` (global, the default), `-l` (pin into the current project's `.toolbase/manifest.yaml`), `-e <path>` (editable — symlink a local source into the cache so `serve` loads tools live, the `pip install -e .` parallel). Multiple versions of a toolkit coexist in the global cache; the binary lives once in the shared cache and the manifest scope is independent of file location.
- `toolbase list` / `toolbase uninstall <name>` — manage installed toolkits.

### Serving

- `toolbase serve` — multi-toolkit MCP aggregator (stdio). Each installed toolkit runs in its own subprocess in its own Python environment; the orchestrator aggregates them and exposes the union as a single MCP server. A crashed toolkit auto-restarts with exponential backoff and doesn't take the orchestrator down. Supports positional toolkit names, `--group`, `--enable-tool`, `--disable-tool`, `--dry-run`, `--call-timeout`.
- `toolbase groups` — manage named tool subsets that span toolkits.
- `toolbase logs` — tail the serve log with Rich coloring.

### Configuration and setup

- `toolbase config <show|edit|path|set|unset|validate>` — manage per-toolkit config at `~/.toolbase/config/<toolkit>.yaml`. Toolkits declare a `config:` block in `toolkit.yaml` (seven types: `string`, `secret`, `path`, `integer`, `float`, `boolean`, `choice`); the human-editable file is the canonical source, prompts are scaffolding.
- `toolbase setup <toolkit>` (`--reset`, `--check`) — run a toolkit's `setup.py` for involved setup: full prompts, resumable SHA256-verified downloads with auto-extraction (tar/zip, zip-slip defended), and derived-state writes via `ctx.set_config(...)`.

### Platform

- **Multi-tier execution:** same-Python toolkits run in venv, different-Python toolkits run under conda (auto-detected). Docker mode is detected and refused with a clear "coming in Phase 3B" message.
- **HTTP-loopback architecture** between the orchestrator and per-toolkit subprocesses.
- **Per-tool selection** per serve session or persistently in `~/.toolbase/serve.yaml`.
- **Skills surfacing:** a toolkit's `skills/*.md` files are auto-mirrored to `~/.claude/skills/` (symlinked on POSIX for live edits, copied on Windows) so Claude Code discovers them.
- **Agent-friendly:** every state-modifying command supports `--yes`, `--no`, `--no-input`; non-TTY stdin auto-applies non-interactive behavior.
- **`tb` alias:** every command is available as `tb` as well as `toolbase`.
- Python 3.12+ required.
