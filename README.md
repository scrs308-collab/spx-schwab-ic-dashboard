# SPX Schwab Iron Condor Dashboard

A local dashboard that pulls Schwab option-chain data and ranks SPX/SPXW iron condor candidates.

This is read-only. It does not place trades.

## What you need

1. Schwab developer app credentials:
   - `SCHWAB_CLIENT_ID`
   - `SCHWAB_CLIENT_SECRET`
   - `SCHWAB_REDIRECT_URI`

2. Python 3.11+

## Install

Open PowerShell in this folder and run:

```powershell
python -m pip install -r requirements.txt
```

## Configure

Copy `.env.example` to `.env`:

```powershell
copy .env.example .env
```

Edit `.env` and fill in your Schwab app info.

## First login

Run:

```powershell
python schwab_auth.py
```

It will print a login URL. Open it, sign in, approve access, then paste the full redirected URL back into PowerShell.

This saves your token to:

```text
tokens.json
```

## Run dashboard

```powershell
python server.py
```

Then open:

```text
http://localhost:8000
```

## Notes

- Uses Schwab option chain endpoint.
- Defaults to SPX.
- Scores iron condors using:
  - distance from current price
  - credit/risk ratio
  - expected move
  - bid/ask spread filter
- No order placement.
