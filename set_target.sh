#!/bin/bash
NEW_URL="$1"
[ -z "$NEW_URL" ] && { echo "Usage: set_target.sh <url>"; exit 1; }

echo "$NEW_URL" > /tmp/target_url.txt

export TARGET_URL="$NEW_URL"
export PORT="${PORT:-10000}"
export SESSION_COOKIE_NAME="${SESSION_COOKIE_NAME:-session_id}"
export USER_COOKIE_NAME="${USER_COOKIE_NAME:-username}"

envsubst '${PORT} ${TARGET_URL} ${SESSION_COOKIE_NAME} ${USER_COOKIE_NAME}' \
    < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

nginx -s reload
echo "Target URL set to $NEW_URL"
