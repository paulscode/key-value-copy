import { compat, types as T } from "../deps.ts";

// The second argument MUST equal the `version` in manifest.yaml. StartOS uses it
// to mark how far the data has been migrated; if it lags the package version, the
// service is considered un-migrated and gets stuck on "Starting" after an update.
// The Makefile's check-0351 target enforces this equality at build time.
export const migration: T.ExpectedExports.migration = compat.migrations
    .fromMapping({}, "1.1.0.0");
