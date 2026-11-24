#!/usr/bin/env python3
"""OpenBao on the agent machine: a free, self-hosted stand-in for Azure Key Vault. Standard library only.

  python ops/openbao/bao.py install          download, verify, configure and start OpenBao (once per machine)
  python ops/openbao/bao.py start | stop     start or stop the server (it unseals itself)
  python ops/openbao/bao.py autostart [off]  start it at every sign-in, with no window (Windows; no admin)
  python ops/openbao/bao.py status           running? sealed? version
  python ops/openbao/bao.py put PATH FIELD   store a secret; the value is asked for and never shown

Everything lives in ~/.openbao (BAO_HOME), readable by your account only:
  bin/bao(.exe)  config.hcl  data/ (raft)  unseal.key  audit.log  server.log
  root-token     admin token, used only by `put`; pipelines never see it
  behave-ci.json the pipeline's AppRole (role_id + secret_id): read-only on secret/behave/*, from 127.0.0.1 only
"""
import argparse
import getpass
import hashlib
import io
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

VERSION = "2.7.3"
# The Windows zip's SHA-256, from the release's checksums.txt, pinned here: a swapped release asset is refused.
# A new VERSION needs its new digest.
ZIP_SHA256 = "969e4872b21eeee7c9b4cd312e77027e624528ca7bc9dc2361311b97e34e6e90"
ADDR = "http://127.0.0.1:8200"
HOME = Path(os.environ.get("BAO_HOME", Path.home() / ".openbao"))
WINDOWS = os.name == "nt"
BIN = HOME / "bin" / ("bao.exe" if WINDOWS else "bao")
CI_POLICY = 'path "secret/data/behave/*" {\n  capabilities = ["read"]\n}\n'
RUN_KEY, RUN_NAME = r"Software\Microsoft\Windows\CurrentVersion\Run", "OpenBao-behave"


