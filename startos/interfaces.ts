import { sdk } from './sdk'
import { port } from './utils'

export const setInterfaces = sdk.setupInterfaces(async ({ effects }) => {
  const multihost = sdk.MultiHost.of(effects, 'ui')
  // Bind the container's HTTP port. StartOS automatically fronts it with TLS on
  // the LAN and exposes a .onion over Tor.
  const uiMultiOrigin = await multihost.bindPort(port, {
    protocol: 'http',
  })
  const main = sdk.createInterface(effects, {
    name: 'User Interface',
    id: 'main',
    description:
      'The Key Value Copy web interface for managing encrypted key-value pairs',
    type: 'ui',
    masked: false,
    schemeOverride: null,
    username: null,
    path: '',
    query: {},
  })

  const mainReceipt = await uiMultiOrigin.export([main])

  return [mainReceipt]
})
