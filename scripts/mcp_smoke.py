#!/usr/bin/env python3
"""MCP smoke test: read initialize + tools/list over stdio and assert basics.

Usage:
    <mcp-server-command> | python3 scripts/mcp_smoke.py
"""

import json
import sys


def main() -> None:
    seen_init = False
    seen_tools = False
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("id") == 1 and "result" in d:
            assert "serverInfo" in d["result"], d
            print("initialize OK:", d["result"]["serverInfo"])
            seen_init = True
        if d.get("id") == 2 and "result" in d:
            names = [t["name"] for t in d["result"]["tools"]]
            assert "zspace_ls" in names, names
            print("tools/list OK:", len(names), "tools")
            seen_tools = True
    assert seen_init, "no initialize response received"
    assert seen_tools, "no tools/list response received"


if __name__ == "__main__":
    main()