def api(method, path, token=None, body=None, ok=(200, 204)):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"{ADDR}/v1/{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json", **({"X-Vault-Token": token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read()
    except urllib.error.HTTPError as exc:
        if exc.code in ok:
            text = exc.read()
        else:
            sys.exit(f"OpenBao {method} /v1/{path}: HTTP {exc.code} {exc.read().decode(errors='replace')[:300]}")
    return json.loads(text) if text.strip() else {}


def health():
    """sys/health answers 200 active, 429 standby, 501 not initialized, 503 sealed; None when not running."""
    try:
        return api("GET", "sys/health", ok=(200, 429, 472, 473, 501, 503))
    except urllib.error.URLError:
        return None


def lock(path):
    """Only the current account may read `path` (a file, or a folder and all it holds)."""
    if WINDOWS:
        user = os.environ["USERNAME"]
        grant = f"{user}:(OI)(CI)F" if path.is_dir() else f"{user}:F"
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", grant], check=True, capture_output=True)
    else:
        path.chmod(0o700 if path.is_dir() else 0o600)


def write_private(path, text):
    path.write_text(text, encoding="utf-8", newline="")
    lock(path)


def download():
    """The release zip, checked against the pinned ZIP_SHA256 before anything is unpacked."""
    if not WINDOWS:
        sys.exit(f"install downloads the Windows build; elsewhere install OpenBao {VERSION} to {BIN}, then run install")
    name = f"openbao_{VERSION}_windows_amd64.zip"
    blob = urllib.request.urlopen(f"https://github.com/openbao/openbao/releases/download/v{VERSION}/{name}",
                                  timeout=300).read()
    if hashlib.sha256(blob).hexdigest() != ZIP_SHA256:
        sys.exit(f"{name}: SHA-256 mismatch, refusing to install")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        BIN.parent.mkdir(parents=True, exist_ok=True)
        BIN.write_bytes(z.read("bao.exe"))
    print(f"OpenBao {VERSION}: {name} verified (SHA-256), {BIN}")


def write_config():
    p = HOME.as_posix()
    config = f"""# OpenBao for the behave pipeline: one node on this machine, reachable from this machine only.
ui            = false
disable_mlock = true
api_addr      = "{ADDR}"
cluster_addr  = "http://127.0.0.1:8201"

listener "tcp" {{
  address         = "127.0.0.1:8200"
  cluster_address = "127.0.0.1:8201"
  tls_disable     = true  # loopback only; nothing leaves this machine
}}

storage "raft" {{
  path    = "{p}/data"
  node_id = "behave-1"
}}

# Unseals itself on start from this key (32 random bytes, hex). It sits beside the data, so it protects the data
# exactly as well as your account does: the point is no secret in the repo, in Azure DevOps or in a log.
seal "static" {{
  current_key_id = "unseal-1"
  current_key    = "file://{p}/unseal.key"
}}

audit "file" "local" {{
  description = "Every request, secrets hashed"
  options = {{
    file_path = "{p}/audit.log"
  }}
}}
"""
    (HOME / "data").mkdir(parents=True, exist_ok=True)
    (HOME / "config.hcl").write_text(config, encoding="utf-8")
    if not (HOME / "unseal.key").exists():
        write_private(HOME / "unseal.key", secrets.token_hex(32))  # no newline: OpenBao reads the file untrimmed


def start():
    if health() is not None:
        return print("OpenBao is already running")
    if not BIN.exists():
        sys.exit(f"{BIN} not found: run `python ops/openbao/bao.py install` first")
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0
    log = (HOME / "server.log").open("ab")
    proc = subprocess.Popen([str(BIN), "server", f"-config={HOME / 'config.hcl'}"], stdout=log, stderr=log,
                            stdin=subprocess.DEVNULL, creationflags=flags, start_new_session=not WINDOWS)
    (HOME / "server.pid").write_text(str(proc.pid), encoding="utf-8")
    for _ in range(60):
        state = health()
        if state is not None and (not state.get("initialized") or not state.get("sealed")):
            return print(f"OpenBao {state.get('version', '')} running at {ADDR} (pid {proc.pid}, log {HOME / 'server.log'})")
        if proc.poll() is not None:
            sys.exit(f"OpenBao exited at start (code {proc.returncode}): see {HOME / 'server.log'}")
        time.sleep(0.5)
    sys.exit(f"OpenBao did not answer within 30 s: see {HOME / 'server.log'}")


def stop():
    pid_file = HOME / "server.pid"
    if not pid_file.exists():
        return print("no server.pid: not started by this tool")
    pid = pid_file.read_text(encoding="utf-8").strip()
    # The image filter keeps a stale pid (after a reboot) from killing some other process.
    cmd = ["taskkill", "/F", "/FI", f"PID eq {pid}", "/FI", "IMAGENAME eq bao.exe"] if WINDOWS else ["kill", pid]
    subprocess.run(cmd, capture_output=True)
    for _ in range(20):
        if health() is None:
            pid_file.unlink()
            return print(f"OpenBao (pid {pid}) stopped")
        time.sleep(0.25)
    sys.exit(f"OpenBao still answers at {ADDR}: pid {pid} was not its process; server.pid kept")


def autostart(state):
    """A per-user Run entry that calls `start` at sign-in: no admin, no service, no console window."""
    if not WINDOWS:
        sys.exit("autostart is Windows-only; elsewhere use a systemd user unit or an @reboot cron line")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if state == "off":
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass
            return print("autostart off")
        # The base Python, not a venv's: this file needs only the standard library, and a venv can be deleted.
        pythonw = Path(getattr(sys, "_base_executable", sys.executable)).with_name("pythonw.exe")
        if not pythonw.exists():
            sys.exit(f"{pythonw} not found: run autostart with a Python that has pythonw.exe")
        # shortcut: a BAO_HOME set only in this shell is not seen at sign-in; make it a user variable if you move it.
        cmd = subprocess.list2cmdline([pythonw, Path(__file__).resolve(), "start"])
        winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, cmd)
    print(f"autostart on: {cmd}")


def status():
    state = health()
    if state is None:
        print(f"OpenBao is not running at {ADDR}: python ops/openbao/bao.py start")
        return 1
    print(json.dumps({k: state.get(k) for k in ("initialized", "sealed", "standby", "version")}))
    return 0


def initialize():
    """Init on the first start; then the KV store, the AppRole and the read-only policy, until behave-ci.json
    exists (so an install that failed half-way is finished by the next one)."""
    if not (health() or {}).get("initialized"):
        init = api("POST", "sys/init", body={"recovery_shares": 1, "recovery_threshold": 1})
        write_private(HOME / "recovery-key.txt", init["recovery_keys_base64"][0])
        write_private(HOME / "root-token", init["root_token"])
    if (HOME / "behave-ci.json").exists():
        return print("already set up")
    root = (HOME / "root-token").read_text(encoding="utf-8").strip()
    for _ in range(60):  # the raft node needs a moment to unseal and lead after init
        state = health() or {}
        if state.get("sealed") is False and state.get("standby") is False:
            break
        time.sleep(0.5)
    else:
        sys.exit("OpenBao did not become active after init: see server.log")
    mounts, auths = api("GET", "sys/mounts", root), api("GET", "sys/auth", root)
    if "secret/" not in mounts.get("data", mounts):
        api("POST", "sys/mounts/secret", root, {"type": "kv", "options": {"version": "2"}})
    if "approle/" not in auths.get("data", auths):
        api("POST", "sys/auth/approle", root, {"type": "approle"})
    api("PUT", "sys/policies/acl/behave-ci", root, {"policy": CI_POLICY})
    api("POST", "auth/approle/role/behave-ci", root, {
        "token_policies": ["behave-ci"], "token_ttl": "10m", "token_max_ttl": "20m",
        "secret_id_bound_cidrs": ["127.0.0.1/32"], "token_bound_cidrs": ["127.0.0.1/32"]})
    role_id = api("GET", "auth/approle/role/behave-ci/role-id", root)["data"]["role_id"]
    secret_id = api("POST", "auth/approle/role/behave-ci/secret-id", root, {})["data"]["secret_id"]
    write_private(HOME / "behave-ci.json", json.dumps({"role_id": role_id, "secret_id": secret_id}))
    print("initialized: kv-v2 at secret/, AppRole behave-ci (read-only on secret/behave/*), audit log on")


def install():
    HOME.mkdir(parents=True, exist_ok=True)
    lock(HOME)
    if not BIN.exists():
        download()
    write_config()
    start()
    initialize()
    print(f"next: python ops/openbao/bao.py put behave/youtube api_key")


def _root_for(path):
    """The root token, once `path` is a pipeline secret's path and OpenBao runs (owner tools only)."""
    if not path.startswith("behave/") or ".." in path.split("/"):
        sys.exit("pipeline secrets live under behave/ (the only path the behave-ci AppRole can read)")
    if health() is None:
        sys.exit(f"OpenBao is not running at {ADDR}: python ops/openbao/bao.py start")
    return (HOME / "root-token").read_text(encoding="utf-8").strip()


def read(path):
    """The secret's fields ({} when it does not exist yet)."""
    current = api("GET", f"secret/data/{path}", _root_for(path), ok=(200, 404)).get("data") or {}
    return current.get("data") or {}


def store(path, field, value):
    """Set one field of a secret and keep the others (check-and-set on the version read); return the new version."""
    root = _root_for(path)
    current = api("GET", f"secret/data/{path}", root, ok=(200, 404)).get("data") or {}
    data = {**(current.get("data") or {}), field: value}
    version = (current.get("metadata") or {}).get("version", 0)
    return api("POST", f"secret/data/{path}", root, {"data": data, "options": {"cas": version}})["data"]["version"]


def put(path, field):
    _root_for(path)  # check the path and the server before asking for the value
    value = getpass.getpass(f"value for {path}#{field} (hidden): ").strip()
    if not value:
        sys.exit("empty value, nothing stored")
    print(f"stored {path}#{field} ({len(value)} characters), version {store(path, field, value)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("install", "start", "stop", "status"):
        sub.add_parser(name)
    p = sub.add_parser("put")
    p.add_argument("path", help="e.g. behave/youtube")
    p.add_argument("field", help="e.g. api_key")
    sub.add_parser("autostart").add_argument("state", nargs="?", choices=("on", "off"), default="on")
    args = parser.parse_args()
    if args.cmd == "put":
        return put(args.path, args.field)
    if args.cmd == "autostart":
        return autostart(args.state)
    return {"install": install, "start": start, "stop": stop, "status": status}[args.cmd]()


if __name__ == "__main__":
    sys.exit(main())
