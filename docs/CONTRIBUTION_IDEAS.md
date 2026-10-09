# Contribution Ideas

A curated backlog of concrete, research-backed ways to improve zspace-cli —
from 15-minute wins to multi-week features. Each idea says **what**, **why**,
**where to look**, and a rough **difficulty**.

> How to claim one: open (or find) the matching issue, comment that you're
> working on it, and ask questions in
> [Discussions](https://github.com/skyzhao1223/zspace-cli/discussions).
> Stale claims get released after ~2 weeks of silence.

Difficulty: 🟢 easy (hours) · 🟡 medium (days) · 🔴 large (a week+)

---

## Core / SDK

### 🟢 Linux auth-path verification (Windows: ✅ confirmed)
Windows is **verified**: a real install keeps `vuex.json` at
`C:\Users\<user>\AppData\Roaming\zspace` = `%APPDATA%\zspace` — exactly
candidate #1 in `auth.py::_candidate_dirs()` (community report via issue #7,
2026-09; `windows-latest` is in the CI matrix too). **Linux remains guessed**
(`~/.zspace`, `~/.config/zspace`, `~/zspace`, flatpak layouts). If you run the
ZSpace client on Linux, report your actual path in issue #7 and fix the
candidate list + add a regression test.
**Why:** Windows users are covered; Linux support still ships unverified.

### 🟡 i18n for CLI output
All CLI messages are Chinese (`OK 已上传到 …`). Add an English mode via
`ZS_LANG=en` / `--lang`, keeping zh as default. Typer help strings too.
**Why:** the README is English-first; international users hit a wall at runtime.

## The `/znetdisk/*` Baidu NetDisk integration (🟡, read side shipped)

The NAS ships an official Baidu NetDisk (百度网盘) module that the desktop proxy
exposes under `/znetdisk/*` with the standard token/nasid/device_id auth and the
same `{code:"200", msg, data}` envelope as the file API.

> ✅ **Shipped** (this repo, `zs baidu` + `client.baidu_*` + docs): the
> **read-only** side — `auth/check`, `auth/userinfo`, `file/list`, `task/list`,
> `fail/list` were probed against a real NAS and wrapped; `share/verify`,
> `share/filelist`, `task/action` are wrapped from source (not exercised live).
> Full field lists, the `down_state`/`taskFailed` semantics and the
> measured-vs-inferred split now live in
> [`skills/zspace-nas/api-reference.md`](../skills/zspace-nas/api-reference.md)
> → 「百度网盘集成 API」, with the UX/gating story in
> [`docs/integrations.md`](integrations.md) → Baidu NetDisk.

Endpoints, with what's wrapped vs. still open:

| Endpoint | Notes | Status |
|----------|-------|--------|
| `auth/check`, `auth/userinfo` | `userinfo` returns baidu `uk`, `vip_type`, **`iot_vip_type`** (百度NAS会员), `quota`, `iot_vip_cashier` | ✅ wrapped (measured) |
| `file/list` | `{path, page(1-based), limit}` → own-pan entries with **`fs_id`** (note: not `fsid`), `server_filename`, `size`, `isdir` | ✅ wrapped (measured) |
| `task/list` | `{page, limit, state}` (`state`: `""`/`running`/`done`/`pause`/`fail`) → `data.list[]`, `task_count`, `unfinished_task` | ✅ wrapped (measured) |
| `fail/list` | `{page, limit, task_id?}` → `data.list[]`, `total`; entries carry `baidu_fail_code`, `advice`, `fail_reason` | ✅ wrapped (measured) |
| `share/verify` | `{short_url, pwd}` → `data.spwd` (server-side share verification, no local cookie) | ✅ wrapped (source only) |
| `share/filelist` | `{short_url, spwd, page, limit, path}` → shared entries incl. **`fsid`**, `md5`, `size` | ✅ wrapped (source only) |
| `task/action` | `{method, task_id?}`; methods `resume/pause/clean/pause_all/resume_all/clean_all/clean_all_done/resume_fail_all/clean_fail_all` | ✅ wrapped (write; `zs baidu retry` only maps `resume`/`resume_fail_all`, confirm-gated) |
| `share/transfer`, `share/transfer_result` | save share → own pan; **gated**: non-NAS-VIP gets `code 15 需要NAS会员权限` | ⛔ documented, not wrapped (mutating + gated) |
| `file/download` | `{file_ids, save_path}` — NAS-side download task from own pan (accepted for non-VIP but observed **0 B/s stall** — Baidu throttles non-VIP openapi hard) | ⛔ documented, not wrapped (mutating + stalls) |
| `file/upload`, `file/newdir`, `auth/token`, `auth/logout`, `sync/*`, `autobackup/*`, `membership/active`, `order/*`, `zdrive/baidu/rclone/mountinfo` | write/config/auth endpoints (module 2934 in the NAS Vue app defines them all) | ⛔ documented, not wrapped (mutating) |

