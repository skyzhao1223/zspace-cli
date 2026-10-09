# Integrations

How to pair zspace-cli with the tools you already use for media and automation.

## Jellyfin / Emby

zspace-cli does not scrape metadata itself — it reads/renames/moves files on the
NAS. Use it **before** ingesting into Jellyfin/Emby so library scans pick up
clean names:

```bash
# inspect a library folder
zs ls /sata11/my/data/影视

# move/copy with a single rename via --name
zs up ./Movie.2024.1080p.mkv /sata11/my/data/影视 --name "Movie (2024).mkv"

# bulk: move everything matching a pattern into a series folder
zs mv '/sata11/my/data/下载/*.mkv' /sata11/my/data/剧集/Series/Season\ 01
```

> `zs mv`/`zs cp`/`zs rm`/`zs down` accept `* ?` glob patterns on the source
> (see [CLI options](../README.md#cli-options)).

After the files land, trigger a Jellyfin/Emby library scan (or wait for its
watcher). zspace-cli's job is done — naming is now library-friendly.

## MoviePilot / nas-tools / AutoFilm

These scrapers watch download folders and rename on their own. zspace-cli can
prepare a clean **staging** area so they don't choke on download residue
(`.bt.td`, `!qB`, part files). The bundled `download-cleaner` skill automates
this triage (partials / torrents / installers / archives → a suggested action
per file):

```bash
# list download residue that should be cleaned up
zs find ".bt.td"

# move finished media into a clean staging dir
zs mv '/sata11/my/data/下载/*.mkv' /sata11/my/data/待入库
```

Pairing zspace-cli with
[media-manager-skill](https://github.com/skyzhao1223/media-manager-skill) gives
you a naming-problem scan (`mm-scan --source zspace ...`) before the scraper
runs — surface issues in one pass instead of letting MoviePilot rename them
incorrectly.

## media-manager-skill (ZSpace mode)

`media-manager-skill[zspace]` reuses zspace-cli's desktop-client login. Once
`zs check` passes, run:

```bash
mm-scan --source zspace /sata11/my/data
```

The adapter talks to the same `ZSpaceClient`, so whatever platform auto-detection
works for `zs` (macOS / Windows / Linux, or `ZS_CONFIG_DIR`) also works for the
scanner.

## Baidu NetDisk (百度网盘) via the NAS `/znetdisk/*` module

The NAS ships an official Baidu NetDisk module; the desktop-client proxy exposes
it under `/znetdisk/*` with the **same** token/nasid/device_id auth and
`{code:"200", msg, data}` envelope as the file API. zspace-cli wraps the
**read-only** half of it — no local Baidu cookie needed, because the NAS verifies
shares and pulls files server-side.

```bash
zs baidu check                 # is a Baidu account linked? VIP flags + quota
zs baidu ls /                  # browse your own pan through the NAS
zs baidu tasks --state fail    # transfer tasks (state: running/done/pause/fail)
zs baidu fails --task-id <id>  # per-file failure reasons (baidu_fail_code, advice)
zs baidu retry <task-id>       # resume a paused/failed task (mutates; asks first)
```

SDK equivalents: `client.baidu_check()`, `baidu_userinfo()`, `baidu_ls(path)`,
`baidu_tasks(state)`, `baidu_fail_list(task_id)`, `baidu_share_verify()`,
`baidu_share_list()`, and `baidu_task_action(method, task_id)`. `check`/`ls`/
`tasks`/`fails` accept `--json`.

**Auth it needs.** A Baidu account must already be linked to the NAS account —
`zs baidu check` reports `is_login`. Linking is a browser OAuth flow the web UI
drives (`/znetdisk/auth/token`); the CLI/SDK deliberately do **not** do it, and
never print the returned OAuth URL. There is no MCP tool for this integration:
see below.

**What is gated / unverified.** Share **transfer** (`share/transfer`) and
NAS-side **download** (`file/download`) require 百度NAS会员 (`iot_vip_type == 1`);
non-members get `code 15 需要NAS会员权限`, and non-VIP `file/download` tasks were
observed stalled at 0 B/s (Baidu throttles non-VIP openapi hard). Those, plus
every write/config endpoint (`sync/*`, `autobackup/*`, `order/*`,
`membership/active`, `auth/token`, `auth/logout`, `file/upload`, `file/newdir`),
are **documented but not wrapped** — they mutate remote state or are membership-
gated, so they can't be verified without changing a real account. The wrapped
read endpoints (`auth/check`, `auth/userinfo`, `file/list`, `task/list`,
`fail/list`) were probed read-only against a real NAS; `share/verify`,
`share/filelist` and `task/action` are sourced from the NAS web app's code but
not exercised live. Full endpoint table, field lists, `down_state` semantics and
the measured-vs-inferred split: [skills/zspace-nas/api-reference.md](../skills/zspace-nas/api-reference.md#百度网盘集成-apiznetdisk).

**Relationship to `baidu-pan-skill`.** These are two complementary routes, not
duplicates:

| | zspace-cli `/znetdisk/*` (this) | [baidu-pan-skill](https://github.com/skyzhao1223/baidu-pan-skill) |
|---|---|---|
| Path | NAS-side: server verifies the share, NAS pulls to disk | Local: browser cookie → transfer-save → chunked download |
| Needs | A Baidu account linked to the NAS | Your own browser Baidu login (cookie extraction) |
| Gate | 百度NAS会员 for transfer/direct-download | No NAS membership, but Baidu's account-level throttle applies |
| Best for | Pan → NAS when you have NAS VIP | Pan → local (then `zs up` to NAS) when you don't |

So a share link can go **straight to the NAS** through this module if you have
NAS VIP; otherwise `baidu-pan-skill` downloads it locally and `zs up` uploads it.
Neither path bypasses Baidu's throttling or the membership gate — both document
them as-is.

> **Interoperability note.** This is unofficial reverse-engineering of the NAS's
> Baidu module. Endpoint names, `code 15`, the `down_state` values and field
> names can break on any NAS firmware or client update; pin failures to the exact
> `code`/`msg` when reporting.

## Organizer skill family (any NAS via mount)

`zs skill` installs 9 read-only organizer skills on top of `zspace-nas`:
`nas-report` (entry point), `file-sorter`, `photo-organizer`, `music-organizer`,
`work-organizer`, `portfolio-organizer`, `download-cleaner`, `dedup-finder`,
`backup-auditor`.

Their scanners are pure-stdlib and run on **mounted paths** (SMB/NFS), so they
work with any NAS brand — not just ZSpace. Run `nas-report` first for a storage
profile that routes you to the right specialist. Writes always go through the
agent after you confirm an old→new plan: on ZSpace via `zs mv/rename/mkdir`
(no mount needed), elsewhere via plain `mv -n` on the mount. Full list and
workflows: [skills/README.md](../skills/README.md).

## MCP clients (Claude Desktop / Cursor / etc.)

Add the server to your MCP config:

```json
{
  "mcpServers": {
    "zspace": { "command": "zs-mcp", "args": [] }
  }
}
```

Available tools: `zspace_check`, `zspace_pool_info`, `zspace_disk_stats`,
`zspace_ls`, `zspace_info`, `zspace_rename`, `zspace_mkdir`, `zspace_move`,
`zspace_copy`, `zspace_remove`, `zspace_search`, `zspace_tree`,
`zspace_upload`, `zspace_download`.

Errors come back as a structured `{"error": "..."}` result, so agents can react
instead of crashing.

## Docker headless

When the desktop client is reachable over the network (not localhost), point
the container at it:

```bash
docker run --rm -e ZS_BASE_URL=http://<nas-or-client-ip>:13579 \
  -v ~/.config/zspace:/config zspace-cli zs-mcp
```

See [docker-compose.yml](../docker-compose.yml) for the full compose setup and
the `ZS_BASE_URL` / mount caveats.

## Automation / scripts

Every read-only command supports `--json` for machine consumption:

```bash
zs ls /sata11/my/data --json | jq '.[] | select(.is_dir) | .path'
zs check --json | jq -r '.ok'
zs tree /sata11/my/data -d 2 --json
```

Upload/download stream in place (no full-file buffering) and report progress —
safe for multi-GB media files over a scripted pipeline.
