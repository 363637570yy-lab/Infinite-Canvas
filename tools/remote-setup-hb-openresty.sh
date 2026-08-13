#!/usr/bin/env bash
set -euo pipefail
CONF_DIR=/opt/1panel/apps/openresty/openresty/conf/conf.d
SITE_CONF=$CONF_DIR/hb.qnzn.top.conf
BACKUP_DIR=/opt/ai-video/deploy-backups/infinite-canvas-hero8152-$(date +%Y%m%d-%H%M%S)-hb-openresty
mkdir -p "$BACKUP_DIR"
cp -a "$CONF_DIR" "$BACKUP_DIR/openresty-conf.d"
cat > "$SITE_CONF" <<'EOF'
upstream infinite_canvas_hero8152 {
    server 127.0.0.1:20890;
    keepalive 16;
}
server {
    listen 80;
    server_name hb.qnzn.top;
    location ~ /.well-known/acme-challenge {
        allow all;
        root /usr/share/nginx/html;
    }
    location ^~ /assets/ {
        proxy_pass http://infinite_canvas_hero8152;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;
    }
    location ^~ /output/ {
        proxy_pass http://infinite_canvas_hero8152;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;
    }
    location / {
        proxy_pass http://infinite_canvas_hero8152;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 1800s;
        proxy_send_timeout 1800s;
    }
}
EOF
docker exec 1Panel-openresty-Mvvy openresty -t
docker exec 1Panel-openresty-Mvvy openresty -s reload
echo backup=$BACKUP_DIR
curl -sI -H 'Host: hb.qnzn.top' http://127.0.0.1/assets/output/online_7af2a62d4d.png | grep -iE 'HTTP/|Content-Type:' | head -3
