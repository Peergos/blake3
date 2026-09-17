// Check our hashes against the reference b3sum binary: node test/b3sum.mjs
//
// Independent of test_vectors.json. Also checks the case the whole plan depends on -
// a file's hash rebuilt from merged 4 MiB subtree chaining values - since that is the
// claim "b3sum on the file prints what we store" actually makes.

import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";
import { hash, subtreeCV, mergeNonRoot, mergeRoot, toHex, CHUNK_LEN } from "../src/blake3.js";

const MiB = 1024 * 1024;
const CHUNK = 4 * MiB;
const SIZES = [0, 1, 1023, 1024, 1025, 64 * 1024, CHUNK - 1, CHUNK, CHUNK + 1, 8 * MiB, 10 * MiB];

/** The root hash from 4 MiB subtree CVs, or null when that does not apply. */
function viaSubtrees(bytes) {
    if (bytes.length <= CHUNK || bytes.length % CHUNK !== 0)
        return null;
    let level = [];
    for (let off = 0; off < bytes.length; off += CHUNK)
        level.push(subtreeCV(bytes.subarray(off, off + CHUNK), off / CHUNK_LEN));
    while (level.length > 2) {
        const next = [];
        for (let i = 0; i < level.length; i += 2)
            next.push(mergeNonRoot(level[i], level[i + 1]));
        level = next;
    }
    return toHex(mergeRoot(level[0], level[1]));
}

const dir = mkdtempSync(join(tmpdir(), "b3sum-check-"));
let failures = 0;
try {
    for (const size of SIZES) {
        const bytes = size === 0 ? new Uint8Array(0) : randomBytes(size);
        const path = join(dir, size + ".bin");
        writeFileSync(path, bytes);
        const want = execFileSync("b3sum", ["--no-names", path], { encoding: "utf8" }).trim();
        const input = new Uint8Array(bytes);
        for (const [label, got] of [["one-shot", toHex(hash(input))], ["4MiB subtrees", viaSubtrees(input)]]) {
            if (got === null)
                continue;
            const ok = got === want;
            if (!ok)
                failures++;
            console.log(`${String(size).padStart(9)}  ${label.padEnd(14)} ${ok ? "ok" : "MISMATCH"} ${got}`);
        }
    }
} finally {
    rmSync(dir, { recursive: true, force: true });
}
console.log(failures === 0 ? "\nall match b3sum" : `\n${failures} MISMATCHES`);
process.exit(failures === 0 ? 0 : 1);
