#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="xvpn-panel"
APP_DIR="/opt/xvpn-panel"
DATA_DIR="/var/lib/xvpn-panel"
ENV_FILE="/etc/xvpn-panel.env"
SERVICE_FILE="/etc/systemd/system/xvpn-panel.service"
NGINX_SITE="/etc/nginx/sites-available/xvpn-panel"
BACKUP_DIR="/var/backups/xvpn-panel"
LEGACY_APP_DIR="/opt/vpn-panel"
LEGACY_DATA_DIR="/var/lib/vpn-panel"
LEGACY_ENV_FILE="/etc/vpn-panel.env"
LEGACY_SERVICE="vpn-panel.service"
LEGACY_BACKUP_SERVICE="vpn-panel-backup.service"
LEGACY_BACKUP_TIMER="vpn-panel-backup.timer"
LEGACY_NGINX_SITE="/etc/nginx/sites-available/vpn-panel"
LEGACY_NGINX_LINK="/etc/nginx/sites-enabled/vpn-panel"
LEGACY_BACKUP_DIR="/var/backups/vpn-panel"
APP_PORT="26818"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRESH_INSTALL=0
ADMIN_PASSWORD=""
DOMAIN=""
USE_CF="0"
HTTPS_OK="0"
UPGRADE_MODE="0"
RECONFIGURE_DOMAIN="0"
LEGACY_SERVICE_WAS_ACTIVE="0"
INSTALL_VERSION="$(tr -d '[:space:]' < "$SCRIPT_DIR/VERSION" 2>/dev/null || echo unknown)"
[[ "${1:-}" == "--reconfigure-domain" ]] && RECONFIGURE_DOMAIN="1"

C_RESET='\033[0m'; C_BLUE='\033[1;34m'; C_GREEN='\033[1;32m'; C_YELLOW='\033[1;33m'; C_RED='\033[1;31m'; C_DIM='\033[2m'
info(){ echo -e "${C_BLUE}[INFO]${C_RESET} $*"; }
ok(){ echo -e "${C_GREEN}[OK]${C_RESET} $*"; }
warn(){ echo -e "${C_YELLOW}!${C_RESET} $*"; }
fail(){ echo -e "${C_RED}[ERROR]${C_RESET} $*" >&2; exit 1; }

on_error(){
  local line="${1:-unknown}"
  trap - ERR
  if [[ "${LEGACY_SERVICE_WAS_ACTIVE:-0}" == "1" ]] && ! systemctl is-active --quiet xvpn-panel 2>/dev/null; then
    systemctl start "$LEGACY_SERVICE" >/dev/null 2>&1 || true
  fi
  fail "安装在第 $line 行失败。可执行：journalctl -u xvpn-panel -n 100 --no-pager 查看服务日志。"
}
trap 'on_error $LINENO' ERR

[[ ${EUID} -eq 0 ]] || fail "请使用 root 运行：bash install.sh"
[[ -f "$SCRIPT_DIR/run.py" && -d "$SCRIPT_DIR/app" ]] || fail "请在解压后的 XVPN Panel 发布目录内运行 install.sh"
[[ "$SCRIPT_DIR" != "$APP_DIR" ]] || fail "请不要直接在 /opt/xvpn-panel 内运行安装器；请从解压目录运行。"

clear || true
echo "========================================"
echo "        XVPN Panel v$INSTALL_VERSION"
echo "          自动安装程序"
echo "========================================"
echo

if [[ -f /etc/os-release ]]; then
  . /etc/os-release
  case "${ID:-}" in
    debian|ubuntu) ;;
    *) warn "当前系统 ${PRETTY_NAME:-unknown} 未列入首选支持范围；推荐 Debian 12。" ;;
  esac
else
  fail "无法识别 Linux 发行版"
fi

info "[1/7] 安装系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx curl ca-certificates sqlite3 dnsutils tzdata unzip
ok "系统依赖完成"

# One-time migration from legacy pre-XVPN internal names.
LEGACY_MIGRATED=0
if [[ ! -f "$ENV_FILE" && -f "$LEGACY_ENV_FILE" ]]; then
  cp -a "$LEGACY_ENV_FILE" "$ENV_FILE"
  LEGACY_MIGRATED=1
