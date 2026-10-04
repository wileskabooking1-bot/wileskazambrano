# main.py
import argparse, time, logging
import requests
from collect import (universe, shortlist, trade_counts, dossier, market_state,
                     chain_state, social_state)
from filter import free_kill, trade_kill, chain_kill, soft_kill
from pick import pick
from thresholds import NO_SOCIAL_CUT, DARK_TICKET_CUT
import book

CHAIN_SET     = {1399811149: "solana", 56: "bsc", 8453: "bsc", 4663: "robinhood"}
CYCLE_SECONDS = 900
GT_PER_MINUTE = 10          # free tier
GT_UNIVERSE   = 6           # 3 chains x 2 pages, spent before the funnel starts
GT_DOSSIER    = 3           # what is left for dossiers in the same minute
DEX_BUDGET    = 25          # DexScreener calls per cycle, pass two only
log = logging.getLogger("desk")


class StandDown(Exception):
    """The judge is unreachable or a question set is malformed. Every token would hit
       the same wall, so the cycle ends here instead of guessing."""


def _reject(stats, stage, t, k):
    book.sit(t["tid"], k)
    stats[stage][k] = stats[stage].get(k, 0) + 1
    log.info("reject %s %s at %s: %s", t["ticker"], t["tid"], stage, k)


def run_once(fomo, judge, desk, bank, shadow=True):
    if (h := book.held()):                       # RISK owns the desk right now
        log.info("holding %s for %.0f min, no scan this cycle",
                 h["ticker"], h["minutes"])
        return None, {"held": h["ticker"], "minutes": round(h["minutes"])}

    stats = {"seen": 0, "benched": 0, "free": {}, "trade": {},
             "chain": {}, "soft": {}}
    survivors = []
    gt_slots, dex_slots = GT_DOSSIER, DEX_BUDGET

    ids = universe()                             # fresh pools, 3 chains, GT_UNIVERSE slots
    for t in shortlist(fomo, ids):               # pass one: free, no per-token requests
        stats["seen"] += 1
        if book.benched(t["tid"]):               # already judged, still serving its time
            stats["benched"] += 1
            continue
        if (k := free_kill(t)):
            _reject(stats, "free", t, k)
            continue

        if dex_slots <= 0 or gt_slots <= 0:
            break                                # out of budget, not out of ideas

        t |= trade_counts(t)                     # pass two: one DexScreener call
        dex_slots -= 1
        if (k := trade_kill(t)):
            _reject(stats, "trade", t, k)
            continue

        try:
            d = dossier(t)                       # pass three: one GeckoTerminal slot
            gt_slots -= 1
        except Exception as e:
            log.warning("dossier failed %s: %s", t["ticker"], e)
            gt_slots -= 1                        # a failed call still cost you the slot
            book.sit(t["tid"], "dossier_failed")
            continue                             # missing is missing, not a pass

        if (k := chain_kill(d)):
            _reject(stats, "chain", d, k)        # facts bench longest
            continue

        d["intended_ticket_usd"] = round(bank * 0.06, 2)   # the most SIZE could ever allow
        d["x_account"] = desk.read_x(d["x_handle"]) if d["x_handle"] else None

        chain_set = CHAIN_SET[d["net"]]
        ans = {}
        try:
            ans |= judge("market", market_state(d))["answers"]          # pass four
            ans |= judge(chain_set, chain_state(chain_set, d))["answers"]
            if d["x_account"]:
                ans |= judge("social", social_state(d))["answers"]
        except RuntimeError as e:                # 422: the question is wrong, not the token
            raise StandDown(f"malformed question: {e}")
        except (requests.ConnectionError, requests.Timeout) as e:
            raise StandDown(f"judge unreachable: {e}")
        except Exception as e:
            log.warning("judge failed %s: %s", d["ticker"], e)
            continue                             # no bench: the token is not at fault

        if (k := soft_kill(ans)):
            _reject(stats, "soft", d, k)
            continue

        survivors.append((d, ans))

    log.info("cycle: %(seen)s seen, %(benched)s benched, free %(free)s, "
             "trade %(trade)s, chain %(chain)s, soft %(soft)s", stats)

    if not survivors:
        return None, stats
    if len(survivors) == 1:                      # a choice over one option proves nothing
        d, ans = survivors[0]
        order = {"model": "single-survivor", "size_factor": 1.0, "confidence": None,
                 "token": {"ticker": d["ticker"], "address": d["addr"],
                           "network_id": d["net"], "chain": d["chain"]},
                 "why": ans}
        if "account_is_the_project" not in ans:
            order["size_factor"] *= NO_SOCIAL_CUT
        if ans.get("data_coverage", {}).get("choice") == "dark":
            order["size_factor"] *= DARK_TICKET_CUT
        order["size_factor"] = round(order["size_factor"], 2)
    else:
        try:
            order = pick(judge, survivors)       # pass five
        except RuntimeError as e:
            raise StandDown(f"malformed pick: {e}")
        except (requests.ConnectionError, requests.Timeout) as e:
            raise StandDown(f"judge unreachable at pick: {e}")

    if order is None:
        return None, stats
    order["order_id"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if shadow:
        desk.log_shadow(order, stats)            # written, never sent
        return None, stats

    book.take(order)          # the desk is now held. No scan until RISK calls release().
    return order, stats


def main(fomo, judge, desk, shadow=True, once=False):
    """desk is your Grok Bot side. It has to provide five things:
         bank()                 -> float, free cash right now
         read_x(handle)         -> the X block SOCIAL collects with its plugin, or None
         log_shadow(order, st)  -> append a row for the shadow week
         report(order, stats)   -> one line to Telegram, trade or no trade
         send_to_seats(order)   -> hand it to SIZE, then FILLS, then RISK, in that order
    """
    while True:
        try:
            fomo.token()                         # Privy bearer lives ~60 min, refresh it
            order, stats = run_once(fomo, judge, desk, desk.bank(), shadow)
            desk.report(order, stats)            # every cycle, trade or not
            if order:
                desk.send_to_seats(order)
        except StandDown as e:
            log.error("standing down this cycle: %s", e)
            desk.report(None, {"stand_down": str(e)})
        except Exception as e:
            log.exception("cycle blew up: %s", e)
        if once:
            return
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="run the desk")
    ap.add_argument("--live", action="store_true",
                    help="send orders and take the book. Default is shadow mode.")
    ap.add_argument("--once", action="store_true", help="one cycle, then exit")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from fomo_api import Fomo
    from judge_client import judge
    from desk import Desk
    main(Fomo(), judge, Desk(), shadow=not a.live, once=a.once)
