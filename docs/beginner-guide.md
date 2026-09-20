# Beginner's Guide — Let AI Organize Your NAS (No Coding Required)

> If you can install an app and copy-paste, you can set this up in ~15-30 minutes.
> Every step shows what success looks like (✅) and what to do if it fails (❌).
>
> 中文用户请读[《新手指南》中文版](beginner-guide.zh.md)。

## What is this?

Your NAS has probably accumulated years of clutter: photos dumped from phones,
downloads nobody remembers, eight "final" versions of the same document.

This project hands your **AI assistant** (Claude Code, Cursor, …) a set of
**organizer playbooks** ("skills"). Then you just say:

> "Organize the photos on my NAS by date"

The AI scans, shows you a plan, and **only acts after you approve it**.

## Three safety promises

1. **Read-only scans first** — every workflow starts with "look, don't touch" and produces a report
2. **You approve before any change** — the AI shows an old→new plan and waits for your OK
3. **Deletes quarantine first** — files marked for deletion are parked in a quarantine folder first

The project is open source (MIT) — all code is public on
[GitHub](https://github.com/skyzhao1223/zspace-cli). It never asks for your NAS
password (ZSpace mode reuses the desktop client you're already logged into).

## What you need

- [ ] A NAS (ZSpace, Synology, QNAP, UGREEN — any brand)
- [ ] A computer (this guide assumes **macOS**; Windows notes included)
- [ ] An AI assistant that can run commands: Claude Code (recommended), Cursor, etc.
- [ ] 15-30 minutes

## The only 6 terms you need

| Term | Plain English |
|------|---------------|
| NAS | The "network hard drive box" on your home/office network |
| Terminal | The app where you type commands (Spotlight → "Terminal") |
| Command | A line of text you paste into Terminal and run with Enter |
| Mount | Making a NAS folder appear as a local disk on your computer |
| AI assistant (Agent) | An AI that can operate your computer, e.g. Claude Code |
| Skill | An "organizer playbook" for the AI — this project ships 9 |

---

## Step 1: Check Python

Open Terminal, paste, press Enter:

```bash
python3 --version
```

- ✅ Shows `Python 3.9.6` or newer → continue
- ❌ `command not found` → install from https://www.python.org/downloads/, reopen Terminal

## Step 2: Install this project

```bash
pip3 install zspace-cli
```

- ✅ Ends with `Successfully installed zspace-cli-...` → done
- ❌ `command not found: pip3` → use `python3 -m pip install zspace-cli`

## Step 3 (ZSpace users): Verify the connection

Prerequisite: the ZSpace **desktop client is running and logged in** on this Mac.

```bash
zs check
```

- ✅ Connection OK → skip to Step 5 (no mounting needed)
- ❌ Fails → make sure the ZSpace app is open and logged in, retry

## Step 3 (Synology / QNAP / UGREEN / …): Mount your NAS folder

**macOS**:

1. Finder menu → Go → Connect to Server… (⌘K)
2. Enter `smb://YOUR-NAS-IP` (e.g. `smb://192.168.1.100`) → Connect
3. Enter your NAS username/password, pick the share (e.g. `photo`)
4. It appears under Finder → Locations; its full path is `/Volumes/photo`

**Windows**: This PC → right-click → Map network drive → `\\YOUR-NAS-IP\photo`
→ it becomes a drive letter (e.g. `Z:`); use `Z:\photo` wherever this guide
says `/Volumes/photo`.

> Don't know the NAS IP? Check its web admin page or your router's device list.

## Step 4: Give the playbooks to your AI assistant

```bash
mkdir -p ~/my-nas
zs skill ~/my-nas/skills/
```

- ✅ Shows `OK copied 9 skills` → done
- Only want a few? `zs skill --list` to browse, then
  `zs skill ~/my-nas/skills/ --only nas-report,photo-organizer`

Then open your AI assistant **with that folder as the project**:

- Claude Code: in Terminal, `cd ~/my-nas`, then run `claude`
- Cursor / Trae: File → Open Folder → pick `my-nas`

## Step 5: Say something

Talk to the AI in plain language:

| You say | What the AI does |
|---------|------------------|
| "Give me a storage report for my NAS" | Full-disk profile: what eats space, what's messiest, where to start |
| "Organize /Volumes/photo by date" | Scans photos → proposes an archive plan → moves after you confirm |
| "Find duplicate files" | Content-level exact de-dup (zero false positives), lists a removal plan |
| "Clean up my downloads folder" | Triages installers / torrents / partial downloads / media to file away |
| "Are my backups still fresh?" | Audits backups: stale, missing, or rotatable old versions |
| (ZSpace) "List the files in /sata11/my/data" | Browses directly — no mount needed |

**Recommended first line: "Give me a storage report"** — it tells you where to start.

## No AI assistant? Just run the reports

The reports are human-readable. Run one manually:

```bash
python3 ~/my-nas/skills/nas-report/nas_report.py report --root /Volumes/photo
```

Paste the output into any chat AI and ask "how should I organize this?" —
you'll get a plan; you just execute it yourself.

## Troubleshooting (FAQ)

| Symptom | Fix |
|---------|-----|
| `command not found: zs` | Close and reopen Terminal; re-check Step 2 said "Successfully installed" |
| `command not found: python3` / `pip3` | Go back to Step 1 |
| `zs check` fails | The ZSpace desktop client must be **open and logged in** (installed isn't enough) |
| Mount fails | Double-check IP / credentials; make sure SMB is enabled in the NAS admin |
| AI can't find the skills | The project you opened must contain the `skills` folder (Step 4); reopen it |
| Scan is slow | Normal for big NAS libraries; ask the AI to add `--sample 2000` first |
| Still nervous | Tell the AI: "plan only, don't execute anything" |

## Next

- What each of the 9 skills does + trigger phrases: [skills/README.md](../skills/README.md)
- Full command & technical docs: [root README](../README.md)
- Stuck or ideas: [open an issue](https://github.com/skyzhao1223/zspace-cli/issues) —
  beginner questions are especially welcome; your question becomes the next person's docs
