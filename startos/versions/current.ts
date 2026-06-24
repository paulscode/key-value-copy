import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'

export const current = VersionInfo.of({
  version: '1.0.0:2',
  releaseNotes: {
    en_US:
      'First release packaged for StartOS 0.4.0 (also available for 0.3.5.1). No functional changes to the app itself.',
  },
  migrations: {
    up: async ({ effects }) => {},
    down: IMPOSSIBLE,
  },
})
