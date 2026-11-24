"""HTTP stand-ins: the order API, SAP's IDoc-XML endpoint and sales order OData service, a Workato recipe + jobs API.

shortcut: the IDoc endpoint reuses the framework's own IDoc rules (e2e.idoc.validate); a real SAP decides on its own.
"""
import datetime as dt
import hmac
import itertools
import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .. import idoc
from ..workato import sign
from . import sap

DELAY_S = 0.3  # how long the "asynchronous" work takes, so the clients' waiting is exercised


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def order_errors(data):
    """The order API's input rules."""
    if not isinstance(data, dict):
        return ["body: must be a JSON object"]
    errors = []
    ref = data.get("external_ref")
    if not (isinstance(ref, str) and 1 <= len(ref) <= 35):
        errors.append("external_ref: required, 1-35 characters")
    currency = data.get("currency")
    if not (isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency)):
        errors.append("currency: required, an ISO 4217 code such as EUR")
    items = data.get("items")
    if not isinstance(items, list) or not items:
        errors.append("items: at least one item")
        return errors
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"items[{i}]: must be an object")
            continue
        if not (isinstance(item.get("material"), str) and item["material"]):
            errors.append(f"items[{i}].material: required")
        quantity = item.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or quantity <= 0:
            errors.append(f"items[{i}].quantity: must be a number above 0")
        if not (isinstance(item.get("unit"), str) and item["unit"]):
            errors.append(f"items[{i}].unit: required")
    return errors


class WebSim:
    def __init__(self, raw):
        api, workato = raw["api"], raw["workato"]
        self.api_token = api["token"]
        self.idempotency_header = api.get("idempotency_header", "Idempotency-Key")
        self.workato_token = workato["api_token"]
        self.recipe_id = str(workato["recipe_id"])
        self.webhook_secret = workato.get("webhook_secret")
        self.signature_header = workato.get("signature_header", "X-Signature")
        self.sap_key = raw.get("sap", {}).get("api_key") or ""
        self.lock = threading.RLock()
        self.orders, self.replays, self.idocs, self.jobs = {}, {}, {}, []
        self._job_ids = itertools.count(1)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), type("Handler", (_Handler,), {"sim": self}))
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def later(self, fn):
        timer = threading.Timer(DELAY_S, fn)
        timer.daemon = True
        timer.start()

    def create_order(self, data, source):
        order = {"id": "ord_" + uuid.uuid4().hex[:16], "external_ref": data["external_ref"],
                 "currency": data["currency"], "items": data["items"], "status": "accepted",
                 "source": source, "created_at": _now()}
        with self.lock:
            self.orders[order["id"]] = order
        return order

    def post_idoc(self, docnum, root, errors):
        if errors:
            status = {"status": "51", "status_text": "Application document not posted: " + "; ".join(errors)}
        else:
            order = self.create_order(idoc.to_order(root), "idoc")
            status = {"status": "53", "status_text": f"Application document posted: {order['id']}"}
        with self.lock:
            self.idocs[docnum].update(status)

    def run_recipe(self, job, event):
        order = {k: event.get(k) for k in ("external_ref", "currency", "items")}
        errors = order_errors(order)
        if not errors:
            self.create_order(order, "workato")
        with self.lock:
            job.update(status="failed" if errors else "succeeded", error="; ".join(errors) or None,
                       completed_at=_now())


