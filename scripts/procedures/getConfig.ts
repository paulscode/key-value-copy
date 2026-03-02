import { compat, types as T } from "../deps.ts";

// Generate a random default password for first-time setup
const chars =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
let defaultPassword = "";
for (let i = 0; i < 24; i++) {
  defaultPassword += chars[Math.floor(Math.random() * chars.length)];
}

export const getConfig: T.ExpectedExports.getConfig = compat.getConfig({
  password: {
    type: "string" as const,
    name: "Password",
    description:
      "Password required to access the Key Value Copy web interface. A random password is pre-filled on first install — save it as-is or change it.",
    nullable: false,
    masked: true,
    copyable: true,
    pattern: "^.{8,}$",
    "pattern-description": "Must be at least 8 characters",
    default: defaultPassword,
  },
});
