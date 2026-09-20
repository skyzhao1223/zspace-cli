# Contributing

Guidelines for hacking on zspace-cli — the core package and the Agent skills family.

## Dev setup

```bash
pip install -e ".[dev]"        # core dev tools (works on Python 3.9)
pip install -e ".[mcp,dev]"    # + MCP server deps (needs Python 3.10+)
```

## Quality gates (what CI runs)

| Check | Command | Notes |
|-------|---------|-------|
| Lint | `ruff check src tests skills` | **ruff ≥ 0.11 required** — `pyproject.toml` references `UP045`; older ruff fails to even parse the config |
| Tests | `pytest -q` | coverage gate 75%; skill scripts are excluded via `omit` (they are wheel *data*, exercised by their own smoke tests) |
| Types | `python -m pyright` | `include = ["src"]`, evaluated with Python 3.9 semantics — this **does** cover the packaged skill copies |
| Skill smoke | `for f in skills/*/tests/smoke.sh; do bash "$f"; done` | fully offline (fixtures in `mktemp -d`); CI runs them on Python 3.9 |
| Skill sync | `diff -rq skills/<n> src/zspace_cli/skills/<n>` | the two copies must be identical — see below |

## Skills live in two places (IMPORTANT)

```
skills/<name>/                 ← source of truth — edit here
src/zspace_cli/skills/<name>/  ← packaged copy shipped inside the wheel
```

`zs skill` installs from the **packaged copy** (`package-data` in `pyproject.toml`).
Edit `skills/` without mirroring and the wheel ships stale skills. After any skill
change, run:

```bash
for d in skills/*/; do
  n=$(basename "$d")
  rm -rf "src/zspace_cli/skills/$n"
  cp -R "$d" "src/zspace_cli/skills/$n"
done
cp skills/README.md src/zspace_cli/skills/README.md
find src/zspace_cli/skills -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
```

CI fails if the two trees drift.

## Authoring a new skill

Anatomy — copy any of the 8 existing skills as a template:

```
skills/<name>/
├── SKILL.md        # agent-facing workflow doc (what the LLM reads)
├── <name>.py       # read-only scan CLI
├── tests/smoke.sh  # offline end-to-end test
└── README.md       # developer doc
```

### Hard rules

1. **Read-only scripts.** No apply/mv/rm subcommands. The scanner reports problems;
   the agent drafts an `old → new` plan; the user confirms; the agent executes
   (`mv -n` on the mount, or `zs` CLI / MCP on ZSpace). Deletes quarantine first.
2. **Pure stdlib, Python ≥ 3.9.** `from __future__ import annotations`; zero
   third-party imports — users run these from a copied folder with whatever
   `python3` they have.
3. **Mount-path based.** Scanners take `--root /Volumes/...` (SMB/NFS mount) and
   never call the ZSpace API — that is what keeps them cross-NAS.
4. **Skip system noise.** Reuse the standard `SKIP_DIRS` set (`@eaDir`, `#recycle`,
   `.Trashes`, `node_modules`, …); never follow symlinks; ignore dotfiles silently
   (except `._*` AppleDouble, which is flagged as junk).
5. **Positive validation.** Define the compliant shape and report deviations —
   don't enumerate dirt patterns. Semantic judgment (name lookups, project
   attribution) belongs to the LLM, not to regex.
6. **Consistent CLI + JSON.** Subcommand `scan` (or `report`) with
   `--root --json --output --max-depth --sample --top`; result JSON is
   `{skill, root, generated_at, stats, count, issues}`; each issue is
   `{path (relative), name, is_dir, problems[], …extras}`; human output groups
   issues by problem type.

### SKILL.md frontmatter

```yaml
---
name: <skill-name>            # must match the directory name (smoke checks this)
description: Use when 用户想…   # one line: what it does + when to trigger
  触发词: …                    # trigger phrases (smoke checks this exists)
  不适用: …                    # out of scope → which sibling skill handles it
---
```

Body sections (mirror an existing skill): 概述 / Prerequisites / 命令 / 期望结构 /
工作流场景 / 整理顺序 / 命名速查 / 写操作通道 / 关键约束 / 踩坑 / 已知 gap / 故障排查.

### smoke.sh structure

Five tests, fully offline:

1. SKILL.md frontmatter valid (name matches dir; has 触发词/不适用)
2. `python3 -m py_compile` the scanner
3. Pure-function unit cases (import the module; assert parsers/classifiers)
4. Fixture tree end-to-end: build known-bad files under `mktemp -d`, run
   `scan --output`, assert expected problems appear **and compliant fixtures
   produce zero false positives**
5. `--help` works

### New-skill checklist

- [ ] `bash skills/<name>/tests/smoke.sh` passes (ideally also under a 3.9 interpreter)
- [ ] `ruff check skills` passes
- [ ] mirrored to `src/zspace_cli/skills/<name>/`
- [ ] rows added to `skills/README.md` (清单 + 触发示例 tables), packaged copy re-synced
- [ ] skill tables in root `README.md` + `docs/README.zh.md` updated
- [ ] `pytest -q` still green

## Core package notes

- The ZSpace API is **unofficial** — community-documented from the desktop client's
  local proxy (`127.0.0.1:13579`). Endpoint table: root README → "API endpoints".
  Expect breakage when ZSpace updates the client; error-code handling
  (`N001411` path denied, `N001208` re-login, …) lives in `client.py` / `auth.py`.
- `cli.py` keeps `Optional[...]` annotations **on purpose** — typer resolves
  annotations at runtime on py3.9. Don't "modernize" them to `X | None`.
- MCP tool params use `Annotated[..., Field(description=...)]`; wrap lines to stay
  ≤ 100 chars (ruff E501).

## Release

Releases run through the **Release** workflow (Actions → Release → Run workflow,
choose `patch`/`minor`/`major`). It bumps `pyproject.toml` (the single source of
version truth — `__init__.py` reads it via importlib.metadata), builds, publishes
to PyPI, commits `chore: release vX.Y.Z`, tags, and creates a GitHub Release.

> `server.json` (MCP registry metadata) is **not** auto-bumped — update its two
> `version` fields manually when preparing a release.

## PR checklist

- [ ] `ruff check src tests skills` clean (ruff ≥ 0.11)
- [ ] `pytest -q` green (coverage ≥ 75%)
- [ ] `python -m pyright` clean (if you touched `src/`)
- [ ] all `skills/*/tests/smoke.sh` pass (if you touched skills)
- [ ] dual copies in sync (if you touched skills)
- [ ] docs updated (`skills/README.md` + both READMEs) if the change is user-facing
