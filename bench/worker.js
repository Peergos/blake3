// One hashing worker. Timing starts when the worker already holds the ArrayBuffer: a
// candidate that must first copy the bytes somewhere pays for that here.
import { subtreeCV, hash, CHUNK_LEN } from "../src/blake3.js";
import { load } from "./wasm.js";
import { subtreeCV as fastSubtreeCV, subtreeCVSmall } from "../src/blake3-fast.js";

let wasm = {};
async function wasmFor(variant) {
    if (!wasm[variant])
        wasm[variant] = await load("./wasm/blake3-" + variant + ".wasm");
    return wasm[variant];
}

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
    if (mode === "blake3-fast") {
        fastSubtreeCV(bytes, 0);
        return;
    }
    if (mode === "blake3-fast-small") {
        subtreeCVSmall(bytes, 0);
        return;
    }
    if (mode === "blake3-oneshot") {
        hash(bytes);
        return;
    }
    // the wasm candidates: "copy" includes the memcpy into linear memory, which is what
    // an upload pays; "nocopy" shows the kernel alone, to price the copy separately
    if (mode.startsWith("wasm-")) {
        const [, variant, kind] = mode.split("-");
        const w = await wasmFor(variant);
        if (kind === "nocopy")
            w.subtreeCVInPlace(bytes.length, 0);
        else if (kind === "copyonly")
            w.copyIn(bytes);
        else
            w.subtreeCV(bytes, 0);
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
    // loopStart is after warmup and after any module/wasm compilation, so the aggregate
    // rate below is hashing throughput rather than worker startup
    const loopStart = performance.now();
    for (let i = 0; i < runs; i++) {
        const t0 = performance.now();
        await once(mode, bytes);
        times.push(performance.now() - t0);
    }
    self.postMessage({ times, loopMs: performance.now() - loopStart });
};
