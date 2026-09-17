#!/usr/bin/env python3
"""Profile the JS in Firefox and report what the JIT did with it.

Release Firefox has no IONFLAGS, but the Gecko profiler can be driven from environment
variables and dumped on shutdown, and its frame table records the JIT tier each JS frame
was running in. That is what settles "is this function even being Ion compiled?".

    tools/profile.py [shape] [seconds]     # shape: fast, mem, loop, small, x2

Prints self time by frame, with the tier: ion, baseline, blinterp or interpreter.
"""
import collections
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import browser as bt


def run(shape, seconds):
    profile_path = os.path.join(tempfile.mkdtemp(prefix="b3-prof-"), "profile.json")
    port = bt.free_port()
    profile_dir = tempfile.mkdtemp(prefix="b3-ff-prof-")
    with open(os.path.join(profile_dir, "user.js"), "w") as f:
        f.write('user_pref("marionette.port", %d);\n' % port)
    env = {
        **os.environ,
        "MOZ_PROFILER_STARTUP": "1",
        # 1ms sampling, and the features that give JS frames with their JIT tier
        "MOZ_PROFILER_STARTUP_INTERVAL": "1",
        "MOZ_PROFILER_STARTUP_FEATURES": "js,stackwalk,cpu,threads",
        "MOZ_PROFILER_STARTUP_FILTERS": "GeckoMain,DOM Worker",
        "MOZ_PROFILER_SHUTDOWN": profile_path,
    }
    proc = subprocess.Popen(
        ["firefox", "--marionette", "--new-instance", "--headless",
         "--profile", profile_dir, "about:blank"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    httpd, http_port = bt.serve()
    try:
        m = bt.Marionette(port)
        m.get("http://127.0.0.1:%d/bench/profile.html?shape=%s&seconds=%d"
              % (http_port, shape, seconds))
        res = m.results(seconds + 120)
        print(m.text())
        # a clean shutdown is what writes the profile out, and it has to be allowed to
        # finish: killing the process as soon as the file appears truncates it
        try:
            m.cmd("Marionette:Quit", {"flags": ["eAttemptQuit"]})
        except Exception:
            pass
    finally:
        httpd.shutdown()
        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=30)
    if not os.path.exists(profile_path):
        print("no profile written to %s" % profile_path, file=sys.stderr)
        sys.exit(1)
    return profile_path, res


def report(path):
    with open(path) as f:
        profile = json.load(f)

    threads = list(profile.get("threads", []))
    for proc in profile.get("processes", []):
        threads.extend(proc.get("threads", []))

    interesting = []
    for thread in threads:
        samples = thread["samples"]["data"]
        if not samples:
            continue
        strings = thread.get("stringTable") or thread.get("stringArray")
        frame_schema = thread["frameTable"]["schema"]
        stack_schema = thread["stackTable"]["schema"]
        s_idx = {name: i for i, name in enumerate(thread["samples"]["schema"])}
        stacks = thread["stackTable"]["data"]
        frames = thread["frameTable"]["data"]
        loc_i, impl_i = frame_schema["location"], frame_schema["implementation"]

        def describe(frame_index):
            row = frames[frame_index]
            location = strings[row[loc_i]]
            # rows are variable length: only JS frames carry an implementation
            impl = None
            if len(row) > impl_i and isinstance(row[impl_i], int):
                impl = strings[row[impl_i]]
            return location, impl

        self_time = collections.Counter()
        total = 0
        for sample in samples:
            stack_index = sample[s_idx["stack"]]
            if stack_index is None:
                continue
            total += 1
            self_time[describe(stacks[stack_index][stack_schema["frame"]])] += 1
        ours = sum(c for (loc, _), c in self_time.items() if "blake3" in loc)
        if ours:
            interesting.append((ours, thread.get("name"), total, self_time))

    if not interesting:
        print("no samples landed in blake3 code")
        return
    interesting.sort(reverse=True, key=lambda x: x[0])
    ours, name, total, self_time = interesting[0]
    print("\n=== %s: %d samples, %d in blake3 code ===" % (name, total, ours))
    for (location, impl), count in self_time.most_common(10):
        print("  %5.1f%%  %-12s %s" % (100.0 * count / total, impl or "-", location[:92]))

    tiers = collections.Counter()
    for (location, impl), count in self_time.items():
        if "blake3" in location:
            tiers[impl or "no tier recorded"] += count
    print("  --- blake3 frames by JIT tier ---")
    for tier, count in tiers.most_common():
        print("  %5.1f%% of blake3 time: %s" % (100.0 * count / ours, tier))


if __name__ == "__main__":
    shape = sys.argv[1] if len(sys.argv) > 1 else "fast"
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    path, res = run(shape, seconds)
    print("profile: %s (%.1f MB)" % (path, os.path.getsize(path) / 1e6))
    report(path)
