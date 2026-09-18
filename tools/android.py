#!/usr/bin/env python3
"""Run a page in the real Android WebView and print what it reported.

There is no marionette or chromedriver for a phone's WebView, so the page posts its
results back to the server instead (`?report=1`), and this waits for that.

Needs: adb, a booted device or emulator, and the tiny WebView host app from
tools/wvbench - `tools/android.py --build` builds and installs it with the SDK build
tools directly, no gradle.

    tools/android.py [bench|test|wasm|micro] [--build]

Chrome for Android uses the same engine, but it is a different app with different
settings, so this measures the WebView that an app embeds.
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST_PKG = "org.peergos.wvbench"
HOST_ACTIVITY = HOST_PKG + "/.MainActivity"
# adb reverse rather than the emulator's 10.0.2.2 alias: it works on a real device over
# usb too, and does not depend on how the emulator's networking is set up
HOST_FROM_DEVICE = "127.0.0.1"

PAGES = {"bench": "bench/", "test": "test/", "wasm": "test/wasm.html",
         "micro": "bench/micro.html", "fuzz": "bench/fuzz.html"}


def adb(*args, check=True):
    return subprocess.run(["adb", *args], capture_output=True, text=True, check=check).stdout.strip()


def device_ready():
    try:
        return adb("shell", "getprop", "sys.boot_completed").strip() == "1"
    except subprocess.CalledProcessError:
        return False


def build_and_install():
    """Build the host apk with the SDK build tools. No gradle, no network."""
    src = os.path.join(ROOT, "tools", "wvbench")
    sdk = os.environ.get("ANDROID_HOME") or os.path.expanduser("~/Android/Sdk")
    build_tools = sorted(os.listdir(os.path.join(sdk, "build-tools")))[-1]
    bt = os.path.join(sdk, "build-tools", build_tools)
    platform = sorted(p for p in os.listdir(os.path.join(sdk, "platforms")) if p.startswith("android-"))[-1]
    android_jar = os.path.join(sdk, "platforms", platform, "android.jar")
    out = os.path.join(src, "build")
    os.makedirs(out, exist_ok=True)

    def run(*cmd, **kw):
        subprocess.run(cmd, check=True, cwd=src, stdout=subprocess.DEVNULL, **kw)

    print("building the host apk with %s against %s" % (build_tools, platform))
    run("javac", "--release", "17", "-classpath", android_jar, "-d", os.path.join(out, "classes"),
        "src/org/peergos/wvbench/MainActivity.java")
    run(os.path.join(bt, "d8"), "--lib", android_jar, "--output", out,
        *[os.path.join(out, "classes", "org", "peergos", "wvbench", f)
          for f in os.listdir(os.path.join(out, "classes", "org", "peergos", "wvbench"))])
    apk_unsigned = os.path.join(out, "unsigned.apk")
    run(os.path.join(bt, "aapt"), "package", "-f", "-M", "AndroidManifest.xml",
        "-I", android_jar, "-F", apk_unsigned)
    subprocess.run([os.path.join(bt, "aapt"), "add", apk_unsigned, "classes.dex"],
                   check=True, cwd=out, stdout=subprocess.DEVNULL)
    keystore = os.path.join(out, "debug.ks")
    if not os.path.exists(keystore):
        run("keytool", "-genkeypair", "-keystore", keystore, "-storepass", "android",
            "-keypass", "android", "-alias", "debug", "-keyalg", "RSA", "-keysize", "2048",
            "-validity", "365", "-dname", "CN=debug", stderr=subprocess.DEVNULL)
    aligned = os.path.join(out, "aligned.apk")
    apk = os.path.join(out, "wvbench.apk")
    run(os.path.join(bt, "zipalign"), "-f", "4", apk_unsigned, aligned)
    if os.path.exists(apk):
        os.remove(apk)
    run(os.path.join(bt, "apksigner"), "sign", "--ks", keystore, "--ks-pass", "pass:android",
        "--key-pass", "pass:android", "--out", apk, aligned, stderr=subprocess.DEVNULL)
    # a previously installed build signed with a different debug key cannot be updated
    out = subprocess.run(["adb", "install", "-r", apk], capture_output=True, text=True)
    if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in (out.stdout + out.stderr):
        print("replacing an install signed with a different key")
        adb("uninstall", HOST_PKG, check=False)
        out = subprocess.run(["adb", "install", "-r", apk], capture_output=True, text=True)
    if out.returncode != 0:
        print(out.stdout + out.stderr, file=sys.stderr)
        raise SystemExit("install failed")
    print("installed " + os.path.basename(apk))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    page = args[0] if args else "bench"
    if "--build" in sys.argv:
        build_and_install()
    if not device_ready():
        print("no booted device: start one with\n"
              "  $ANDROID_HOME/emulator/emulator -avd <avd> -no-window -gpu swiftshader_indirect",
              file=sys.stderr)
        return 2
    if HOST_PKG not in adb("shell", "pm", "list", "packages"):
        print("host app not installed: run with --build", file=sys.stderr)
        return 2

    webview = adb("shell", "dumpsys", "webviewupdate")
    for line in webview.splitlines():
        if "Current WebView package" in line:
            print(line.strip())

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import browser as bt
    bt.REPORTS.clear()
    httpd, port = bt.serve()
    adb("reverse", "tcp:%d" % port, "tcp:%d" % port)
    url = "http://%s:%d/%s?report=1" % (HOST_FROM_DEVICE, port, PAGES[page])
    print("loading %s in the WebView" % url)
    adb("shell", "am", "force-stop", HOST_PKG, check=False)
    adb("shell", "am", "start", "-n", HOST_ACTIVITY, "-a", "android.intent.action.VIEW",
        "-d", url)
    try:
        deadline = time.time() + 1800
        while time.time() < deadline and not bt.REPORTS:
            time.sleep(2)
    finally:
        adb("shell", "am", "force-stop", HOST_PKG, check=False)
        adb("reverse", "--remove", "tcp:%d" % port, check=False)
        httpd.shutdown()
    if not bt.REPORTS:
        print("the WebView reported nothing; logcat:", file=sys.stderr)
        print(adb("logcat", "-d", "-s", "wvbench:I", "chromium:E"), file=sys.stderr)
        return 1
    report = bt.REPORTS[0]
    out_path = os.path.join(ROOT, "webview-%s.json" % page)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    if "rows" in report:
        print("\n%s, %s cores" % (report.get("userAgent", "?")[:70], report.get("cores")))
        for r in report["rows"]:
            print("  %-24s %d MiB x%-3d median %8.1f ms   per-worker %6.0f MiB/s   aggregate %6.0f MiB/s"
                  % (r["mode"], r["sizeMiB"], r["workers"], r["medianMs"],
                     r["perWorkerMiBs"], r["aggregateMiBs"]))
        print("\nverdict: %s" % json.dumps(report.get("verdict"), indent=2))
    else:
        print(json.dumps(report, indent=2))
    print("\nfull report written to %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
