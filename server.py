from typing import Dict, Optional
import os
import secrets

from fastapi import FastAPI, Query, Depends, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from schwab_client import get_option_chain


app = FastAPI(title="SPX Schwab IC Dashboard")
app.mount("/static", StaticFiles(directory="static"), name="static")

security = HTTPBasic()

APP_USERNAME = os.getenv("APP_USERNAME", "joe")
APP_PASSWORD = os.getenv("APP_PASSWORD", "change-me")


def require_login(credentials: HTTPBasicCredentials = Depends(security)):
    username_ok = secrets.compare_digest(credentials.username, APP_USERNAME)
    password_ok = secrets.compare_digest(credentials.password, APP_PASSWORD)

    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials.username


@app.get("/")
def home(user: str = Depends(require_login)):
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


def flatten_chain(chain: Dict) -> Dict[str, Dict[str, Dict[float, dict]]]:
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


def expiration_dte(exp_key: str) -> int:
    try:
        return int(str(exp_key).split(":")[-1])
    except Exception:
        return 999


def clean_expiration(exp_key: Optional[str]) -> Optional[str]:
    if not exp_key:
        return None
    return str(exp_key).split(":")[0]


def choose_expiration(flat: Dict, preferred_days: Optional[int]):
    expirations = sorted(set(flat["calls"].keys()) & set(flat["puts"].keys()))

    if not expirations:
        return None

    if preferred_days is None:
        return expirations[0]

    return min(expirations, key=lambda x: abs(expiration_dte(x) - preferred_days))


def option_price(opt):
    if not opt:
        return None

    return mid(
        opt.get("bid"),
        opt.get("ask"),
        opt.get("last"),
    )


def spread_ok(opt, max_spread=2.00):
    if not opt:
        return False

    return opt_spread(opt) is not None and opt_spread(opt) <= max_spread


def opt_spread(opt):
    try:
        bid = float(opt.get("bid") or 0)
        ask = float(opt.get("ask") or 0)
    except Exception:
        return None

    if bid <= 0 or ask <= 0:
        return None

    return round(ask - bid, 2)


def opt_delta(opt):
    try:
        return abs(float(opt.get("delta") or 0))
    except Exception:
        return 0.0


def extract_iv_rank(chain: Dict, current_iv: float):
    keys = (
        "ivRank",
        "iv_rank",
        "impliedVolatilityRank",
        "impliedVolatilityPercentile",
        "volatilityRank",
    )

    for key in keys:
        try:
            value = chain.get(key)
            if value is not None:
                return round(float(value), 2), "schwab"
        except Exception:
            continue

    underlying = chain.get("underlying") or {}

    for key in keys:
        try:
            value = underlying.get(key)
            if value is not None:
                return round(float(value), 2), "schwab_underlying"
        except Exception:
            continue

    return round(current_iv, 2), "current_iv_proxy"


def estimate_expected_move_from_straddle(flat: Dict, exp: Optional[str], underlying: float) -> float:
    if not exp:
        return 0.0

    calls = flat.get("calls", {}).get(exp, {})
    puts = flat.get("puts", {}).get(exp, {})

    if not calls or not puts:
        return 0.0

    common_strikes = set(calls.keys()) & set(puts.keys())

    if not common_strikes:
        return 0.0

    nearest_strike = min(common_strikes, key=lambda s: abs(s - underlying))

    call_price = option_price(calls.get(nearest_strike))
    put_price = option_price(puts.get(nearest_strike))

    if call_price is None or put_price is None:
        return 0.0

    return round(call_price + put_price, 2)


def pop_from_delta(delta):
    return max(0, min(100, round((1 - abs(delta)) * 100, 1)))


def trade_grade(score):
    if score >= 90:
        return "A+"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def recommendation_score(
    nearest_buffer: float,
    expected_move: float,
    credit: float,
    wing_width: int,
    condor_pop: float,
    put_delta: float,
    call_delta: float,
    put_buffer: float,
    call_buffer: float,
    avg_leg_spread: float,
    max_spread: float,
    lower_be: float,
    upper_be: float,
    underlying: float,
    iv_rank: float,
):
    target_short_delta = 0.10
    em_buffer_ratio = nearest_buffer / max(expected_move, 1)
    credit_width_ratio = credit / max(wing_width, 1)
    spread_quality = max(0, 1 - avg_leg_spread / max(max_spread, 0.01))
    delta_quality = max(
        0,
        1 - (abs(put_delta - target_short_delta) + abs(call_delta - target_short_delta)) / 0.30,
    )
    symmetry = max(0, 1 - abs(put_buffer - call_buffer) / max(nearest_buffer, 1))

    score = 0
    score += min(30, em_buffer_ratio * 22)
    score += min(22, credit_width_ratio * 220)
    score += min(18, condor_pop / 100 * 22)
    score += min(12, delta_quality * 12)
    score += min(8, symmetry * 8)
    score += min(6, spread_quality * 6)
    score += 4 if lower_be < underlying < upper_be else 0
    score += min(5, max(0, iv_rank - 20) / 80 * 5)

    return round(score, 1), {
        "em_buffer_ratio": round(em_buffer_ratio, 2),
        "delta_quality": round(delta_quality, 2),
        "symmetry": round(symmetry, 2),
        "spread_quality": round(spread_quality, 2),
    }


