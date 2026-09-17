// CI runner: node test/node.mjs
import { readFileSync } from "node:fs";
import { runTests } from "./tests.mjs";

const vectors = JSON.parse(readFileSync(new URL("./test_vectors.json", import.meta.url), "utf8"));
const t0 = Date.now();
const { passed, failed, failures } = runTests(vectors);
for (const f of failures)
    console.error("FAIL " + f.name + "\n  got  " + f.got + "\n  want " + f.want);
console.log((failed === 0 ? "OK" : "FAILED") + ": " + passed + " passed, " + failed
    + " failed in " + ((Date.now() - t0) / 1000).toFixed(1) + "s");
process.exit(failed === 0 ? 0 : 1);
