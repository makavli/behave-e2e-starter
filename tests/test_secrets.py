"""Self-tests for e2e/secrets.py against a stub OpenBao, and for the api_key header."""
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from e2e import config, secrets
from e2e.config import ConfigError
from e2e.rest import Http, without_auth

VALUE = "s3cret-value-never-printed"


class StubBao(BaseHTTPRequestHandler):
    revoked = []

    def log_message(self, *args):
        pass

    def reply(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/v1/auth/approle/login":
            if body == {"role_id": "r", "secret_id": "s"}:
                return self.reply(200, {"auth": {"client_token": "t-1"}})
            return self.reply(400, {"errors": ["invalid role or secret ID"]})
        if self.path == "/v1/auth/token/revoke-self":
            StubBao.revoked.append(self.headers.get("X-Vault-Token"))
            return self.reply(204)
        self.reply(404, {"errors": []})

    def do_GET(self):
        if self.headers.get("X-Vault-Token") != "t-1":
            return self.reply(403, {"errors": ["permission denied"]})
        if self.path == "/v1/secret/data/behave/youtube":
            return self.reply(200, {"data": {"data": {"api_key": VALUE}, "metadata": {"version": 1}}})
        self.reply(404, {"errors": []})


class SecretsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), StubBao)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.creds = Path(cls.tmp.name) / "approle.json"
        cls.creds.write_text(json.dumps({"role_id": "r", "secret_id": "s"}), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def cfg(self, wanted, creds=None, addr=None):
        return config.Config({
            "openbao": {"addr": addr or f"http://127.0.0.1:{self.server.server_port}",
                        "approle_file": str(creds or self.creds)},
            "secrets": wanted}, "t")

    def test_reads_the_secret_and_revokes_the_token(self):
        StubBao.revoked.clear()
        self.assertEqual(secrets.fetch(self.cfg({"YT": "behave/youtube#api_key"})), {"YT": VALUE})
        self.assertEqual(StubBao.revoked, ["t-1"])

    def test_no_secrets_section_means_no_call(self):
        self.assertEqual(secrets.fetch(config.Config({}, "t")), {})

    def test_errors_name_the_place_never_the_value(self):
        cases = [
            ({"YT": "behave/youtube#nope"}, None, "no field 'nope'"),
            ({"YT": "behave/youtube"}, None, "<path>#<field>"),
            ({"YT": "behave/missing#k"}, None, "HTTP 404"),
            ({"OPT": "behave/missing#k?", "YT": "behave/missing#k"}, None, "HTTP 404"),
        ]
        for wanted, creds, message in cases:
            with self.subTest(message), self.assertRaisesRegex(ConfigError, message) as caught:
                secrets.fetch(self.cfg(wanted, creds))
            self.assertNotIn(VALUE, str(caught.exception))

    def test_refused_login_and_missing_credentials(self):
        bad = Path(self.tmp.name) / "bad.json"
        bad.write_text(json.dumps({"role_id": "r", "secret_id": "wrong"}), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "invalid role or secret ID"):
            secrets.fetch(self.cfg({"YT": "behave/youtube#api_key"}, bad))
        with self.assertRaisesRegex(ConfigError, "no AppRole credentials"):
            secrets.fetch(self.cfg({"YT": "behave/youtube#api_key"}, Path(self.tmp.name) / "none.json"))

    def test_optional_secret_missing_path_or_field_is_left_unset(self):
        wanted = {"A": "behave/missing#k?", "B": "behave/youtube#nope?", "C": "behave/youtube#api_key?"}
        self.assertEqual(secrets.fetch(self.cfg(wanted)), {"C": VALUE})

    def test_unreachable_server(self):
        with self.assertRaisesRegex(ConfigError, "not reachable"):
            secrets.fetch(self.cfg({"YT": "behave/youtube#api_key"}, addr="http://127.0.0.1:9"))


class MaskTest(unittest.TestCase):
    def test_agent_escapes_and_one_command_per_line(self):
        self.assertEqual(secrets.mask_commands("abc-123"), ["##vso[task.setsecret]abc-123"])
        self.assertEqual(secrets.mask_commands("a;b]c%d\n\nline2"), [
            "##vso[task.setsecret]a%3Bb%5Dc%AZP25d",  # agents that decode %AZP25
            "##vso[task.setsecret]a%3Bb%5Dc%d",       # agents that do not
            "##vso[task.setsecret]line2"])


class RedactTest(unittest.TestCase):
    def test_secret_in_a_report_is_replaced_and_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            leak, clean = Path(tmp, "a", "result.json"), Path(tmp, "b.xml")
            leak.parent.mkdir()
            leak.write_text(f'{{"step": "key={VALUE}", "again": "{VALUE}"}}', encoding="utf-8")
            clean.write_text("<ok/>", encoding="utf-8")
            self.assertEqual(secrets.redact(tmp, [VALUE, ""]), [leak])
            self.assertEqual(leak.read_text(encoding="utf-8"), '{"step": "key=***", "again": "***"}')
            self.assertEqual(clean.read_text(encoding="utf-8"), "<ok/>")
            self.assertEqual(secrets.redact(tmp, []), [])

    def test_longer_secret_first_and_escaped_forms(self):
        quoted = 'pa"ss&<word>\nline2'
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp, "r.txt")
            as_xml_attr = 'pa&quot;ss&amp;&lt;word&gt;\nline2'
            report.write_text(f"{VALUE}-long | {json.dumps(quoted)} | {as_xml_attr}", encoding="utf-8")  # CRLF on Windows
            secrets.redact(tmp, [VALUE, VALUE + "-long", quoted])
            self.assertEqual(report.read_text(encoding="utf-8"), '*** | "***\\n***" | ***\n***')