fi
if [[ ! -f "$DATA_DIR/panel.db" && -f "$LEGACY_DATA_DIR/panel.db" ]]; then
  mkdir -p "$DATA_DIR"
  cp -a "$LEGACY_DATA_DIR/." "$DATA_DIR/"
  LEGACY_MIGRATED=1
fi
if [[ ! -f "$NGINX_SITE" && -f "$LEGACY_NGINX_SITE" ]]; then
  cp -a "$LEGACY_NGINX_SITE" "$NGINX_SITE"
  ln -sfn "$NGINX_SITE" /etc/nginx/sites-enabled/xvpn-panel
  rm -f "$LEGACY_NGINX_LINK"
  LEGACY_MIGRATED=1
fi
if [[ -d "$LEGACY_BACKUP_DIR" && ! -e "$BACKUP_DIR" ]]; then
  mkdir -p "$(dirname "$BACKUP_DIR")"
  cp -a "$LEGACY_BACKUP_DIR" "$BACKUP_DIR"
  LEGACY_MIGRATED=1
fi
if [[ "$LEGACY_MIGRATED" == "1" ]]; then
  ok "检测到旧版内部命名，已迁移到 XVPN 路径；现有数据和配置已保留"
fi

if [[ -f "$ENV_FILE" && -f "$DATA_DIR/panel.db" ]]; then
  UPGRADE_MODE="1"
  if [[ "$RECONFIGURE_DOMAIN" != "1" && -f "$NGINX_SITE" ]]; then
    DOMAIN="$(awk '/^[[:space:]]*server_name[[:space:]]+/ {gsub(/;/, "", $2); if ($2 != "_") {print $2; exit}}' "$NGINX_SITE" 2>/dev/null || true)"
  fi
  ok "检测到现有 XVPN Panel，将进入原地升级模式；数据库、加密密钥和管理员密码都会保留"
fi

PUBLIC_IP="$(curl -4fsS --max-time 8 https://api.ipify.org 2>/dev/null || true)"
if [[ -z "$PUBLIC_IP" ]]; then
  PUBLIC_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
fi

info "[2/7] 配置访问域名"
if [[ "$UPGRADE_MODE" == "1" && "$RECONFIGURE_DOMAIN" != "1" ]]; then
  if [[ -n "$DOMAIN" ]]; then
    ok "升级沿用现有域名：$DOMAIN（如需更换，安装完成后执行 xvpn -> 域名 / HTTPS 管理）"
  else
    warn "升级模式未识别到域名，将保留现有 Web 配置；稍后可执行 xvpn domain 配置。"
  fi
else
  echo
  [[ -n "$PUBLIC_IP" ]] && echo "当前服务器 IPv4：$PUBLIC_IP"
  read -r -p "是否现在配置域名并自动申请 HTTPS？ [Y/n]: " want_domain
  if [[ "${want_domain,,}" == "n" || "${want_domain,,}" == "no" ]]; then
    DOMAIN=""
    warn "已跳过域名配置，将先使用 HTTP；安装后执行 xvpn domain 可随时配置 HTTPS。"
  else
    while true; do
      read -r -p "请输入 Panel 域名（例如 panel.example.com）： " DOMAIN
      DOMAIN="${DOMAIN,,}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"
      if [[ "$DOMAIN" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]; then break; fi
      warn "域名格式不正确，请只输入域名，不要带 http://、https:// 或路径。"
    done

    echo
    read -r -p "是否开启 Cloudflare 小云朵（Proxied）？ [y/N]: " ans
    if [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]]; then
      USE_CF="1"
      echo "Cloudflare 设置："
      echo "  A 记录：${DOMAIN} -> ${PUBLIC_IP:-本机公网IP}"
      echo "  小云朵：可以开启"
      echo "  HTTPS 成功后：SSL/TLS 使用 Full (strict)"
    else
      echo "DNS 设置：A 记录 ${DOMAIN} -> ${PUBLIC_IP:-本机公网IP}"
    fi
    echo

    while true; do
      RESOLVED="$(dig +short A "$DOMAIN" 2>/dev/null | head -n1 || true)"
      if [[ -n "$RESOLVED" ]]; then
        if [[ "$USE_CF" == "1" ]]; then ok "域名已能解析（Cloudflare 模式显示 Cloudflare IP 属正常）"; break; fi
        if [[ -z "$PUBLIC_IP" || "$RESOLVED" == "$PUBLIC_IP" ]]; then ok "DNS 已正确解析到本机：$RESOLVED"; break; fi
        warn "当前解析结果：$RESOLVED；本机 IPv4：${PUBLIC_IP:-未知}"
      else
        warn "暂时查不到 $DOMAIN 的 A 记录"
      fi
      read -r -p "完成 DNS 后按 Enter 重新检测；输入 s 跳过检测继续： " retry
      [[ "${retry,,}" == "s" ]] && break
    done
  fi
