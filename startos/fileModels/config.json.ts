import { FileHelper, z } from '@start9labs/start-sdk'
import { sdk } from '../sdk'

// The single user-facing setting: the password that guards the web UI. Stored on
// the main volume as config.json. main.ts reads it and passes it to the
// container entrypoint as KVC_PASSWORD; the Configure action lets the user view
// or change it. A random value is seeded on first install (see init/seedFiles).
export const configJson = FileHelper.json(
  {
    base: sdk.volumes.main,
    subpath: '/config.json',
  },
  z.object({
    password: z.string().catch(''),
    enable_api: z.boolean().catch(true),
    conflict_strategy: z.enum(['preserve', 'lww']).catch('preserve'),
    delete_strategy: z.enum(['edit-wins', 'delete-wins']).catch('edit-wins'),
    tombstone_days: z.number().int().catch(90),
  }),
)
