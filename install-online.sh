#!/usr/bin/env bash
set -Eeuo pipefail
REPO="kkx999/xvpn"
BRANCH="main"
TMP="$(mktemp -d /tmp/xvpn-online-XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

fail(){ echo "[ERROR] $*" >&2; exit 1; }
ok(){ echo "[OK] $*"; }
info(){ echo "[INFO] $*"; }
warn(){ echo "! $*"; }
[[ ${EUID} -eq 0 ]] || fail "请使用 root 运行。"

REQUESTED=""
case "${1:-}" in
  "") ;;
  --version|-v) REQUESTED="${2:-}"; [[ -n "$REQUESTED" ]] || fail "缺少版本号。" ;;
  v*|[0-9]*) REQUESTED="$1" ;;
  *) fail "用法：install-online.sh [--version v1.2.0]" ;;
esac

if ! command -v curl >/dev/null 2>&1 || ! command -v unzip >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y curl unzip ca-certificates python3
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

echo "========================================"
echo "       XVPN Panel 一键部署 / 升级"
echo "========================================"

ARCHIVE="$TMP/source.zip"
TARGET_VERSION=""
SOURCE_LABEL=""

if [[ -z "$REQUESTED" ]]; then
  TARGET_VERSION="$(curl -fsSL --connect-timeout 8 --max-time 15 "https://raw.githubusercontent.com/$REPO/$BRANCH/VERSION" 2>/dev/null | tr -d '[:space:]' || true)"
  [[ -n "$TARGET_VERSION" ]] || fail "无法读取在线版本信息，请确认发布源已更新。"
  info "目标版本：v$TARGET_VERSION"
  curl -fL --connect-timeout 10 --max-time 180 "https://github.com/$REPO/archive/refs/heads/$BRANCH.zip" -o "$ARCHIVE"
  SOURCE_LABEL="在线最新版"
else
  valid_version "$REQUESTED" || fail "版本格式无效，例如 v1.2.0 或 v1.2.0-rc1。"
  TARGET_VERSION="$(normalize_version "$REQUESTED")"
  TAG="v$TARGET_VERSION"
  info "目标版本：$TAG"

  RELEASE_JSON="$TMP/release.json"
  if curl -fsSL --connect-timeout 8 --max-time 20 \
      -H 'Accept: application/vnd.github+json' \
      "https://api.github.com/repos/$REPO/releases/tags/$TAG" -o "$RELEASE_JSON" 2>/dev/null; then
    readarray -t URLS < <(python3 - "$RELEASE_JSON" "$TARGET_VERSION" <<'PY'
import json,sys
j=json.load(open(sys.argv[1],encoding='utf-8')); v=sys.argv[2]
want=f'xvpn-panel-v{v}.zip'; legacy=f'vpn-panel-v{v}.zip'; asset=''; sums=''
for a in j.get('assets',[]):
    if a.get('name') in (want, legacy): asset=a.get('browser_download_url','')
    elif a.get('name')=='SHA256SUMS.txt': sums=a.get('browser_download_url','')
print(asset); print(sums)
PY
)
    ASSET_URL="${URLS[0]:-}"; SUMS_URL="${URLS[1]:-}"
    if [[ -n "$ASSET_URL" ]]; then
      info "正在获取正式发布包：$TAG"
      curl -fL --connect-timeout 10 --max-time 180 "$ASSET_URL" -o "$ARCHIVE"
      if [[ -n "$SUMS_URL" ]]; then
        curl -fsSL --connect-timeout 8 --max-time 30 "$SUMS_URL" -o "$TMP/SHA256SUMS.txt"
        EXPECTED="$(awk -v n="xvpn-panel-v${TARGET_VERSION}.zip" -v old="vpn-panel-v${TARGET_VERSION}.zip" '$2==n || $2=="*"n || $2==old || $2=="*"old {print $1; exit}' "$TMP/SHA256SUMS.txt")"
        if [[ -n "$EXPECTED" ]]; then
          ACTUAL="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
          [[ "$ACTUAL" == "$EXPECTED" ]] || fail "SHA-256 校验失败，已停止安装。"
          ok "SHA-256 校验通过"
        fi
      fi
      SOURCE_LABEL="正式发布包 $TAG"
    fi
  fi

  if [[ ! -s "$ARCHIVE" ]]; then
    warn "正式发布包未找到，尝试版本标签：$TAG"
    curl -fL --connect-timeout 10 --max-time 180 "https://github.com/$REPO/archive/refs/tags/$TAG.zip" -o "$ARCHIVE" \
      || fail "找不到版本 $TAG。请确认该版本已发布。"
    SOURCE_LABEL="版本标签 $TAG"
  fi
fi

unzip -t "$ARCHIVE" >/dev/null || fail "下载包 ZIP 完整性检查失败。"
ok "ZIP 完整性检查通过"
unzip -q "$ARCHIVE" -d "$TMP/src"
INSTALL="$(find "$TMP/src" -maxdepth 5 -type f -name install.sh -print | head -n1)"
[[ -n "$INSTALL" ]] || fail "安装包中未找到 install.sh。"
DIR="$(dirname "$INSTALL")"
[[ -f "$DIR/run.py" && -d "$DIR/app" && -f "$DIR/VERSION" ]] || fail "安装包结构不完整。"
PACKAGE_VERSION="$(tr -d '[:space:]' < "$DIR/VERSION")"
[[ "$PACKAGE_VERSION" == "$TARGET_VERSION" ]] || fail "版本不匹配：请求 v$TARGET_VERSION，包内为 v$PACKAGE_VERSION。"

CUR="$(current_version)"
if [[ -n "$REQUESTED" && -n "$CUR" && "$CUR" != "$TARGET_VERSION" ]]; then
  CMP="$(version_compare "$TARGET_VERSION" "$CUR")"
  echo
  echo "当前版本：v$CUR"
  echo "目标版本：v$TARGET_VERSION"
  echo "来源：$SOURCE_LABEL"
  [[ "$CMP" == "older" ]] && warn "这是降级操作。安装器会先备份现有数据库，但旧版未必兼容新版数据库结构。"
  read -r -p "确认继续？ [y/N]: " CONFIRM
  [[ "${CONFIRM,,}" == "y" || "${CONFIRM,,}" == "yes" ]] || { echo "已取消。"; exit 0; }
fi

exec bash "$INSTALL"