fi

info "[3/7] 准备应用与数据"
mkdir -p "$APP_DIR" "$DATA_DIR" "$BACKUP_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
if [[ -f "$ENV_FILE" || -f "$DATA_DIR/panel.db" || -f "$APP_DIR/data/panel.db" ]]; then
  mkdir -p "$BACKUP_DIR/$STAMP"
  [[ -f "$ENV_FILE" ]] && cp -a "$ENV_FILE" "$BACKUP_DIR/$STAMP/xvpn-panel.env"
  [[ -f "$DATA_DIR/panel.db" ]] && cp -a "$DATA_DIR/panel.db" "$BACKUP_DIR/$STAMP/panel.db"
  [[ -f "$APP_DIR/data/panel.db" ]] && cp -a "$APP_DIR/data/panel.db" "$BACKUP_DIR/$STAMP/panel-legacy.db"
  ok "旧配置/数据库已备份到 $BACKUP_DIR/$STAMP"
fi

# Older-version database location migration.
if [[ ! -f "$DATA_DIR/panel.db" && -f "$APP_DIR/data/panel.db" ]]; then
  cp -a "$APP_DIR/data/panel.db" "$DATA_DIR/panel.db"
  ok "已迁移旧版数据库"
fi
# Persistent data stays outside the application directory during upgrades.
rm -rf "$APP_DIR/data" 2>/dev/null || true

# Replace application code while keeping persistent data outside APP_DIR.
find "$APP_DIR" -mindepth 1 -maxdepth 1 ! -name '.venv' ! -name 'data' -exec rm -rf {} + 2>/dev/null || true
cp -a "$SCRIPT_DIR/app" "$APP_DIR/"
cp -a "$SCRIPT_DIR/run.py" "$APP_DIR/"
cp -a "$SCRIPT_DIR/requirements.txt" "$APP_DIR/"
cp -a "$SCRIPT_DIR/reset-admin-password.py" "$APP_DIR/"
cp -a "$SCRIPT_DIR/backup-worker.py" "$APP_DIR/"
cp -a "$SCRIPT_DIR/VERSION" "$APP_DIR/"
cp -a "$SCRIPT_DIR/xvpn" "$APP_DIR/"
cp -a "$SCRIPT_DIR/install-online.sh" "$APP_DIR/"
cp -a "$SCRIPT_DIR/domain-manager.sh" "$APP_DIR/"
chmod 755 "$APP_DIR/xvpn" "$APP_DIR/install-online.sh" "$APP_DIR/domain-manager.sh"
ln -sfn "$APP_DIR/xvpn" /usr/local/bin/xvpn
rm -f /etc/xvpn-panel-update.conf /etc/vpn-panel-update.conf 2>/dev/null || true
rm -rf "$APP_DIR/app/__pycache__"

if ! id -u xvpn-panel >/dev/null 2>&1; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin xvpn-panel
fi
chown -R root:root "$APP_DIR"
chown -R xvpn-panel:xvpn-panel "$DATA_DIR"
chmod 750 "$DATA_DIR"
ok "应用目录完成"
ok "已安装管理命令：xvpn"

info "[4/7] 创建 Python 环境与安全配置"
if [[ -f "$DATA_DIR/panel.db" && ! -f "$ENV_FILE" ]]; then
  fail "检测到已有数据库但 /etc/xvpn-panel.env 丢失。为避免生成新 FERNET_KEY 导致旧节点无法解密，请先恢复安全配置备份。"
fi
rm -rf "$APP_DIR/.venv"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" >/dev/null

if [[ ! -f "$ENV_FILE" ]]; then
  FRESH_INSTALL=1
  SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  FERNET_KEY="$("$APP_DIR/.venv/bin/python" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  ADMIN_PASSWORD="$(python3 - <<'PY'
import secrets,string
chars=string.ascii_letters+string.digits
while True:
    p=''.join(secrets.choice(chars) for _ in range(14))
    if any(x.islower() for x in p) and any(x.isupper() for x in p) and any(x.isdigit() for x in p):
        print(p); break
PY
)"
  cat > "$ENV_FILE" <<EOF
