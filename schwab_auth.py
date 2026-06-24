import base64
import json
import os
import urllib.parse
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("SCHWAB_CLIENT_ID")
CLIENT_SECRET = os.getenv("SCHWAB_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SCHWAB_REDIRECT_URI", "https://127.0.0.1")

AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
TOKEN_FILE = Path("tokens.json")

def require_env():
    missing = []
    if not CLIENT_ID:
        missing.append("SCHWAB_CLIENT_ID")
    if not CLIENT_SECRET:
        missing.append("SCHWAB_CLIENT_SECRET")
    if missing:
        raise SystemExit(f"Missing .env values: {', '.join(missing)}")

def build_login_url():
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)

def exchange_code(code: str):
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    r = requests.post(TOKEN_URL, headers=headers, data=data, timeout=30)
    if r.status_code >= 400:
        print(r.text)
        r.raise_for_status()
    token = r.json()
    TOKEN_FILE.write_text(json.dumps(token, indent=2), encoding="utf-8")
    return token

def main():
    require_env()
    print("\nOpen this Schwab login URL:\n")
    print(build_login_url())
    print("\nAfter login, your browser may show a page that fails to load. That is fine.")
    redirected = input("\nPaste the full redirected URL here:\n> ").strip()

    parsed = urllib.parse.urlparse(redirected)
    qs = urllib.parse.parse_qs(parsed.query)
    code = qs.get("code", [None])[0]
    if not code:
        raise SystemExit("No authorization code found in that URL. Schwab has chosen theatrical inconvenience.")

    token = exchange_code(code)
    print("\nToken saved to tokens.json.")
    print("Access token expires in:", token.get("expires_in"), "seconds")

if __name__ == "__main__":
    main()
