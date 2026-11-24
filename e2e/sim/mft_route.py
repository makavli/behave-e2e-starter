"""The MFT route the simulator runs: inbound -> checks -> outbound + archive, or error (+ <name>.error.txt)."""
import hashlib
import logging
import os
import re
import shutil
import threading

ALLOWED = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.(csv|txt|xml|json|bin)")
log = logging.getLogger(__name__)


class MftRoute:
    def __init__(self, root, interval_s=0.1):
        self.root, self.interval_s = root, interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(5)

    def _loop(self):
        while not self._stop.wait(self.interval_s):
            try:
                self.sweep()
            except Exception:
                log.exception("MFT route sweep failed")

    def _path(self, area, name=""):
        return os.path.join(self.root, area, name)

    def sweep(self):
        for name in os.listdir(self._path("inbound")):
            if name.endswith((".part", ".sha256")):  # uploads in progress and checksum sidecars
                continue
            problem = self._problem(name)
            if problem:
                self._move(name, "error")
                with open(self._path("error", name + ".error.txt"), "w", encoding="utf-8") as f:
                    f.write(problem + "\n")
                continue
            tmp = self._path("outbound", name + ".part")
            shutil.copyfile(self._path("inbound", name), tmp)
            os.replace(tmp, self._path("outbound", name))
            self._move(name, "archive")

    def _problem(self, name):
        if not ALLOWED.fullmatch(name):
            return "file name or type not allowed on this route"
        with open(self._path("inbound", name), "rb") as f:
            data = f.read()
        if not data:
            return "empty file"
        sidecar = self._path("inbound", name + ".sha256")
        if os.path.exists(sidecar):
            with open(sidecar, encoding="utf-8") as f:
                expected = (f.read().split() or [""])[0].lower()
            if hashlib.sha256(data).hexdigest() != expected:
                return "SHA-256 checksum mismatch"
        return None

    def _move(self, name, area):
        for n in (name, name + ".sha256"):
            if os.path.exists(self._path("inbound", n)):
                os.replace(self._path("inbound", n), self._path(area, n))