SECRET_KEY=$SECRET_KEY
FERNET_KEY=$FERNET_KEY
DATABASE_PATH=$DATA_DIR/panel.db
ADMIN_PASSWORD=$ADMIN_PASSWORD
PANEL_NAME=XVPN Panel
PANEL_SUBTITLE=私人访问控制台
ADMIN_ALLOWED_IPS=
TRUST_PROXY=1
TOKEN_DAYS=30
COOKIE_SECURE=0
EOF
  chmod 600 "$ENV_FILE"
  ok "已自动生成加密密钥与管理员初始密码"
else
  # Keep existing crypto/database credentials, only normalize deployment-specific values.
  if grep -q '^DATABASE_PATH=' "$ENV_FILE"; then sed -i "s#^DATABASE_PATH=.*#DATABASE_PATH=$DATA_DIR/panel.db#" "$ENV_FILE"; else echo "DATABASE_PATH=$DATA_DIR/panel.db" >> "$ENV_FILE"; fi
  grep -q '^TRUST_PROXY=' "$ENV_FILE" && sed -i 's/^TRUST_PROXY=.*/TRUST_PROXY=1/' "$ENV_FILE" || echo 'TRUST_PROXY=1' >> "$ENV_FILE"
  # 继续保留旧版英文副标题自动中文化；自定义过的副标题保持不变。
  if grep -q '^PANEL_SUBTITLE=Private Access Console$' "$ENV_FILE"; then
    sed -i 's/^PANEL_SUBTITLE=Private Access Console$/PANEL_SUBTITLE=私人访问控制台/' "$ENV_FILE"
  elif ! grep -q '^PANEL_SUBTITLE=' "$ENV_FILE"; then
    echo 'PANEL_SUBTITLE=私人访问控制台' >> "$ENV_FILE"
  fi
  # Preserve the current cookie security mode on upgrades. Reconfiguration may temporarily use HTTP.
  if [[ "$UPGRADE_MODE" != "1" || "$RECONFIGURE_DOMAIN" == "1" ]]; then
    grep -q '^COOKIE_SECURE=' "$ENV_FILE" && sed -i 's/^COOKIE_SECURE=.*/COOKIE_SECURE=0/' "$ENV_FILE" || echo 'COOKIE_SECURE=0' >> "$ENV_FILE"
  fi
  if [[ ! -f "$DATA_DIR/panel.db" ]]; then
    FRESH_INSTALL=1
    ADMIN_PASSWORD="$(python3 - <<'PY2'
import secrets,string
chars=string.ascii_letters+string.digits
while True:
    p=''.join(secrets.choice(chars) for _ in range(14))
    if any(x.islower() for x in p) and any(x.isupper() for x in p) and any(x.isdigit() for x in p):
        print(p); break
PY2
)"
    grep -q '^ADMIN_PASSWORD=' "$ENV_FILE" && sed -i "s/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=$ADMIN_PASSWORD/" "$ENV_FILE" || echo "ADMIN_PASSWORD=$ADMIN_PASSWORD" >> "$ENV_FILE"
  fi
  chmod 600 "$ENV_FILE"
  ok "检测到已有安全配置，已保留原加密密钥"
fi

# Rename only the untouched historical default; custom panel names are preserved.
if grep -q '^PANEL_NAME=VPN Panel$' "$ENV_FILE" 2>/dev/null; then
  sed -i 's/^PANEL_NAME=VPN Panel$/PANEL_NAME=XVPN Panel/' "$ENV_FILE"
fi
if [[ -f "$DATA_DIR/panel.db" ]]; then
  sqlite3 "$DATA_DIR/panel.db" "UPDATE system_settings SET value='XVPN Panel' WHERE key='panel_name' AND value='VPN Panel';" 2>/dev/null || true
fi

if systemctl is-active --quiet "$LEGACY_SERVICE" 2>/dev/null; then
  LEGACY_SERVICE_WAS_ACTIVE="1"
  systemctl stop "$LEGACY_SERVICE"
fi
systemctl stop "$LEGACY_BACKUP_TIMER" >/dev/null 2>&1 || true

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=XVPN Panel
After=network.target

