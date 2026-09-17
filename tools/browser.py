#!/usr/bin/env python3
"""Run a page in a real browser and print what it produced.

Firefox is driven over Marionette, which is built in, so no driver download. Chromium
needs chromedriver from the distro. Usage:

    tools/browser.py firefox test          # run the test page
    tools/browser.py firefox bench         # run the benchmark
    tools/browser.py chromium bench

Each page sets window.__results when it is done; this waits for that and prints it.
"""
import http.server
import json
import os
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def serve():
    """A local http server, because module imports and fetch() do not work on file://."""
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=ROOT, **kw)

        def log_message(self, *a):
            pass

        def end_headers(self):
            # cross origin isolation, so the page may use SharedArrayBuffer - the
            # benchmark needs it to compare worker threads against wasm threads
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
            super().end_headers()

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Marionette:
    def __init__(self, port):
        deadline = time.time() + 40
        while True:
            try:
                self.s = socket.create_connection(("127.0.0.1", port), timeout=120)
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.5)
        self.buf = b""
        self.msgid = 0
        self._recv()
        self.cmd("WebDriver:NewSession", {"capabilities": {}})

    def _recv(self):
        while b":" not in self.buf:
            self.buf += self.s.recv(65536)
        n, _, rest = self.buf.partition(b":")
        n, self.buf = int(n), rest
        while len(self.buf) < n:
            self.buf += self.s.recv(65536)
        out, self.buf = self.buf[:n], self.buf[n:]
        return json.loads(out)

    def cmd(self, name, params=None):
        self.msgid += 1
        msg = json.dumps([0, self.msgid, name, params or {}])
        self.s.sendall(f"{len(msg)}:{msg}".encode())
        while True:
            r = self._recv()
            if isinstance(r, list) and r[0] == 1 and r[1] == self.msgid:
                if r[2]:
                    raise RuntimeError(f"{name}: {r[2]}")
                return r[3]

    def get(self, url):
        self.cmd("WebDriver:Navigate", {"url": url})

    def js(self, script):
        return self.cmd("WebDriver:ExecuteScript", {"script": script, "args": []})["value"]

    def results(self, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self.js("return window.__results || null;")
            if r is not None:
                return r
            time.sleep(1)
        raise RuntimeError("page produced no results within %ds" % timeout)

    def text(self):
        return self.js("var o = document.getElementById('out'); return o ? o.textContent : '';")


def run_firefox(url, timeout):
    port = free_port()
    profile = tempfile.mkdtemp(prefix="b3-ff-")
    proc = subprocess.Popen(
        ["firefox", "--marionette", "--new-instance", "--headless", "--profile", profile, "about:blank"],
        env={**os.environ, "MOZ_MARIONETTE_PORT": str(port)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # the port preference has to be in the profile: the env var alone is not enough
        with open(os.path.join(profile, "user.js"), "w") as f:
            f.write('user_pref("marionette.port", %d);\n' % port)
        m = Marionette(port)
        m.get(url)
        res = m.results(timeout)
        print(m.text())
        return res
    finally:
        proc.terminate()


class Chromedriver:
    def __init__(self, binary):
        self.port = free_port()
        self.proc = subprocess.Popen([binary, "--port=%d" % self.port],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        body = {"capabilities": {"alwaysMatch": {"goog:chromeOptions": {
            "args": ["--headless=new", "--no-sandbox", "--disable-dev-shm-usage"]}}}}
        self.session = self._post("/session", body)["value"]["sessionId"]

    def _post(self, path, body):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=600).read())

    def get(self, url):
        self._post("/session/%s/url" % self.session, {"url": url})

    def js(self, script):
        return self._post("/session/%s/execute/sync" % self.session,
                          {"script": script, "args": []})["value"]

    def results(self, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self.js("return window.__results || null;")
            if r is not None:
                return r
            time.sleep(1)
        raise RuntimeError("page produced no results within %ds" % timeout)

    def close(self):
        self.proc.terminate()


def run_chromium(url, timeout):
    binary = None
    for candidate in ["chromedriver", "/usr/bin/chromedriver", "/usr/lib/chromium/chromedriver"]:
        if subprocess.call(["sh", "-c", "command -v %s >/dev/null 2>&1 || test -x %s" % (candidate, candidate)]) == 0:
            binary = candidate
            break
    if binary is None:
        print("chromedriver not installed: apt install chromium-chromedriver", file=sys.stderr)
        sys.exit(2)
    d = Chromedriver(binary)
    try:
        d.get(url)
        res = d.results(timeout)
        print(d.js("var o = document.getElementById('out'); return o ? o.textContent : '';"))
        return res
    finally:
        d.close()


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    browser, page = sys.argv[1], sys.argv[2]
    timeout = int(sys.argv[3]) if len(sys.argv) > 3 else 600
    httpd, port = serve()
    pages = {"test": "test/", "bench": "bench/", "wasm": "test/wasm.html",
             "micro": "bench/micro.html"}
    url = "http://127.0.0.1:%d/%s" % (port, pages.get(page, page))
    res = run_firefox(url, timeout) if browser == "firefox" else run_chromium(url, timeout)
    httpd.shutdown()
    if page in ("test", "wasm"):
        sys.exit(0 if res.get("failed") == 0 else 1)
    print()
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
