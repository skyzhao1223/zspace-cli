# Security Policy

## Reporting a vulnerability

**Please do NOT open a public issue for security problems.**

Use GitHub's private vulnerability reporting instead:

1. Open the [Security Advisories](https://github.com/skyzhao1223/zspace-cli/security/advisories/new) page of this repo
2. Click **Report a vulnerability** and describe the issue

Or contact the maintainer directly: `skyzhao1223@users.noreply.github.com`.

You can expect an initial response within ~7 days. If accepted, a fix will be
developed and coordinated with you before public disclosure; you will be
credited in the advisory unless you prefer to stay anonymous.

## Scope — what counts as a security issue here

zspace-cli is a local-first tool: it reads the ZSpace desktop client's login
state (`vuex.json`: token / nasId / deviceId) and talks to the client's
loopback proxy (`127.0.0.1:13579`). It never handles your ZSpace *password*
and performs no SSH/DDNS/port-forwarding.

Things we care about:

- Token or credential material leaking (logs, error messages, temp files,
  crash dumps, MCP tool output)
- Requests escaping the loopback proxy to unintended hosts (SSRF-style issues)
- Path traversal that lets a NAS response write outside the requested local
  directory (`zs down`, skill scanners)
- Code execution via crafted NAS API responses or malicious filenames
  (skills are copied into agent projects and run by LLM agents — a hostile
  filename that breaks a scanner's shell assumptions is in scope)
- `zs skill` copying files outside the target project directory

Out of scope:

- Vulnerabilities in the ZSpace desktop client, NAS firmware, or the
  `127.0.0.1:13579` proxy itself (report those to ZSpace / 极空间官方)
- Attacks that require an already-compromised machine or physical access
- The unofficial API surface breaking after a client update (that's a
  compatibility bug, not a security one — file a normal issue)

## Supported versions

| Version | Supported          |
|---------|--------------------|
| latest PyPI release | ✅ security fixes |
| older releases | ❌ upgrade instead |

This project moves fast with a single maintainer; security fixes ship as patch
releases via the Release workflow (PyPI Trusted Publishing).
