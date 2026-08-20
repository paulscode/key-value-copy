# key-value-copy — StartOS wrapper
#
# Builds TWO packages from one tree:
#   • 0.4.0   (start-sdk TS):  startos/ + start-cli   -> key-value-copy-040.s9pk
#   • 0.3.5.1 (Embassy SDK):   manifest.yaml + scripts/embassy.js + start-sdk
#                                                       -> key-value-copy-0351.s9pk
# One Dockerfile and one runtime contract serve both. Each package can be built
# universal (x86_64 + aarch64 in one file) or per-arch (much faster to iterate).
#
# Common targets:
#   make / make 040            0.4.0 universal package
#   make 040-x86_64            0.4.0, single arch (the fast dev loop)
#   make 040-aarch64           0.4.0, single arch (slow: arm64 via QEMU)
#   make 0351                  0.3.5.1 universal package
#   make 0351-x86_64           0.3.5.1, single arch
#   make release               BOTH universal packages -> builds/<version>/ + SHA256SUMS
#   make install-040[-x86_64]  install the matching build to your StartOS server
#   make install-0351[-x86_64]
#   make clean
#
# Prerequisites: start-cli, start-sdk, docker buildx, node/npm, deno (0.3.x), yq.

# The 0.4.x packing CLI. Pinned to the 0.4.0 line ON PURPOSE: `start-cli` on PATH
# tracks whatever StartOS release is newest, and a 1.x CLI stamps osVersion 1.x
# into the s9pk, which a 0.4.0.1 server will not install. It also wants a
# "packaging workspace" in the parent directory and signs with a workspace key
# rather than ~/.startos/developer.key.pem — a different signer than every
# release shipped so far. Override only if you know which OS you are targeting.
START_CLI ?= start-cli-0.4.0-beta.9

# Package id comes from the 0.4.x manifest (the always-present source of truth).
PKG_ID := $(shell awk -F"'" '/id:/ {print $$2; exit}' startos/manifest/index.ts)
# Release version (e.g. 1.0.0) read from the 0.3.x manifest.
PKG_VERSION := $(shell yq -r '.version' manifest.yaml 2>/dev/null)
BUILD_DIR := builds/$(PKG_VERSION)

STARTOS_FILES := $(shell find startos -type f 2>/dev/null)
# Anything that affects the bundled image (invalidates a repack when changed).
IMAGE_FILES := Dockerfile docker_entrypoint_040.sh docker_entrypoint.sh \
               server.py store.py sync_engine.py parse_config.py \
               index.html app.js styles.css favicon.svg

.DELETE_ON_ERROR:
.PHONY: all 040 040-x86_64 040-aarch64 \
        0351 0351-x86_64 0351-aarch64 \
        release check-0351 _pack-0351 \
        install-040 install-040-x86_64 install-040-aarch64 \
        install-0351 install-0351-x86_64 install-0351-aarch64 \
        check-init clean

all: 040

# ───────────────────────────── 0.4.x (start-sdk) ─────────────────────────
040:         $(PKG_ID)-040.s9pk
040-x86_64:  $(PKG_ID)-040-x86_64.s9pk
040-aarch64: $(PKG_ID)-040-aarch64.s9pk

$(PKG_ID)-040.s9pk: javascript/index.js $(IMAGE_FILES) icon.png | check-init
	$(START_CLI) s9pk pack --icon icon.png -o $@

$(PKG_ID)-040-x86_64.s9pk: javascript/index.js $(IMAGE_FILES) icon.png | check-init
	$(START_CLI) s9pk pack --icon icon.png --arch=x86_64 -o $@

$(PKG_ID)-040-aarch64.s9pk: javascript/index.js $(IMAGE_FILES) icon.png | check-init
	$(START_CLI) s9pk pack --icon icon.png --arch=aarch64 -o $@

javascript/index.js: $(STARTOS_FILES) tsconfig.json node_modules
	npm run check
	npm run build

node_modules: package.json
	npm i

# ───────────────────────────── 0.3.5.x (Embassy) ─────────────────────────
# Per-arch image tars are built with buildx and packed by start-sdk. arm64 is
# cross-built under QEMU emulation.
0351: check-0351
	rm -rf docker-images && mkdir -p docker-images
	docker buildx build --tag start9/$(PKG_ID)/main:$(PKG_VERSION) \
	  --platform=linux/amd64 -o type=docker,dest=docker-images/x86_64.tar .
	docker buildx build --tag start9/$(PKG_ID)/main:$(PKG_VERSION) \
	  --platform=linux/arm64 -o type=docker,dest=docker-images/aarch64.tar .
	$(MAKE) _pack-0351 SUFFIX=

0351-x86_64: check-0351
	rm -rf docker-images && mkdir -p docker-images
	docker buildx build --tag start9/$(PKG_ID)/main:$(PKG_VERSION) \
	  --platform=linux/amd64 -o type=docker,dest=docker-images/x86_64.tar .
	$(MAKE) _pack-0351 SUFFIX=-x86_64

