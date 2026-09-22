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

## The `/znetdisk/*` Baidu NetDisk integration (🔴, high impact)

The NAS ships an official Baidu NetDisk (百度网盘) module that the desktop proxy
exposes under `/znetdisk/*`. Observed working (via the local proxy with the
standard token/nasid/device_id auth):

| Endpoint | Notes |
|----------|-------|
| `auth/check`, `auth/userinfo`, `auth/token`, `auth/logout` | `userinfo` returns baidu uk, vip_type, **iot_vip_type** (百度NAS会员), quota |
| `share/verify` | `{short_url, pwd}` → `data.spwd` (server-side share verification) |
| `share/filelist` | `{short_url, spwd, page, limit, path}` → shared entries incl. `fsid`, `md5`, `size` |
| `share/transfer`, `share/transfer_result` | save share → own pan; **gated**: non-NAS-VIP gets `code 15 需要NAS会员权限` |
| `file/download` | `{file_ids, save_path}` — create NAS-side download task from own pan (accepted for non-VIP, but observed **0 B/s stall** — Baidu throttles non-VIP openapi hard; verify before building UX on it) |
| `task/list`, `task/action` | task states: 1=downloading 2=paused 4=done 5=queued 6=retrying; actions: `resume/pause/clean/pause_all/resume_all/clean_all/clean_all_done/resume_fail_all/clean_fail_all` |
| `fail/list`, `autobackup/*`, `sync/*`, `membership/active`, `order/*` | unexplored |

Deliverables could be: `zs baidu check|ls|tasks|retry` CLI + SDK methods + a
`zspace-nas` skill section documenting the above. Even **docs-only** (adding
this table to `skills/zspace-nas/api-reference.md` with caveats) is a valuable
PR. The web-client source is reachable: `http://127.0.0.1:13579/home/` serves
the NAS's Vue app; upload/task logic lives in lazy chunks (search
`znetdisk/share/transfer`).

## Skills family

### 🟢 EXIF-based photo dating (`photo-organizer`) — roadmap
Optional `--exif` mode using `exiftool`/`mdls` when available, falling back to
mtime. Pure stdlib constraint means shelling out; keep it opt-in.

### 🟡 Per-skill config overrides — roadmap
Whitelist dirs / extension sets via a `skills/<name>/config.json` overlay so
scanners stop flagging intentional structures.

### 🟡 Growth-trend reports — roadmap
`nas-report` snapshots are JSON; add `nas-report diff a.json b.json` (new
files, growth by category/dir, churn). Pure stdlib, offline-testable — great
skill-contributor task.

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
