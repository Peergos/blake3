// The test body, shared by the node runner and the browser page.
// Returns {passed, failed, failures: [...]} and never throws for a test failure.

import { hash, subtreeCV, mergeNonRoot, mergeRoot, toHex, CHUNK_LEN } from "../src/blake3.js";

/** The official vectors' input: a repeating 251 byte pattern. */
function testInput(len) {
    const out = new Uint8Array(len);
    for (let i = 0; i < len; i++)
        out[i] = i % 251;
    return out;
}

export function runTests(vectors) {
    const failures = [];
    let passed = 0;
    const check = (name, got, want) => {
        if (got === want)
            passed++;
        else
            failures.push({ name, got, want });
    };

    // 1. The official BLAKE3 test vectors: our hash() is b3sum.
    for (const c of vectors.cases) {
        const input = testInput(c.input_len);
        check("vector len=" + c.input_len, toHex(hash(input)), c.hash.slice(0, 64));
    }

    // 2. A subtree CV is not a hash: the two must differ, or we are finalising when we
    //    should not be: chunk CVs are stored and merged, not finalised.
    const oneChunk = testInput(CHUNK_LEN);
    check("chunk CV differs from chunk hash",
        toHex(subtreeCV(oneChunk, 0)) === toHex(hash(oneChunk)) ? "same" : "different", "different");

    // 3. The property everything rests on: hashing a file in aligned power-of-two
    //    subtrees and merging the CVs gives the real BLAKE3 hash of the file.
    //    Sizes in 1KiB chunks, with the subtree size an upload would use.
    for (const [totalChunks, subtreeChunks] of [[2, 1], [4, 1], [4, 2], [8, 2], [8, 4], [16, 4], [64, 16]]) {
        const bytes = testInput(totalChunks * CHUNK_LEN);
        const cvs = [];
        for (let i = 0; i < totalChunks; i += subtreeChunks)
            cvs.push(subtreeCV(bytes.subarray(i * CHUNK_LEN, (i + subtreeChunks) * CHUNK_LEN), i));
        // merge pairwise up the tree, the last merge being the root
        let level = cvs;
        while (level.length > 2) {
            const next = [];
            for (let i = 0; i < level.length; i += 2)
                next.push(mergeNonRoot(level[i], level[i + 1]));
            level = next;
        }
        const root = level.length === 1 ? null : mergeRoot(level[0], level[1]);
        check("subtrees of " + subtreeChunks + " chunks rebuild hash of " + totalChunks + " chunks",
            root === null ? "single" : toHex(root), toHex(hash(bytes)));
    }

    // 4. Misuse is rejected rather than silently wrong: a subtree must be a whole,
    //    power-of-two, aligned number of chunks.
    const rejects = (name, fn) => {
        try {
            fn();
            failures.push({ name, got: "accepted", want: "rejected" });
        } catch (e) {
            passed++;
        }
    };
    rejects("rejects a partial chunk", () => subtreeCV(testInput(CHUNK_LEN + 1), 0));
    rejects("rejects 3 chunks", () => subtreeCV(testInput(3 * CHUNK_LEN), 0));
    rejects("rejects a misaligned start", () => subtreeCV(testInput(2 * CHUNK_LEN), 1));

    // 5. The real shape: 4 MiB subtrees of an 8 MiB and a 16 MiB input.
    //    This is the case that makes the root hash the file's real BLAKE3 hash.
    const MiB = 1024 * 1024;
    for (const totalMiB of [8, 16]) {
        const bytes = testInput(totalMiB * MiB);
        const per = 4 * MiB;
        const cvs = [];
        for (let off = 0; off < bytes.length; off += per)
            cvs.push(subtreeCV(bytes.subarray(off, off + per), off / CHUNK_LEN));
        let level = cvs;
        while (level.length > 2) {
            const next = [];
            for (let i = 0; i < level.length; i += 2)
                next.push(mergeNonRoot(level[i], level[i + 1]));
            level = next;
        }
        check("4MiB subtrees rebuild hash of " + totalMiB + "MiB",
            toHex(mergeRoot(level[0], level[1])), toHex(hash(bytes)));
    }

    return { passed, failed: failures.length, failures };
}