0351-aarch64: check-0351
	rm -rf docker-images && mkdir -p docker-images
	docker buildx build --tag start9/$(PKG_ID)/main:$(PKG_VERSION) \
	  --platform=linux/arm64 -o type=docker,dest=docker-images/aarch64.tar .
	$(MAKE) _pack-0351 SUFFIX=-aarch64

# Internal: bundle the Deno procedures and pack whatever tars are in docker-images.
_pack-0351: scripts/embassy.js
	start-sdk pack
	mv $(PKG_ID).s9pk $(PKG_ID)-0351$(SUFFIX).s9pk

scripts/embassy.js: $(shell find scripts -name '*.ts' 2>/dev/null)
	deno run --allow-read --allow-write --allow-env --allow-net scripts/bundle.ts

# Version declared in the 0.3.x migration; must equal manifest.yaml's version or
# StartOS leaves the service stuck on "Starting" after an in-place update.
MIGRATION_VERSION := $(shell grep fromMapping scripts/procedures/migrations.ts | grep -oE '"[0-9][0-9.]*"' | tr -d '"')

# Fail early with a clear message if the 0.3.x prerequisites are missing.
check-0351:
	@test -f manifest.yaml || { echo "ERROR: manifest.yaml not found — the 0.3.5.1 (Embassy) half is not in place."; exit 1; }
	@test -f scripts/embassy.ts || { echo "ERROR: scripts/embassy.ts not found — the 0.3.5.1 procedures are not in place."; exit 1; }
	@command -v yq >/dev/null || { echo "ERROR: yq is required to read manifest.yaml."; exit 1; }
	@test -n "$(PKG_VERSION)" || { echo "ERROR: could not read .version from manifest.yaml."; exit 1; }
	@test "$(MIGRATION_VERSION)" = "$(PKG_VERSION)" || { echo "ERROR: migration version '$(MIGRATION_VERSION)' (scripts/procedures/migrations.ts) != manifest version '$(PKG_VERSION)' (manifest.yaml). Update fromMapping(...) to match, or the 0.3.x service hangs on 'Starting' after an update."; exit 1; }

# ─────────────────────────────── Release (both) ──────────────────────────
# Both universal packages (x86_64 + aarch64) into builds/<version>/ + SHA256SUMS.
release: check-0351 javascript/index.js | check-init
	rm -rf $(BUILD_DIR) && mkdir -p $(BUILD_DIR)
	$(MAKE) 0351
	cp $(PKG_ID)-0351.s9pk $(BUILD_DIR)/$(PKG_ID)-0351.s9pk
	$(START_CLI) s9pk pack --icon icon.png -o $(BUILD_DIR)/$(PKG_ID)-040.s9pk
	cd $(BUILD_DIR) && sha256sum *.s9pk > SHA256SUMS
	@echo ""
	@echo "Release builds in $(BUILD_DIR):"
	@ls -lh $(BUILD_DIR)/
	@echo ""
	@cat $(BUILD_DIR)/SHA256SUMS

# ─────────────────────────────────── Install ─────────────────────────────
install-040:         $(PKG_ID)-040.s9pk
	$(START_CLI) package install -s $(PKG_ID)-040.s9pk
install-040-x86_64:  $(PKG_ID)-040-x86_64.s9pk
	$(START_CLI) package install -s $(PKG_ID)-040-x86_64.s9pk
install-040-aarch64: $(PKG_ID)-040-aarch64.s9pk
	$(START_CLI) package install -s $(PKG_ID)-040-aarch64.s9pk
install-0351:         $(PKG_ID)-0351.s9pk
	$(START_CLI) package install -s $(PKG_ID)-0351.s9pk
install-0351-x86_64:  $(PKG_ID)-0351-x86_64.s9pk
	$(START_CLI) package install -s $(PKG_ID)-0351-x86_64.s9pk
install-0351-aarch64: $(PKG_ID)-0351-aarch64.s9pk
	$(START_CLI) package install -s $(PKG_ID)-0351-aarch64.s9pk

# Ensure the packing CLI is the right one and a developer key exists. The version
# check is not pedantry: `start-cli` on PATH silently became a 1.x build between
# the 1.1.0.0 and 1.1.1.0 releases, and 1.x packs a package a 0.4.0.1 server will
# not install, signed by a different key than every release shipped so far.
check-init:
	@command -v $(START_CLI) >/dev/null || { echo "ERROR: '$(START_CLI)' not found. The 0.4.x package needs a 0.4.0-line CLI. Override with 'make START_CLI=<path>' if you are deliberately targeting another StartOS version."; exit 1; }
	@$(START_CLI) --version | grep -q ' 0\.4\.0' || { echo "ERROR: '$(START_CLI)' reports '`$(START_CLI) --version`', which is not a 0.4.0-line CLI. See the START_CLI comment at the top of this Makefile."; exit 1; }
	@test -f ~/.startos/developer.key.pem || $(START_CLI) init-key

clean:
	rm -rf docker-images builds javascript node_modules
	rm -f $(PKG_ID)-040*.s9pk $(PKG_ID)-0351*.s9pk $(PKG_ID).s9pk
	rm -f scripts/*.js
