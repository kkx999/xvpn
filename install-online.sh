#!/usr/bin/env bash
set -Eeuo pipefail

REPO="kkx999/xvpn"
TMP="$(mktemp -d /tmp/xvpn-online-XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

fail(){ echo "[ERROR] $*" >&2; exit 1; }
ok(){ echo "[OK] $*"; }
info(){ echo "[INFO] $*"; }
warn(){ echo "[WARN] $*"; }
[[ ${EUID} -eq 0 ]] || fail "请使用 root 运行。"

REQUESTED=""
case "${1:-}" in
  "") ;;
  --version|-v) REQUESTED="${2:-}"; [[ -n "$REQUESTED" ]] || fail "缺少版本号。" ;;
  v*|[0-9]*) REQUESTED="$1" ;;
  *) fail "用法：install-online.sh [--version v1.2.1]" ;;
esac

if ! command -v curl >/dev/null 2>&1 || ! command -v unzip >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1 || ! command -v sha256sum >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y curl unzip ca-certificates python3 coreutils
fi

normalize_version(){ local v="${1#v}"; v="${v#V}"; echo "$v"; }
valid_version(){ [[ "$1" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+([._-]?[A-Za-z]+([._-]?[0-9]+)?)?$ ]]; }
current_version(){
  if [[ -f /opt/xvpn-panel/VERSION ]]; then tr -d '[:space:]' < /opt/xvpn-panel/VERSION
  elif [[ -f /opt/vpn-panel/VERSION ]]; then tr -d '[:space:]' < /opt/vpn-panel/VERSION
  fi
}
version_compare(){ python3 - "$1" "$2" <<'PY'
import re,sys
def k(v):
 v=v.strip().lstrip('vV'); m=re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:[-._]?([A-Za-z]+)[-._]?(\d+)?)?',v)
 if not m:return None
 a,b,c=map(int,m.group(1,2,3)); label=(m.group(4) or '').lower(); n=int(m.group(5) or 0); rank={'dev':10,'alpha':20,'a':20,'beta':30,'b':30,'rc':40,'':100}.get(label,50); return a,b,c,rank,n,label
A,B=k(sys.argv[1]),k(sys.argv[2])
print('unknown' if A is None or B is None else 'newer' if A>B else 'same' if A==B else 'older')
PY
}

clear || true
echo "========================================"
echo "       XVPN Panel 一键部署 / 升级"
echo "========================================"

RELEASE_JSON="$TMP/release.json"
if [[ -n "$REQUESTED" ]]; then
  valid_version "$REQUESTED" || fail "版本格式无效，例如 v1.2.1。"
  TARGET_VERSION="$(normalize_version "$REQUESTED")"
  TAG="v$TARGET_VERSION"
  info "正在获取正式版本：$TAG"
  API_URL="https://api.github.com/repos/$REPO/releases/tags/$TAG"
else
  info "正在获取最新正式版本..."
  API_URL="https://api.github.com/repos/$REPO/releases/latest"
fi

curl -fsSL --connect-timeout 8 --max-time 20 \
  -H 'Accept: application/vnd.github+json' \
  "$API_URL" -o "$RELEASE_JSON" \
  || fail "无法获取正式版本信息，请稍后重试。"

readarray -t META < <(python3 - "$RELEASE_JSON" <<'PY'
import json,sys
j=json.load(open(sys.argv[1],encoding='utf-8'))
tag=(j.get('tag_name') or '').strip()
version=tag.lstrip('vV')
want=f'xvpn-panel-v{version}.zip'
asset=''; sums=''
for a in j.get('assets',[]):
    name=a.get('name','')
    if name==want:
        asset=a.get('browser_download_url','')
    elif name=='SHA256SUMS.txt':
        sums=a.get('browser_download_url','')
print(tag)
print(version)
print(asset)
print(sums)
PY
)

TAG="${META[0]:-}"
TARGET_VERSION="${META[1]:-}"
ASSET_URL="${META[2]:-}"
SUMS_URL="${META[3]:-}"

