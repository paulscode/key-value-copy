import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'

export const current = VersionInfo.of({
  version: '1.1.0:0',
  releaseNotes: {
    en_US:
      "Adds companion-app support: securely sync your vault to a phone or tablet over your local network or Tor, with per-device access keys you can grant and revoke individually from the new Devices screen. Includes automatic conflict resolution when the same entry is edited in two places (with a one-tap resolver), a faster SQLite storage engine (existing entries migrate automatically), configurable sync policies, and security hardening. No change to how you use the app day to day.",
  },
  migrations: {
    up: async ({ effects }) => {},
    down: IMPOSSIBLE,
  },
})
