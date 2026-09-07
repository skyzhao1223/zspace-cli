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
(`.bt.td`, `!qB`, part files):

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