[Service]
Type=simple
User=xvpn-panel
Group=xvpn-panel
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/gunicorn --workers 1 --threads 4 --bind 127.0.0.1:$APP_PORT --access-logfile - --error-logfile - run:app
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable xvpn-panel >/dev/null
systemctl restart xvpn-panel
sleep 2
systemctl is-active --quiet xvpn-panel || { journalctl -u xvpn-panel -n 80 --no-pager; fail "XVPN Panel 服务启动失败"; }
curl -fsS "http://127.0.0.1:$APP_PORT/api/v1/health" >/dev/null || fail "Panel 健康检查失败"
ok "XVPN Panel 服务已启动"

# Remove legacy management entry points only after the new service is healthy.
systemctl disable --now "$LEGACY_SERVICE" >/dev/null 2>&1 || true
systemctl disable --now "$LEGACY_BACKUP_TIMER" >/dev/null 2>&1 || true
rm -f /usr/local/bin/vpn

# Reliable automatic-backup scheduler. The timer wakes every five minutes;
# actual due-time decisions are stored in the Panel database and managed from the web UI.
BACKUP_SERVICE_FILE="/etc/systemd/system/xvpn-panel-backup.service"
BACKUP_TIMER_FILE="/etc/systemd/system/xvpn-panel-backup.timer"
cat > "$BACKUP_SERVICE_FILE" <<EOF
[Unit]
Description=XVPN Panel Scheduled Backup Check
After=network-online.target xvpn-panel.service
Wants=network-online.target

[Service]
Type=oneshot
User=xvpn-panel
Group=xvpn-panel
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/backup-worker.py
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=$DATA_DIR
EOF

cat > "$BACKUP_TIMER_FILE" <<EOF
[Unit]
Description=Run XVPN Panel Backup Scheduler

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
AccuracySec=30s
Persistent=true
Unit=xvpn-panel-backup.service

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now xvpn-panel-backup.timer >/dev/null
rm -f /etc/systemd/system/vpn-panel.service /etc/systemd/system/vpn-panel-backup.service /etc/systemd/system/vpn-panel-backup.timer
systemctl daemon-reload
ok "自动备份定时器已启用（网页开启自动备份后按设置执行）"

# ADMIN_PASSWORD is bootstrap-only. Remove plaintext after the first successful start.
if [[ "$FRESH_INSTALL" == "1" ]]; then
  sed -i '/^ADMIN_PASSWORD=/d' "$ENV_FILE"
fi

info "[5/7] 配置 Nginx 反向代理"
# Allow browser backup uploads up to 50MB. Upgrade existing site in place.
if [[ -f "$NGINX_SITE" ]]; then
  sed -i 's/client_max_body_size[[:space:]]\+[0-9]\+[mM];/client_max_body_size 52m;/' "$NGINX_SITE" || true
fi
if [[ "$UPGRADE_MODE" == "1" && "$RECONFIGURE_DOMAIN" != "1" && -f "$NGINX_SITE" ]]; then
  nginx -t
  systemctl reload nginx
  ok "升级保留现有 Nginx / Cloudflare 配置，并已应用备份上传限制"
else
if [[ -n "$DOMAIN" ]]; then
  SERVER_NAME="$DOMAIN"
else
  SERVER_NAME="_"
fi
cat > "$NGINX_SITE" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $SERVER_NAME;

    client_max_body_size 52m;

    location / {
        proxy_pass http://127.0.0.1:$APP_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 15s;
        proxy_read_timeout 60s;
    }
}
EOF
ln -sfn "$NGINX_SITE" /etc/nginx/sites-enabled/xvpn-panel
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx >/dev/null
systemctl reload nginx
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "Nginx Full" >/dev/null || true
  ok "检测到 UFW 已启用，已放行 Nginx 80/443"
fi
ok "Nginx 已配置，应用内部端口仅监听 127.0.0.1:$APP_PORT"

fi

info "[6/7] 配置 HTTPS"
if [[ "$UPGRADE_MODE" == "1" && "$RECONFIGURE_DOMAIN" != "1" && -n "$DOMAIN" && -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]]; then
  HTTPS_OK=1
  ok "升级沿用现有 HTTPS 证书，不重复申请"
elif [[ "$UPGRADE_MODE" == "1" && "$RECONFIGURE_DOMAIN" != "1" && -z "$DOMAIN" ]]; then
  warn "无法识别现有域名，已保留原 Nginx 配置并跳过证书变更"
