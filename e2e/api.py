"""Client for a REST API under test: [api], or any named section such as [youtube]."""
import json as _json
import uuid

from .config import need
from .rest import KEY_HEADER, Http, without_auth


def related(metadata, data, media_type):
    """A multipart/related body, Google's media upload: the JSON metadata, then the file. (body, content type)"""
    boundary = "e2e-" + uuid.uuid4().hex
    head = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{_json.dumps(metadata)}\r\n"
            f"--{boundary}\r\nContent-Type: {media_type}\r\n\r\n").encode()
    return head + data + f"\r\n--{boundary}--\r\n".encode(), f"multipart/related; boundary={boundary}"


class ApiClient:
    def __init__(self, cfg, name="api"):
        self.cfg, self.name = cfg, name
        self.base = need(cfg, name, "base_url").rstrip("/")
        self.http = Http(cfg)

    def call(self, method, path, *, json=None, headers=None, anonymous=False, api_key=None):
        """anonymous drops every credential; api_key replaces them with that key (to prove a wrong one is refused)."""
        url = self.base + path
        if anonymous or api_key:
            cfg = without_auth(self.cfg)
            if api_key:
                cfg.update(api_key=api_key, api_key_header=self.cfg.get("api_key_header") or KEY_HEADER)
            with Http(cfg) as http:
                return http.request(method, url, json=json, headers=headers)
        return self.http.request(method, url, json=json, headers=headers)

    def upload(self, path, metadata, data, media_type):
        """POST a file and its metadata to the section's upload_url (a media upload endpoint, e.g. YouTube's)."""
        body, content_type = related(metadata, data, media_type)
        url = need(self.cfg, self.name, "upload_url").rstrip("/") + path
        return self.http.request("POST", url, data=body, headers={"Content-Type": content_type})

    def close(self):
        self.http.close()
