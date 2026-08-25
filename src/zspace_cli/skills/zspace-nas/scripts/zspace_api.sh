#!/bin/bash
# ZSpace NAS API client — operates through the local 极空间 desktop client proxy.
# Requires: 极空间 desktop client running on macOS.

set -euo pipefail

ZSPACE_CONFIG_DIR="$HOME/Library/Application Support/zspace"
VUEX_FILE="$ZSPACE_CONFIG_DIR/vuex.json"
BASE_URL="http://127.0.0.1:13579"

_load_credentials() {
  if [ ! -f "$VUEX_FILE" ]; then
    echo "ERROR: 极空间客户端未安装或未登录 ($VUEX_FILE not found)" >&2
    exit 1
  fi
  TOKEN=$(python3 -c "import json; d=json.load(open('$VUEX_FILE')); print(d['state']['user']['token'])")
  NASID=$(python3 -c "import json; d=json.load(open('$VUEX_FILE')); print(d['state']['nas']['nasId'])")
  DEVICE_ID=$(python3 -c "import json; d=json.load(open('$VUEX_FILE')); print(d['state']['app']['deviceId'])")
}

_check_client_running() {
  if ! pgrep -f "极空间.app" >/dev/null 2>&1; then
    echo "ERROR: 极空间客户端未运行" >&2
    exit 1
  fi
  if ! curl -s --connect-timeout 2 "$BASE_URL/home/" >/dev/null 2>&1; then
    echo "ERROR: 无法连接到极空间本地代理 ($BASE_URL)" >&2
    exit 1
  fi
}

zspace_api() {
  local endpoint="$1"; shift
  curl -s -X POST -b "token=$TOKEN" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "token=$TOKEN" \
    --data-urlencode "nasid=$NASID" \
    --data-urlencode "plat=web" \
    --data-urlencode "version=2.3.2026042401" \
    --data-urlencode "device_id=$DEVICE_ID" \
    --data-urlencode "_l=zh_cn" \
    "$@" \
    "${BASE_URL}${endpoint}?&rnd=$(date +%s)${RANDOM}_${RANDOM}&webagent=v2"
}

# --- Public commands ---

