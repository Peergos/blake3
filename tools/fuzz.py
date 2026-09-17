#!/usr/bin/env python3
"""Fuzz every implementation against b3sum, with random data at random lengths.

The test vectors are a fixed set of lengths, and most of the code here is generated, so
a length dependent mistake - a partial final block, an odd chunk count, a subtree at a
high offset - would not necessarily show up there. This generates random files, takes
b3sum as the oracle for the whole-input hash, and uses the readable reference as the
oracle for subtree chaining values, which b3sum cannot produce.

    tools/fuzz.py [iterations] [seed] [firefox|chromium]

Every failure prints the length and the seed, so it can be reproduced exactly.
"""
import json
import os
import random
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KiB = 1024
MiB = 1024 * KiB


def lengths(rng, n):
    """Random lengths, weighted towards the boundaries where mistakes live."""
    interesting = [0, 1, 63, 64, 65, 1023, 1024, 1025, 2047, 2048, 2049,
                   4 * MiB - 1, 4 * MiB, 4 * MiB + 1]
    out = []
    for i in range(n):
        roll = rng.random()
        if roll < 0.25:
            out.append(rng.choice(interesting))
        elif roll < 0.55:
            # near a chunk or block boundary
            base = rng.choice([64, 1024, 64 * KiB, MiB])
            out.append(max(0, base * rng.randint(1, 8) + rng.randint(-2, 2)))
        elif roll < 0.9:
            out.append(rng.randint(0, 2 * MiB))
        else:
            out.append(rng.randint(0, 6 * MiB))
    return out


def subtree_cases(rng, length):
    """Valid subtree requests inside a file of this length: a power of two number of
    whole chunks, starting at a multiple of that count."""
    total_chunks = length // KiB
    cases = []
    if total_chunks < 1:
        return cases
    for _ in range(3):
        max_pow = 0
        while (1 << (max_pow + 1)) <= min(total_chunks, 1024):
            max_pow += 1
        chunks = 1 << rng.randint(0, max_pow)
        starts = total_chunks // chunks
        if starts < 1:
            continue
        index = rng.randrange(starts) * chunks
        cases.append({"chunks": chunks, "index": index})
    return cases


def main():
    # arguments in any order: a browser name, and up to two numbers (iterations, seed)
    args = sys.argv[1:]
    browser = "firefox"
    numbers = []
    for arg in args:
        if arg in ("firefox", "chromium"):
            browser = arg
        else:
            try:
                numbers.append(int(arg))
            except ValueError:
                print("unrecognised argument: %s" % arg, file=sys.stderr)
                return 2
    iterations = numbers[0] if numbers else 40
    seed = numbers[1] if len(numbers) > 1 else random.randrange(1 << 30)
    if not shutil.which("b3sum"):
        print("b3sum not installed", file=sys.stderr)
        return 2
    rng = random.Random(seed)
    print("seed %d, %d files" % (seed, iterations))

    data_dir = os.path.join(ROOT, "bench", "fuzz-data")
    shutil.rmtree(data_dir, ignore_errors=True)
    os.makedirs(data_dir)
    manifest = []
    try:
        for i, length in enumerate(lengths(rng, iterations)):
            name = "%03d.bin" % i
            path = os.path.join(data_dir, name)
            with open(path, "wb") as f:
                f.write(rng.randbytes(length))
            digest = subprocess.run(["b3sum", "--no-names", path],
                                    capture_output=True, text=True, check=True).stdout.strip()
            manifest.append({"name": name, "len": length, "b3sum": digest,
                             "subtrees": subtree_cases(rng, length)})
        with open(os.path.join(data_dir, "manifest.json"), "w") as f:
            json.dump(manifest, f)
        total_bytes = sum(m["len"] for m in manifest)
        print("%.1f MiB of random data, %d subtree cases"
              % (total_bytes / MiB, sum(len(m["subtrees"]) for m in manifest)))

        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import browser as bt
        httpd, port = bt.serve()
        url = "http://127.0.0.1:%d/bench/fuzz.html" % port
        res = (bt.run_firefox(url, 1800) if browser == "firefox"
               else bt.run_chromium(url, 1800))
        httpd.shutdown()
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)

    failures = res.get("failures", [])
    for f in failures:
        print("FAIL %s len=%s %s\n  got  %s\n  want %s"
              % (f.get("impl"), f.get("len"), f.get("case", ""), f.get("got"), f.get("want")))
    print("\n%d checks, %d failures (seed %d)" % (res.get("checks", 0), len(failures), seed))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
