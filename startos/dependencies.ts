import { sdk } from './sdk'

// Key Value Copy is fully self-contained — no other packages are required.
export const setDependencies = sdk.setupDependencies(async ({ effects }) => ({}))
