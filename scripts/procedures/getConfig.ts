import { compat, types as T } from "../deps.ts";

// Generate a random default password for first-time setup
const chars =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
let defaultPassword = "";
for (let i = 0; i < 24; i++) {
  defaultPassword += chars[Math.floor(Math.random() * chars.length)];
}

// NOTE on 0.3.5.1 config: embassy only lets *nullable* fields be added to an
// existing install without breaking config generation on upgrade (a missing
// non-nullable field errors — the default only applies to a fresh config). Of
// the value types, only `string` and `number` support `nullable`, so the policy
// knobs are modelled as nullable strings (validated by pattern) + a nullable
// number, rather than the enum/toggle used on 0.4.0. A blank value falls back to
// the server-side default. (0.4.0 uses proper dropdowns/toggles.)
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
  "conflict-strategy": {
    type: "string" as const,
    name: "Conflict resolution",
    description:
      "How to handle the same entry edited in two places at once. 'preserve' keeps both versions (recommended — nothing is lost); 'lww' keeps only the most recent edit. Leave blank for the default (preserve).",
    nullable: true,
    default: null,
    pattern: "^(preserve|lww)$",
    "pattern-description": "Must be 'preserve' or 'lww'",
  },
  "delete-strategy": {
    type: "string" as const,
    name: "Edit vs. delete",
    description:
      "When one device edits an entry that another deleted: 'edit-wins' brings it back (recommended); 'delete-wins' honors the deletion. Leave blank for the default (edit-wins).",
    nullable: true,
    default: null,
    pattern: "^(edit-wins|delete-wins)$",
    "pattern-description": "Must be 'edit-wins' or 'delete-wins'",
  },
  "tombstone-days": {
    type: "number" as const,
    name: "Deleted-entry retention (days)",
    description:
      "How long to remember deletions so an offline device can still learn about them. Leave blank for the default (90).",
    nullable: true,
    default: null,
    range: "[1,3650]",
    integral: true,
    units: "days",
  },
  "enable-api": {
    type: "string" as const,
    name: "Companion-device sync",
    description:
      "Set to 'false' to disable companion-app pairing/sync entirely (web interface only). Leave blank to keep it enabled.",
    nullable: true,
    default: null,
    pattern: "^(true|false)$",
    "pattern-description": "Must be 'true' or 'false'",
  },
});