class _Handler(BaseHTTPRequestHandler):
    sim: WebSim
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _send(self, code, body=None):
        self._send_raw(code, "application/json", b"" if body is None else json.dumps(body).encode())

    def _send_raw(self, code, content_type, data):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self, token):
        given = self.headers.get("Authorization", "").encode()
        if hmac.compare_digest(given, f"Bearer {token}".encode()):
            return True
        self._send(401, {"error": "unauthorized"})
        return False

    def _route(self, method):
        url = urlsplit(self.path)
        parts = [p for p in url.path.split("/") if p]
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if method == "POST" else b""
        try:
            match method, parts:
                case "POST", ["api", "orders"]:
                    self._create_order(body)
                case "GET", ["api", "orders"]:
                    self._find_orders(parse_qs(url.query))
                case "GET", ["api", "orders", order_id]:
                    self._get_order(order_id)
                case "POST", ["idoc"]:
                    self._receive_idoc(body)
                case "GET", ["idoc", "status", docnum]:
                    self._idoc_status(docnum)
                case "POST", ["workato", "webhooks", _recipe]:
                    self._webhook(body)
                case "GET", ["workato", "api", "recipes", recipe_id, "jobs"]:
                    self._jobs(recipe_id)
                case "GET", ["sap", "opu", "odata", "sap", sap.SERVICE, *rest]:
                    base = f"{self.sim.url}/sap/opu/odata/sap/{sap.SERVICE}"
                    self._send_raw(*sap.answer(rest, url.query, self.headers.get("apikey"), self.sim.sap_key, base))
                case _:
                    self._send(404, {"error": f"no route for {method} {url.path}"})
        except Exception as exc:
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    # order API
    def _create_order(self, body):
        sim = self.sim
        if not self._authorized(sim.api_token):
            return
        try:
            data = json.loads(body)
        except ValueError:
            return self._send(400, {"errors": ["body: not valid JSON"]})
        errors = order_errors(data)
        if errors:
            return self._send(400, {"errors": errors})
        key = self.headers.get(sim.idempotency_header)
        with sim.lock:
            if key and key in sim.replays:
                order_id, first = sim.replays[key]
                if first != data:
                    return self._send(409, {"error": "idempotency key reused with a different body"})
                return self._send(200, sim.orders[order_id])
            order = sim.create_order(data, "api")
            if key:
                sim.replays[key] = (order["id"], data)
        self._send(201, order)

    def _find_orders(self, query):
        if not self._authorized(self.sim.api_token):
            return
        ref = query.get("external_ref", [None])[0]
        with self.sim.lock:
            items = [o for o in self.sim.orders.values() if ref is None or o["external_ref"] == ref]
        self._send(200, {"items": items})

    def _get_order(self, order_id):
        if not self._authorized(self.sim.api_token):
            return
        with self.sim.lock:
            order = self.sim.orders.get(order_id)
        self._send(200, order) if order else self._send(404, {"error": "order not found"})

    # SAP IDoc-XML endpoint
    def _receive_idoc(self, body):
        try:
            root = idoc.parse(body)
        except ValueError as exc:
            return self._send(400, {"error": f"malformed IDoc XML: {exc}"})
        docnum = idoc.control(root).get("DOCNUM", "")
        with self.sim.lock:
            self.sim.idocs[docnum] = {"docnum": docnum, "status": "64",
                                      "status_text": "IDoc ready to be transferred to application"}
        errors = idoc.validate(root)
        self.sim.later(lambda: self.sim.post_idoc(docnum, root, errors))
        self._send(200, {"docnum": docnum, "status": "64"})

    def _idoc_status(self, docnum):
        with self.sim.lock:
            status = dict(self.sim.idocs.get(docnum) or {})
        self._send(200, status) if status else self._send(404, {"error": "IDoc not found"})

    # Workato recipe webhook and jobs API
    def _webhook(self, body):
        sim = self.sim
        if sim.webhook_secret:
            given = self.headers.get(sim.signature_header, "").encode()
            if not hmac.compare_digest(given, sign(body, sim.webhook_secret).encode()):
                return self._send(401, {"error": "invalid signature"})
        try:
            event = json.loads(body)
        except ValueError:
            event = None
        if not isinstance(event, dict):
            return self._send(400, {"error": "event must be a JSON object"})
        job = {"id": next(sim._job_ids), "title": f"Order event {event.get('correlation_id', '')}",
               "status": "pending", "started_at": _now(), "completed_at": None, "error": None}
        with sim.lock:
            sim.jobs.insert(0, job)
        sim.later(lambda: sim.run_recipe(job, event))
        self._send(200, {"status": "received"})

    def _jobs(self, recipe_id):
        sim = self.sim
        if not self._authorized(sim.workato_token):
            return
        if recipe_id != sim.recipe_id:
            return self._send(404, {"error": "recipe not found"})
        with sim.lock:
            items = [dict(job) for job in sim.jobs]
        self._send(200, {"items": items,
                         "job_succeeded_count": sum(j["status"] == "succeeded" for j in items),
                         "job_failed_count": sum(j["status"] == "failed" for j in items)})
