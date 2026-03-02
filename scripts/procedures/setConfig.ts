import { compat, types as T } from "../deps.ts";

// compat.setConfig saves config to /root/start9/config.yaml
// The entrypoint reads the password from there and writes password.txt
export const setConfig: T.ExpectedExports.setConfig = compat.setConfig;
