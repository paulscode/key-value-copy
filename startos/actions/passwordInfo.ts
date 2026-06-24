import { T } from '@start9labs/start-sdk'
import { configJson } from '../fileModels/config.json'
import { sdk } from '../sdk'

// Surfaces the current web-UI password so the user can copy it. Replaces the
// 0.3.x "Properties" page.
export const passwordInfo = sdk.Action.withoutInput(
  'password-info',

  async ({ effects }) => ({
    name: 'Show Password',
    description: 'Display the password for the Key Value Copy web interface',
    warning: null,
    allowedStatuses: 'any',
    group: null,
    visibility: 'enabled',
  }),

  async ({ effects }): Promise<T.ActionResult & { version: '1' }> => {
    const config = await configJson.read().once()
    return {
      version: '1' as const,
      title: 'Web Interface Password',
      message:
        'Use this password to log in to the Key Value Copy web interface.',
      result: {
        type: 'single' as const,
        value: config?.password ?? '',
        copyable: true,
        qr: false,
        masked: true,
      },
    }
  },
)
