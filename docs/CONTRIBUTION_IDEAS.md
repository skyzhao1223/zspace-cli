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

### 🟡 Resumable sliced upload (`/v2/file/tmpinfo`)
`client.py::_upload_sliced()` restarts from `seek=0` when a process dies.
The desktop client supports resuming: it keeps `finishedSize` and queries
`GET /v2/file/tmpinfo` (see `Jo` in the client's `background.js`, extractable
from `app.asar`). The exact parameter set is **not yet known** — naive guesses
(`path`, `size`, `uuid` combos) return `N001212 参数有误`. Reverse-engineer the
real params (e.g. by watching the desktop client resume an interrupted upload
via a local proxy like mitmproxy/whistle), then persist `{uuid, target,
finished}` locally and resume.
**Why:** multi-GB uploads over flaky relays are exactly when resume matters.

### 🟡 Parallel slice upload
Slices are uploaded strictly sequentially. The client's vuex has
`uploadProcess: 4` and `uploadSlice` hints at concurrency. Test whether the NAS
accepts concurrent/out-of-order slices for one `uuid` (start 2–4 slices in
parallel; verify with a round-trip MD5). If yes, add `--jobs N` to `zs up`.
**Why:** ~2–4x upload throughput on high-latency relays.

### 🟢 Post-upload integrity check (`zs up --verify`)
After upload, `zs down` the file (or fetch a server-side hash — probe
`POST /v2/file/hash`) and compare MD5 with the local file. The sliced protocol
was validated this way manually (680 MB round-trip, byte-identical); productize
it as an opt-in flag.

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

### 🟢 Port the `config.json` overlay to the other 7 skills — roadmap #15 (partly shipped)
The mechanism landed in #47 for **`file-sorter` and `photo-organizer`**: an
optional `skills/<name>/config.json`, resolved next to the *installed script*
(`__file__`, not cwd), carrying `whitelist_dirs` and `extension_overrides` — so a
proprietary CAD extension or an intentional `原盘/VIDEO_TS` tree no longer needs
a code edit that the next `pip install` overwrites. Read #47's implementation
before porting; the remaining 7 skills (`nas-report`, `music-`, `work-`,
`portfolio-organizer`, `download-cleaner`, `dedup-finder`, `backup-auditor`)
still hardcode their compliant shapes. One skill per PR is fine, and each needs a
smoke case proving the overlay is picked up from the script's own directory.

> Note for the roadmap checkboxes: #15 is **partly** done, so it stays `[ ]` with
> an annotation rather than getting ticked. This list has drifted three times now
> (#16, #14, #15) because shipping a feature and updating the roadmap are
> separate acts — if your PR closes or advances a roadmap item, tick/annotate it
> in **both** READMEs and update this file in the same PR.

> ✅ Shipped since this list was written:
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
