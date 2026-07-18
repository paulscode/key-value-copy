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
  enable_api: Value.toggle({
    name: 'Enable companion-device sync',
    description:
      'Allow companion apps to pair and sync over the API. Turn this off to restrict the vault to the web interface only (existing device keys stop working while off).',
    default: true,
  }),
  conflict_strategy: Value.select({
    name: 'Conflict resolution',
    description:
      'When the same entry is changed in two places at once, keep both versions (recommended — nothing is lost) or keep only the most recent edit.',
    default: 'preserve',
    values: { preserve: 'Keep both (recommended)', lww: 'Most recent edit wins' },
  }),
  delete_strategy: Value.select({
    name: 'Edit vs. delete',
    description:
      'When one device edits an entry that another deleted, keep the edit (recommended — brings the entry back) or honor the deletion.',
    default: 'edit-wins',
    values: {
      'edit-wins': 'Keep the edit (recommended)',
      'delete-wins': 'Honor the delete',
    },
  }),
  tombstone_days: Value.number({
    name: 'Deleted-entry retention',
    description:
      'How long to remember deletions so a device that was offline can still learn about them. After this, old deletions are compacted away.',
    default: 90,
    required: true,
    integer: true,
    min: 1,
    max: 3650,
    units: 'days',
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
