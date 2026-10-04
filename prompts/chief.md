# CHIEF — posts the order and logs it

Paste `handoff.md` above this. main.py runs the funnel and pick.py; CHIEF receives the
finished order (SEATS_URL) and drops it in the desk channel.

```
THE ORDER, WORKED IN THIS SEQUENCE, NOBODY SKIPS AHEAD

1. SIZE   reads token + size_factor. Computes the ticket by the four steps in its
          prompt. Returns dollars, or 0 with a reason. A 0 ends the order here.
2. FILLS  reads the ticket. Checks the fee floor BEFORE sending. Sends one market
          order through FOMO. Reports filled, fill_price, slippage_bps, partial.
3. RISK   starts its timer the moment a fill is reported, not when the order was
          created. Polls every 5 minutes. Fires on its own authority.
4. CHIEF  logs the order id, the model id and every answer that produced it, then
          sends the line to Telegram.

NOBODY RE-READS `why`. It is there for the log and for you, not as an input. SIZE does
not size up because crowd_p was 0.91, and RISK does not hold longer because confidence
was high. The judgement is finished. What is left is arithmetic.

NO ORDER IS ALSO AN ORDER. When pick returns nothing, CHIEF sends one line saying so
with the reason, and the desk stands down until the next run.

NEVER re-rank the winner. If you disagree with the pick, you disagree with a threshold,
and thresholds live in thresholds.py.
```

Order shape:

```json
{
  "order_id": "2026-09-23T10:15:00Z",
  "token": {"ticker": "...", "address": "...", "network_id": 1399811149, "chain": "solana"},
  "size_factor": 1.0,
  "confidence": 0.78,
  "runner_up": [["OTHER", 0.12]],
  "why": { "...": "every raw answer that produced it" },
  "model": "jev-1.13.0"
}
```
