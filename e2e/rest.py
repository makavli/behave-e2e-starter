"""HTTP sessions built from a config section: TLS, auth, default timeout, retries."""
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import ROOT, ConfigError
from .poll import Fail

# Everything that can authenticate a call, the extra headers included (an API-management key, for example).
AUTH_KEYS = ("token", "api_key", "api_key_header", "username", "password", "token_url", "client_id", "client_secret",
             "refresh_token", "scope", "headers")
KEY_HEADER = "X-API-Key"  # the default header for api_key


def tls_verify(value):
    """verify_tls: true / false (also as the strings a variable gives), or a CA bundle path relative to the repo."""
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    if isinstance(value, str):
        path = Path(value)
        return str(path if path.is_absolute() else ROOT / path)
    return bool(value)


class Http(requests.Session):
    """A requests.Session with the section's settings.

    Keys: verify_tls (true | false | CA bundle path), request_timeout_s, retries, headers, and one auth:
    token (bearer) | username + password (basic) | token_url + client_id + client_secret (OAuth2: with refresh_token, a
    user's stored consent, e.g. Google; without it, client credentials).
    api_key (+ api_key_header) goes with any of them; a header, never the URL, so the key stays out of logs.
    """

    def __init__(self, cfg):
        super().__init__()
        self.verify = tls_verify(cfg.get("verify_tls", True))
        self.timeout = float(cfg.get("request_timeout_s", 30))
        # Only idempotent calls are retried on 429/5xx; connection failures are retried for all.
        retry = Retry(total=int(cfg.get("retries", 2)), backoff_factor=0.5, status_forcelist=(429, 502, 503, 504),
                      allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}), raise_on_status=False)
        for prefix in ("http://", "https://"):
            self.mount(prefix, HTTPAdapter(max_retries=retry))
        self.headers.update(cfg.get("headers", {}))
        if cfg.get("api_key"):
            self.headers[cfg.get("api_key_header") or KEY_HEADER] = cfg["api_key"]
        if cfg.get("token"):
            self.headers["Authorization"] = f"Bearer {cfg['token']}"
        elif cfg.get("username"):
            self.auth = (cfg["username"], cfg.get("password", ""))
        elif cfg.get("token_url"):
            self.headers["Authorization"] = f"Bearer {self._oauth_token(cfg)}"

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        return super().request(method, url, **kwargs)

    def _oauth_token(self, cfg):
        if cfg.get("refresh_token"):
            form = {"grant_type": "refresh_token", "refresh_token": cfg["refresh_token"]}
        else:
            form = {"grant_type": "client_credentials"}
        form.update(client_id=cfg.get("client_id"), client_secret=cfg.get("client_secret"))
        if cfg.get("scope"):
            form["scope"] = cfg["scope"]
        r = super().request("POST", cfg["token_url"], data=form, timeout=self.timeout)
        if r.status_code != 200:
            raise ConfigError(f"OAuth2 token request to {cfg['token_url']} failed: HTTP {r.status_code} {r.text[:200]}")
        return r.json()["access_token"]


def without_auth(cfg):
    return {k: cfg[k] for k in cfg if k not in AUTH_KEYS}  # auth values are never even resolved


def expect_ok(response, what):
    """2xx passes; 401/403 stop a wait (retrying cannot fix credentials); anything else means "not yet"."""
    if response.ok:
        return response
    if response.status_code in (401, 403):
        raise Fail(f"{what}: HTTP {response.status_code}, credentials refused")
    raise AssertionError(f"{what}: HTTP {response.status_code} {response.text[:200]}")
