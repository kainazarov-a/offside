# OFFSIDE

**An autonomous agent that catches prediction markets offside.**

OFFSIDE ingests TxLINE's real-time World Cup feed (odds + match events, every update
anchored on Solana), detects sharp information events with deterministic quant logic,
and simultaneously measures — in milliseconds — how far behind the retail crowd
(Polymarket) is at each event. It then paper-trades those dislocations with a
latency-aware execution model and full fee accounting, and reports honest PnL,
signal accuracy (Brier score), and lag distributions.

Track: **Trading Tools and Agents** · World Cup Hackathon by TxODDS × Solana × Superteam Earn.

---

## Who is this for

1. **Trading teams & B2B market operators** — a production-style monitor of line
   sharpness and cross-venue latency: which venues lag, by how much, and whether
   the lag is economically exploitable after fees and execution latency.
2. **TxODDS itself** — the tool quantifies, with live data, the economic value of
   TxLINE's speed advantage over the largest prediction market on earth.

## Why this wins the track (criteria mapping)

- **Core Functionality & Data Ingestion** — resilient async SSE/REST ingest of
  TxLINE odds + scores, unified event bus, reconnection with backoff, guest-JWT
  auto-refresh on 401, feed-health telemetry, self-logging of unknown enum values.
- **Autonomous Operation** — headless agent: detects, decides, paper-executes,
  logs and reports with zero human input. Ran unattended overnight through real
  World Cup knockout matches. One process serves everything.
- **Logic & Code Architecture** — deterministic rules (implied-probability jump
  triggers, goal events from `Stats` increments — VAR-rollback safe, penalty
  shootouts excluded by design), latency-parameterized paper fills, break-even
  math printed next to every result. No black boxes.
- **Innovation & Novelty** — first live cross-venue lag meter between a
  professional odds feed and a prediction market, plus **on-chain verification of
  the goals themselves** via TxODDS Merkle roots on Solana.
- **Production Readiness** — config-driven, replayable, systemd unit included,
  documented, honest about its own execution assumptions.

## The three verbs

1. **Measure.** TxLINE 1X2 (demargined, pure probabilities) vs Polymarket CLOB
   mid — per outcome, per match, sparkline overlays, lag histogram, dislocation
   table. Crowd prices arrive over the CLOB **WebSocket market channel**
   (millisecond precision) with a REST midpoint poller as a safety net and a
   120s silence watchdog against known silent-freeze behavior.
2. **Trade (paper).** Deterministic triggers → simulated fills with configurable
   latency and spread paid both ways → PnL, win rate, average trade, Brier
   accuracy of signals. Strictly paper: this is a measurement instrument.
3. **Prove.** Every goal is verifiable on Solana. The **Verify** tab fetches the
   Merkle proof for a chosen goal (`/scores/stat-validation`) and executes a
   real `validateStatV2` **view call on the mainnet Txoracle program** against
   the `daily_scores_roots` PDA. The verdict shown is the program's, not ours.

## Demo insurance: REPLAY mode

OFFSIDE can re-run any finished match (started 6h–2w ago) through the exact same
ingestion + detector pipeline at ×N speed, using `/scores/historical/{fid}` and
`/odds/updates/{day}/{hour}/{interval}`. Replays run in their own `R{fid}`
lane and never touch live match state. The crowd feed is live-only, so the
paper trader idles during replay — stated honestly in the UI.

## Architecture (actual repo layout)

```
offside/
  tools/
    get_token.mjs         wallet + on-chain subscribe() + API token (no Phantom)
    verify_stat.mjs       Merkle proof -> validateStatV2 view() on mainnet Txoracle
  backend/
    engine.py             event bus, jump/goal detector, lag meter,
                          latency-aware paper trader, Brier accuracy, snapshots
    txline.py             TxLINE adapter: fixtures, demargined 1X2, goals from
                          Stats, kickoff/FT phases, JWT refresh, reconnects
    polymarket.py         market autodiscovery (Gamma) + CLOB WS market channel
                          + REST midpoint poller + watchdog, supervised loops
    replay.py             historical replay driver (same pipeline, R-lane)
    verify.py             verify candidates/goals/proof orchestration
    sim.py                synthetic feed (demo/dev without any external deps)
    server.py             FastAPI (lifespan): REST + SSE stream + static UI
    probe_txline.py       endpoint recon (schemas-first development)
  frontend/               vanilla SPA, no build step; canvas sparklines
    design/tokens.css     locked design language (black / acid lime / mono)
  config/match_map.json   manual TxLINE fixture -> Polymarket token override
  deploy/offside.service  systemd unit (Restart=always)
  probe_out/              raw API responses + goals_seen.log (gitignored data)
  .secrets/               txline.json tokens (gitignored)
  wallet.json             throwaway local keypair (gitignored)
```

UI tabs: **LIVE** (match cards: dual sharp/crowd bars, sparklines, score, event
tape) · **AGENT** (signals, open positions, accuracy) · **LAG LAB** (crowd
reaction-time histogram, dislocation table) · **PNL** (equity curve, honest
assumptions line, trades) · **REPLAY** · **VERIFY** (goal → Merkle proof →
on-chain verdict + Solscan link).

## TxLINE endpoints used (tech-doc requirement)

REST/SSE: `POST /auth/guest/start` · `POST /api/token/activate` ·
`GET /api/fixtures/snapshot` · `GET /api/odds/snapshot/{fid}` ·
`GET /api/odds/stream` (SSE) · `GET /api/scores/stream` (SSE) ·
`GET /api/scores/historical/{fid}` ·
`GET /api/odds/updates/{epochDay}/{hourOfDay}/{interval}` ·
`GET /api/scores/stat-validation?fixtureId&seq&statKeys`.
On-chain (mainnet program `9ExbZjAapQww1vfcisDmrngPinHTEfpjYRWMunJgcKaA`):
`subscribe()` for the free tier, `validateStatV2().view()` against the
`["daily_scores_roots", u16le(epochDay)]` PDA.
Polymarket: Gamma events API, CLOB `/midpoint`, CLOB WS market channel.

## Quickstart

```bash
npm install
# real-time tier: fund printed address with ~0.02 SOL, then
node tools/get_token.mjs --network mainnet --level 12

pip install httpx fastapi uvicorn websockets

python backend/server.py --sim    # synthetic demo, zero external deps
python backend/server.py --live   # TxLINE mainnet + Polymarket
# open http://127.0.0.1:8000
```

## Honesty (built into the product)

Paper trading only. Execution latency and spread are modeled and displayed next
to every PnL figure. No profitability promises — only measurements. Disclaimer
in the footer. This is not betting advice and places no real bets anywhere.

## Security notes

- `wallet.json` is a throwaway local keypair used only for the free-tier
  subscription signature. Never commit it. Never reuse it for funds.
- `.secrets/` holds API tokens. Gitignored.

## Status

- [x] Token bootstrap (Phantom-free), mainnet level 12 (real-time) active
- [x] TxLINE ingest built against real probed schemas; unit tests green
- [x] Polymarket autodiscovery + WS + poller + watchdog
- [x] Engine: detector / lag meter / paper trader / Brier
- [x] FastAPI server (lifespan) + SSE + frontend SPA (6 tabs)
- [x] Replay mode (R-lane, feed-time minutes)
- [x] Verify tab: Merkle proof + on-chain `validateStatV2` view
- [ ] Public deploy link · demo video · submission
