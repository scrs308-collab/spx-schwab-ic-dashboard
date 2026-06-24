from datetime import date
from typing import Dict, List, Optional, Tuple
import math

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from schwab_client import get_option_chain

app = FastAPI(title="SPX Schwab IC Dashboard")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def home():
    return FileResponse("static/index.html")

def mid(bid, ask, last=None):
    try:
        bid = float(bid or 0)
        ask = float(ask or 0)
        last = float(last or 0)
    except Exception:
        return None

    if bid > 0 and ask > 0:
        return round((bid + ask) / 2, 2)
    if last > 0:
        return round(last, 2)
    return None

def flatten_chain(chain: Dict) -> Dict[str, Dict[float, dict]]:
    calls = {}
    puts = {}

    for exp_key, strikes in chain.get("callExpDateMap", {}).items():
        calls.setdefault(exp_key, {})
        for strike, contracts in strikes.items():
            if contracts:
                calls[exp_key][float(strike)] = contracts[0]

    for exp_key, strikes in chain.get("putExpDateMap", {}).items():
        puts.setdefault(exp_key, {})
        for strike, contracts in strikes.items():
            if contracts:
                puts[exp_key][float(strike)] = contracts[0]

    return {"calls": calls, "puts": puts}

def choose_expiration(flat, preferred_days: Optional[int]):
    expirations = sorted(set(flat["calls"].keys()) & set(flat["puts"].keys()))
    if not expirations:
        return None

    if preferred_days is None:
        return expirations[0]

    def dte(exp_key):
        # Schwab keys often look like: 2026-06-16:0
        try:
            return int(str(exp_key).split(":")[-1])
        except Exception:
            return 999

    return min(expirations, key=lambda x: abs(dte(x) - preferred_days))

def option_price(opt):
    return mid(opt.get("bid"), opt.get("ask"), opt.get("last"))

def spread_ok(opt, max_spread=1.50):
    bid = float(opt.get("bid") or 0)
    ask = float(opt.get("ask") or 0)
    if bid <= 0 or ask <= 0:
        return False
    return (ask - bid) <= max_spread

def recommend_ics(chain: Dict, dte: Optional[int], wing_width: int, min_credit: float, max_spread: float, count: int):
    underlying = float(chain.get("underlyingPrice") or chain.get("underlying", {}).get("last") or 0)
    volatility = float(chain.get("volatility") or 15)
    expected_move = underlying * (volatility / 100) * math.sqrt(max(dte or 1, 1) / 365)

    flat = flatten_chain(chain)
    exp = choose_expiration(flat, dte)
    if not exp:
        return {"underlying": underlying, "error": "No matching expiration found."}

    calls = flat["calls"][exp]
    puts = flat["puts"][exp]
    call_strikes = sorted(calls.keys())
    put_strikes = sorted(puts.keys())

    candidates = []
    for sp in put_strikes:
        lp = sp - wing_width
        if lp not in puts:
            continue
        if sp > underlying - expected_move * 0.65:
            continue

        for sc in call_strikes:
            lc = sc + wing_width
            if lc not in calls:
                continue
            if sc < underlying + expected_move * 0.65:
                continue

            legs = [puts[lp], puts[sp], calls[sc], calls[lc]]
            if not all(spread_ok(o, max_spread) for o in legs):
                continue

            lp_price = option_price(puts[lp])
            sp_price = option_price(puts[sp])
            sc_price = option_price(calls[sc])
            lc_price = option_price(calls[lc])
            if None in [lp_price, sp_price, sc_price, lc_price]:
                continue

            credit = round((sp_price - lp_price) + (sc_price - lc_price), 2)
            if credit < min_credit:
                continue

            max_profit = round(credit * 100, 2)
            max_loss = round((wing_width - credit) * 100, 2)
            lower_be = round(sp - credit, 2)
            upper_be = round(sc + credit, 2)
            put_buffer = round(underlying - sp, 2)
            call_buffer = round(sc - underlying, 2)
            nearest_buffer = min(put_buffer, call_buffer)
            credit_risk = round(max_profit / max_loss, 3) if max_loss > 0 else 0

            score = 0
            score += min(40, nearest_buffer / max(expected_move, 1) * 25)
            score += min(30, credit_risk * 60)
            score += min(20, credit * 3)
            score += 10 if lower_be < underlying < upper_be else 0

            candidates.append({
                "expiration": exp,
                "underlying": underlying,
                "iv": volatility,
                "expected_move": round(expected_move, 2),
                "long_put": lp,
                "short_put": sp,
                "short_call": sc,
                "long_call": lc,
                "credit": credit,
                "max_profit": max_profit,
                "max_loss": max_loss,
                "lower_be": lower_be,
                "upper_be": upper_be,
                "put_buffer": put_buffer,
                "call_buffer": call_buffer,
                "nearest_buffer": nearest_buffer,
                "credit_risk": credit_risk,
                "score": round(score, 1)
            })

    candidates.sort(key=lambda x: (x["score"], x["credit"]), reverse=True)
    return {
        "symbol": chain.get("symbol"),
        "underlying": underlying,
        "expiration": exp,
        "iv": volatility,
        "expected_move": round(expected_move, 2),
        "results": candidates[:count],
        "total_found": len(candidates)
    }

@app.get("/api/recommend")
def api_recommend(
    symbol: str = Query("$SPX"),
    dte: int = Query(0),
    wing_width: int = Query(25),
    min_credit: float = Query(0.80),
    max_spread: float = Query(2.00),
    strike_count: int = Query(10),
    count: int = Query(10)
):
    if symbol.upper() == "SPX":
        symbol = "$SPX"

    chain = get_option_chain(symbol=symbol, strike_count=strike_count)
    return recommend_ics(chain, dte=dte, wing_width=wing_width, min_credit=min_credit, max_spread=max_spread, count=count)
