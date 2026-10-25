import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'

export const current = VersionInfo.of({
  version: '1.1.1:3',
  releaseNotes: {
    en_US:
      'Moves the base image off Alpine 3.20, which reached end of life, to ' +
      'Alpine 3.24, picking up security fixes in OpenSSL, musl, zlib and the ' +
      'CA certificates. ' +
      'Uses version 3.1.0 of the packaging SDK.',
  },
  migrations: {
    // Nothing to migrate: the image changed, and nothing on disk does.
    up: async ({ effects }) => {},
    down: IMPOSSIBLE,
  },
})
