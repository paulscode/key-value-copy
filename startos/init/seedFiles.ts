import { utils } from '@start9labs/start-sdk'
import { configJson } from '../fileModels/config.json'
import { sdk } from '../sdk'

// On first install, generate a strong random password so the package is usable
// out of the box (the user can change it later via the Configure action).
export const seedFiles = sdk.setupOnInit(async (effects) => {
  const existing = await configJson.read().once()
  if (!existing || !existing.password) {
    await configJson.write(effects, {
      password: utils.getDefaultString({ charset: 'a-z,A-Z,0-9', len: 24 }),
    })
  }
})
