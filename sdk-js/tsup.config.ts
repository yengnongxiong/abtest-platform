import { defineConfig } from "tsup";

// ESM for bundlers and modern Node, CJS for require(); minified because bundle size is a
// published success criterion (PRD §5).
export default defineConfig({
  entry: ["src/index.ts"],
  format: ["esm", "cjs"],
  dts: {
    // tsup's declaration build always sets the `baseUrl` option, which TypeScript 6
    // deprecates. tsup is no longer maintained, so silence it for this step only
    // (see docs/decisions.md, "Toolchain versions").
    compilerOptions: { ignoreDeprecations: "6.0" },
  },
  minify: true,
  target: "es2020",
  clean: true,
});
