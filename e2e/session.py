"""Run state shared by the step modules: config, clients opened per scenario, waits and placeholders."""
import logging
import re
import secrets
import time
import uuid

from . import api as _api, idoc as _idoc, mft as _mft, poll, workato as _workato

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_CLIENTS = {"api": _api.ApiClient, "mft": _mft.connect, "idoc": _idoc.IdocClient, "workato": _workato.WorkatoClient}


def new_vars():
    """Fresh ids for one scenario, so parallel runs and retries never see each other's data."""
    return {
        "cid": "E2E" + uuid.uuid4().hex[:12].upper(),
        "docnum": str(10**15 + secrets.randbelow(9 * 10**15)),  # 16 digits, like an SAP DOCNUM
    }


def render(context, text):
    """Fill {{name}} placeholders from the scenario's variables ({{cid}}, {{docnum}}, remembered fields)."""
    def sub(match):
        name = match.group(1)
        if name not in context.vars:
            raise ValueError(f"unknown placeholder {{{{{name}}}}}; known: {', '.join(sorted(context.vars))}")
        return str(context.vars[name])
    return _PLACEHOLDER.sub(sub, text)


def remember_response(context, response, started):
    """Keep an HTTP response for the response steps: status, parsed body, time since `started`."""
    context.response = response
    context.elapsed_ms = (time.perf_counter() - started) * 1000
    try:
        context.body = response.json()
    except ValueError:
        context.body = None


def set_file(context, name, data):
    """Make `data` the scenario's MFT file; its name must carry {{cid}}."""
    name = render(context, name)
    if context.vars["cid"] not in name:
        raise ValueError("MFT file names must contain {{cid}}: it keeps runs apart and lets cleanup find the files")
    context.file = (name, data)
    context.sidecar = None


class Session:
    def __init__(self, cfg, timeout_s, poll_s):
        self.cfg, self.timeout_s, self.poll_s = cfg, timeout_s, poll_s
        self._open = {}

    def client(self, name):
        if name not in self._open:
            section = self.cfg.section(name)
            # Any other section is a named REST API, e.g. [youtube] after: Given the "youtube" API
            self._open[name] = _CLIENTS[name](section) if name in _CLIENTS else _api.ApiClient(section, name)
        return self._open[name]

    api = property(lambda self: self.client("api"))
    mft = property(lambda self: self.client("mft"))
    idoc = property(lambda self: self.client("idoc"))
    workato = property(lambda self: self.client("workato"))

    def eventually(self, probe, what):
        return poll.eventually(probe, timeout_s=self.timeout_s, poll_s=self.poll_s, what=what)

    def close(self):
        for name, client in self._open.items():
            try:
                client.close()
            except Exception as exc:  # a dead connection must not fail the next scenario
                logging.warning("closing %s client failed: %s", name, exc)
        self._open.clear()
