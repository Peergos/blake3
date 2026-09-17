#!/usr/bin/env python3
"""Check our hashes against the b3sum binary, which is the requirement in blake3.md:
"b3sum on the file must print what we store".

Independent of test_vectors.json - a shared misreading of the spec would pass those and
fail here. Generates random files, including sizes either side of the 4 MiB chunk
boundary, hashes them with b3sum, then has the browser hash the same bytes both one-shot
and as merged 4 MiB subtree chaining values.

    tools/crosscheck.py [firefox|chromium]
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MiB = 1024 * 1024
# sizes that exercise: sub-chunk, exactly one 4MiB chunk, either side of it, and
# multi-chunk inputs whose last chunk is partial
SIZES = [0, 1, 1023, 1024, 1025, 4 * MiB - 1, 4 * MiB, 4 * MiB + 1, 8 * MiB, 10 * MiB]


def main():
    browser = sys.argv[1] if len(sys.argv) > 1 else "firefox"
    if not shutil.which("b3sum"):
        print("b3sum not installed: cargo install b3sum, or apt install b3sum", file=sys.stderr)
        return 2
    data_dir = os.path.join(ROOT, "bench", "crosscheck-data")
    os.makedirs(data_dir, exist_ok=True)
    expected = {}
    try:
        for size in SIZES:
            name = "%d.bin" % size
            path = os.path.join(data_dir, name)
            with open(path, "wb") as f:
                f.write(os.urandom(size))
            out = subprocess.run(["b3sum", "--no-names", path], capture_output=True, text=True, check=True)
            expected[name] = out.stdout.strip()
        with open(os.path.join(data_dir, "files.json"), "w") as f:
            json.dump(sorted(expected), f)

        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import browser as browser_tool
        httpd, port = browser_tool.serve()
        url = "http://127.0.0.1:%d/bench/crosscheck.html" % port
        res = (browser_tool.run_firefox(url, 300) if browser == "firefox"
               else browser_tool.run_chromium(url, 300))
        httpd.shutdown()

        failures = 0
        for name, want in sorted(expected.items(), key=lambda kv: int(kv[0].split(".")[0])):
            got = res.get(name, {})
            for label in ("oneShot", "subtrees"):
                value = got.get(label)
                if value is None:
                    continue  # subtrees is absent for inputs below one chunk
                status = "ok" if value == want else "MISMATCH"
                if value != want:
                    failures += 1
                print("%-14s %-9s %s %s" % (name, label, status, value))
        print()
        print("b3sum agreement: %s" % ("all match" if failures == 0 else "%d MISMATCHES" % failures))
        return 0 if failures == 0 else 1
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
