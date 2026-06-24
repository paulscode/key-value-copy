#!/bin/sh
# StartOS 0.3.5.x entrypoint. Reads the rendered config from the main volume,
# maps it to the environment the shared supervisor expects, writes the Properties
# page data, then hands off. (0.4.x sets that environment directly and runs
# docker_entrypoint_040.sh.)
set -e

CONFIG=/root/start9/config.yaml
DATA_DIR=/root/data

mkdir -p "$DATA_DIR" /root/start9

# Read the password from config.yaml (set via getConfig/setConfig). Parse without
# depending on yq in the image.
PASSWORD=""
if [ -f "$CONFIG" ]; then
    PASSWORD=$(python3 -c "
import re
with open('$CONFIG') as f:
    for line in f:
        m = re.match(r'^password:\s*(.+)', line.strip())
        if m:
            v = m.group(1).strip()
            if (v.startswith('\"') and v.endswith('\"')) or (v.startswith(\"'\") and v.endswith(\"'\")):
                v = v[1:-1]
            print(v)
            break
" 2>/dev/null)
fi

# Fallbacks: persisted file, then generate.
if [ -z "$PASSWORD" ] && [ -f "$DATA_DIR/password.txt" ]; then
    PASSWORD=$(tr -d '\n' < "$DATA_DIR/password.txt")
fi
if [ -z "$PASSWORD" ]; then
    PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    echo "Generated initial password"
fi

# Properties page data for StartOS 0.3.x.
cat > /root/start9/stats.yaml << EOF
version: 2
data:
  Password:
    type: string
    value: "${PASSWORD}"
    description: Password to access the Key Value Copy web interface
    copyable: true
    qr: false
    masked: true
EOF

export KVC_PASSWORD="$PASSWORD"
export KVC_DATA_DIR="$DATA_DIR"
export KVC_STATIC_DIR=/var/www/html
export KVC_PORT=80
export KVC_BIND=0.0.0.0

exec /usr/local/bin/docker_entrypoint_040.sh
