import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Dict

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("SCHWAB_CLIENT_ID")
CLIENT_SECRET = os.getenv("SCHWAB_CLIENT_SECRET")
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
MARKET_BASE = "https://api.schwabapi.com/marketdata/v1"
TOKEN_FILE = Path("tokens.json")

class SchwabAuthError(Exception):
    pass

def _basic_header():
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    return {"Authorization": f"Basic {basic}"}

def load_tokens() -> Dict[str, Any]:
    refresh = os.getenv("SCHWAB_REFRESH_TOKEN")

    if refresh:
        return {
            "refresh_token": refresh,
            "access_token": os.getenv("SCHWAB_ACCESS_TOKEN"),
            "expires_in": int(os.getenv("SCHWAB_EXPIRES_IN", "0")),
            "_saved_at": int(os.getenv("SCHWAB_SAVED_AT", "0")),
        }

    if not TOKEN_FILE.exists():
        raise SchwabAuthError("No Schwab token found. Set SCHWAB_REFRESH_TOKEN or run python schwab_auth.py first.")

    return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))

def save_tokens(tokens: Dict[str, Any]):
    if os.getenv("SCHWAB_REFRESH_TOKEN"):
        return

    TOKEN_FILE.write_text(json.dumps(tokens, indent=2), encoding="utf-8")

def refresh_access_token() -> str:
    tokens = load_tokens()
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise SchwabAuthError("No refresh_token found. Run python schwab_auth.py again.")

    headers = {
        **_basic_header(),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
    }
    r = requests.post(TOKEN_URL, headers=headers, data=data, timeout=30)
    if r.status_code >= 400:
        raise SchwabAuthError(f"Refresh failed: {r.status_code} {r.text}")
    new_tokens = r.json()

    # Schwab may or may not return a new refresh token. Keep the old one if absent.
    if "refresh_token" not in new_tokens:
        new_tokens["refresh_token"] = refresh

    new_tokens["_saved_at"] = int(time.time())
    save_tokens(new_tokens)
    return new_tokens["access_token"]

def get_access_token() -> str:
    tokens = load_tokens()
    access = tokens.get("access_token")
    saved_at = tokens.get("_saved_at", 0)
    expires_in = tokens.get("expires_in", 0)

    # If token has no saved timestamp, try it first. If it fails, request wrapper refreshes.
    if access and saved_at and expires_in:
        if time.time() < saved_at + expires_in - 60:
            return access

    return refresh_access_token()

def schwab_get(path: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{MARKET_BASE}{path}"

    r = requests.get(url, headers=headers, params=params or {}, timeout=30)
    if r.status_code == 401:
        token = refresh_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        r = requests.get(url, headers=headers, params=params or {}, timeout=30)

    if r.status_code >= 400:
        raise RuntimeError(f"Schwab API error {r.status_code}: {r.text}")

    return r.json()

def get_quote(symbol: str):
    return schwab_get(f"/{symbol}/quotes")

def get_option_chain(symbol: str, contract_type="ALL", strike_count=60,
                     strategy=None, from_date=None, to_date=None):

    params = {
        "symbol": symbol,
        "contractType": contract_type,
        "strikeCount": strike_count,
    }

    if strategy:
        params["strategy"] = strategy

    if from_date:
        params["fromDate"] = from_date

    if to_date:
        params["toDate"] = to_date

    return schwab_get("/chains", params)