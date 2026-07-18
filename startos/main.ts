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
  const config = (await configJson.read().const(effects)) ?? {
    password: '',
    enable_api: true,
    conflict_strategy: 'preserve' as const,
    delete_strategy: 'edit-wins' as const,
    tombstone_days: 90,
  }

  // Best-effort endpoint discovery: hand the container its own LAN (.local, TLS)
  // and Tor (.onion) URLs so device pairing can embed both. Never fatal — on any
  // failure the web UI falls back to the origin the admin is already using.
  const endpointEnv: Record<string, string> = {}
  try {
    const si = await sdk.serviceInterface.getOwn(effects, 'main').const()
    const urls: string[] = si?.addressInfo?.format('urlstring') ?? []
    const onion = urls.find((u) => u.includes('.onion'))
    const lan = urls.find((u) => u.includes('.local'))
    const ip = urls.find((u) => /^https?:\/\/\d+\.\d+\.\d+\.\d+/.test(u))
    if (onion) endpointEnv.KVC_ONION_URL = onion
    if (lan) endpointEnv.KVC_LAN_URL = lan
    if (ip) endpointEnv.KVC_LAN_IP = ip
  } catch (e) {
    console.warn('Endpoint discovery failed; pairing will use the browser origin:', e)
  }

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
        ...endpointEnv,
        KVC_PASSWORD: config.password,
        KVC_ENABLE_API: String(config.enable_api),
        KVC_CONFLICT_STRATEGY: config.conflict_strategy,
        KVC_DELETE_STRATEGY: config.delete_strategy,
        KVC_TOMBSTONE_DAYS: String(config.tombstone_days),
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
