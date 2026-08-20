import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'

export const current = VersionInfo.of({
  version: '1.1.1:0',
  releaseNotes: {
    en_US:
      'Adds scoped access keys for machines: a key can now be limited to entries whose names start with a chosen prefix and marked read-only, so a script or appliance can fetch just the values it needs without being able to read — or change — the rest of the vault. Scoped keys read through a new export endpoint and are refused on the full-vault sync endpoints. Existing keys are unscoped and unaffected, as is the companion app. No change to how you use the app day to day.',
  },
  migrations: {
    up: async ({ effects }) => {},
    down: IMPOSSIBLE,
  },
})
