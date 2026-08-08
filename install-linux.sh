#!/usr/bin/env bash
set -euo pipefail

# One-command native installer for systemd Linux distributions.
REPOSITORY_URL="${1:-https://github.com/dinggood615/news-data-collection-platform.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/news-data-collection-platform}"
SERVICE_USER="newsplatform"
PUBLIC_PORT="${PORT:-5555}"
BACKEND_PORT=8000
TLS_DIR=/etc/news-platform/tls
DOMAIN="${DOMAIN:-}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-}"

die() { echo "错误：$*" >&2; exit 1; }
[ "${EUID}" -eq 0 ] || die "请使用 sudo 运行"
[ -d /run/systemd/system ] || die "原生安装需要 systemd；容器环境请使用 Docker 安装。"

install_packages() {
  if command -v apt-get >/dev/null; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates git python3 python3-venv python3-pip build-essential openssl curl nginx
  elif command -v dnf >/dev/null; then
    dnf install -y ca-certificates git python3 python3-pip gcc gcc-c++ make openssl curl nginx
  elif command -v yum >/dev/null; then
    yum install -y ca-certificates git python3 python3-pip gcc gcc-c++ make openssl curl nginx
  elif command -v zypper >/dev/null; then
    zypper --non-interactive install ca-certificates git python3 python3-pip gcc gcc-c++ make openssl curl nginx
  elif command -v pacman >/dev/null; then
    pacman -Sy --noconfirm ca-certificates git python python-pip base-devel openssl curl nginx
  else
    die "未识别的软件包管理器。支持 apt、dnf、yum、zypper、pacman。"
  fi
}

install_certbot() {
  if command -v certbot >/dev/null; then return; fi
  if command -v apt-get >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y certbot
  elif command -v dnf >/dev/null; then
    dnf install -y certbot
  elif command -v yum >/dev/null; then
    yum install -y certbot
  elif command -v zypper >/dev/null; then
    zypper --non-interactive install certbot
  elif command -v pacman >/dev/null; then
    pacman -Sy --noconfirm certbot
  else
    die "无法安装 Certbot。请先安装 Certbot 后重新执行，并传入 DOMAIN。"
  fi
}

valid_domain() {
  [ -z "$DOMAIN" ] || printf '%s' "$DOMAIN" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$'
}

git_repo() {
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    git -c http.extraHeader="Authorization: Bearer ${GITHUB_TOKEN}" "$@"
  else
    git "$@"
  fi
}

install_packages
valid_domain || die "DOMAIN 格式不正确；请只填写域名，例如 news.example.com。"
id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
if [ -d "$INSTALL_DIR/.git" ]; then git_repo -C "$INSTALL_DIR" pull --ff-only; else git_repo clone "$REPOSITORY_URL" "$INSTALL_DIR"; fi
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip wheel
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
if [ ! -f "$INSTALL_DIR/.env" ]; then
  cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
  sed -i "s|APP_SECRET=.*|APP_SECRET=$(openssl rand -hex 32)|;s|ADMIN_USERNAME=.*|ADMIN_USERNAME=admin|;s|ADMIN_PASSWORD=.*|ADMIN_PASSWORD=admin|;s|DATABASE_PATH=.*|DATABASE_PATH=$INSTALL_DIR/data/news.sqlite3|" "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
fi
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$INSTALL_DIR/data"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
# Initialize SQLite before the authenticated web endpoint is exposed.  This
# prevents a first-request race from creating an empty database file.
su -s /bin/bash "$SERVICE_USER" -c "set -a; source '$INSTALL_DIR/.env'; set +a; cd '$INSTALL_DIR'; .venv/bin/python -c 'from app.database import init_db; init_db()'"
# Download the free English-to-Chinese offline Argos model used by built-in feeds.
# A temporary model-index failure must not prevent the platform from starting.
su -s /bin/bash "$SERVICE_USER" -c "cd '$INSTALL_DIR'; .venv/bin/python -m app.translation_setup" || echo "提示：离线翻译模型暂未下载完成；平台将保留原文并会在下次安装/更新时重试。"
# Invoke explicitly with bash: Git mirrors may not preserve executable bits.

