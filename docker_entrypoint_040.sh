#!/bin/sh
# Shared, env-driven supervisor. Used directly on StartOS 0.4.x (the daemon runs
# this with the environment already set) and by plain `docker run`. The 0.3.5.x
# package execs this from docker_entrypoint.sh after mapping config.yaml -> env.
#
# Inputs (all optional, with sensible defaults):
#   KVC_PASSWORD    web-UI password. If empty, falls back to the persisted
#                   password.txt, and if that is missing, a random one is made.
#   KVC_DATA_DIR    data volume mountpoint (default /data)
#   KVC_STATIC_DIR  static web root baked into the image (default /var/www/html)
#   KVC_PORT        port the server binds (default 80)
#   KVC_BIND        bind address (default 0.0.0.0)
set -e

DATA_DIR="${KVC_DATA_DIR:-/data}"
STATIC_DIR="${KVC_STATIC_DIR:-/var/www/html}"
PORT="${KVC_PORT:-80}"
BIND="${KVC_BIND:-0.0.0.0}"

echo "Starting Key Value Copy..."

mkdir -p "$DATA_DIR"

# Resolve the password: explicit env wins, then the persisted file, then generate.
PASSWORD="${KVC_PASSWORD:-}"
if [ -z "$PASSWORD" ] && [ -f "$DATA_DIR/password.txt" ]; then
    PASSWORD=$(tr -d '\n' < "$DATA_DIR/password.txt")
fi
if [ -z "$PASSWORD" ]; then
    PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    echo "Generated initial password"
fi

# Persist for the Python server (which reads it from password.txt).
printf '%s\n' "$PASSWORD" > "$DATA_DIR/password.txt"

export KVC_DATA_DIR="$DATA_DIR"
export KVC_STATIC_DIR="$STATIC_DIR"
export KVC_PORT="$PORT"
export KVC_BIND="$BIND"

exec tini -- python3 /usr/local/bin/server.py
