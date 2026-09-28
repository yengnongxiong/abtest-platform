// Prints the built SDK's size, minified and minified+gzipped, and fails above the budget
// (PRD §5: at most 5 KB min+gzip). Run after `npm run build`.
import { readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";

const BUDGET_BYTES = 5 * 1024;
let overBudget = false;
for (const file of ["dist/index.js", "dist/index.cjs"]) {
  const code = readFileSync(file);
  const gzipped = gzipSync(code, { level: 9 }).length;
  console.log(`${file}: ${String(code.length)} B minified, ${String(gzipped)} B min+gzip`);
  overBudget ||= gzipped > BUDGET_BYTES;
}
if (overBudget) {
  console.error(`over the ${String(BUDGET_BYTES)} B min+gzip budget`);
  process.exit(1);
}
