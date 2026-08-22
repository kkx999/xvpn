#!/usr/bin/env bash
set -Eeuo pipefail

REPO="kkx999/xvpn"
BRANCH="${XVPN_BRANCH:-main}"
TMP="$(mktemp -d /tmp/xvpn-bootstrap-XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

fail(){ echo "[ERROR] $*" >&2; exit 1; }
info(){ echo "[INFO] $*"; }
ok(){ echo "[OK] $*"; }

[[ ${EUID} -eq 0 ]] || fail "请使用 root 运行安装命令。"

if ! command -v curl >/dev/null 2>&1 || ! command -v unzip >/dev/null 2>&1; then
  command -v apt-get >/dev/null 2>&1 || fail "缺少 curl/unzip，且当前系统没有 apt-get。推荐 Debian 12。"
  info "正在安装下载依赖..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y curl unzip ca-certificates
fi

ARCHIVE="$TMP/xvpn.zip"
SRC="$TMP/src"
URL="https://github.com/$REPO/archive/refs/heads/$BRANCH.zip"

info "正在下载 XVPN Panel v1 源码..."
curl -fL --retry 3 --retry-delay 2 --connect-timeout 10 --max-time 180 "$URL" -o "$ARCHIVE" \
  || fail "下载失败，请检查服务器到 GitHub 的网络。"

unzip -t "$ARCHIVE" >/dev/null || fail "下载文件不是有效 ZIP。"
mkdir -p "$SRC"
unzip -q "$ARCHIVE" -d "$SRC"

INSTALL="$(find "$SRC" -maxdepth 3 -type f -name install.sh -print | head -n1)"
[[ -n "$INSTALL" ]] || fail "安装包中未找到 install.sh。"
DIR="$(dirname "$INSTALL")"
[[ -d "$DIR/app" && -f "$DIR/VERSION" && -f "$DIR/selftest.py" ]] || fail "源码结构不完整，已停止安装。"

VERSION="$(tr -d '[:space:]' < "$DIR/VERSION")"
ok "已获取 XVPN Panel v$VERSION"

echo
bash "$INSTALL"
