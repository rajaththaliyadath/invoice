#!/bin/bash
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y python3-venv python3-pip nginx libreoffice-calc-nogui git

INV=/var/www/invoice
if [ ! -d "$INV/.git" ]; then
  git clone https://github.com/rajaththaliyadath/invoice.git "$INV"
else
  git -C "$INV" pull --ff-only
fi

cd "$INV"
python3 -m venv .venv
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  SECRET="$(openssl rand -base64 48)"
  {
    echo "DJANGO_SECRET_KEY=$SECRET"
    echo "DJANGO_DEBUG=0"
    echo "DJANGO_ALLOWED_HOSTS=invoices.rajatht.me,127.0.0.1,localhost"
    echo "CSRF_TRUSTED_ORIGINS=https://invoices.rajatht.me"
  } > .env
fi
chown www-data:www-data .env
chmod 640 .env

mkdir -p "$INV/data" "$INV/media/invoices"
chown www-data:www-data "$INV/data"
# Migrations as www-data so SQLite + WAL files in data/ are owned correctly (avoids "readonly database").
sudo -u www-data bash -c "cd \"$INV\" && export \$(grep -v '^#' .env | xargs) && .venv/bin/python manage.py migrate --noinput"

.venv/bin/python manage.py collectstatic --noinput

install -m 644 deploy/gunicorn-invoices.service /etc/systemd/system/gunicorn-invoices.service
systemctl daemon-reload
systemctl enable gunicorn-invoices
systemctl restart gunicorn-invoices

cat > /etc/nginx/sites-available/invoices.rajatht.me <<'NGINX'
server {
    server_name invoices.rajatht.me;
    client_max_body_size 25m;

    location /static/ {
        alias /var/www/invoice/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
    location /media/ {
        alias /var/www/invoice/media/;
        expires 7d;
        add_header Cache-Control "public";
    }
    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;
        proxy_read_timeout 120s;
    }

    listen 80;
    listen [::]:80;
}
NGINX

ln -sf /etc/nginx/sites-available/invoices.rajatht.me /etc/nginx/sites-enabled/invoices.rajatht.me
nginx -t
systemctl reload nginx

# TLS (reuse admin contact pattern from your domain)
certbot --nginx -d invoices.rajatht.me --non-interactive --agree-tos --redirect \
  -m admin@rajatht.me --no-eff-email || true

systemctl reload nginx 2>/dev/null || true

chown -R www-data:www-data "$INV/data" "$INV/media" "$INV/staticfiles" 2>/dev/null || true

systemctl is-active gunicorn-invoices
curl -sI -m 10 http://127.0.0.1:8001/ | head -5 || true
