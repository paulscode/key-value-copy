import { configJson } from '../fileModels/config.json'
import { sdk } from '../sdk'

const { InputSpec, Value } = sdk

export const inputSpec = InputSpec.of({
  password: Value.text({
    name: 'Password',
    description:
      'Password required to access the Key Value Copy web interface. A random password is generated on first install — keep it or change it here.',
    required: true,
    default: { charset: 'a-z,A-Z,0-9', len: 24 },
    masked: true,
    minLength: 8,
    patterns: [
      { regex: '^.{8,}$', description: 'Must be at least 8 characters' },
    ],
    // Offer a "generate" button so users can roll a fresh strong password.
    generate: { charset: 'a-z,A-Z,0-9', len: 24 },
  }),
})

export const config = sdk.Action.withInput(
  // id
  'config',

  // metadata
  async ({ effects }) => ({
    name: 'Configure',
    description: 'Set the password for the Key Value Copy web interface',
    warning: null,
    allowedStatuses: 'any',
    group: null,
    visibility: 'enabled',
  }),

  // form input specification
  inputSpec,

  // pre-fill the form with the current password
  async ({ effects }) => configJson.read().once(),

  // the execution function
  async ({ effects, input }) => configJson.merge(effects, input),
)
