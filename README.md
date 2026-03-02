# Key Value Copy

A browser-based tool for storing and copying sensitive key-value pairs with a bioluminescent dark UI theme.

Values are encrypted at rest using AES-256-GCM and never displayed in plain text unless explicitly revealed.

![Theme: Dark with green neon bioluminescence](favicon.svg)

---

## Getting Started

No build step or external dependencies required — just Python 3.

### Quick Start

```bash
python3 server.py
```

On first run, a random password is generated and printed to the console. Open [http://localhost:8765](http://localhost:8765) and enter the password to log in. The password is stored in `data/password.txt`.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KVC_PORT` | `8765` | Port to listen on |
| `KVC_BIND` | `localhost` | Bind address |
| `KVC_DATA_DIR` | `./data` | Directory for data files |
| `KVC_STATIC_DIR` | `.` | Directory containing static files |
| `KVC_SESSION_TTL` | `86400` | Session timeout in seconds (24h) |

---

## Usage

### Adding an Entry

1. Click the glowing **+** button in the bottom-right corner (or press **Ctrl+N** / **Cmd+N**).
2. Enter a **Key** — this is the label (e.g. `AWS_SECRET_KEY`, `DB Password`). Keys can contain letters, numbers, symbols, and spaces.
3. Enter a **Value** — this is the sensitive data. The input is masked by default. Click the eye icon to toggle visibility while typing.
4. Click **Save** to store the entry.

### Copying a Value

Click the **copy icon** (overlapping rectangles) on any row. The decrypted value is placed on your clipboard and a toast notification confirms the action. The clipboard is **automatically cleared after 30 seconds** for security.

### Revealing a Value

Click the **eye icon** on any row to temporarily reveal the decrypted value inline. It **auto-hides after 5 seconds**, or click again to hide immediately.

### Editing an Entry

Click the **pencil icon** on any row to open the edit modal with the current key and value pre-filled.

### Deleting an Entry

Click the **trash icon** on any row. A confirmation dialog appears showing the key name. Confirm to permanently remove the entry.

### Reordering Entries

Grab the **drag handle** (six dots on the left of each row) and drag up or down to reorder entries. The new order is saved automatically.

### Exporting Entries

1. Click the **Export** button in the header toolbar.
2. Enter a password and confirm it. This password encrypts the export file using PBKDF2 (600,000 iterations) + AES-256-GCM.
3. A `.kvc` file is downloaded containing all entries, encrypted with your chosen password.

### Importing Entries

1. Click the **Import** button in the header toolbar.
2. Select a `.kvc` export file (click or drag-and-drop).
3. Enter the password that was used during export.
4. Imported entries are appended below any existing entries, preserving their original order.
5. If an imported key matches an existing key, the imported key is automatically renamed with a suffix — e.g. `API_KEY` becomes `API_KEY (1)`, then `API_KEY (2)`, etc.
6. An incorrect password displays an error message — no data is imported.

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| **Ctrl+N** / **Cmd+N** | Add new entry |
| **Escape** | Close any open modal |

---

## Security Model

| Feature | Detail |
|---------|--------|
| **Authentication** | Password-protected login; session tokens expire after 24 hours |
| **Encryption** | AES-256-GCM via the Web Crypto API |
| **Key storage** | Encryption key stored as JWK on the server (separate from password) |
| **Data storage** | Encrypted ciphertext + IV stored on the server in `store.json` |
| **Password independence** | Changing the login password does not affect stored data or the encryption key |
| **Value masking** | Values are never rendered in the DOM unless explicitly peeked |
| **Auto-hide** | Revealed values automatically re-mask after 5 seconds |
| **Clipboard hygiene** | Clipboard is cleared 30 seconds after copying |
| **Secure context** | Web Crypto API requires `https://` or `localhost` |
| **Fallback** | If Web Crypto is unavailable (e.g. `file://`), values are Base64-encoded instead |
| **Export encryption** | PBKDF2 (600K iterations, SHA-256) + AES-256-GCM with random salt & IV |
| **Import safety** | Wrong password produces a clear error; duplicate keys are auto-renamed |

### Limitations

- The encryption key is stored alongside the data on the server. This protects against casual inspection but not against an attacker with full access to the filesystem.
- Export files are strongly encrypted but only as secure as the password chosen.

---

## Data Persistence

All entries and the encryption key are stored on the server in the `data/` directory (or `KVC_DATA_DIR`). Data **persists across**:

- Page reloads, tab closes, browser restarts
- Different browsers, devices, or machines
- Server restarts

Data is **lost** when:

- The `data/` directory is deleted
- The server is uninstalled without a backup

---

## Project Structure

```
key-value-copy/
├── index.html              # Application markup
├── styles.css              # Bioluminescent dark theme
├── app.js                  # Application logic, crypto, drag-drop
├── server.py               # Backend server (Python 3 stdlib)
├── favicon.svg             # Leaf-key hybrid icon
├── icon.png                # StartOS service icon
├── Dockerfile              # Docker image (alpine + nginx)
├── docker_entrypoint.sh    # Container entrypoint
├── manifest.yaml           # StartOS service manifest
├── instructions.md         # StartOS service instructions
├── Makefile                # Build system for .s9pk
├── scripts/
│   ├── bundle.ts           # Deno bundler script
│   ├── deps.ts             # StartOS SDK imports
│   ├── embassy.ts          # Re-exports all procedures
│   └── procedures/
│       ├── getConfig.ts    # Service configuration spec
│       ├── setConfig.ts    # Apply configuration
│       ├── properties.ts   # Service properties
│       ├── migrations.ts   # Version migrations
│       └── healthChecks.ts # Web UI health check
├── LICENSE
└── README.md               # This file
```

---

## Building for StartOS

The project can be packaged as an `.s9pk` file for installation on [StartOS](https://start9.com/) (v0.3.5.x).

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with `buildx` support
- [Deno](https://deno.land/) (for bundling TypeScript scripts)
- [start-sdk](https://github.com/Start9Labs/start-os/tree/sdk/backend) (`start-sdk` CLI)
- [yq](https://github.com/mikefarah/yq) (YAML processor)

### Build

```bash
# Build for all architectures (x86_64 + aarch64)
make

# Build for a single architecture
make x86    # x86_64 only
make arm    # aarch64 only

# Clean build artifacts
make clean
```

This produces `builds/key-value-copy.s9pk` along with a `builds/SHA256SUMS` file. The package can be sideloaded onto a StartOS server via the dashboard or installed with:

```bash
make install
```

> **Note:** `make install` requires `~/.embassy/config.yaml` with `host: http://your-server.local`.

---

## License

MIT