class ApiKeyTest(unittest.TestCase):
    def test_key_goes_in_a_header_and_anonymous_drops_it(self):
        cfg = {"api_key": "k", "api_key_header": "X-Goog-Api-Key", "headers": {"X-Trace": "1"}}
        self.assertEqual(Http(cfg).headers["X-Goog-Api-Key"], "k")
        self.assertEqual(Http({"api_key": "k"}).headers["X-API-Key"], "k")
        self.assertEqual(without_auth(cfg), {})
        self.assertEqual(without_auth({"refresh_token": "r", "base_url": "u"}), {"base_url": "u"})


class StubToken(BaseHTTPRequestHandler):
    forms = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        StubToken.forms.append({k: v[0] for k, v in form.items()})
        data = json.dumps({"access_token": "a-" + form["grant_type"][0]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class OAuthTest(unittest.TestCase):
    def test_refresh_token_grant_and_client_credentials(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), StubToken)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            cfg = {"token_url": f"http://127.0.0.1:{server.server_port}/token", "client_id": "c", "client_secret": "s"}
            self.assertEqual(Http({**cfg, "refresh_token": "r"}).headers["Authorization"], "Bearer a-refresh_token")
            self.assertEqual(Http(cfg).headers["Authorization"], "Bearer a-client_credentials")
            self.assertEqual(StubToken.forms, [
                {"grant_type": "refresh_token", "refresh_token": "r", "client_id": "c", "client_secret": "s"},
                {"grant_type": "client_credentials", "client_id": "c", "client_secret": "s"}])
        finally:
            server.shutdown()

    def test_consent_url_pkce_challenge_is_rfc_7636s_example(self):
        with mock.patch.object(sys, "path", [str(config.ROOT / "ops" / "openbao")] + sys.path):
            import google_oauth
        url = google_oauth.consent_url("c", "http://127.0.0.1:1", "s", "st", "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["code_challenge"], ["E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"])
        self.assertEqual((query["code_challenge_method"], query["access_type"]), (["S256"], ["offline"]))


if __name__ == "__main__":
    unittest.main()