**Correction learned while wiring this up.** The earlier note here (and #12)
listed `down_state` as `5=queued, 6=retrying`. The NAS web app's task-center
render code (module `39137`) actually branches on `1=downloading, 2=paused,
4=done, 6=queued, 9=creating-backup-dir, 10=hashing-files`, and "retrying" is
**not a distinct state** — it's `down_state==1 && retry_times>0`. A live
`task/list` sample (`down_state=6, retry_times=0`, 31 unfinished) matches
`6=queued`, not `6=retrying`. `zs baidu tasks` and api-reference.md use the
source-backed mapping. "Failure" is likewise a combination (`fail_reason`, or
`fail_num>0 && down_state==4`, or `baidu_limit/illegal_content/space_fulle`),
never a single `down_state`.

**Still open (the interesting remaining work):**
- A `baidu-backup` **skill** for agents (scan pan → propose a save plan → the
  membership-gated transfer/download stays human-confirmed). Needs a NAS-VIP
  account to validate end-to-end.
- Live verification of `share/verify` → `share/filelist` → `share/transfer` on a
  NAS-VIP account (this PR could only source them, not exercise the gated writes).

Source: the NAS's Vue app at `http://127.0.0.1:13579/home/`; the endpoints are
defined in lazy chunk `64392` (module `2934`), the task-center UI in chunk
`4548` (module `39137`). `grep -a znetdisk app.asar` finds **nothing** — this
module runs on the NAS, not in the desktop Electron client.


## Skills family

### 🟡 Port file-sorter's execution-safety checks to the other 8 skills
`file-sorter` now detects three ways a plan can fail at *execute* time:
`shell_risk()` (names starting with `-`, or containing `` " `` `` ` `` `$` `\`
newline), `_blocked_by_file()` (a target directory occupied by a same-named
file) and `_probe_case_fold()` (case-only collisions, which make the second
`mv -n` silently no-op on APFS/NTFS). The other 8 skills emit `old → new`
plans too and detect none of it — `photo-organizer` is the most exposed since
it proposes `YYYY/YYYY-MM/` targets for large camera dumps, exactly where
`IMG_1234.JPG` next to `img_1234.jpg` shows up.

These are ~40 lines each and copy cleanly; the pure functions are already unit
tested in `file-sorter`'s smoke.sh, so porting is mostly wiring plus one
fixture per skill. One skill per PR is fine. Family-level guidance already
exists in `skills/README.md` → 「执行阶段的两个坑」, so a skill that hasn't
been ported yet is at least documented.

> Note for the roadmap checkboxes: this list has drifted four times now (#16,
> #14, #15 twice) because shipping a feature and updating the roadmap are
> separate acts — if your PR closes or advances a roadmap item, tick/annotate it
> in **both** READMEs and update this file in the same PR.

> ✅ Shipped since this list was written:
> - **Resumable sliced upload** (#53, roadmap-adjacent, issue #10) —
>   `GET /v2/file/tmpinfo?path=&uuid=` answers with `data.size`, the bytes the
>   NAS already accepted for that session; the session uuid is recomputable
>   locally (`md5(mtime_ms + size + target)`), so **no state needs persisting**,
>   which is simpler than what this entry proposed. Two measurements shaped the
>   implementation: after a completed upload the session is consumed and the same
>   query returns `N001315`, so "no session" and "already finished" are one
>   signal; and re-sending `seek=0` while a session exists is accepted, so
>   falling back to a full re-send is always safe. `zs up` defaults to `--resume`
>   with a `--no-resume` escape hatch. The old note here that the parameter set
>   was unknown and naive guesses returned `N001212` is obsolete — `path` +
>   `uuid` alone suffice.
> - ❌ **Parallel slice upload — answered no, deliberately not implemented**
>   (issue #11). `uploadProcess: 4` is how many *files* upload at once, not
>   slices of one file: it has exactly one read site in the whole `app.asar`,
>   inside `UploadCenter.uploadNext()`. Measured on a real NAS (12 MB / six
>   2 MB slices): sequential 11.27 s and 4-thread in-order 10.41 s both
>   round-trip MD5-identical, but reverse order gave 4× `N001530 无法断点续传`
>   plus a 502 and a file that **still downloaded with the wrong MD5** — silent
>   corruption. The in-order speedup was ~8%, not 2–4×, because RTT is not the
>   bottleneck on a local proxy. Full write-up in
>   `skills/zspace-nas/api-reference.md` so nobody re-runs it. Still untested:
>   the high-latency relay case this entry was actually about.
> - **Post-upload integrity check** — shipped as `zs up --verify` (downloads the
>   file back and compares MD5; opt-in so the default path is unchanged).
>   `POST /v2/file/hash` was *not* used: it is defined in `app.asar` but never
>   called anywhere in the bundle, so its contract is unknown.
> - **`config.json` overlay ported to all 8 scanners** (#47 → #52 → #54, roadmap
>   #15, now ticked in both READMEs). `nas-report` stays out deliberately.
>   The key set is *not* uniform, and that is on purpose: `whitelist_dirs` means
>   "this structure is intentional, don't flag it" and only exists in the five
>   scanners that judge directory-name compliance; the other three get `skip_dirs`
>   ("don't descend"), and `dedup-finder` — having no extension tables at all —
>   gets `prefer_keep_hints` instead. Giving all eight the same key with different
>   meanings would make a config copied between skills behave surprisingly.
>   The family table in `skills/README.md` records which keys each skill accepts
>   and what a hit actually does, because `whitelist_dirs` has **four** distinct
>   effects across those five (still counted as `protected` / whole subtree
>   skipped / directory-name exemption only / "not a project").
> - `photo-organizer --exif` (roadmap #14) — shells out to `exiftool`, falls back
>   to macOS `mdls` (Spotlight), then to mtime; opt-in so the default path is
>   byte-identical to before and the pure-stdlib rule still holds.
> - `nas-report diff OLD.json NEW.json` (growth by category/dir, new+vanished
>   large files, rate & ETA) — was "Growth-trend reports", roadmap #16.
> - **Windows execution path in every SKILL.md** — was "…in the other 8 skills".
>   Each `写操作通道` now has a PowerShell row (`New-Item` / `Move-Item` without
>   `-Force` / `robocopy /MOV /XC /XN /XO`) plus a pointer to the family table.
> - **Skill smoke tests run on windows-latest** — the `skills` job is now an
>   ubuntu+windows matrix. Worth knowing: turning it on immediately found a
>   real bug (every scanner raised `UnicodeEncodeError` printing its Chinese
>   report to a redirected stdout, i.e. exactly how an agent calls it). The
>   fixtures did survive git-bash as-is — `mktemp /tmp/...`, `touch -t`, `seq`,
>   `head -c /dev/urandom` and CJK filenames all work; only `PY=python` (no
>   `python3.exe`) and `PYTHONIOENCODING=utf-8` for the harness's own `✓` output
>   were needed.
> - **`.gitattributes` + LF normalization** (#36) — `nas_report.py` and its
>   packaged copy are LF now, so a text-mode edit can no longer silently rewrite
>   600 lines. `*.sh`/`*.yml`/`*.md` are covered too, which matters because CI
>   runs the smoke scripts through bash on Windows.

### 🔴 Syncthing / 同步空间 helper
The client runs a Syncthing fork (`ZSpaceSync`, GUI `127.0.0.1:8384`, API key
in `~/Library/Application Support/zspace/<account>/.config/config.xml`; two
devices paired with the NAS, zero folders by default). A `zs sync` surface
(list/add/monitor sync folders via the Syncthing REST API) would unlock the
fastest large-file path — but folder pairing with the NAS side is orchestrated
by ZSpace's own UI; investigate what the NAS accepts before committing to UX.

### 🔴 `zs mount` — WebDAV helper
The desktop client serves WebDAV on `127.0.0.1:13601`; credentials land in
`<account>/mount/mount.conf` (used by `ZSpaceMount`, an rclone fork) — but the
password goes stale after client restarts (observed 401). A `zs mount` that
refreshes/reads credentials and mounts via macFUSE/fuse-t would make every
existing tool (rsync, cp, editors) work against the NAS. Figure out how the
client regenerates the password (Electron main process, `app.asar` →
search `uzmount`).

## Distribution & DX

### 🟢 Publish Docker image to GHCR
`Dockerfile` + `docker-compose.yml` exist (headless `ZS_BASE_URL` mode) but
nothing is published. Add a `docker/build-push-action` job to `release.yml`
tagged `ghcr.io/skyzhao1223/zspace-cli:{version,latest}`.

### 🟢 Examples folder
`examples/`: 3–4 tiny scripts (backup Downloads nightly, photo-organizer dry
run, MCP client snippet, sliced-upload of a huge file) — copy-paste starting
points beat prose.

### 🟡 Benchmark + resilience suite against client updates
The API is unofficial; every ZSpace client update can break things. A
`scripts/api_smoke.py` that exercises every endpoint against a real NAS with
assertions on response *shape* (not content) would turn "it broke" issues into
pinpointed diffs. Opt-in (`ZS_SMOKE=1`), never in CI (needs a NAS).

---

*Maintainers: keep this list honest — remove what ships, add what you learn.*
