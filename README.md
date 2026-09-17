# Phase 0 spike: can a browser BLAKE3 match WebCrypto sha256?

This is phase 0 of `~/dev/blake3.md` - the part that can kill the plan. No Peergos code
is involved and nothing here is meant to ship: it exists to produce a number.

**The question.** Peergos wants to replace its sha256 tree hash with a real BLAKE3 tree
over 4 MiB chunks. Uploads hash in the browser, where there is no WebCrypto BLAKE3, so
the hash has to be our own code. The target from the plan is **>=742 MiB/s (<=6.7 ms per
5 MiB)**, i.e. parity with WebCrypto sha256.

## Verdict: the target is met, by wasm, with room to spare

**The reference Rust crate compiled to wasm32 with its `wasm32_simd` feature reaches
2020 MiB/s in Firefox and 1732 MiB/s in Chromium - 2.1x and 2.1x WebCrypto sha256** -
hashing a 4 MiB chunk in 2.0-2.3 ms against a 6.7 ms budget, copy into linear memory
included. The artefact is 29 KB.

Two results matter as much as the headline:

- **The `wasm32_simd` crate feature is the whole difference.** Without it the same build
  is 617 MiB/s (Firefox); with it, 2020. `RUSTFLAGS=-C target-feature=+simd128` alone
  does nothing (613 vs 617), because the crate's wasm SIMD lives behind that feature,
  not behind the target feature. Anyone repeating this must pass
  `features = ["wasm32_simd"]` or they will measure a portable scalar build and conclude
  wasm is merely adequate.
- **The copy into linear memory is not the tax the plan feared**: 0.1 ms per 4 MiB,
  about 1.5% of the hash. Kernel-only (6.6 ms) and copy-included (6.5 ms) are within
  noise of each other for the scalar build. No need to avoid the copy.

**Pure scalar JavaScript is 6-7x too slow** - 140 MiB/s in Firefox, 120 in Chromium,
about 0.15x sha256 - and parallelism does not save it: 16 workers reach 613 MiB/s
aggregate, still under the single-threaded target, while per-worker throughput falls to
61 MiB/s.

## Measured

Both browsers headless on this machine (16 cores reported), hashing from the point where
a worker already holds the `ArrayBuffer` through to a chaining value, as the plan
requires. `blake3-subtree` and `wasm-*-copy` are the real workload: the CV of one
aligned 4 MiB chunk.

| 1 worker | Firefox 155 | Chromium |
|---|---|---|
| WebCrypto SHA-256, 5 MiB (the bar) | 5.1 ms - **984 MiB/s** | 6.1 ms - **822 MiB/s** |
| WebCrypto SHA-512, 5 MiB | 12.2 ms - 408 MiB/s | 11.3 ms - 441 MiB/s |
| **wasm, `wasm32_simd` feature, 4 MiB** | **2.0 ms - 2020 MiB/s** | **2.3 ms - 1732 MiB/s** |
| wasm, portable scalar, 4 MiB | 6.5 ms - 617 MiB/s | 5.9 ms - 680 MiB/s |
| wasm, `+simd128` flag only, 4 MiB | 6.5 ms - 613 MiB/s | 5.9 ms - 678 MiB/s |
| wasm copy into linear memory alone | 0.1 ms | 0.1 ms |
| pure JS, 4 MiB subtree | 28.6 ms - 140 MiB/s | 33.4 ms - 120 MiB/s |
| pure JS, 5 MiB one-shot | 37.3 ms - 134 MiB/s | 41.6 ms - 120 MiB/s |

| aggregate across workers | Firefox 155 | Chromium |
|---|---|---|
| wasm simd x2 / x4 / x16 | 1507 / 2720 / **4014 MiB/s** | 1472 / 2385 / **4483 MiB/s** |
| pure JS x2 / x4 / x16 | 206 / 387 / 613 MiB/s | 163 / 327 / 529 MiB/s |
| sha256 x16 | 2858 MiB/s | 4652 MiB/s |

At 16 workers wasm BLAKE3 matches or beats sha256 in aggregate (4014 vs 2858 in Firefox,
4483 vs 4652 in Chromium), so the parallel upload path does not regress either.

Sizes, which land in the initial page load: 12 KB scalar, 14 KB with `+simd128`, **29 KB
with `wasm32_simd`**.

## What is here

- `wasm/` — a small Rust crate wrapping the reference `blake3` crate, built for
  wasm32-unknown-unknown, exposing the plan's subtree entry points: `subtree_cv`
  (which is `set_input_offset` + `update` + `finalize_non_root`), `merge_non_root`,
  `merge_root`, plus `hash_all` for the vectors. Deliberately **not** wasm-bindgen: one
  exported memory and a preallocated input buffer, so the artefact is small, there is no
  generated JS glue, and the copy into linear memory stays visible to the benchmark.
