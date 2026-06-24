import { sdk } from './sdk'

// Back up the entire main volume — it only holds the small encrypted store and
// the config, both of which the user expects to survive a restore.
export const { createBackup, restoreInit } = sdk.setupBackups(
  async ({ effects }) => sdk.Backups.ofVolumes('main'),
)
