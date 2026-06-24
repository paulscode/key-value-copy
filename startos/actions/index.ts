import { sdk } from '../sdk'
import { config } from './config'
import { passwordInfo } from './passwordInfo'

export const actions = sdk.Actions.of()
  .addAction(config)
  .addAction(passwordInfo)