- `src/blake3.js` — the pure JS candidate: `hash` (one-shot, what `b3sum` prints) plus
  the same subtree API — `subtreeCV`, `mergeNonRoot`, `mergeRoot`. Written with the V8
  techniques from https://parsa.wtf/blake3/ that cost nothing in readability: state in
  locals rather than arrays, straight-line rounds, permutation by renaming, no allocation
  per block. **Not** the generated-code or SIMD versions from that write-up.
- `test/tests.mjs` — the 35 official BLAKE3 vectors, the subtree-rebuild property at
  several sizes including the 4 MiB Peergos shape, and rejection of misaligned subtrees.
- `test/wasm.html` — the same vectors through each wasm build, and every wasm subtree
  call and merge checked against the JS implementation.
- `test/b3sum.mjs` — the claim checked against the reference binary.
- `bench/` — the benchmark page, worker, wasm loader, and the built `.wasm` files.
- `tools/browser.py` — runs a page headless in Firefox (Marionette, no driver needed) or
  Chromium (chromedriver) and prints what it produced.
- `tools/crosscheck.py` — the b3sum comparison, driven through a browser.

## Running it

```bash
python3 tools/browser.py firefox test        # js tests, in a browser
python3 tools/browser.py firefox wasm        # wasm vs vectors and vs the js impl
python3 tools/browser.py chromium bench      # the numbers above
python3 tools/crosscheck.py firefox          # vs the b3sum binary
node test/node.mjs                           # js tests, no browser
node test/b3sum.mjs                          # vs b3sum, no browser

# rebuilding the wasm (needs: rustup target add wasm32-unknown-unknown)
cd wasm && RUSTFLAGS="-C target-feature=+simd128" \
  cargo build --release --target wasm32-unknown-unknown
cp target/wasm32-unknown-unknown/release/blake3_wasm.wasm ../bench/wasm/blake3-simdfeature.wasm
```

## Verified

- All 35 official BLAKE3 test vectors pass, in Firefox and Chromium, for the JS
  implementation and for all three wasm builds (132 assertions).
- Every wasm subtree CV and merge agrees with the JS implementation, at several chunk
  indices including a 4 MiB subtree at offset 4 MiB.
- `b3sum` agreement on random files at 0, 1, 1023, 1024, 1025, 4 MiB−1, 4 MiB, 4 MiB+1,
  8 MiB and 10 MiB bytes.
- The property the migration rests on: an 8 MiB file hashed as two 4 MiB subtree chaining
  values and merged gives the same hash `b3sum` prints. This is what makes the stored
  root hash the file's real BLAKE3 hash rather than a tree of our own.

## Still to do before phase 0 is closed

The target is met, so the plan is not blocked. What remains is coverage, not a decision:

1. **Android WebView** — the slowest thing that has to do this, and the easiest to
   forget. Nothing here has been run on a phone. wasm SIMD is available in modern
   WebView, but the margin is 3x on desktop, not 30x.
2. **More than one machine.** These are all one desktop; a low-end laptop is the case
   that decides whether the margin is comfortable.
3. **Hand-written WASM SIMD, and runtime-generated SIMD WASM.** Now unnecessary: the
   crate's own `wasm32_simd` beats the target by 2x. Left unmeasured deliberately.
4. **An optimised pure JS attempt** — only interesting if we ever want to avoid shipping
   wasm. At 0.15x sha256 for readable scalar JS, the write-up's techniques would have to
   find 5x.
5. **SharedArrayBuffer threads inside one wasm instance** versus N independent workers.
   N workers already reach 4 GiB/s aggregate, so this is an optimisation, not a question.
6. **CSP.** Phase 4's note stands: shipping wasm needs `wasm-unsafe-eval` in the page's
   header, which is the one real cost of choosing wasm over JS.

## What the numbers mean for the plan

The plan's fallbacks are not needed. On this hardware, in both browsers:

- The browser hash is **2x faster than the sha256 it replaces**, single threaded, and
  matches or beats it across 16 workers.
- The artefact is **29 KB**, and the copy into linear memory costs **1.5%**.
- So the phase 0 exit criterion is met, and the chunk size and hash change can proceed
  on the strength of it — with the Android WebView number still owed before anyone
  relies on the margin.

**The pure JS implementation stays** in this repo, not as a candidate but as the
executable specification: it is readable, it agrees with `b3sum`, and the wasm build is
tested against it.