def recommend_ics(
    chain: Dict,
    dte: Optional[int],
    wing_width: int,
    min_credit: float,
    max_spread: float,
    count: int,
    buffer_mult: float,
    min_iv_rank: Optional[float],
    max_iv_rank: Optional[float],
    min_short_delta: Optional[float],
    max_short_delta: Optional[float],
    min_credit_width: Optional[float],
):
    underlying = float(
        chain.get("underlyingPrice")
        or chain.get("underlying", {}).get("last")
        or 0
    )
    volatility = float(chain.get("volatility") or 0)
    iv_rank, iv_rank_source = extract_iv_rank(chain, volatility)

    if min_iv_rank is not None and iv_rank < min_iv_rank:
        return {
            "symbol": chain.get("symbol"),
            "underlying": underlying,
            "iv": volatility,
            "iv_rank": iv_rank,
            "iv_rank_source": iv_rank_source,
            "error": f"IV Rank {iv_rank} is below the minimum filter of {min_iv_rank}.",
            "results": [],
            "total_found": 0,
        }

    if max_iv_rank is not None and iv_rank > max_iv_rank:
        return {
            "symbol": chain.get("symbol"),
            "underlying": underlying,
            "iv": volatility,
            "iv_rank": iv_rank,
            "iv_rank_source": iv_rank_source,
            "error": f"IV Rank {iv_rank} is above the maximum filter of {max_iv_rank}.",
            "results": [],
            "total_found": 0,
        }

    flat = flatten_chain(chain)
    exp = choose_expiration(flat, dte)

    if not exp:
        return {
            "symbol": chain.get("symbol"),
            "underlying": underlying,
            "iv": volatility,
            "iv_rank": iv_rank,
            "iv_rank_source": iv_rank_source,
            "error": "No matching expiration found.",
            "results": [],
            "total_found": 0,
        }

    expected_move = estimate_expected_move_from_straddle(flat, exp, underlying)

    calls = flat["calls"][exp]
    puts = flat["puts"][exp]

    call_strikes = sorted(calls.keys())
    put_strikes = sorted(puts.keys())

    candidates = []
    rejects = {
        "credit_too_low": 0,
        "bid_ask_too_wide": 0,
        "missing_mid_price": 0,
        "buffer_too_small": 0,
        "no_matching_wing": 0,
        "delta_out_of_range": 0,
        "credit_width_too_low": 0,
    }

    min_put_short = underlying - expected_move * buffer_mult
    min_call_short = underlying + expected_move * buffer_mult

    for sp in put_strikes:
        lp = sp - wing_width

        if lp not in puts:
            rejects["no_matching_wing"] += 1
            continue

        if sp > min_put_short:
            rejects["buffer_too_small"] += 1
            continue

        for sc in call_strikes:
            lc = sc + wing_width

            if lc not in calls:
                rejects["no_matching_wing"] += 1
                continue

            if sc < min_call_short:
                rejects["buffer_too_small"] += 1
                continue

            legs = [puts[lp], puts[sp], calls[sc], calls[lc]]

            if not all(spread_ok(o, max_spread) for o in legs):
                rejects["bid_ask_too_wide"] += 1
                continue

            lp_price = option_price(puts[lp])
            sp_price = option_price(puts[sp])
            sc_price = option_price(calls[sc])
            lc_price = option_price(calls[lc])

            if None in [lp_price, sp_price, sc_price, lc_price]:
                rejects["missing_mid_price"] += 1
                continue

            credit = round((sp_price - lp_price) + (sc_price - lc_price), 2)

            if credit < min_credit:
                rejects["credit_too_low"] += 1
                continue

            max_profit = round(credit * 100, 2)
            max_loss = round((wing_width - credit) * 100, 2)

            lower_be = round(sp - credit, 2)
            upper_be = round(sc + credit, 2)

            put_buffer = round(underlying - sp, 2)
            call_buffer = round(sc - underlying, 2)
            nearest_buffer = min(put_buffer, call_buffer)

            credit_risk = round(max_profit / max_loss, 3) if max_loss > 0 else 0

            put_delta = opt_delta(puts[sp])
            call_delta = opt_delta(calls[sc])

            if min_short_delta is not None and (put_delta < min_short_delta or call_delta < min_short_delta):
                rejects["delta_out_of_range"] += 1
                continue

            if max_short_delta is not None and (put_delta > max_short_delta or call_delta > max_short_delta):
                rejects["delta_out_of_range"] += 1
                continue

            credit_width_ratio = credit / max(wing_width, 1)
            if min_credit_width is not None and credit_width_ratio < min_credit_width:
                rejects["credit_width_too_low"] += 1
                continue

            put_pop = pop_from_delta(put_delta)
            call_pop = pop_from_delta(call_delta)
            condor_pop = round((put_pop / 100) * (call_pop / 100) * 100, 1)
            avg_leg_spread = sum(opt_spread(o) or max_spread for o in legs) / len(legs)

            score, score_parts = recommendation_score(
                nearest_buffer=nearest_buffer,
                expected_move=expected_move,
                credit=credit,
                wing_width=wing_width,
                condor_pop=condor_pop,
                put_delta=put_delta,
                call_delta=call_delta,
                put_buffer=put_buffer,
                call_buffer=call_buffer,
                avg_leg_spread=avg_leg_spread,
                max_spread=max_spread,
                lower_be=lower_be,
                upper_be=upper_be,
                underlying=underlying,
                iv_rank=iv_rank,
            )

            candidates.append({
                "expiration": clean_expiration(exp),
                "raw_expiration": exp,
                "underlying": underlying,
                "iv": volatility,
                "iv_rank": iv_rank,
                "iv_rank_source": iv_rank_source,
                "expected_move": round(expected_move, 2),
                "long_put": lp,
                "short_put": sp,
                "short_call": sc,
                "long_call": lc,
                "credit": credit,
                "credit_width_ratio": round(credit_width_ratio, 3),
                "max_profit": max_profit,
                "max_loss": max_loss,
                "lower_be": lower_be,
                "upper_be": upper_be,
                "put_buffer": put_buffer,
                "call_buffer": call_buffer,
                "nearest_buffer": nearest_buffer,
                "credit_risk": credit_risk,
                "put_delta": put_delta,
                "call_delta": call_delta,
                "put_pop": put_pop,
                "call_pop": call_pop,
                "condor_pop": condor_pop,
                "avg_leg_spread": round(avg_leg_spread, 2),
                **score_parts,
                "grade": trade_grade(score),
                "score": score,
            })

    candidates.sort(key=lambda x: (x["score"], x["credit"], x["nearest_buffer"]), reverse=True)

    return {
        "symbol": chain.get("symbol"),
        "underlying": underlying,
        "expiration": clean_expiration(exp),
        "raw_expiration": exp,
        "iv": volatility,
        "iv_rank": iv_rank,
        "iv_rank_source": iv_rank_source,
        "expected_move": round(expected_move, 2),
        "results": candidates[:count],
        "total_found": len(candidates),
        "rejects": rejects,
        "best": candidates[0] if candidates else None,
    }


@app.get("/api/recommend")
def api_recommend(
    symbol: str = Query("SPX"),
    dte: int = Query(0),
    wing_width: int = Query(25),
    min_credit: float = Query(0.80),
    max_spread: float = Query(2.00),
    strike_count: int = Query(50),
    count: int = Query(10),
    buffer_mult: float = Query(1.0),
    min_iv_rank: Optional[float] = Query(30),
    max_iv_rank: Optional[float] = Query(65),
    min_short_delta: Optional[float] = Query(0.05),
    max_short_delta: Optional[float] = Query(0.15),
    min_credit_width: Optional[float] = Query(0.20),
):
    clean_symbol = symbol.upper().strip()

    if clean_symbol == "SPX":
        clean_symbol = "$SPX"

    chain = get_option_chain(symbol=clean_symbol, strike_count=strike_count)

    return recommend_ics(
        chain=chain,
        dte=dte,
        wing_width=wing_width,
        min_credit=min_credit,
        max_spread=max_spread,
        count=count,
        buffer_mult=buffer_mult,
        min_iv_rank=min_iv_rank,
        max_iv_rank=max_iv_rank,
        min_short_delta=min_short_delta,
        max_short_delta=max_short_delta,
        min_credit_width=min_credit_width,
    )