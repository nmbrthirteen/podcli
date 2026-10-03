import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // `npm run build` compiles src/**/*.test.ts into dist/**/*.test.js. Without
    // this, a stale build directory makes vitest run every test twice, once
    // against the TS source, once against whatever JS it compiled last time.
    exclude: ["**/node_modules/**", "**/dist/**"],
  },
});
