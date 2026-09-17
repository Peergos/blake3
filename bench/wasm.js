// Loading the raw wasm module and calling its subtree API.
//
// No wasm-bindgen: the module exports its memory and a handful of functions, so the glue
// is this file. Note where the copy happens - `copyIn` is the cost the JS candidate does
// not pay, and the benchmark times it as part of the wasm path.

export async function load(url) {
    const bytes = await (await fetch(url)).arrayBuffer();
    const { instance } = await WebAssembly.instantiate(bytes, {});
    const e = instance.exports;
    const memory = e.memory;
    const inputPtr = e.input_ptr();
    const outputPtr = e.output_ptr();
    const mergePtr = e.merge_input_ptr();
    const capacity = e.input_capacity();

    // Views over the module's linear memory. Taken once: a fresh Uint8Array per chunk
    // would be an allocation per hash, on every chunk.
    const mem = new Uint8Array(memory.buffer);
    const input = mem.subarray(inputPtr, inputPtr + capacity);
    const output = mem.subarray(outputPtr, outputPtr + 32);
    const mergeIn = mem.subarray(mergePtr, mergePtr + 64);

    return {
        exports: e,
        capacity,
        /** The copy the wasm candidate has to pay before it can hash anything. */
        copyIn(bytes) {
            if (bytes.length > capacity)
                throw new Error("input " + bytes.length + " exceeds wasm buffer " + capacity);
            input.set(bytes);
        },
        /** Hash bytes already copied in. */
        subtreeCVInPlace(len, chunkIndex) {
            e.subtree_cv(len, chunkIndex >>> 0, Math.floor(chunkIndex / 4294967296) >>> 0);
            return output;
        },
        /** copy + hash, which is what an upload actually costs. */
        subtreeCV(bytes, chunkIndex) {
            this.copyIn(bytes);
            return this.subtreeCVInPlace(bytes.length, chunkIndex).slice();
        },
        hashAll(bytes) {
            this.copyIn(bytes);
            e.hash_all(bytes.length);
            return output.slice();
        },
        mergeNonRoot(left, right) {
            mergeIn.set(left, 0);
            mergeIn.set(right, 32);
            e.merge_non_root(0n, 0n);
            return output.slice();
        },
        mergeRoot(left, right) {
            mergeIn.set(left, 0);
            mergeIn.set(right, 32);
            e.merge_root();
            return output.slice();
        },
    };
}
