# The desk

Jev takes the judgements. Code takes the arithmetic. Grok Bot seats size, fill and exit.

```
0. UNIVERSE  GeckoTerminal new_pools, 3 chains          -> fresh launches      collect.universe
1. LIST      FOMO filterTokens, 20 per call             -> hundreds            collect.shortlist
2. FREE CUT  age, liquidity, volume, mcap. No network   -> tens                filter.free_kill
3. TRADE CUT DexScreener buys and sells, one per token  -> a handful           filter.trade_kill
4. DOSSIER   GeckoTerminal info + chain RPC + X         -> three per cycle     collect.dossier, filter.chain_kill
5. JUDGE     market + chain + social per token          -> scored shortlist    judge.py, filter.soft_kill
6. PICK      one choice over the shortlist              -> one token, or none  pick.py
```

| file | what it is |
|---|---|
| `judge.py` | the only holder of `TYPESAFE_API_KEY`. `POST /judge` with a desk secret. Raw answers out. |
| `desk_api.py` | `judge.py` plus `/book/held` and `/book/release` for RISK. This is what you run. |
| `judge_client.py` | how every seat (and `main.py`) reaches the judge. |
| `questions.py` | every question the desk can ask. The one file you reread. |
| `thresholds.py` | every number. Retune here and nowhere else. |
| `filter.py` | the order the numbers fire in. |
| `collect.py` | GeckoTerminal, FOMO, DexScreener, Solana RPC. Builds the states. |
| `fomo_api.py` | your logged-in FOMO session, read out of Chrome over CDP. |
| `pick.py` | one `choice` + one `noul` over the survivors. |
| `book.py` | one position at a time, a bench whose length depends on what fired, and holder/price snapshots that give the shape question real growth. |
| `main.py` | the shift. Every 15 minutes. Shadow by default. |
| `run_desk.py` | setup, start, check and seats in one command, on Mac, Windows and Linux. |
| `desk.py` | the Grok Bot side `main.py` needs: bank, SOCIAL read, shadow log, Telegram, seats. |
| `prompts/` | HANDOFF, SOCIAL, CHIEF, SIZE, FILLS, RISK. Paste into the seats. |

## Quick start

```bash
git clone https://github.com/wileskabooking1-bot/wileskazambrano.git
cd wileskazambrano
python run_desk.py setup     # key, desk secret, bank, Telegram, FOMO login. Once.
python run_desk.py start     # judge + tunnel + Chrome + the shift, in shadow mode
python run_desk.py seats     # what to paste into each Grok Bot seat
python run_desk.py check     # is everything up and answering
```

`setup` asks only for what needs you: the Jev key, the Telegram bot token, and one FOMO
login in a Chrome window it opens with its own desk profile (Chrome 136+ will not expose
your normal profile to the desk). Install `cloudflared` once so the bots can reach the
judge. `start --live` sends real orders.

The rest of this section is the same setup by hand.

## Setup, in order

**1. Jev key.** console.typesafe.ai → Keys → create, copy it immediately.

```bash
export TYPESAFE_API_KEY="ts-..."            # windows: setx TYPESAFE_API_KEY "ts-..." then a new terminal
curl -X POST https://api.typesafe.ai/v1/systemone \
  -H "Authorization: Bearer $TYPESAFE_API_KEY" -H "Content-Type: application/json" \
  -d '{"state":"payouts have been failing for 3 days","model":"jev-latest",
       "questions":{"urgent":{"type":"noul","instructions":"This conveys urgency"}}}'
```

A 401 here is the key. Anything after this that fails is the question.

**2. Install** (Python 3.10+):

```bash
pip install -r requirements.txt
```

**3. Run the judge** on the one machine that holds the key:

```bash
export DESK_SECRET="$(openssl rand -hex 24)"
uvicorn desk_api:app --host 0.0.0.0 --port 8080
cloudflared tunnel --url http://localhost:8080     # bots run in xAI's cloud
```

Bots get `JUDGE_URL=https://<tunnel>/judge` and `DESK_SECRET`. Never the TypeSafe key.
Prove the link from a bot's own terminal with the curl in `prompts/handoff.md`.

**4. FOMO session.** Start Chrome with `--remote-debugging-port=9222 --user-data-dir=~/.desk-chrome`
(Chrome 136+ ignores the port on your normal profile), log into fomo.family, leave the tab open. Then check the field mapping once against a real token:

```bash
python fomo_api.py <addr>:<netId>
```

If the keys in that output are not `marketCap / liquidity / volume24 / holders / priceUSD /
change5m.. / createdAt`, fix `_row()` in `fomo_api.py`. It is the only place that maps them.

**5. Seats.** Paste `prompts/handoff.md` above SCAN, VET, SOCIAL and CHIEF. Paste
`social.md`, `chief.md`, `size.md`, `fills.md`, `risk.md` into their seats.

**6. Shadow week.**

```bash
cp .env.example .env    # fill it, then load it into your shell
python main.py --once   # one cycle, read the log
python main.py          # shadow, every 15 min, writes shadow.jsonl, never sends
```

Read only the rows where the desk and your old logic disagree. Under ten rejections a day
means the filter is misconfigured. After a week: `python main.py --live`.

## Tests

```bash
python -m pytest -q tests
```

Offline: the judge endpoint with a fake Jev, the filter, pick, the book and a full
`run_once` with faked network.

## The bill

```
cost_per_call = (state_tokens + question_tokens) / 1_000_000 * 0.042
10 calls x 1,400 tokens = 14,000 tokens = $0.00059 per cycle, ~5.7 cents a day
```
