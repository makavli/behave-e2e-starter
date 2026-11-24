"""Secrets from OpenBao (ops/openbao): AppRole login, KV v2 reads, token revoked after. Standard library only,
so ci/run.py can fetch secrets before the venv exists.

  [openbao]
  addr = "http://127.0.0.1:8200"
  mount = "secret"
  approle_file = "~/.openbao/behave-ci.json"   # {"role_id", "secret_id"}, kept on the agent machine
  [secrets]
  YOUTUBE_API_KEY = "behave/youtube#api_key"    # environment variable = "<path>#<field>"
  YOUTUBE_REFRESH_TOKEN = "behave/youtube-oauth#refresh_token?"  # a trailing ?: optional, unset while not stored

Values never appear in messages: errors name the variable, the path and the field only.
"""
import json
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

from .config import ConfigError, need


class _Missing(ConfigError):
    """The secret's path does not exist."""


def _call(addr, method, path, token=None, body=None):
    req = urllib.request.Request(f"{addr}/v1/{path}", method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"X-Vault-Token": token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read()
    except urllib.error.HTTPError as exc:
        try:
            errors = "; ".join(json.loads(exc.read()).get("errors", []))
        except ValueError:
            errors = ""
        raise (_Missing if exc.code == 404 else ConfigError)(
            f"OpenBao {method} /v1/{path}: HTTP {exc.code} {errors}".rstrip()) from None
    except OSError as exc:  # URLError, refused, timeout
        raise ConfigError(f"OpenBao at {addr} is not reachable ({getattr(exc, 'reason', exc)}): "
                          "start it with `python ops/openbao/bao.py start`") from None
    return json.loads(text) if text.strip() else {}


def mask_commands(value):
    """The ##vso[task.setsecret] lines that make an Azure agent mask `value` in every later log line.

    The agent unescapes ; ] and %AZP25 in the data and masks line by line: each line goes escaped, and also with
    its % left as is, for agents that do not decode %AZP25 (masking an extra string is harmless).
    """
    commands = []
    for line in value.splitlines():
        if line.strip():
            plain = line.replace(";", "%3B").replace("]", "%5D")
            pct = line.replace("%", "%AZP25").replace(";", "%3B").replace("]", "%5D")
            commands += [f"##vso[task.setsecret]{form}" for form in dict.fromkeys((pct, plain))]
    return commands


def redact(folder, values):
    """Replace every secret value in the files under `folder` with ***; return the files that held one.

    Reports are published (Allure tab, artifacts): a value that reached one must not leave the agent machine.
    """
    # Line by line (a file may hold CRLF), each line also as JSON and XML write it; longest first, so a secret that
    # holds another is not left half-shown.
    forms = {f for v in values for line in v.splitlines() if line.strip()
             for f in (line, json.dumps(line)[1:-1], json.dumps(line, ensure_ascii=False)[1:-1],
                       escape(line), escape(line, {'"': "&quot;"}))}
    needles = [f.encode() for f in sorted(forms, key=len, reverse=True)]
    leaked = []
    for path in sorted(p for p in Path(folder).rglob("*") if p.is_file()):
        data = original = path.read_bytes()
        for needle in needles:
            data = data.replace(needle, b"***")
        if data != original:
            path.write_bytes(data)
            leaked.append(path)
    return leaked


def fetch(cfg):
    """{variable: value} for every [secrets] entry; {} when the config lists none."""
    wanted = cfg.section("secrets", required=False)
    if not wanted:
        return {}
    bao = cfg.section("openbao")
    addr = need(bao, "openbao", "addr").rstrip("/")
    mount = bao.get("mount", "secret")
    creds_file = Path(need(bao, "openbao", "approle_file")).expanduser()
    try:
        creds = json.loads(creds_file.read_text(encoding="utf-8"))
        login = {"role_id": creds["role_id"], "secret_id": creds["secret_id"]}
    except (OSError, ValueError, KeyError):
        raise ConfigError(f"no AppRole credentials in {creds_file}: run `python ops/openbao/bao.py install` "
                          "on the agent machine") from None
    token = _call(addr, "POST", "auth/approle/login", body=login)["auth"]["client_token"]
    try:
        found, read = {}, {}
        for name in wanted:
            ref = wanted[name]
            optional = ref.endswith("?")
            path, _, field = ref.removesuffix("?").partition("#")
            if not path or not field:
                raise ConfigError(f"[secrets] {name} = '{ref}': write it as '<path>#<field>'")
            if path not in read:
                try:
                    read[path] = _call(addr, "GET", f"{mount}/data/{path}", token)["data"]["data"]
                except _Missing:
                    if not optional:
                        raise
                    continue  # not cached: a required entry on the same path still gets its 404
            if field not in read[path]:
                if optional:
                    continue
                raise ConfigError(f"OpenBao {mount}/{path} has no field '{field}' (for {name})")
            found[name] = str(read[path][field])
        return found
    finally:
        try:
            _call(addr, "POST", "auth/token/revoke-self", token)  # the token dies with the run, not at its TTL
        except ConfigError:
            pass
