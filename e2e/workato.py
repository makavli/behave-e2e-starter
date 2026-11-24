"""Workato: start a recipe through its webhook, then follow its job through the Workato jobs API.

The job must show the event's correlation_id somewhere in its jobs-API record (its title, or a job-report
column), so each test finds its own job. One jobs page holds up to 1,000 jobs, newest first, which is plenty
between a send and its first poll.
"""
import hashlib
import hmac
import json

from .config import need
from .rest import Http, expect_ok

FINAL_STATUSES = {"succeeded", "failed"}


def sign(body, secret):
    """HMAC-SHA256 of the raw body, hex: what a signed webhook carries in its signature header."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class WorkatoClient:
    def __init__(self, cfg):
        self.cfg = cfg
        common = {k: cfg[k] for k in ("verify_tls", "request_timeout_s", "retries") if k in cfg}
        self.hook = Http(common)  # webhook URLs carry their own token
        self.api = Http({**common, "token": cfg.get("api_token")})

    def send(self, event, *, forge_signature=False):
        body = json.dumps(event, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json"}
        secret = self.cfg.get("webhook_secret")
        if secret:
            headers[self.cfg.get("signature_header", "X-Signature")] = sign(body, secret + ("x" if forge_signature else ""))
        return self.hook.post(need(self.cfg, "workato", "webhook_url"), data=body, headers=headers)

    def job_for(self, correlation_id):
        base = need(self.cfg, "workato", "api_base").rstrip("/")
        recipe = need(self.cfg, "workato", "recipe_id")
        body = expect_ok(self.api.get(f"{base}/recipes/{recipe}/jobs"), "Workato jobs API").json()
        jobs = body.get("items", []) if isinstance(body, dict) else body
        return next((job for job in jobs if correlation_id in json.dumps(job)), None)

    def close(self):
        self.hook.close()
        self.api.close()
