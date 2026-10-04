# pick.py
from thresholds import PICK_MIN_WORTH, PICK_MIN_CONF, DARK_TICKET_CUT, NO_SOCIAL_CUT


def summary(d, ans) -> str:
    """Two lines per candidate, built from answers Jev already gave.
       Never the raw dossier. A fat state costs accuracy."""
    bits = [f"{d['chain']}, {d['age_minutes']:.0f}m old, ${d['mcap_usd']:,.0f} mcap, "
            f"${d['liquidity_usd']:,.0f} liq, {d['holder_count'] or '?'} holders",
            f"crowd {ans['shape']['probabilities']['crowd']:.2f}, "
            f"concentration risk {ans['concentration_is_exit_risk']['noul']:.2f}"]

    if "authority_risk" in ans:
        bits.append(f"authority {ans['authority_risk']['choice']}")
    if "sell_side_risk" in ans:
        bits.append(f"sell side {ans['sell_side_risk']['choice']}")
    if "data_coverage" in ans:
        bits.append(f"data {ans['data_coverage']['choice']}")
    if "account_is_the_project" in ans:
        bits.append(f"official account {ans['account_is_the_project']['noul']:.2f}, "
                    f"effort {ans['effort']['score']:.1f}")
    else:
        bits.append("no usable X account")
    return "; ".join(bits)


def labels(survivors) -> list[str]:
    """Choice options must be unique. Two chains can launch the same ticker on the
       same day, so a repeated ticker gets the start of its address appended."""
    tickers = [d["ticker"] for d, _ in survivors]
    return [t if tickers.count(t) == 1 else f"{t}_{d['addr'][:6]}"
            for t, (d, _) in zip(tickers, survivors)]


def pick(judge, survivors) -> dict | None:
    """survivors: [(dossier, answers), ...]. Returns the order, or None."""
    if not survivors:
        return None

    names = labels(survivors)
    state = {"candidates": [{"ticker": n, "summary": summary(d, a)}
                            for n, (d, a) in zip(names, survivors)]}
    r = judge("pick", state)
    best, worth = r["answers"]["best"], r["answers"]["worth_trading_at_all"]

    if worth["noul"] < PICK_MIN_WORTH:
        return None                      # every candidate is mediocre. Normal outcome.
    if best["confidence"] < PICK_MIN_CONF:
        return None                      # flat over ten options means no favourite.

    d, ans = dict(zip(names, survivors)).get(best["choice"], (None, None))
    if d is None:
        return None                      # the schema guarantees the option is in the
                                         # list, so log this one and stand down.

    size_factor = 1.0
    if ans.get("data_coverage", {}).get("choice") == "dark":
        size_factor *= DARK_TICKET_CUT   # less visibility, smaller ticket
    if "account_is_the_project" not in ans:
        size_factor *= NO_SOCIAL_CUT

    return {"model": r["model"],
            "token": {"ticker": d["ticker"], "address": d["addr"],
                      "network_id": d["net"], "chain": d["chain"]},
            "size_factor": round(size_factor, 2),
            "confidence": best["confidence"],
            "runner_up": sorted(best["probabilities"].items(),
                                key=lambda kv: -kv[1])[1:2],
            "why": {k: v for k, v in ans.items()}}
