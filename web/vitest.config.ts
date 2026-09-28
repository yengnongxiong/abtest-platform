import { defineConfig } from "vitest/config";

// Resolve "@/..." imports from tsconfig.json's paths, as Next.js does, so tests can import
// app code that uses them.
export default defineConfig({ resolve: { tsconfigPaths: true } });
