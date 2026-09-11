import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Node-side tooling, not part of the app bundle. These are CommonJS scripts
    // run directly with `node`, so the Next.js/TypeScript rules — notably the
    // ban on `require()` — do not apply to them.
    "scripts/**",
  ]),
]);

export default eslintConfig;
