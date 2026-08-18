#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
[[ -n "$VERSION" ]] || { echo "VERSION 为空" >&2; exit 1; }
OUT_DIR="$ROOT/dist"
PKG="vpn-panel-v${VERSION}.zip"
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
python3 - "$ROOT" "$OUT_DIR/$PKG" "$VERSION" <<'PY'
import os,sys,zipfile
from pathlib import Path
root=Path(sys.argv[1]).resolve(); out=Path(sys.argv[2]).resolve(); version=sys.argv[3]
ignore_dirs={'.git','.venv','dist','__pycache__','.pytest_cache','.idea','.vscode'}
ignore_names={'.DS_Store'}
suffix=version.rsplit("-",1)[-1] if "-" in version else version
prefix=f"vpn-panel-{suffix}/"
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
)
echo
echo "发布资产已生成："
echo "  $OUT_DIR/$PKG"
echo "  $OUT_DIR/SHA256SUMS.txt"
echo
echo "建议 GitHub Release Tag：v$VERSION"