cat >/etc/systemd/system/news-platform.service <<EOF
[Unit]
Description=新闻数据采集平台
After=network-online.target
Wants=network-online.target

[Service]
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $BACKEND_PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

install -d -m 700 "$TLS_DIR"
if [ ! -f "$TLS_DIR/cert.pem" ] || [ ! -f "$TLS_DIR/key.pem" ]; then
  openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 3650 -subj "/CN=$(hostname -f 2>/dev/null || hostname)" -keyout "$TLS_DIR/key.pem" -out "$TLS_DIR/cert.pem"
  chmod 600 "$TLS_DIR/key.pem"
fi
if [ -d /etc/nginx/sites-available ]; then
  NGINX_SITE=/etc/nginx/sites-available/news-platform
  NGINX_ENABLED=/etc/nginx/sites-enabled/news-platform
  install -m 644 "$INSTALL_DIR/nginx/tender-platform.conf" "$NGINX_SITE"
  ln -sfn "$NGINX_SITE" "$NGINX_ENABLED"
  rm -f /etc/nginx/sites-enabled/default
else
  NGINX_SITE=/etc/nginx/conf.d/news-platform.conf
  install -m 644 "$INSTALL_DIR/nginx/tender-platform.conf" "$NGINX_SITE"
fi
# Telegram Webhook only supports standard HTTPS ports and the bundled Nginx
# configuration always keeps 443.  Avoid writing the same listen directive
# twice when the dashboard is explicitly configured for 443.
if [ "$PUBLIC_PORT" != "443" ]; then
  sed -i "s/listen 5555 ssl;/listen $PUBLIC_PORT ssl;/" "$NGINX_SITE"
else
  sed -i '/listen 5555 ssl;/d' "$NGINX_SITE"
fi
if [ -n "$DOMAIN" ]; then
  sed -i "s/server_name _;/server_name $DOMAIN;/" "$NGINX_SITE"
fi
nginx -t
systemctl daemon-reload
systemctl enable --now news-platform.service
systemctl enable nginx.service
systemctl restart nginx.service
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  # Check Uvicorn directly.  Nginx may still be reloading its certificate or
  # access-control configuration, so it should not make a successful install
  # look like a failed application start.
  if curl -fsS "http://127.0.0.1:$BACKEND_PORT/healthz" >/dev/null; then
    break
  fi
  [ "$attempt" -eq 10 ] && die "平台健康检查失败，请执行：journalctl -u news-platform -n 80 --no-pager"
  sleep 2
done
if [ -n "$DOMAIN" ]; then
  echo "正在为 $DOMAIN 申请 Let's Encrypt 证书；请确认 DNS 已解析到本服务器且防火墙/安全组已放行 80、443。"
  install_certbot
  systemctl stop nginx.service
  CERTBOT_ARGS=(certonly --standalone --non-interactive --agree-tos --keep-until-expiring -d "$DOMAIN")
  if [ -n "$LETSENCRYPT_EMAIL" ]; then CERTBOT_ARGS+=(--email "$LETSENCRYPT_EMAIL"); else CERTBOT_ARGS+=(--register-unsafely-without-email); fi
  if ! certbot "${CERTBOT_ARGS[@]}"; then
    systemctl start nginx.service
    die "Let's Encrypt 证书申请失败。请检查域名解析和 80/443 入站规则，然后重新运行安装命令。"
  fi
  ln -sfn "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$TLS_DIR/cert.pem"
  ln -sfn "/etc/letsencrypt/live/$DOMAIN/privkey.pem" "$TLS_DIR/key.pem"
  systemctl start nginx.service
  systemctl reload nginx.service
  echo "HTTPS 证书已就绪：Telegram Webhook 请填写 https://$DOMAIN（不要带 :$PUBLIC_PORT）。"
else
  echo "提示：当前使用自签名证书，仅适合仪表盘测试；Telegram Webhook 需要有效域名证书。"
  echo "需要 Telegram 时，请使用 DOMAIN=你的域名 LETSENCRYPT_EMAIL=你的邮箱 重新运行安装命令。"
fi
echo "完成：访问 https://服务器IP:$PUBLIC_PORT。初始账户 admin/admin，请立即修改。"
echo "已配置免费离线翻译模型（Argos）；企业微信 Webhook 请在平台内填写。"
