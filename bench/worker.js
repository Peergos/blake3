// One hashing worker. Timing starts when the worker already holds the ArrayBuffer, as
// blake3.md requires: a candidate that must first copy the bytes somewhere pays for it.
import { subtreeCV, hash, CHUNK_LEN } from "../src/blake3.js";

function makeBytes(len, seed) {
    const b = new Uint8Array(len);
    let x = seed | 1;
    for (let i = 0; i < len; i++) {
        x ^= x << 13; x ^= x >>> 17; x ^= x << 5; x |= 0;
        b[i] = x & 0xff;
    }
    return b;
}

async function once(mode, bytes) {
    if (mode === "sha256") {
        await crypto.subtle.digest("SHA-256", bytes);
        return;
    }
    if (mode === "sha512") {
        await crypto.subtle.digest("SHA-512", bytes);
        return;
    }
    if (mode === "blake3-subtree") {
        subtreeCV(bytes, 0);
        return;
    }
    if (mode === "blake3-oneshot") {
        hash(bytes);
        return;
    }
    throw new Error("unknown mode " + mode);
}

self.onmessage = async (e) => {
    const { mode, sizeBytes, runs, warmup, seed } = e.data;
    const bytes = makeBytes(sizeBytes, seed);
    for (let i = 0; i < warmup; i++)
        await once(mode, bytes);
    const times = [];
    for (let i = 0; i < runs; i++) {
        const t0 = performance.now();
        await once(mode, bytes);
        times.push(performance.now() - t0);
    }
    self.postMessage({ times });
};
