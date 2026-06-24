import { setupManifest } from '@start9labs/start-sdk'

const short = 'Secure key-value storage with encrypted export/import'

const long =
  'Key Value Copy is a browser-based tool for storing and copying sensitive key-value pairs. Values are encrypted at rest using AES-256-GCM via the Web Crypto API and never displayed in plain text unless explicitly revealed. Features include clipboard auto-clear, password-protected export/import, drag-and-drop reordering, and a bioluminescent dark UI theme.'

export const manifest = setupManifest({
  id: 'key-value-copy',
  title: 'Key Value Copy',
  license: 'MIT',
  packageRepo: 'https://github.com/paulscode/key-value-copy',
  upstreamRepo: 'https://github.com/paulscode/key-value-copy',
  marketingUrl: 'https://github.com/paulscode/key-value-copy',
  donationUrl: null,
  description: { short, long },
  volumes: ['main'],
  images: {
    main: {
      source: {
        dockerBuild: {
          dockerfile: 'Dockerfile',
          workdir: '.',
        },
      },
      arch: ['x86_64', 'aarch64'],
    },
  },
  // Self-contained: serves its own static UI and tiny Python backend, no
  // external services required.
  dependencies: {},
})
