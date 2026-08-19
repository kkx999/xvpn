#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION" 2>/dev/null || true)"
fail(){ echo "[ERROR] $*" >&2; exit 1; }
ok(){ echo "[OK] $*"; }

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "正式 VERSION 必须是 x.y.z，当前：${VERSION:-empty}"

grep -Fq "> 当前正式版本：\`v${VERSION}\`。" "$ROOT/README.md" || fail "README 正式版本号与 VERSION 不一致"
grep -q "^  version: ${VERSION}$" "$ROOT/APP_API_OPENAPI.yaml" || fail "OpenAPI 版本号与 VERSION 不一致"
grep -q "^# XVPN Panel v${VERSION}$" "$ROOT/RELEASE_NOTES.md" || fail "Release Notes 版本号与 VERSION 不一致"
grep -Fq "**Release Tag**：\`v${VERSION}\`" "$ROOT/GITHUB_PROJECT.md" || fail "GitHub Release Tag 与 VERSION 不一致"

required=(
  app/traffic.py
  app/event_log.py
  app/app_updates.py
  app/templates/dashboard.html
  app/templates/base.html
  app/templates/login.html
  app/templates/settings.html
  app/templates/app_update.html
  install-online.sh
  install.sh
  xvpn
  APP_API.md
  APP_API_OPENAPI.yaml
)
for f in "${required[@]}"; do
  [[ -e "$ROOT/$f" ]] || fail "缺少正式发布文件：$f"
done

[[ ! -e "$ROOT/vpn" ]] || fail "源码根目录仍残留旧 vpn 管理脚本"
grep -q '今日流量概览' "$ROOT/app/templates/dashboard.html" || fail "首页不是今日流量概览版本"
grep -q 'brand-mark">X<' "$ROOT/app/templates/base.html" || fail "后台品牌标识不是 X"
grep -q 'brand-mark large">X<' "$ROOT/app/templates/login.html" || fail "登录页品牌标识不是 X"
grep -q '手动备份一次' "$ROOT/app/templates/settings.html" || fail "备份主操作不是手动备份一次"
grep -q '/app/update' "$ROOT/app/api.py" || fail "缺少 Android App 更新 API"
grep -q 'Latest Release' "$ROOT/app/templates/app_update.html" || fail "缺少 App 更新管理页"
grep -q '最低允许运行版本' "$ROOT/app/templates/app_update.html" || fail "App 更新页缺少最低版本下拉"
grep -q 'app_update_repository' "$ROOT/app/templates/app_update.html" || fail "App 更新页缺少可编辑 Android 仓库"
grep -q 'minimum_reachable' "$ROOT/app/app_updates.py" || fail "App 更新策略缺少最低版本可达性保护"
grep -q 'app_update_last_stale' "$ROOT/app/app_updates.py" || fail "App 更新缓存缺少失败状态持久化"
if grep -RIn --exclude='release-check.sh' --exclude-dir='__pycache__' -E '1\.2\.1-dev|当前测试版本' "$ROOT" >/dev/null; then
  fail "正式源码仍残留 v1.2.1 测试版本标识"
fi
grep -q 'releases/latest' "$ROOT/xvpn" || fail "xvpn check 未切到 Latest Release"
grep -q 'releases/latest' "$ROOT/install-online.sh" || fail "在线安装器未切到 Latest Release"
if grep -q 'archive/refs/heads' "$ROOT/install-online.sh"; then
  fail "在线安装器仍会安装 main 分支源码"
fi
if grep -q 'raw.githubusercontent.com/.*/VERSION' "$ROOT/xvpn"; then
  fail "xvpn check 仍在读取 main/VERSION"
fi
grep -q 'cp -a "$SCRIPT_DIR/install-online.sh" "$APP_DIR/"' "$ROOT/install.sh" || fail "安装器没有持久化本地更新器"

python3 -m py_compile "$ROOT"/app/*.py "$ROOT"/run.py "$ROOT"/backup-worker.py "$ROOT"/reset-admin-password.py
bash -n "$ROOT/xvpn"
bash -n "$ROOT/install-online.sh"
bash -n "$ROOT/install.sh"
bash -n "$ROOT/domain-manager.sh"

ok "正式发布前检查通过：v$VERSION"
