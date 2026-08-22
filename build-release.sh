#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([._-]?[A-Za-z]+([._-]?[0-9]+)?)?$ ]] || { echo "[ERROR] VERSION 无效：$VERSION" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "[ERROR] 需要 python3" >&2; exit 1; }

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

python3 - "$STAGE/$PKG" "$OUT/$PKG.zip" "$OUT/SHA256SUMS.txt" <<'PY'
import ast
import hashlib
import pathlib
import sys
import zipfile

root = pathlib.Path(sys.argv[1])
archive = pathlib.Path(sys.argv[2])
sums = pathlib.Path(sys.argv[3])

for path in root.rglob('*.py'):
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
print('Python syntax check: OK')

archive.unlink(missing_ok=True)
with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for path in sorted(root.rglob('*')):
        if path.is_file():
            zf.write(path, path.relative_to(root.parent).as_posix())

with zipfile.ZipFile(archive, 'r') as zf:
    bad = zf.testzip()
    if bad:
        raise SystemExit(f'ZIP integrity check failed: {bad}')
    names = set(zf.namelist())
    required = {
        f'{root.name}/install.sh',
        f'{root.name}/selftest.py',
        f'{root.name}/VERSION',
        f'{root.name}/app/__init__.py',
        f'{root.name}/app/api.py',
        f'{root.name}/app/node_profile.py',
    }
    missing = sorted(required - names)
    if missing:
        raise SystemExit('release archive missing: ' + ', '.join(missing))

sha = hashlib.sha256(archive.read_bytes()).hexdigest()
sums.write_text(f'{sha}  {archive.name}\n', encoding='utf-8')
print('ZIP integrity check: OK')
print('SHA-256:', sha)
PY

echo "[OK] 发布包：$OUT/$PKG.zip"
echo "[OK] 校验文件：$OUT/SHA256SUMS.txt"
cat "$OUT/SHA256SUMS.txt"
