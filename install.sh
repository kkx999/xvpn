#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/xvpn-panel"
DATA_DIR="/var/lib/xvpn-panel"
BACKUP_DIR="/var/backups/xvpn-panel"
ENV_FILE="/etc/xvpn-panel.env"
SERVICE_FILE="/etc/systemd/system/xvpn-panel.service"
BACKUP_SERVICE="/etc/systemd/system/xvpn-panel-backup.service"
BACKUP_TIMER="/etc/systemd/system/xvpn-panel-backup.timer"
NGINX_SITE="/etc/nginx/sites-available/xvpn-panel"
NGINX_LINK="/etc/nginx/sites-enabled/xvpn-panel"
APP_PORT=26818
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$SCRIPT_DIR/VERSION" 2>/dev/null || echo 1.0)"

info(){ echo "[INFO] $*"; }
ok(){ echo "[OK] $*"; }
warn(){ echo "[WARN] $*"; }
fail(){ echo "[ERROR] $*" >&2; exit 1; }

env_value(){
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  awk -v k="$key" 'index($0,k"=")==1 {sub(/^[^=]*=/,""); print; exit}' "$ENV_FILE" 2>/dev/null || true
}

nginx_domain(){
  [[ -f "$NGINX_SITE" ]] || return 0
  awk '/^[[:space:]]*server_name[[:space:]]+/ {gsub(/;/,"",$2);if($2!="_"){print $2;exit}}' "$NGINX_SITE" 2>/dev/null || true
}

set_env_value(){
  local key="$1" value="$2"
  [[ -f "$ENV_FILE" ]] || return 0
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s#^${key}=.*#${key}=${value}#" "$ENV_FILE"
  else
    echo "${key}=${value}" >> "$ENV_FILE"
  fi
}

wait_panel_health(){
  local i
  for i in $(seq 1 20); do
    if curl -fsS --connect-timeout 1 --max-time 2 "http://127.0.0.1:$APP_PORT/api/v1/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_http_proxy(){
  local host="$1" i
  for i in $(seq 1 15); do
    if curl -fsS --connect-timeout 1 --max-time 3 -H "Host: $host" "http://127.0.0.1/api/v1/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_https_proxy(){
  local domain="$1" i
  for i in $(seq 1 20); do
    if curl -fsS --connect-timeout 2 --max-time 5 --resolve "$domain:443:127.0.0.1" "https://$domain/api/v1/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

restore_nginx_site(){
  local backup="$1"
  if [[ -n "$backup" && -f "$backup" ]]; then
    cp -a "$backup" "$NGINX_SITE"
    ln -sfn "$NGINX_SITE" "$NGINX_LINK"
  else
    rm -f "$NGINX_SITE" "$NGINX_LINK"
  fi
  nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
}

[[ ${EUID} -eq 0 ]] || fail "请使用 root 运行：bash install.sh"
[[ -d "$SCRIPT_DIR/app" && -f "$SCRIPT_DIR/run.py" && -f "$SCRIPT_DIR/requirements.txt" ]] || fail "请在 XVPN Panel 发布目录内运行 install.sh"

if [[ -f /etc/os-release ]]; then
  . /etc/os-release
  case "${ID:-}" in debian|ubuntu) ;; *) warn "推荐 Debian 12；当前系统：${PRETTY_NAME:-unknown}";; esac
fi

echo "========================================"
echo "      XVPN Panel Mihomo v$VERSION"
echo "========================================"

info "[1/7] 安装系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx curl ca-certificates sqlite3 dnsutils tzdata unzip
systemctl enable --now nginx >/dev/null
ok "系统依赖完成"

FRESH=0
if [[ ! -f "$ENV_FILE" || ! -f "$DATA_DIR/panel.db" ]]; then FRESH=1; fi