cmd_check() {
  _check_client_running
  _load_credentials
  echo "OK: 极空间客户端已连接"
  echo "  NAS ID: $NASID"
  echo "  Base URL: $BASE_URL"
  zspace_api "/zspool/info" 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
if d['code'] == '200':
    for p in d['data']['pool_list']:
        total = p['total_size'] / (1024**4)
        free = p['free_size'] / (1024**4)
        print(f\"  Pool '{p['name']}': {total:.1f}TB total, {free:.1f}TB free\")
"
}

cmd_ls() {
  local path="${1:-/sata11/my/data}"
  _load_credentials
  zspace_api "/v2/file/list" \
    --data-urlencode "path=$path" \
    --data-urlencode "show_hidden=0" | python3 -c "
import json, sys
d = json.load(sys.stdin)
if d['code'] != '200':
    print(f\"ERROR: {d['msg']}\", file=sys.stderr); sys.exit(1)
for f in d['data']['list']:
    is_dir = f['is_dir'] == '1'
    size = int(f.get('size', 0))
    if is_dir:
        t = '[DIR]'
    elif size > 1073741824:
        t = f'{size/1073741824:.1f}GB'
    elif size > 1048576:
        t = f'{size/1048576:.1f}MB'
    elif size > 1024:
        t = f'{size/1024:.1f}KB'
    else:
        t = f'{size}B'
    print(f'{t:>12}  {f[\"name\"]}')
print(f'--- {len(d[\"data\"][\"list\"])} items ---')
"
}

cmd_info() {
  local path="$1"
  _load_credentials
  zspace_api "/v2/file/info" --data-urlencode "path=$path" | python3 -m json.tool
}

cmd_rename() {
  local path="$1"
  local newname="$2"
  _load_credentials
  zspace_api "/v2/file/modify" \
    --data-urlencode "path=$path" \
    --data-urlencode "newname=$newname" | python3 -c "
import json, sys
d = json.load(sys.stdin)
if d['code'] == '200':
    print(f\"OK: renamed to '{d['data']['name']}'\")
else:
    print(f\"ERROR: {d['msg']}\", file=sys.stderr); sys.exit(1)
"
}

cmd_move() {
  local src="$1"
  local dest="$2"
  _load_credentials
  local body="token=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$TOKEN'))")&nasid=$NASID&plat=web&version=2.3.2026042401&device_id=$DEVICE_ID&_l=zh_cn"
  body+="&paths%5B%5D=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$src'))")"
  body+="&to=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$dest'))")"
  curl -s -X POST -b "token=$TOKEN" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "$body" \
    "${BASE_URL}/v2/file/move?&rnd=$(date +%s)${RANDOM}_${RANDOM}&webagent=v2" | python3 -c "
import json, sys
d = json.load(sys.stdin)
if d['code'] == '200':
    print('OK: moved successfully')
else:
    print(f\"ERROR [{d['code']}]: {d['msg']}\", file=sys.stderr); sys.exit(1)
"
}

cmd_mkdir() {
  local path="$1"
  local name="$2"
  _load_credentials
  zspace_api "/v2/file/newdir" \
    --data-urlencode "parent=$path" \
    --data-urlencode "name=$name" \
    --data-urlencode "rename=0" | python3 -c "
import json, sys
d = json.load(sys.stdin)
if d['code'] == '200':
    print(f\"OK: created '{d['data']['name']}' at {d['data']['path']}\")
else:
    print(f\"ERROR [{d['code']}]: {d['msg']}\", file=sys.stderr); sys.exit(1)
"
}

cmd_cp() {
  local src="$1"
  local dest="$2"
  _load_credentials
  local body="token=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$TOKEN'))")&nasid=$NASID&plat=web&version=2.3.2026042401&device_id=$DEVICE_ID&_l=zh_cn"
  body+="&paths%5B%5D=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$src'))")"
  body+="&to=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$dest'))")"
  curl -s -X POST -b "token=$TOKEN" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "$body" \
    "${BASE_URL}/v2/file/copy?&rnd=$(date +%s)${RANDOM}_${RANDOM}&webagent=v2" | python3 -c "
import json, sys
d = json.load(sys.stdin)
if d['code'] == '200':
    print('OK: copy started')
else:
    print(f\"ERROR [{d['code']}]: {d['msg']}\", file=sys.stderr); sys.exit(1)
"
}

cmd_rm() {
  local path="$1"
  _load_credentials
  local body="token=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$TOKEN'))")&nasid=$NASID&plat=web&version=2.3.2026042401&device_id=$DEVICE_ID&_l=zh_cn"
  body+="&paths%5B%5D=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$path'))")"
  curl -s -X POST -b "token=$TOKEN" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "$body" \
    "${BASE_URL}/v2/file/remove?&rnd=$(date +%s)${RANDOM}_${RANDOM}&webagent=v2" | python3 -c "
import json, sys
d = json.load(sys.stdin)
if d['code'] == '200':
    print('OK: deleted')
else:
    print(f\"ERROR [{d['code']}]: {d['msg']}\", file=sys.stderr); sys.exit(1)
"
}

cmd_search() {
  local keyword="$1"
  local path="${2:-/sata11/my/data}"
  _load_credentials
  zspace_api "/v2/file/list" \
    --data-urlencode "path=$path" \
    --data-urlencode "show_hidden=0" | python3 -c "
import json, sys
keyword = '$keyword'.lower()
d = json.load(sys.stdin)
if d['code'] != '200':
    print(f\"ERROR: {d['msg']}\", file=sys.stderr); sys.exit(1)
matches = [f for f in d['data']['list'] if keyword in f['name'].lower()]
for f in matches:
    t = '[DIR]' if f['is_dir'] == '1' else f.get('size', '')
    print(f'{f[\"path\"]}')
print(f'--- {len(matches)} matches ---')
"
}

cmd_tree() {
  local path="${1:-/sata11/my/data}"
  local depth="${2:-2}"
  _load_credentials
  python3 -c "
import json, subprocess, sys

def api_list(path):
    result = subprocess.run([
        'curl', '-s', '-X', 'POST', '-b', 'token=$TOKEN',
        '-H', 'Content-Type: application/x-www-form-urlencoded',
        '--data-urlencode', 'token=$TOKEN',
        '--data-urlencode', 'nasid=$NASID',
        '--data-urlencode', 'plat=web',
        '--data-urlencode', 'version=1.0',
        '--data-urlencode', 'device_id=$DEVICE_ID',
        '--data-urlencode', '_l=zh_cn',
        '--data-urlencode', f'path={path}',
        '--data-urlencode', 'show_hidden=0',
        '${BASE_URL}/v2/file/list?webagent=v2'
    ], capture_output=True, text=True)
    d = json.loads(result.stdout)
    return d['data']['list'] if d['code'] == '200' else []

def tree(path, prefix, depth, max_depth):
    if depth >= max_depth:
        return
    items = api_list(path)
    dirs = [f for f in items if f['is_dir'] == '1']
    files = [f for f in items if f['is_dir'] != '1']
    all_items = dirs + files
    for i, f in enumerate(all_items):
        is_last = (i == len(all_items) - 1)
        connector = '└── ' if is_last else '├── '
        label = f['name'] + '/' if f['is_dir'] == '1' else f['name']
        print(f'{prefix}{connector}{label}')
        if f['is_dir'] == '1':
            ext = '    ' if is_last else '│   '
            tree(f['path'], prefix + ext, depth + 1, max_depth)

print(path.split('/')[-1] + '/')
tree('$path', '', 0, $depth)
"
}

# --- Main ---
case "${1:-help}" in
  check)  cmd_check ;;
  ls)     cmd_ls "${2:-}" ;;
  info)   cmd_info "$2" ;;
  rename) cmd_rename "$2" "$3" ;;
  move)   cmd_move "$2" "$3" ;;
  cp)     cmd_cp "$2" "$3" ;;
  mkdir)  cmd_mkdir "$2" "$3" ;;
  rm)     cmd_rm "$2" ;;
  search) cmd_search "$2" "${3:-}" ;;
  tree)   cmd_tree "${2:-}" "${3:-2}" ;;
  help|*)
    cat <<'USAGE'
ZSpace NAS CLI — 通过极空间客户端本地代理操作 NAS 文件（全部操作已验证）

Usage: zspace_api.sh <command> [args]

Commands:
  check                         检查连接状态
  ls [path]                     列出目录内容 (默认 /sata11/my/data)
  info <path>                   查看文件/目录详细信息
  rename <path> <newname>       重命名文件/目录
  move <src_path> <dest_dir>    移动文件/目录
  cp <src_path> <dest_dir>      复制文件/目录
  mkdir <parent_path> <name>    创建新目录
  rm <path>                     删除文件/目录
  search <keyword> [path]       在目录中搜索文件名
  tree [path] [depth]           树形显示目录结构 (默认深度2)

Path format: /sata11/my/data/...
USAGE
    ;;
esac
