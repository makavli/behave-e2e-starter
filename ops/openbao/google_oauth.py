"""Store a Google OAuth refresh token in OpenBao for the behave runs, without ever showing it. Standard library only.

  python ops/openbao/google_oauth.py [path] [scope]      default: behave/youtube-oauth, YouTube (youtube.force-ssl)

Before: an OAuth client of type "Desktop app" in Google Cloud (the project with the YouTube Data API), then
  python ops/openbao/bao.py put behave/youtube-oauth client_id
  python ops/openbao/bao.py put behave/youtube-oauth client_secret
This opens Google's consent page in your browser: sign in with the account that owns the channel and allow. Google
sends a one-time code back to this machine (127.0.0.1, a random port); it is exchanged for a refresh token (with PKCE),
stored in <path>#refresh_token. Run it again when a run says the token was expired or revoked.
"""
import base64
import hashlib
import http.server
import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import bao

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE = "https://www.googleapis.com/auth/youtube.force-ssl"  # one scope for upload, captions, read and delete


class _Redirect(http.server.BaseHTTPRequestHandler):
    timeout = 10  # a browser's idle preconnect socket must not block the real redirect behind it

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if "code" in query or "error" in query:
            self.server.answer = query
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Done: you can close this tab and go back to the terminal.")

    def log_message(self, *args):  # the request line holds the code: keep it off the console
        pass


def consent_url(client_id, redirect, scope, state, verifier):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": scope,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "behave/youtube-oauth"
    scope = sys.argv[2] if len(sys.argv) > 2 else YOUTUBE
    client = bao.read(path)
    if not {"client_id", "client_secret"} <= client.keys():
        sys.exit(f"store the OAuth client first: python ops/openbao/bao.py put {path} client_id (then client_secret)")

    server = http.server.HTTPServer(("127.0.0.1", 0), _Redirect)
    server.answer, server.timeout = None, 300
    server.handle_timeout = lambda: sys.exit("no answer from the browser within 5 minutes: nothing stored")
    redirect = f"http://127.0.0.1:{server.server_port}"
    state, verifier = secrets.token_urlsafe(16), secrets.token_urlsafe(64)
    url = consent_url(client["client_id"], redirect, scope, state, verifier)
    print(f"Opening Google's consent page. If no browser opens, paste this into one:\n{url}", flush=True)
    webbrowser.open(url)
    while server.answer is None:  # a favicon request, for example, is not the answer
        server.handle_request()
    answer = server.answer
    server.server_close()

    if answer.get("state", [""])[0] != state:
        sys.exit("the answer does not belong to this request (state differs): nothing stored")
    if "error" in answer:
        sys.exit(f"Google refused: {answer['error'][0]}")
    form = urllib.parse.urlencode({
        "code": answer["code"][0], "client_id": client["client_id"], "client_secret": client["client_secret"],
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=form), timeout=30) as response:
            tokens = json.load(response)
    except urllib.error.HTTPError as exc:  # Google's error body names the error, never a token
        sys.exit(f"token exchange refused: HTTP {exc.code} {exc.read()[:300].decode(errors='replace')}")
    refresh = tokens.get("refresh_token")
    if not refresh:
        sys.exit("Google sent no refresh token: remove the app at https://myaccount.google.com/permissions, run again")
    print(f"stored {path}#refresh_token ({len(refresh)} characters), version {bao.store(path, 'refresh_token', refresh)}")


if __name__ == "__main__":
    main()
