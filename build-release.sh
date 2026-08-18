#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
[[ -n "$VERSION" ]] || { echo "VERSION 为空" >&2; exit 1; }

bash "$ROOT/release-check.sh"

OUT_DIR="$ROOT/dist"
PKG="xvpn-panel-v${VERSION}.zip"
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
python3 - "$ROOT" "$OUT_DIR/$PKG" "$VERSION" <<'PY'
import sys,zipfile
from pathlib import Path
root=Path(sys.argv[1]).resolve(); out=Path(sys.argv[2]).resolve(); version=sys.argv[3]
ignore_dirs={'.git','.venv','dist','__pycache__','.pytest_cache','.idea','.vscode'}
ignore_names={'.DS_Store'}
prefix=f"xvpn-panel-{version}/"
with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    for p in sorted(root.rglob('*')):
        rel=p.relative_to(root)
        if any(part in ignore_dirs for part in rel.parts):
            continue
        if p.name in ignore_names or p==out:
            continue
        if p.is_file():
            z.write(p, prefix + rel.as_posix())
print(out)
PY
(
  cd "$OUT_DIR"
  sha256sum "$PKG" > SHA256SUMS.txt
  unzip -t "$PKG" >/dev/null
)

PKG_VERSION="$(unzip -p "$OUT_DIR/$PKG" "xvpn-panel-${VERSION}/VERSION" | tr -d '[:space:]')"
[[ "$PKG_VERSION" == "$VERSION" ]] || { echo "发布包 VERSION 不一致" >&2; exit 1; }
unzip -p "$OUT_DIR/$PKG" "xvpn-panel-${VERSION}/app/templates/dashboard.html" | grep -q '今日流量概览' || { echo "发布包缺少新版首页" >&2; exit 1; }
unzip -p "$OUT_DIR/$PKG" "xvpn-panel-${VERSION}/install-online.sh" | grep -q 'releases/latest' || { echo "发布包更新器不是 Release 模式" >&2; exit 1; }

echo
echo "发布资产已生成："
echo "  $OUT_DIR/$PKG"
echo "  $OUT_DIR/SHA256SUMS.txt"
echo
echo "Release Tag：v$VERSION"