DOMAIN="$(env_value PANEL_DOMAIN)"
if [[ -z "$DOMAIN" ]]; then DOMAIN="$(nginx_domain)"; fi
if [[ -n "$DOMAIN" && ! "$DOMAIN" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]; then
  warn "检测到无效的历史域名配置，将重新询问域名。"
  DOMAIN=""
fi

if [[ "$FRESH" == "1" || ( -z "$DOMAIN" && ! -f "$NGINX_SITE" ) ]]; then
  info "[2/7] 配置访问域名"
  PUBLIC_IP="$(curl -4fsS --max-time 8 https://api.ipify.org 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
  [[ -n "$PUBLIC_IP" ]] && echo "当前服务器 IPv4：$PUBLIC_IP"
  read -r -p "是否现在配置域名并申请 HTTPS？ [Y/n]: " WANT_DOMAIN
  if [[ "${WANT_DOMAIN,,}" != "n" && "${WANT_DOMAIN,,}" != "no" ]]; then
    while true; do
      read -r -p "请输入 Panel 域名（例如 panel.example.com）：" DOMAIN
      DOMAIN="${DOMAIN,,}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"
      [[ "$DOMAIN" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] && break
      warn "域名格式不正确"
    done
    echo "请确认 DNS A 记录：$DOMAIN -> ${PUBLIC_IP:-服务器公网 IPv4}"
    read -r -p "DNS 已设置好后按 Enter 继续..." _
  else
    DOMAIN=""
    warn "已跳过域名，先使用 HTTP。以后执行 xvpn domain 配置 HTTPS。"
  fi
else
  if [[ -n "$DOMAIN" ]]; then
    info "[2/7] 检测到现有域名 $DOMAIN，将继续完成/保留当前配置"
  else
    info "[2/7] 检测到现有 v1 数据，将保留当前 HTTP 配置"
  fi
fi

info "[3/7] 准备程序目录"
mkdir -p "$APP_DIR" "$DATA_DIR" "$BACKUP_DIR"
if [[ -f "$ENV_FILE" || -f "$DATA_DIR/panel.db" ]]; then
  STAMP="$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$BACKUP_DIR/preinstall-$STAMP"
  [[ -f "$ENV_FILE" ]] && cp -a "$ENV_FILE" "$BACKUP_DIR/preinstall-$STAMP/xvpn-panel.env"
  [[ -f "$DATA_DIR/panel.db" ]] && cp -a "$DATA_DIR/panel.db" "$BACKUP_DIR/preinstall-$STAMP/panel.db"
fi
rm -rf "$APP_DIR/app" "$APP_DIR/.venv"
cp -a "$SCRIPT_DIR/app" "$APP_DIR/"
for f in run.py requirements.txt reset-admin-password.py backup-worker.py selftest.py VERSION xvpn install-online.sh domain-manager.sh; do
  [[ -e "$SCRIPT_DIR/$f" ]] && cp -a "$SCRIPT_DIR/$f" "$APP_DIR/"
done
chmod 755 "$APP_DIR/xvpn" "$APP_DIR/install-online.sh" "$APP_DIR/domain-manager.sh" 2>/dev/null || true
ln -sfn "$APP_DIR/xvpn" /usr/local/bin/xvpn

if ! id -u xvpn-panel >/dev/null 2>&1; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin xvpn-panel
fi
chown -R root:root "$APP_DIR"
chown -R xvpn-panel:xvpn-panel "$DATA_DIR" "$BACKUP_DIR"
chmod 750 "$DATA_DIR" "$BACKUP_DIR"
ok "程序目录完成"

info "[4/7] 创建 Python 环境和安全配置"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" >/dev/null

info "运行 v$VERSION 内置自检"
(cd "$APP_DIR" && "$APP_DIR/.venv/bin/python" selftest.py) || fail "内置自检失败，已停止安装，不会启动此版本"
ok "代码、路由、后台路径、API 与 Mihomo 节点解析自检通过"

ADMIN_PASSWORD=""
if [[ ! -f "$ENV_FILE" ]]; then
  SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
  FERNET_KEY="$("$APP_DIR/.venv/bin/python" -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
  ADMIN_PASSWORD="$(python3 - <<'PY'
import secrets,string
chars=string.ascii_letters+string.digits
while True:
    p=''.join(secrets.choice(chars) for _ in range(14))
    if any(c.islower() for c in p) and any(c.isupper() for c in p) and any(c.isdigit() for c in p):
        print(p);break
PY
)"
  cat > "$ENV_FILE" <<EOF
SECRET_KEY=$SECRET_KEY
FERNET_KEY=$FERNET_KEY
DATABASE_PATH=$DATA_DIR/panel.db
ADMIN_PASSWORD=$ADMIN_PASSWORD
PANEL_NAME=XVPN Panel
PANEL_SUBTITLE=私人访问控制台
PANEL_DOMAIN=$DOMAIN
ADMIN_ALLOWED_IPS=
TRUST_PROXY=1
TOKEN_DAYS=30
COOKIE_SECURE=0
XVPN_ANDROID_REPOSITORY=kkx999/XVPN-Android
IPAPI_IS_KEY=
EOF
  chmod 600 "$ENV_FILE"
else
  grep -q '^DATABASE_PATH=' "$ENV_FILE" && sed -i "s#^DATABASE_PATH=.*#DATABASE_PATH=$DATA_DIR/panel.db#" "$ENV_FILE" || echo "DATABASE_PATH=$DATA_DIR/panel.db" >> "$ENV_FILE"
  grep -q '^TRUST_PROXY=' "$ENV_FILE" || echo 'TRUST_PROXY=1' >> "$ENV_FILE"
  grep -q '^XVPN_ANDROID_REPOSITORY=' "$ENV_FILE" || echo 'XVPN_ANDROID_REPOSITORY=kkx999/XVPN-Android' >> "$ENV_FILE"
  grep -q '^IPAPI_IS_KEY=' "$ENV_FILE" || echo 'IPAPI_IS_KEY=' >> "$ENV_FILE"
  set_env_value PANEL_DOMAIN "$DOMAIN"
  chmod 600 "$ENV_FILE"
fi
ok "安全配置完成"

info "[5/7] 创建 systemd 服务"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=XVPN Panel
After=network-online.target
Wants=network-online.target

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
ReadWritePaths=$DATA_DIR $BACKUP_DIR

[Install]
WantedBy=multi-user.target
EOF

cat > "$BACKUP_SERVICE" <<EOF
[Unit]
Description=XVPN Panel scheduled backup check
After=xvpn-panel.service

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
ReadWritePaths=$DATA_DIR $BACKUP_DIR
EOF

cat > "$BACKUP_TIMER" <<'EOF'
[Unit]
Description=Check XVPN Panel backup schedule

[Timer]
OnBootSec=5min
OnUnitActiveSec=10min
Persistent=true
Unit=xvpn-panel-backup.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable xvpn-panel xvpn-panel-backup.timer >/dev/null
systemctl restart xvpn-panel
systemctl restart xvpn-panel-backup.timer

if ! wait_panel_health; then
  systemctl status xvpn-panel --no-pager 2>/dev/null || true
  journalctl -u xvpn-panel -n 100 --no-pager 2>/dev/null || true
  fail "Panel 在 20 秒内未通过健康检查"
fi
systemctl is-active --quiet xvpn-panel || { journalctl -u xvpn-panel -n 100 --no-pager; fail "Panel 服务未保持运行"; }
ok "Panel 服务运行正常"

info "[6/7] 配置 Nginx"
CURRENT_NGINX_DOMAIN="$(nginx_domain)"
NGINX_CHANGED=0
NGINX_BACKUP=""
if [[ ! -f "$NGINX_SITE" || "$FRESH" == "1" || ( -n "$DOMAIN" && "$CURRENT_NGINX_DOMAIN" != "$DOMAIN" ) ]]; then
  if [[ -f "$NGINX_SITE" ]]; then
    NGINX_BACKUP="/tmp/xvpn-panel-nginx-install-$(date +%Y%m%d-%H%M%S).conf"
    cp -a "$NGINX_SITE" "$NGINX_BACKUP"
  fi
  SERVER_NAME="${DOMAIN:-_}"
  cat > "$NGINX_SITE" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $SERVER_NAME;

    client_max_body_size 55m;

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
  NGINX_CHANGED=1
fi
ln -sfn "$NGINX_SITE" "$NGINX_LINK"
rm -f /etc/nginx/sites-enabled/default
if ! nginx -t; then
  [[ "$NGINX_CHANGED" == "1" ]] && restore_nginx_site "$NGINX_BACKUP"
  fail "Nginx 配置语法检查失败"
fi
systemctl reload nginx

NGINX_HOST="${DOMAIN:-localhost}"
if ! wait_http_proxy "$NGINX_HOST"; then
  warn "Nginx reload 后暂未就绪，正在重启并重试..."
  systemctl restart nginx
  if ! wait_http_proxy "$NGINX_HOST"; then
    systemctl status nginx --no-pager 2>/dev/null || true
    nginx -T 2>/dev/null | tail -n 120 || true
    [[ "$NGINX_CHANGED" == "1" ]] && restore_nginx_site "$NGINX_BACKUP"
    fail "Nginx 反向代理在重试后仍未通过健康检查"
  fi
fi
ok "Nginx 配置完成"

info "[7/7] HTTPS 与最终检查"
if [[ -n "$DOMAIN" ]]; then
  HTTPS_OK=0
  if [[ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]] && grep -q 'ssl_certificate' "$NGINX_SITE" 2>/dev/null; then
    set_env_value COOKIE_SECURE 1
    systemctl restart xvpn-panel
    if wait_https_proxy "$DOMAIN"; then
      HTTPS_OK=1
      ok "检测到现有 HTTPS 配置并验证通过"
    else
      warn "检测到现有证书，但 HTTPS 未通过验证，将让 Certbot 重新部署配置。"
    fi
  fi

  if [[ "$HTTPS_OK" != "1" ]]; then
    if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect; then
      set_env_value COOKIE_SECURE 1
      set_env_value PANEL_DOMAIN "$DOMAIN"
      systemctl restart xvpn-panel
      systemctl enable --now certbot.timer >/dev/null 2>&1 || true
      if wait_https_proxy "$DOMAIN"; then
        HTTPS_OK=1
        ok "HTTPS 已启用并验证通过"
      else
        systemctl status nginx --no-pager 2>/dev/null || true
        systemctl status xvpn-panel --no-pager 2>/dev/null || true
        fail "Certbot 已执行，但本机 HTTPS 健康检查仍未通过"
      fi
    else
      set_env_value COOKIE_SECURE 0
      systemctl restart xvpn-panel
      warn "HTTPS 自动申请失败；Panel 已通过 HTTP 运行，可稍后执行 xvpn domain。"
    fi
  fi
fi

ADMIN_PATH="$(sqlite3 "$DATA_DIR/panel.db" "SELECT value FROM system_settings WHERE key='admin_path' LIMIT 1;" 2>/dev/null | head -n1 || true)"
ADMIN_PATH="${ADMIN_PATH:-admin}"

echo
echo "========================================"
echo "XVPN Panel v$VERSION 安装完成"
if [[ -n "$DOMAIN" ]]; then
  if grep -q '^COOKIE_SECURE=1' "$ENV_FILE"; then
    echo "后台：https://$DOMAIN/$ADMIN_PATH/login"
  else
    echo "后台：http://$DOMAIN/$ADMIN_PATH/login"
  fi
else
  PUBLIC_IP="$(curl -4fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
  echo "后台：http://${PUBLIC_IP}:80/$ADMIN_PATH/login"
fi
echo "管理员用户名：admin"
[[ -n "$ADMIN_PASSWORD" ]] && echo "初始管理员密码：$ADMIN_PASSWORD"
echo
echo "管理命令：xvpn"
echo "修改后台路径：xvpn admin-path manage-xvpn"
echo "彻底卸载：xvpn uninstall"
echo "========================================"
