# AGENTS.md

Raven AI-collaboration spec. **Read this file before making any code change in this repo.**

Scope: Codex / Claude Code / Claude API / any AI-assisted work. When a rule here conflicts with an ad-hoc instruction in conversation, **this file wins** — unless the user *explicitly* says "ignore rule X in AGENTS.md".

Hard constraints only (violations get reverted / rejected). Soft suggestions and style preferences belong in personal notes or conversation, not here. See the [Maintenance](#maintenance) note before adding sections — shorter is better.

| # | Section | Gist |
|---|---|---|
| 1 | [Code comments](#1-code-comments) | Don't comment unless necessary; source in English (named exemption zones) |
| 2 | [Branch naming](#2-branch-naming) | `<type>/<snake_desc>`; confirm base before cutting |
| 3 | [Commits](#3-commits-conventional-commits) | Conventional Commits, all-English, `Co-authored-by` trailer |
| 4 | [Dependencies](#4-dependencies-uv-only) | `uv` only — never `pip` / hand-edit lockfile |
| 5 | [Tests](#5-tests) | `uv run pytest`; strict file-naming |
| 6 | [Domain terms](#6-domain-terms) | Consult `CONTEXT-MAP.md` before naming; use canonical terms |
| 7 | [Repository assets](#7-repository-assets) | No report assets, web artifacts, or large files in PRs |

---

## 1. Code comments

### §1.1 Top rule: don't add comments unless necessary

- Every new code file must document its purpose in English with an appropriate
  module docstring or equivalent file-level documentation. Inline comments
  still follow the necessity rules below.
- Match the style of surrounding lines. If neighboring code has no comments, **don't** add one to your new line.
- Comment **only** when:
  - the logic is non-obvious;
  - there's a hidden constraint (e.g. call-order sensitivity, a caller must do X first);
  - you need to explain **why**, not **what** (the name already says what).
- **Don't** add comments that:
  - describe what the code does (`# Increment counter` next to `counter += 1`);
  - mark edits (`# ← new` / `# changed this line`);
  - reference a PR / Issue / locally-visible-only doc path (`# Refs: ...` — invisible to others);
  - describe transient task context (`# For the X bug` — stale once the task is done).

### §1.2 When a comment is required, write it in English

- Repo source comments **must not be in another language** — keep comment language consistent across the repo.

### §1.3 Source language: English, with named exemption zones

- The English rule covers **all repo source**, not just comments: string constants, prompts,
  log messages, test fixtures, docs. Non-English content anywhere else gets reverted.
- **Exemption zones** (owner-signed) -- CJK is admissible only here, and only as capability
  data the feature itself needs. One zone per bullet; the leading backticked path is a
  prefix unless marked as an exact file, and a contract test holds the lists equal:
  - `plugins-dist/ppt-engine/` -- the Chinese-deck engine: caption markers, font names,
    language words the model must see, the skill corpus;
  - `tests/test_ppt_engine_*` -- the deck engine's fixtures;
  - `plugins-dist/design-engine/` -- the visual-design engine (owner-signed 0904): the
    fifteen-domain zh skill corpus;
  - `tests/test_design_engine_*` -- the design engine's fixtures;
  - `docs-site/` -- the bilingual documentation site (owner-signed 0918): the
    language-switcher label and any other display string the site config must
    render in Chinese; the site's own pages are already exempt as `*.md`;
  - `raven/i18n/` -- the zh catalog and lexicon;
  - `raven/templates/prompts/zh/` -- the zh prompt pack;
  - `skills/git-story-film/examples/raven-story/zh/` -- the Chinese film variant's
    captions and on-screen labels (owner-approved);
  - `skills/git-story-film/examples/raven-story/storyboard/storyboard.html` -- exact
    file: bilingual reference captions (owner-approved);
  - `skills/git-story-film/scripts/storyboard.py` -- exact file: localized UI labels
    and language-name keys required by the storyboard generator (owner-approved);
  - `tests/test_i18n_*` -- zh-i18n test fixtures: a CJK fixture is admissible **only when
    the test exercises zh functionality**, and the machine-checkable proxy for that is this
    file-name prefix plus an import of `raven.i18n` or a zh plugin module (`raven_ppt` /
    `raven_design`); a file with the prefix but no such import loses the exemption, and a
    fixture that would pass equally well in English is not exempt.
- Markdown files self-govern wherever they live (owner rule; covers `README.zh-CN.md` and
  `docs/` prose): the gate exempts `*.md` by suffix. That suffix is the only blanket rule --
  a non-markdown file under `docs/` has no fallback exemption.
- Machine enforcement: `scripts/check_source_language.py` gates every PR's added lines
  (`make check-source-language`), the way `make check-large-files` enforces section 7. A
  CJK-adding line passes only through one of: a zone prefix or exact file above, the `*.md` suffix, the
  `tests/test_i18n_*` prefix with its import proof, the relocation pass (the same CJK run
  was removed elsewhere in the same diff), or the carrier pass (the file already carried
  CJK at the base revision) -- so relocating existing CJK and editing a file that already
  carries CJK stay legal.

### §1.4 Examples

❌ Non-English review comment copied straight into source:

```python
self.logger = logger.bind(channel=self.name)   # ← new
```

❌ Neighbors have no comments, yet the new line adds a meaningless one:

```python
def __init__(self, config: Any, bus: MessageBus):
    self.config = config
    self.bus = bus
    self._running = False
    self.logger = logger.bind(channel=self.name)   # ← drop this comment
```

✅ Clean, no comment, consistent:

```python
def __init__(self, config: Any, bus: MessageBus):
    self.config = config
    self.bus = bus
    self._running = False
    self.logger = logger.bind(channel=self.name)
```

✅ Rare case that genuinely needs a *why*, in English:

```python
# Bind channel name into logger context so every log entry auto-tags channel.
self.logger = logger.bind(channel=self.name)
```

---

## 2. Branch naming

### §2.1 Format

`<type>/<short-desc>`

| type | Use |
|---|---|
| `feat` | New feature |
| `fix` | Bug fix |
| `refactor` | Refactor (not a feature, not a bug fix) |
| `perf` | Performance |
| `chore` | Misc (deps bump, doc structure, etc.) |
| `docs` | Docs only |
| `test` | Tests only |

`short-desc`: **snake_case**, English, 3–5 words describing the change.

| ✅ Good | ❌ Bad |
|---|---|
| `feat/whatsapp_lid_mapping` | `feat/优化` |
| `fix/cron_dst_transition` | `bugfix` |
| `refactor/cli_cron_sentinel` | `huangjie-test` |
| `chore/upgrade_uv` | `tmp` |

### §2.2 Confirm the base before cutting

- Before cutting any branch (`fix` / `feat` / `refactor` / anything), **ask the user which base to cut from** — don't pick one silently.
- If unspecified, default to **`main`** (the integration branch).
- Flow: `git fetch origin main`, then cut from the latest tip.
- Combined with the branch-first rule: **confirm base + cut the branch, then start editing** — never write on a working branch and carve the branch out afterwards.

---

## 3. Commits (Conventional Commits)

### §3.1 Message format

```
<type>(<scope>): <subject>

<body — optional>

<footer — optional>
```

**type** — same set as §2.1, plus 3 commit-only types:

| type | Meaning |
|---|---|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Docs only |
| `refactor` | Refactor |
| `perf` | Performance |
| `test` | Tests only |
| `build` | Build system / external deps |
| `ci` | CI config |
| `chore` | Other misc |
| `revert` | Revert a prior commit |

**scope** — a top-level subpackage of `raven/`. See the repo-layout page of the documentation site (`docs-site/docs/repo-layout.md`) for the canonical list. Spanning multiple scopes → omit the scope, or use `(*)`.

**subject** — lowercase start; no trailing period; English. The whole header (`<type>(<scope>): <subject>`) must be ≤ 100 chars — the single length rule, enforced by commitlint `header-max-length`.

**footer** (optional):
- `BREAKING CHANGE: <desc>` — triggers a MAJOR bump once public.

A closing keyword does not belong here: squash-merge drops individual commit bodies, so only the PR description can close an issue (§3.3, §3.7).

### §3.1.1 Top rule: the whole message is English (subject + body + footer)

No other languages anywhere in the message — not just the subject; body and footer too.

| Part | Rule |
|---|---|
| subject | English, lowercase start, no period; whole header ≤ 100 chars |
| body | **All English**; when citing a non-English plan / discussion, **translate** it, don't paste |
| punctuation | **ASCII-only** — not just no full-width punctuation (`：`,`，`,`。`,`「」`,`""` …) but also no em-dash `—`, curly quotes, or ellipsis `…` (all non-ASCII, all rejected by CI); no `§`-numbering, no non-English path names; the latin part of a §N.M anchor is fine |
| trailer | `Co-authored-by: ...` is ASCII by format |

**Why:** Conventional-Commits tooling (commitlint / semantic-release / changelog generators) parses ASCII grammar and mis-lints on non-English text; cross-language reviewers and a public commit history both need English.

**Process:**
1. **Before writing:** translate the points in your head to English first — don't write a non-English body then translate (that leaves full-width residue).
2. **After writing:** self-check with `git log -1`; any non-English char → rewrite.
3. **Already committed but violating:** rewrite the message with `git rebase -i` **only after explicit user authorization**; don't rewrite history unprompted (see §3.4).
4. **Syncing colleague fixes:** each fix lands as **one** commit — no same-subject twin pairs — and its body passes the same all-English / ASCII rules; the sync pipeline is not exempt from this section.

### §3.2 ✅ Good / ❌ Bad

✅ Good:

```
feat(cli): rename cron show/remove to get/delete
fix(channels): default allow_from to ['*'] instead of deny-all
refactor(cli): replace --cron-expr with --cron and --every-seconds with --every
feat(importer): add EverOS HTTP backend
```

❌ Bad:
- `更新代码` (non-English + no type/scope);
- `update` (no type/scope);
- `feat: Cron 命令重命名为 get 和 delete.` (uppercase + period + non-English + no scope).

### §3.3 Trailer rules (commit message + PR description)

**✅ Required:**

- `Co-authored-by: Claude (<model-id>) <noreply@anthropic.com>` — when Claude helped write the code, append it at the end of the commit body (blank line above), or at the end of the PR description.
  - `<model-id>` = the **actual current-session model ID** (e.g. `claude-fable-5` / `claude-sonnet-5` / `claude-haiku-4-5`), not a placeholder. The model version keeps per-model contribution distinguishable.
  - Format follows the aider convention; GitHub renders `Co-authored-by` as a co-author on the commit / PR.
- Multiple co-authors → one per line, standard git trailer format (`Name <email>`).
- The repo **squash-merges** PRs (rebase only freshens the branch before push; the merge collapses the branch to one commit on `main`). The squash commit's subject is the PR title and its body is the **PR description** (`squash_merge_commit_message=PR_BODY`) — individual commit bodies are dropped. GitHub still auto-collects each commit's `Co-authored-by` into the squash commit, so keep the trailer in your commit and put `Closes #NNN` + reviewer context in the PR description (that is what lands on `main`).

**❌ Don't add:**
- `Refs: ...` pointing at locally-visible-only / git-ignored paths (invisible to others);
- `🤖 Generated with Claude Code` and similar emoji banners — `Co-authored-by` already conveys co-authorship (and is the structured, machine-readable attribution); a marketing badge adds no attribution value;
- internal commit-hash references / temporary branch names — docs/PRs describe the present state only.

### §3.4 Hard rule: when to commit

- **Don't commit unprompted** — only when the user explicitly says "commit" / "提交" / "save".
- "Commit per phase" written in a plan is **not** pre-authorization — a plan is a reference; committing still needs the user's word.
- After finishing a phase, **report and stop**; wait for acceptance + an explicit commit instruction.
- **Don't** `git commit --amend` a prior commit (unless the user explicitly asks to amend).
- If a pre-commit hook fails, create a **new** commit to fix it — don't amend.

### §3.5 Sync before push + force-push boundary

**Rule:** before pushing a feature branch, base it on the **latest `main`**.

| Step | Command | Note |
|---|---|---|
| 1. Sync remote | `git fetch origin <target>` | Doesn't touch the working tree |
| 2. Dry-run conflicts | `git merge-tree --write-tree HEAD origin/<target>` | exit 0 = clean; non-zero prints conflicts |
| 3. Rebase (if remote ahead) | `git rebase origin/<target>` | Re-applies your branch onto the target tip |
| 4. Re-run tests | `uv run pytest <relevant tests> -x` | Confirm the rebase didn't break anything |
| 5. Push | `git push -u origin <branch>` (first) or `git push --force-with-lease` (after rebase) | — |

**Why:** CI runs "your commits on top of the latest remote" (catches runtime conflicts before merge) and the PR diff stays clean. Rebase here only freshens the branch base before push; the merge itself is a squash, which collapses the branch to a single commit on `main`.

**Force-push boundary:**
- ✅ `--force-with-lease` (checks the remote wasn't changed by others) on **your own feature branch** after a rebase;
- ❌ `git push --force` (blind, can clobber others' pushes);
- ❌ never force-push to long-lived / protected branches (`main`).

### §3.6 Hard rule: Claude push

- Always `git fetch` first to check ahead/behind;
- if the remote target has commits not on your branch, **rebase before pushing** (the §3.5 flow);
- **re-run tests after the rebase**;
- **don't push unprompted** — like §3.4, only when the user says "push";
- use `--force-with-lease`, never `--force`.

### §3.7 After a successful push, offer to open the PR

After pushing a new feature branch, **proactively ask** whether to open the PR with `gh pr create` — don't leave the user to do it in the web UI.

**Title:** same Conventional-Commits grammar as commits (`<type>(<scope>): <subject>`), subject reflecting the PR's overall goal, not any single commit. **Length:** the title plus the ` (#NN)` GitHub appends on squash must keep the header ≤ 100 chars — CI validates the title as that prospective squash header, so a title that passes will not fail commit-lint after merge. Shorter is better.

**Description must be all English** (same as §3.1.1): no other languages / full-width punctuation / `§` numbering anywhere (subject + body + tables + checklist).

**Description structure:** the sections come from `.github/pull_request_template.md` — read that file, do not work from a copy. `gh pr create` fills it in for you; writing `--body` by hand means reproducing its headings exactly.

Filling rules:
- `Summary` — the PR's overall goal + key decisions (summarize the phase evolution for multi-commit PRs);
- `Type` — one box, mirroring the commit type (`feat`→Feature, `fix`→Fix, `docs`→Docs, `refactor`→Refactor, `ci`/`build`→CI / tooling, else Other). The checkbox is not a fresh judgement call;
- `Verification` — the exact commands you ran and their result, not a claim that you ran them;
- `Risk` — user-visible behaviour changes, and how to roll back;
- `Related Issues` — `Fixes #NNN` for what this closes (repeat the keyword per issue: `Fixes #NNN, fixes #MMM`), a bare `#NNN` to reference without closing, `N/A` when there is none;
- check only the boxes you actually satisfied — leave the rest blank and explain in the description; never blanket-check;
- anything the template has no section for but the reviewer needs (breaking change / cherry-pick option / mixed topics) → append to `Summary`.

**Trailer** (with §3.3):
- squash-merge → GitHub auto-collects each commit's `Co-authored-by` into the squash commit, so keep the trailer in your commit and **don't add it to the PR description** (that duplicates it);
- `Closes #NNN` and reviewer context, by contrast, **must** live in the PR description — the squash commit body is taken from it (`squash_merge_commit_message=PR_BODY`), and individual commit bodies are dropped.

**Description must NOT contain** (same as §3.3):
- `🤖 Generated with [Claude Code](https://...)` marketing banners;
- `Refs: ...` to ignored/local-only paths;
- internal branch names / commit-hash references (no reviewer context).

**Preview-verification (required):**
1. After drafting, **scan for any non-ASCII char first** (CI lints the whole message as ASCII-only -- `scripts/check_pr_body.py` is the canonical checker, and the squash commit body is this PR description, so it must be ASCII too):
   ```bash
   uv run python -m scripts.check_pr_body /tmp/pr_description.md
   # must exit 0. The file argument is what gets linted; with no argument the
   # script falls back to PR_BODY and exits 2 when neither supplies a body.
   # The test is per-character, so it catches em-dash / curly quotes / ellipsis,
   # not just CJK -- and stock macOS grep has no -P.
   ```
2. show the full text for preview;
3. only after the user edits/confirms, run `gh pr create --title "..." --body "$(cat /tmp/pr_description.md)"`;
4. report the PR URL.

**Not allowed:**
- pushing and walking away, leaving PR creation to the user;
- delivering a description without running the ASCII scan above.

---

## 4. Dependencies (uv only)

### §4.1 `uv` is the only Python package manager

| Action | Command |
|---|---|
| Add runtime dep | `uv add <package>` |
| Add dev dep | `uv add --dev <package>` |
| Remove dep | `uv remove <package>` |
| Sync env from lockfile | `uv sync` |
| Upgrade one package | `uv lock --upgrade-package <package>` |
| Upgrade all | `uv lock --upgrade` |
| Run a command in the project env | `uv run <command>` |

### §4.2 Forbidden

- ❌ `pip install` / `pip uninstall`;
- ❌ hand-editing `[project.dependencies]` / `[project.optional-dependencies]` / `[dependency-groups]` in `pyproject.toml`;
- ❌ hand-editing `uv.lock`;
- ❌ `pip freeze > requirements.txt`;
- ❌ `python -m pip install ...` to bypass uv.

### §4.3 Exception

If the user *explicitly* says "let me try pip" / "manually add this line to pyproject", follow the user. This rule constrains Claude's **default** behavior, not the user's direct instructions.

---

## 5. Tests

### §5.1 Unit tests

Under `tests/test_*.py`. CLI unit tests use one shape:

```
tests/test_cli_<module>_commands.py
```

- one file per module (aligns with `raven/cli/<module>_commands.py`);
- **don't** split by phase / feature / ticket (no `phase4` / `eve151` suffixes);
- aspect suffixes are allowed:
  - testing a CLI private helper: `test_cli_<helper>.py` (e.g. `test_cli_helpers.py` / `test_cli_stacks.py`);
  - cross-module behavior: `test_cli_<aspect>.py` (e.g. `test_cli_config_precedence.py` / `test_cli_smoke.py`).

### §5.2 Integration tests

Under `tests/integration/test_*.py`. Run against real environments (real LLM / channel / fcntl / subprocess / VM, etc.).

Naming: `test_<scope>_<kind>.py`, where `<kind>` ∈:

| kind | Meaning |
|---|---|
| `e2e` | End-to-end happy path, single/multi module |
| `smoke` | Multi-module interplay, just "it runs" |
| `real_<resource>` | Hits a real resource (`real_vm` / `real_llm` / `real_channel`, …) |

- **`<scope>` must not carry a version / ticket number** (no `v002` / `eve151`) — use a feature/scenario description.

### §5.3 Examples

| ❌ Wrong | ✅ Right | Reason |
|---|---|---|
| `test_cli_cron.py` | `test_cli_cron_commands.py` | missing `_commands` suffix |
| `test_sentinel_cli.py` | `test_cli_sentinel_commands.py` | order reversed |
| `test_cli_sentinel_phase4.py` | merge into `test_cli_sentinel_commands.py` | no phase suffix |
| `tests/integration/test_v002_smoke.py` | `test_<feature>_smoke.py` | no version number |
| `tests/integration/test_eve151_smoke.py` | `test_<feature>_smoke.py` | no ticket number |

### §5.4 Hard rules for Claude

- when changing/adding a CLI command, update the matching `test_cli_<module>_commands.py` — **don't create a new file**;
- when you spot a legacy file violating §5.1 / §5.2, **report it to the user first** — don't rename it unprompted (renames touch git history and may collide with follow-up PRs);
- always run tests via `uv run pytest ...`, never bare `pytest` (per §4).

---

## 6. Domain terms

- Naming a domain concept tracked in `CONTEXT-MAP.md` (the entry point — it routes to `CONTEXT.md` for Runtime terms and `ui-tui/CONTEXT.md` for TUI)? Use the canonical term, not a synonym.
- Coining a new domain term: define it in the matching `CONTEXT.md` in the same change, with a definition verifiable against the code (not guessed) — add an `_Avoid_` list only if a confusable synonym exists.

---

## 7. Repository assets

- Do not commit report assets or standalone web artifacts, regardless of size. This includes images, GIFs, SVGs, videos, audio files, PDFs, HTML files, web manifests, and WASM bundles.
- The application source trees (`bridge/`, `ui-web/`, `ui-tui/`) are exempt from that extension list: a product frontend carries its own entry HTML and icon SVGs as source, not as report assets. Everything else in this section, including the 1 MiB limit, still applies to them.
- Packaged `.jpg` benchmark plates under `plugins-dist/design-engine/raven_design/skills/<skill>/references/` are functional Skill inputs, not report assets, and are allowed: `.jpg` only, only under a skill's `references/`, and the 1 MiB limit still applies.
- Store public-report assets outside git and link to them when needed.
- The git-story-film skill's bundled HTML inputs are allowed at exactly
  `skills/git-story-film/examples/raven-story/raven.html`,
  `skills/git-story-film/examples/raven-story/cast-sheet.html`, and
  `skills/git-story-film/examples/raven-story/storyboard/storyboard.html` (owner-approved).
  These are functional Skill inputs; pre-rendered images, generated exports, and other
  asset types remain prohibited. The 1 MiB limit still applies to every allowed HTML file.
- Do not add or modify files over 1 MiB unless the maintainer explicitly approves it before the commit.
- Run `make check-large-files` when touching docs, demos, reports, assets, or generated outputs; CI enforces the same rule on added and modified PR files.

---

## Maintenance

This file holds **hard constraints only** (rules whose violation gets reverted / rejected). Soft suggestions, design preferences, and style leanings go in personal notes or conversation — not here.

Before adding a section, confirm with the user in conversation first — the shorter AGENTS.md stays, the more useful it is.

<!-- KNOWL_PROJECT_MEMORY -->
## Knowl Project Memory

### Required workflow

1. For every project-specific, personal or research request, call `knowl_query` before repository files or commands, using the words that name the subject: another on-subject term retrieves better, an off-subject one retrieves worse, so do not pad the query and do not trim a real term to shorten it.
2. Skip a new query only when directly relevant active lifecycle context, a same-request query, or manual `knowl_task_start` relevant memory already answers it.
3. Use a relevant active hit immediately. Inspect files only after a miss, conflict, stale/low-confidence memory, or explicit verification request.
4. Query again before switching to a distinct subtask or project area, and before choosing how to build something new — existing tooling and pipelines are project knowledge, and in a linked workspace they often live in a sibling repo, so leave method queries unscoped.
5. Store or update durable knowledge during work and before the final answer — verified findings, stated intent (goals, plans, direction the user voiced) stored as goals with user_stated provenance even while unsettled, and resolved diagnoses stored as skills when the cause will recur (an environment quirk, a config trap — not a typo). The test: could a fresh session recover this from memory alone? Never store raw transcripts, secrets, or transient debugging noise.
6. Listed but not callable is not unavailable. A host may namespace the tools and withhold their schemas until asked, so load the schema for the name your host lists and call it — where names are namespaced from the server key, `knowl_query` appears as `mcp__knowl__knowl_query`. Stop and tell the user only when the tools are genuinely absent, or when every call fails; never silently bypass Knowl.

### Lifecycle modes

- **Automatic host lifecycle:** verified hooks own bootstrap, capture, checkpoints, and finalization. Never call `knowl_task_start`, `knowl_task_checkpoint`, `knowl_task_finish`, or `knowl_session_finish` for that hook-owned session.
- **Manual work loop:** without verified hooks, use `knowl task run` for one bounded command. For resumable work, start once, checkpoint meaningful milestones/blockers with the returned task ID, and finish exactly once after verification. The start result satisfies the initial focused lookup.

Casual conversation, a single memory lookup, and trivial non-resumable work do not create a manual task loop.

### Complete MCP tool routing

| Group | Tools | Routing |
| --- | --- | --- |
| Focused retrieval | `knowl_query` | Default first call for a specific request about project, personal or research knowledge and again when switching areas. Use the words that name the subject, without padding, and omit category unless certain. |
| Context views | `knowl_recent`, `knowl_state`, `knowl_context` | Use recent only without lifecycle bootstrap or for an explicit refresh; state for broad status; context for an explicitly token-budgeted pack. |
| Manual work loop | `knowl_task_start`, `knowl_task_checkpoint`, `knowl_task_finish` | Use only without verified lifecycle hooks: start once, checkpoint meaningful milestones or blockers, and finish once after verification. |
| Durable writes | `knowl_store`, `knowl_ingest_atoms`, `knowl_decide`, `knowl_update` | Store one verified atom, batch verified atoms, record a confirmed decision, or correct/supersede stale memory. Write to the project store; use namespace global only when the user asks or no project is open. |
| History and quality | `knowl_timeline`, `knowl_evidence_list`, `knowl_conflicts`, `knowl_drift`, `knowl_feedback` | Inspect history, evidence, or conflicts when needed; check drift before opening a pull request; record feedback only after actual use, rejection, or correction. |
| Learned skills | `knowl_skill_list`, `knowl_skill_read`, `knowl_skill_run`, `knowl_skill_create` | Discover and read a matching skill before running a trusted entrypoint; create only when explicitly requested. |
| Special and maintenance | `knowl_ingest`, `knowl_synthesize`, `knowl_session_finish`, `knowl_gc_preview`, `knowl_gc_apply` | Raw-source ingest requires an explicit request and configured AI; never send the current conversation silently. Pass profile to knowl_ingest by what the text is (code, general for personal and everyday, academic for sources and research); for mixed text, one call per kind. Synthesis is explicitly scoped and never automatic. Session finish is only for an explicitly owned manual memory-session ID, never a hook session. Preview GC first; apply only after explicit approval. |
| Session handoff | `knowl_handoff` | Use when parking a workstream before ending a session. The next session in this project receives it once, then it is archived. One baton per project -- parking again replaces it. Durable facts still belong in knowl_store. |
| Parked work | `knowl_park`, `knowl_resume` | Park work the user means to come back to: knowl_park mints a short key and returns a paste-ready line to hand them verbatim, since a key reworded is a key lost. knowl_resume takes that key in any later session, from any directory, and returns the brief. Unlike the handoff baton -- which the next session in this project consumes once -- a key is held by the user, is not spent by resuming, and works any number of sessions later. Call knowl_resume as soon as a user supplies a key; with no key it lists what is parked here. |

### Linked repositories

- When this repo is in a workspace, read the **shape** of a `knowl_query` result first. A bare JSON array means every row belongs to this repo. An object keyed by repo name means at least one row does **not** — and a fact from another repo describes **that** repo unless it says otherwise, so verify before applying it here.
- An empty array under this repo's own key means this repo holds nothing on the subject. Read the other keys as background, not as an answer, and treat it as a miss if what they say does not transfer.
- Narrow with `scope: "local"` (this repo alone, always a bare array) or `scope: "workspace"` (every sharing repo, always keyed). `repos: ["<name>"]` restricts to named repos and matches the repo that owns an item; it wins if both are given.
- Method questions — "how do we generate X", "is there a script for Y" — belong to the whole workspace: query them unscoped before building new tooling; a sibling repo's pipeline answers them more often than this repo's files do.
- A `WORKSPACE:` notice names linked repos that matched but were not shown, with counts. Re-query with `repos` to read them.
- Whether a new write is shared is this repo's recorded default, not a fixed rule: joining a linked workspace sets it to workspace visibility, while `--default-visibility repo` keeps writes private. `knowl workspace set` with no flags prints the current value.
- Knowledge already written privately stays private until someone runs `knowl workspace promote`. Only the owning repo can promote, update, or retire its own items.
- When the work you are doing belongs to a LINKED repo, pass `repo: "<name>"` on the call instead of writing it here. The call then applies to that repo exactly as if it had been run there -- what you write is stamped as its own, and retiring its knowledge is allowed, because the repo is correcting itself. Without it a write lands here, attributed here, which is how a finding ends up in the wrong drawer.
- A result carrying `disputed` is contested by the repo named there: read the claim before relying on it. When a linked repo's item is wrong, record a dissent rather than trying to edit it -- the write is refused, and the dissent reaches the owner, who resolves it by superseding the item or rejecting the dissent.

### Safety and freshness

- Correct stale or contradicted memory with `knowl_update` instead of adding a duplicate.
- All writes are secret-validated. Never retry rejected secret material in altered form.
- `Auth: Unsupported` is normal for a local stdio MCP server when the focused retrieval tool is listed.
<!-- /KNOWL_PROJECT_MEMORY -->
