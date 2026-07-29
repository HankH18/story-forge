/// <reference types="vite/client" />

/**
 * Vite's own `ImportMetaEnv` is an index signature returning `any`, so a typo in
 * an env var name typechecks and reads as `undefined` at runtime — the exact
 * shape of the bug this phase existed to fix. Declaring the variable here makes
 * `import.meta.env.VITE_CLERK_PUBLISHABLE_KEY` a `string | undefined` the
 * compiler knows about. Both interfaces MERGE with Vite's rather than replacing
 * them, so `import.meta.env.DEV` and friends still exist.
 */
interface ImportMetaEnv {
  /** Clerk publishable key (`pk_test_…`), from apps/web/.env.local. */
  readonly VITE_CLERK_PUBLISHABLE_KEY?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
