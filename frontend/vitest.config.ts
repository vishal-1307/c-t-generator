import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

/**
 * Component and logic tests.
 *
 * Not a substitute for looking at the page in a browser - they assert
 * behaviour, not appearance: what a state says, which controls exist in it,
 * and what a failure does.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, ".") },
  },
  test: {
    environment: "jsdom",
    globals: true,
    // Both extensions: not every test renders a component, and a `.ts` test
    // matched by nothing is not reported as an error - it simply never runs,
    // which is worse than failing.
    include: ["__tests__/**/*.test.{ts,tsx}"],
  },
});