else
if [[ -n "$DOMAIN" ]]; then
  info "正在为 $DOMAIN 申请并安装 HTTPS 证书..."
  if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect; then
    HTTPS_OK=1
    grep -q '^COOKIE_SECURE=' "$ENV_FILE" && sed -i 's/^COOKIE_SECURE=.*/COOKIE_SECURE=1/' "$ENV_FILE" || echo 'COOKIE_SECURE=1' >> "$ENV_FILE"
    systemctl restart xvpn-panel
    ok "Let's Encrypt HTTPS 证书申请并安装成功"
    systemctl enable --now certbot.timer >/dev/null 2>&1 || true
  else
    warn "HTTPS 证书申请失败，但 Panel 核心服务已经部署完成。"
    if [[ "$USE_CF" == "1" ]]; then
      echo "请检查："
      echo "  - Cloudflare A 记录是否指向本服务器"
      echo "  - 80/443 端口是否放行"
      echo "  - 临时关闭 Cloudflare Always Use HTTPS / 强制 HTTPS Redirect Rule 后重试"
      echo "  - 必要时可临时把小云朵切为 DNS only，申请成功后再打开"
    else
      echo "请检查 DNS A 记录和服务器 80/443 端口。"
    fi
    echo "修复后重新运行：certbot --nginx -d $DOMAIN --redirect"
  fi
else
  warn "HTTP 测试模式跳过 HTTPS"
fi

fi

info "[7/7] 最终检查"
if [[ "$HTTPS_OK" == "1" ]]; then
  curl -fsS --max-time 15 "https://$DOMAIN/api/v1/health" >/dev/null || warn "公网 HTTPS 健康检查暂未通过，可能是 DNS/Cloudflare 刚修改尚未生效"
fi
if [[ "${LEGACY_MIGRATED:-0}" == "1" ]]; then
  rm -rf "$LEGACY_APP_DIR" "$LEGACY_DATA_DIR" "$LEGACY_BACKUP_DIR" 2>/dev/null || true
  rm -f "$LEGACY_ENV_FILE" "$LEGACY_NGINX_SITE" "$LEGACY_NGINX_LINK" /usr/local/bin/vpn 2>/dev/null || true
  if id -u vpn-panel >/dev/null 2>&1; then
    userdel vpn-panel >/dev/null 2>&1 || true
  fi
  ok "旧 vpn-panel 内部路径已清理，运行环境已统一为 xvpn-panel"
fi

ok "安装流程完成"

echo
echo "========================================"
if [[ "$UPGRADE_MODE" == "1" ]]; then
  echo "             升级结果"
else
  echo "             部署结果"
fi
echo "========================================"
if [[ -n "$DOMAIN" && "$HTTPS_OK" == "1" ]]; then
  echo "后台地址：https://$DOMAIN/admin/login"
  echo "App API：https://$DOMAIN/api/v1"
elif [[ -n "$DOMAIN" ]]; then
  echo "临时后台：http://$DOMAIN/admin/login"
  echo "状态：HTTPS 申请失败，请按上面的提示处理"
else
  echo "后台地址：http://${PUBLIC_IP:-服务器IP}/admin/login"
  echo "状态：HTTP 测试模式"
fi
if [[ "$UPGRADE_MODE" == "1" ]]; then
  echo "版本：v$INSTALL_VERSION（数据与现有密码已保留）"
fi
if [[ "$FRESH_INSTALL" == "1" ]]; then
  echo "管理员初始用户名：admin"
  echo "管理员初始密码：$ADMIN_PASSWORD"
  echo "提示：请立即复制保存；服务器不会继续以明文保存这个初始密码。"
else
  echo "管理员账户：保留现有用户名与密码（升级不会重置）"
fi
if [[ "$USE_CF" == "1" && "$HTTPS_OK" == "1" ]]; then
  echo "Cloudflare：小云朵可保持开启，请将 SSL/TLS 设置为 Full (strict)"
fi
echo "========================================"
echo "管理菜单：xvpn"
echo "更新：xvpn check 检查更新；xvpn update 直接更新到最新正式版；xvpn update v版本 安装或重装指定版本"
echo "修改管理员密码：登录后台 -> 设置"
echo "域名 / HTTPS：执行 xvpn -> 域名 / HTTPS 管理"
echo "忘记管理员密码：执行 xvpn -> 重置管理员密码"
echo
