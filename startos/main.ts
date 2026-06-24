import { configJson } from './fileModels/config.json'
import { sdk } from './sdk'
import { port } from './utils'

export const main = sdk.setupMain(async ({ effects }) => {
  /**
   * ======================== Setup ========================
   */
  console.info('Starting Key Value Copy')

  // .const (not .once) so changing the password re-runs main and restarts the
  // server with the new value, rather than waiting for a manual restart.
  const config = (await configJson.read().const(effects)) ?? { password: '' }

  const container = await sdk.SubContainer.of(
    effects,
    { imageId: 'main' },
    sdk.Mounts.of().mountVolume({
      volumeId: 'main',
      subpath: null,
      mountpoint: '/data',
      readonly: false,
    }),
    'key-value-copy',
  )

  /**
   * ======================== Daemons ========================
   */
  return sdk.Daemons.of(effects).addDaemon('main', {
    subcontainer: container,
    exec: {
      command: ['/usr/local/bin/docker_entrypoint_040.sh'],
      env: {
        KVC_PASSWORD: config.password,
        KVC_DATA_DIR: '/data',
        KVC_STATIC_DIR: '/var/www/html',
        KVC_PORT: String(port),
        KVC_BIND: '0.0.0.0',
      },
      runAsInit: true,
    },
    ready: {
      display: 'Web Interface',
      fn: () =>
        sdk.healthCheck.checkPortListening(effects, port, {
          successMessage: 'The Key Value Copy UI is accessible',
          errorMessage: 'The Key Value Copy UI is starting',
        }),
    },
    requires: [],
  })
})
