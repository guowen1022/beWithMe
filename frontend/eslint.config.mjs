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
    // Vendored third-party bundles served as static assets. Linting a minified
    // library is meaningless -- plotly-2.32.0.min.js alone accounted for 186 of
    // 203 errors (92%) and dominated the type-aware lint's memory use.
    "public/**",
  ]),
]);

export default eslintConfig;
