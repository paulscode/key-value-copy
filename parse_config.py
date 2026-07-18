#!/usr/bin/env python3
"""Parse the StartOS 0.3.5.1 config.yaml into `export KVC_*=value` lines.

Only the optional policy/API knobs are emitted, and only when present and
matching a safe charset — so eval'ing the output can never inject, and missing
or malformed values simply fall back to the server-side defaults. Best-effort:
this never raises (the entrypoint runs under `set -e`, so a crash here must not
abort startup — the caller also guards with `|| true`).
"""

import re
import sys

# config.yaml key -> environment variable the server reads
_MAP = {
    "enable-api": "KVC_ENABLE_API",
    "conflict-strategy": "KVC_CONFLICT_STRATEGY",
    "delete-strategy": "KVC_DELETE_STRATEGY",
    "tombstone-days": "KVC_TOMBSTONE_DAYS",
}
_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")   # values safe to eval unquoted


def main():
    if len(sys.argv) < 2:
        return
    vals = {}
    try:
        with open(sys.argv[1]) as f:
            for line in f:
                m = re.match(r"^([a-z0-9-]+):\s*(.*)$", line.rstrip("\n"))
                if m:
                    v = m.group(2).strip().strip('"').strip("'")
                    vals[m.group(1)] = v
    except Exception:
        return
    for key, env in _MAP.items():
        v = vals.get(key, "")
        if v and _SAFE.match(v):
            print(f"export {env}={v}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
