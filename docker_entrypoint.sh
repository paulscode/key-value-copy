#!/bin/sh

echo "Starting Key Value Copy..."

# Ensure data directory exists
mkdir -p /root/data

# Read password from StartOS config (saved by setConfig via compat)
if [ -f /root/start9/config.yaml ]; then
    PASSWORD=$(python3 -c "
import re
with open('/root/start9/config.yaml') as f:
    for line in f:
        m = re.match(r'^password:\s*(.+)', line.strip())
        if m:
            v = m.group(1).strip()
            # Remove surrounding quotes if present
            if (v.startswith('\"') and v.endswith('\"')) or (v.startswith(\"'\") and v.endswith(\"'\")):
                v = v[1:-1]
            print(v)
            break
" 2>/dev/null)
fi

# Fallback: generate password if not set via config
if [ -z "$PASSWORD" ]; then
    if [ -f /root/data/password.txt ]; then
        PASSWORD=$(cat /root/data/password.txt | tr -d '\n')
    else
        PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
        echo "Generated initial password"
    fi
fi

# Write password for the Python server
echo "$PASSWORD" > /root/data/password.txt

# Write properties for StartOS
mkdir -p /root/start9
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

export KVC_DATA_DIR=/root/data
export KVC_STATIC_DIR=/var/www/html
export KVC_PORT=80
export KVC_BIND=0.0.0.0

exec tini python3 /usr/local/bin/server.py
