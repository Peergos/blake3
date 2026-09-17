# Phase 0 spike: can a browser BLAKE3 match WebCrypto sha256?

This is phase 0 of `~/dev/blake3.md` — the part that can kill the plan. No Peergos code
is involved and nothing here is meant to ship: it exists to produce a number.

**The question.** Peergos wants to replace its sha256 tree hash with a real BLAKE3 tree
over 4 MiB chunks. Uploads hash in the browser, where there is no WebCrypto BLAKE3, so
the hash has to be our own code. The target from the plan is **≥742 MiB/s (≤6.7 ms per
5 MiB)**, i.e. parity with WebCrypto sha256.

**The answer so far: pure scalar JavaScript is 6.6× too slow.** It is a genuine
candidate on paper — no CSP change, no second artefact, and it hashes the bytes where
they already are — but not at this speed.

## Measured

Both browsers headless on this machine (16 cores reported), hashing from the point where
a worker already holds the `ArrayBuffer` through to a chaining value, as the plan
requires. `blake3-subtree` is the real workload: the CV of one aligned 4 MiB chunk.

| | Firefox 155 | Chromium |
|---|---|---|
| WebCrypto SHA-256, 5 MiB, 1 worker | 5.6 ms — **899 MiB/s** | 6.2 ms — **801 MiB/s** |
| WebCrypto SHA-512, 5 MiB, 1 worker | 12.6 ms — 397 MiB/s | 10.5 ms — 478 MiB/s |
| **BLAKE3 pure JS, 4 MiB subtree, 1 worker** | 29.3 ms — **136 MiB/s** | 33.1 ms — **121 MiB/s** |
| BLAKE3 pure JS, 5 MiB one-shot, 1 worker | 38.3 ms — 130 MiB/s | 40.9 ms — 122 MiB/s |
| BLAKE3 pure JS, 4 MiB × 2 workers | aggregate 194 MiB/s | aggregate 186 MiB/s |
| BLAKE3 pure JS, 4 MiB × 4 workers | aggregate 367 MiB/s | aggregate 332 MiB/s |
| BLAKE3 pure JS, 4 MiB × 16 workers | aggregate 574 MiB/s | aggregate 568 MiB/s |
| sha256, 5 MiB × 16 workers | aggregate 2899 MiB/s | aggregate 4824 MiB/s |

Read against the plan's two levers:

- **Single thread: 0.15× of sha256.** Nowhere near the target, and single-chunk latency
  is what a small file feels.
- **16 workers: 574 MiB/s aggregate, still under the 742 MiB/s target** — and sha256
  under the same parallelism reaches 2.9–4.8 GiB/s, so parallelism does not close the
  gap, it only moves both numbers up.

Per-worker throughput *falls* as workers are added (136 → 60 MiB/s at 16), so the 16
reported cores are not 16 independent hashing units for this workload.

## What is here

- `src/blake3.js` — BLAKE3 in pure JS: `hash` (one-shot, what `b3sum` prints) plus the
  subtree API the plan demands — `subtreeCV`, `mergeNonRoot`, `mergeRoot`. Written with
  the V8 techniques from https://parsa.wtf/blake3/ that cost nothing in readability:
  state in locals rather than arrays, straight-line rounds, permutation by renaming, no
  allocation per block. **Not** the generated-code or SIMD versions from that write-up.
- `test/tests.mjs` — the 35 official BLAKE3 vectors, the subtree-rebuild property at
  several sizes including the 4 MiB Peergos shape, and rejection of misaligned subtrees.
- `test/b3sum.mjs` — the same claim checked against the reference binary.
- `bench/` — the benchmark page and worker.
- `tools/browser.py` — runs a page headless in Firefox (Marionette, no driver needed) or
  Chromium (chromedriver) and prints what it produced.
- `tools/crosscheck.py` — the b3sum comparison, driven through a browser.

## Running it

```bash
python3 tools/browser.py firefox test        # tests, in a browser
python3 tools/browser.py chromium test
python3 tools/browser.py firefox bench       # the numbers above
python3 tools/crosscheck.py firefox          # vs the b3sum binary
node test/node.mjs                           # same tests, no browser
node test/b3sum.mjs                          # vs b3sum, no browser
```

## Verified

- All 35 official BLAKE3 test vectors pass, in Firefox and Chromium.
- `b3sum` agreement on random files at 0, 1, 1023, 1024, 1025, 4 MiB−1, 4 MiB, 4 MiB+1,
  8 MiB and 10 MiB bytes.
- The property the migration rests on: an 8 MiB file hashed as two 4 MiB subtree
  chaining values and merged gives the same hash `b3sum` prints. This is what makes the
  stored root hash the file's real BLAKE3 hash rather than a tree of our own.

## Not done yet

Phase 0 is not finished until the other candidates in the plan are measured. What is
missing, and why:

1. **Rust reference crate → wasm32 with `+simd128`.** Not built: this machine has apt
   `rustc` with only the host target and no `rustup`, so there is no wasm32 std. Needs
   `rustup target add wasm32-unknown-unknown` and wasm-bindgen, then the same
   `ArrayBuffer`-to-CV measurement — **counting the copy into linear memory**, which the
   JS candidate does not pay.
2. **Hand-written WASM SIMD kernel**, and **runtime-generated SIMD WASM** (the trick the
   write-up ends on, 2.21× its baseline WASM).
3. **An optimised pure JS attempt** before writing pure JS off: the write-up's generated
   straight-line code and little-endian fast path, which this implementation does not
   use. The gap is 6.6×, which is a lot to make up in scalar JS, but the measurement
   here is of a readable implementation, not of the technique at its best.
4. **Android WebView**, the slowest thing that has to do this, and desktop numbers from
   more than one machine.
5. **Subtree API in the WASM candidates.** No JS or WASM BLAKE3 library documents
   `set_input_offset` / `finalize_non_root` / `merge_subtrees_*`, so assume forking one.
   That work is part of the comparison, and it is much easier in readable JS than in
   someone's generated WASM.
6. **SharedArrayBuffer threads inside one WASM instance** versus N independent workers.
   The benchmark server already sends the COOP/COEP headers, so both are available.

## What the numbers mean for the plan

The plan's fallbacks, in its own order of preference, now have evidence:

- *Accept a slower hash for uploads and keep the parallel path.* At 574 MiB/s aggregate
  this is arguable — the network is usually slower — but it is a 5× regression against
  sha256 in the same shape, and it is worse on a phone.
- *Ship WASM where it is fast and keep sha256 trees where it is not.* Still open, and it
  now depends entirely on item 1 above.
- *Stop.* Not yet: nothing here rules out WASM, which is the candidate the plan expected
  to win.

So: **pure JS alone does not clear the bar, and the WASM candidates are now the
deciding measurement.**
