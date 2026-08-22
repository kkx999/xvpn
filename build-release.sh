#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([._-]?[A-Za-z]+([._-]?[0-9]+)?)?$ ]] || { echo "[ERROR] VERSION 无效：$VERSION" >&2; exit 1; }

OUT="${1:-$ROOT/dist}"
PKG="xvpn-panel-v$VERSION"
STAGE="$(mktemp -d /tmp/xvpn-release-XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$OUT" "$STAGE/$PKG"

for path in \
  app \
  VERSION \
  README.md \
  APP_API.md \
  requirements.txt \
  run.py \
  selftest.py \
  reset-admin-password.py \
  backup-worker.py \
  install.sh \
  install-online.sh \
  domain-manager.sh \
  xvpn
do
  [[ -e "$ROOT/$path" ]] || { echo "[ERROR] 发布文件缺失：$path" >&2; exit 1; }
  cp -a "$ROOT/$path" "$STAGE/$PKG/"
done

find "$STAGE/$PKG" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/$PKG" -type f \( -name '*.pyc' -o -name '.DS_Store' \) -delete
chmod 755 "$STAGE/$PKG/install.sh" "$STAGE/$PKG/install-online.sh" "$STAGE/$PKG/domain-manager.sh" "$STAGE/$PKG/xvpn" "$STAGE/$PKG/selftest.py"

python3 - "$STAGE/$PKG" <<'PY'
import ast, pathlib, sys
root = pathlib.Path(sys.argv[1])
for path in root.rglob('*.py'):
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
print('Python syntax check: OK')
PY

ARCHIVE="$OUT/$PKG.zip"
SUMS="$OUT/SHA256SUMS.txt"
rm -f "$ARCHIVE" "$SUMS"
(
  cd "$STAGE"
  zip -qr "$ARCHIVE" "$PKG"
)
unzip -t "$ARCHIVE" >/dev/null
(
  cd "$OUT"
  sha256sum "$PKG.zip" > SHA256SUMS.txt
)

echo "[OK] 发布包：$ARCHIVE"
echo "[OK] 校验文件：$SUMS"
cat "$SUMS"