[[ -n "$TAG" && -n "$TARGET_VERSION" ]] || fail "正式版本信息不完整。"
valid_version "$TARGET_VERSION" || fail "正式版本号格式异常：$TAG"
[[ -n "$ASSET_URL" ]] || fail "正式版本 $TAG 缺少 xvpn-panel-v${TARGET_VERSION}.zip。"
[[ -n "$SUMS_URL" ]] || fail "正式版本 $TAG 缺少 SHA256SUMS.txt，已停止安装。"

if [[ -n "$REQUESTED" && "$(normalize_version "$REQUESTED")" != "$TARGET_VERSION" ]]; then
  fail "版本响应不匹配：请求 $(normalize_version "$REQUESTED")，返回 $TARGET_VERSION。"
fi

CUR="$(current_version || true)"
if [[ -z "$REQUESTED" && -n "$CUR" ]]; then
  CMP="$(version_compare "$TARGET_VERSION" "$CUR")"
  if [[ "$CMP" == "same" ]]; then
    echo "当前版本：v$CUR"
    echo "最新版本：v$TARGET_VERSION"
    ok "当前已经是最新版本，无需更新。"
    exit 0
  fi
  if [[ "$CMP" == "older" ]]; then
    echo "当前版本：v$CUR"
    echo "最新正式版本：v$TARGET_VERSION"
    ok "当前安装版本高于最新正式版本，不执行降级。"
    exit 0
  fi
fi

ARCHIVE="$TMP/xvpn-panel-v${TARGET_VERSION}.zip"
SUMS="$TMP/SHA256SUMS.txt"
info "正在下载 XVPN Panel v$TARGET_VERSION..."
curl -fL --connect-timeout 10 --max-time 180 "$ASSET_URL" -o "$ARCHIVE"
curl -fsSL --connect-timeout 8 --max-time 30 "$SUMS_URL" -o "$SUMS"

EXPECTED="$(awk -v n="xvpn-panel-v${TARGET_VERSION}.zip" '$2==n || $2=="*"n {print $1; exit}' "$SUMS")"
[[ "$EXPECTED" =~ ^[0-9a-fA-F]{64}$ ]] || fail "SHA256SUMS.txt 中未找到有效校验值。"
ACTUAL="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
[[ "${ACTUAL,,}" == "${EXPECTED,,}" ]] || fail "SHA-256 校验失败，已停止安装。"
ok "SHA-256 校验通过"

unzip -t "$ARCHIVE" >/dev/null || fail "下载包 ZIP 完整性检查失败。"
ok "ZIP 完整性检查通过"
unzip -q "$ARCHIVE" -d "$TMP/src"
INSTALL="$(find "$TMP/src" -maxdepth 5 -type f -name install.sh -print | head -n1)"
[[ -n "$INSTALL" ]] || fail "安装包中未找到 install.sh。"
DIR="$(dirname "$INSTALL")"
[[ -f "$DIR/run.py" && -d "$DIR/app" && -f "$DIR/VERSION" && -f "$DIR/xvpn" ]] || fail "安装包结构不完整。"
PACKAGE_VERSION="$(tr -d '[:space:]' < "$DIR/VERSION")"
[[ "$PACKAGE_VERSION" == "$TARGET_VERSION" ]] || fail "版本不匹配：请求 v$TARGET_VERSION，包内为 v$PACKAGE_VERSION。"

if [[ -n "$REQUESTED" && -n "$CUR" && "$CUR" != "$TARGET_VERSION" ]]; then
  CMP="$(version_compare "$TARGET_VERSION" "$CUR")"
  if [[ "$CMP" == "older" ]]; then
    echo
    echo "当前版本：v$CUR"
    echo "目标版本：v$TARGET_VERSION"
    warn "这是降级操作。安装器会先备份现有数据库，但旧版未必兼容新版数据库结构。"
    read -r -p "确认继续？ [y/N]: " CONFIRM
    [[ "${CONFIRM,,}" == "y" || "${CONFIRM,,}" == "yes" ]] || { echo "已取消。"; exit 0; }
  fi
fi

info "开始安装 v$TARGET_VERSION..."
exec bash "$INSTALL"
