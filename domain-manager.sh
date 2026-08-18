#!/usr/bin/env bash
set -Eeuo pipefail

APP_PORT="26818"
ENV_FILE="/etc/xvpn-panel.env"
NGINX_SITE="/etc/nginx/sites-available/xvpn-panel"
NGINX_LINK="/etc/nginx/sites-enabled/xvpn-panel"

C_RESET='\033[0m'; C_BLUE='\033[38;5;75m'; C_GREEN='\033[38;5;78m'; C_YELLOW='\033[38;5;214m'; C_RED='\033[38;5;203m'; C_BOLD='\033[1m'; C_DIM='\033[2m'; C_CYAN='\033[38;5;81m'
say(){ echo -e "$*"; }; ok(){ say "${C_GREEN}[OK]${C_RESET} $*"; }; info(){ say "${C_BLUE}[INFO]${C_RESET} $*"; }; warn(){ say "${C_YELLOW}!${C_RESET} $*"; }; err(){ say "${C_RED}[ERROR]${C_RESET} $*" >&2; }
[[ ${EUID} -eq 0 ]] || { err "请使用 root 运行。"; exit 1; }

current_domain(){
  [[ -f "$NGINX_SITE" ]] || return 0
  awk '/^[[:space:]]*server_name[[:space:]]+/ {gsub(/;/, "", $2); if ($2 != "_") {print $2; exit}}' "$NGINX_SITE" 2>/dev/null || true
}
valid_domain(){ [[ "$1" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]; }

clear || true
say "${C_BOLD}${C_BLUE}XVPN Panel 域名 / HTTPS${C_RESET}"
say "────────────────────────────────────────"
OLD_DOMAIN="$(current_domain)"
if [[ -n "$OLD_DOMAIN" ]]; then
  say "当前域名：${C_CYAN}$OLD_DOMAIN${C_RESET}"
  if [[ -f "/etc/letsencrypt/live/$OLD_DOMAIN/fullchain.pem" ]]; then say "HTTPS：${C_GREEN}已配置${C_RESET}"; else say "HTTPS：${C_YELLOW}未检测到证书${C_RESET}"; fi
else
  say "当前域名：${C_YELLOW}未配置${C_RESET}"
fi
say "────────────────────────────────────────"
echo
read -r -p "是否现在配置/更换域名并申请 HTTPS？ [y/N]: " go
[[ "${go,,}" == "y" || "${go,,}" == "yes" ]] || { warn "已取消，没有修改任何配置。"; exit 0; }

PUBLIC_IP="$(curl -4fsS --max-time 8 https://api.ipify.org 2>/dev/null || true)"
[[ -n "$PUBLIC_IP" ]] || PUBLIC_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -n "$PUBLIC_IP" ]] && say "服务器 IPv4：${C_CYAN}$PUBLIC_IP${C_RESET}"

while true; do
  read -r -p "请输入新域名（例如 panel.example.com）： " DOMAIN
  DOMAIN="${DOMAIN,,}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"
  valid_domain "$DOMAIN" && break
  warn "域名格式不正确，请只输入域名，不要带 http://、https:// 或路径。"
done

echo
read -r -p "是否开启 Cloudflare 小云朵（Proxied）？ [y/N]: " cf
USE_CF=0
if [[ "${cf,,}" == "y" || "${cf,,}" == "yes" ]]; then USE_CF=1; fi

echo
say "${C_BOLD}请先确认 DNS：${C_RESET}"
say "  A 记录：${C_CYAN}$DOMAIN${C_RESET}  →  ${C_CYAN}${PUBLIC_IP:-本机公网 IP}${C_RESET}"
if [[ "$USE_CF" == "1" ]]; then
  say "  Cloudflare：${C_GREEN}小云朵可以开启${C_RESET}"
  say "  证书成功后：SSL/TLS 建议使用 ${C_BOLD}Full (strict)${C_RESET}"
else
  say "  代理状态：DNS only / 普通 DNS"
fi
say "${C_DIM}如果存在错误的 AAAA 记录，请先修正或删除，以免 HTTPS 验证走到错误 IPv6。${C_RESET}"
echo

while true; do
  RESOLVED="$(dig +short A "$DOMAIN" 2>/dev/null | head -n1 || true)"
  if [[ -n "$RESOLVED" ]]; then
    if [[ "$USE_CF" == "1" ]]; then ok "域名已能解析（Cloudflare 模式显示 Cloudflare IP 属正常）"; break; fi
    if [[ -z "$PUBLIC_IP" || "$RESOLVED" == "$PUBLIC_IP" ]]; then ok "DNS 已正确解析：$RESOLVED"; break; fi
    warn "当前 A 记录：$RESOLVED；本机 IPv4：${PUBLIC_IP:-未知}"
  else
    warn "暂时查不到 $DOMAIN 的 A 记录"
  fi
  read -r -p "DNS 设置完成后按 Enter 重试；输入 q 取消： " retry
  [[ "${retry,,}" == "q" ]] && { warn "已取消，没有修改现有 Nginx 配置。"; exit 0; }
done

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/tmp/xvpn-panel-nginx-$STAMP.conf"
[[ -f "$NGINX_SITE" ]] && cp -a "$NGINX_SITE" "$BACKUP"

cat > "$NGINX_SITE" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

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
NGINX
ln -sfn "$NGINX_SITE" "$NGINX_LINK"
rm -f /etc/nginx/sites-enabled/default
if ! nginx -t; then
  [[ -f "$BACKUP" ]] && cp -a "$BACKUP" "$NGINX_SITE"
  nginx -t >/dev/null 2>&1 || true
  err "Nginx 配置检查失败，已恢复原配置。"
  exit 1
fi
systemctl reload nginx

info "正在为 $DOMAIN 申请 HTTPS 证书..."
if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect; then
  if [[ -f "$ENV_FILE" ]]; then
    grep -q '^COOKIE_SECURE=' "$ENV_FILE" && sed -i 's/^COOKIE_SECURE=.*/COOKIE_SECURE=1/' "$ENV_FILE" || echo 'COOKIE_SECURE=1' >> "$ENV_FILE"
    chmod 600 "$ENV_FILE"
  fi
  systemctl restart xvpn-panel
  systemctl enable --now certbot.timer >/dev/null 2>&1 || true
  ok "域名与 HTTPS 配置完成"
  echo
  say "后台：${C_CYAN}https://$DOMAIN/admin/login${C_RESET}"
  say "API： ${C_CYAN}https://$DOMAIN/api/v1${C_RESET}"
  [[ "$USE_CF" == "1" ]] && say "Cloudflare：保持小云朵开启，并使用 ${C_BOLD}Full (strict)${C_RESET}。"
else
  warn "HTTPS 申请失败。"
  if [[ -f "$BACKUP" ]]; then
    cp -a "$BACKUP" "$NGINX_SITE"
    nginx -t >/dev/null && systemctl reload nginx
    warn "为避免影响现有访问，已恢复修改前的 Nginx 配置。"
  fi
  say "请检查 DNS、80/443 端口；Cloudflare 用户必要时可临时关闭强制 HTTPS 规则后重试。"
  exit 1
fi
