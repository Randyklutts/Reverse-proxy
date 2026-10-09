#!/bin/bash
set -e

mkdir -p /var/log/nginx

export TARGET_URL="${TARGET_URL:-https://example.com}"
export PORT="${PORT:-10000}"
export SESSION_COOKIE_NAME="${SESSION_COOKIE_NAME:-session_id}"
export USER_COOKIE_NAME="${USER_COOKIE_NAME:-username}"

echo "$TARGET_URL" > /tmp/target_url.txt

envsubst '${PORT} ${TARGET_URL} ${SESSION_COOKIE_NAME} ${USER_COOKIE_NAME}' \
    < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

nginx -g "daemon off;" &
NGINX_PID=$!

python /app/bot.py &
BOT_PID=$!

python /app/monitor.py &
MONITOR_PID=$!

wait -n $NGINX_PID $BOT_PID $MONITOR_PID
