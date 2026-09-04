# Container image for running the zspace-cli MCP server (stdio transport).
# Used by Glama (https://glama.ai/mcp/servers) and anyone who prefers
# running the server in Docker instead of pip.
#
# Note: zs-mcp only connects to the ZSpace desktop client's local proxy
# (127.0.0.1:13579) when a tool is actually invoked. Startup and MCP
# introspection (initialize / tools/list) work without a client, so the
# container passes Glama's checks out of the box.
FROM python:3.12-slim

RUN pip install --no-cache-dir "zspace-cli[mcp]"

CMD ["zs-mcp"]
