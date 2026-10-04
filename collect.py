import time, logging, requests
from fomo_api import Fomo                      # Privy bearer out of Chrome over CDP

GT  = "https://api.geckoterminal.com/api/v2"
DEX = "https://api.dexscreener.com/latest/dex/tokens"
SOL_RPC = "https://api.mainnet-beta.solana.com"

# the three chains the desk trades, plus Base which shares the BSC question set
GT_NET   = {1399811149: "solana", 4663: "robinhood", 56: "bsc", 8453: "base"}
FOMO_NET = {v: k for k, v in GT_NET.items()}
log = logging.getLogger("desk.collect")


def gt_get(path: str, **params) -> dict:
    """Every GeckoTerminal call goes through here. 10/min on the free tier: a 429
       means back off a full minute, once, never retry in place."""
    r = requests.get(f"{GT}{path}", params=params, timeout=20)
    if r.status_code == 429:
        log.warning("geckoterminal 429, backing off 60s")
        time.sleep(60)
        r = requests.get(f"{GT}{path}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def age_minutes(created) -> float:
    """createdAt comes back as epoch seconds or milliseconds depending on the row."""
    if not created:
        return 0.0
    c = float(created)
    if c > 1e11:                                # milliseconds
        c /= 1000
    return max(0.0, (time.time() - c) / 60)


def universe(nets=("solana", "bsc", "robinhood"), pages=2) -> list[str]:
    """Where the whole thing starts. Fresh pools per chain -> ['<addr>:<netId>', ...].
       Costs one GeckoTerminal slot per chain per page, so keep pages small."""
    ids, seen = [], set()
    for net in nets:
        for page in range(1, pages + 1):
            try:
                r = gt_get(f"/networks/{net}/new_pools", page=page)
            except Exception as e:
                log.warning("new_pools %s p%s failed: %s", net, page, e)
                break
            for pool in r.get("data", []):
                base = ((pool.get("relationships") or {}).get("base_token") or {})
                gid  = (base.get("data") or {}).get("id")      # 'solana_<addr>'
                if not gid:
                    continue
                addr = gid.split("_", 1)[1]
                tid  = f"{addr}:{FOMO_NET[net]}"
                if tid not in seen:
                    seen.add(tid)
                    ids.append(tid)
    return ids


def normalise(tid: str, m: dict) -> dict:
    """FOMO's field names become the desk's field names, once, here.
       Every file downstream reads these names and only these."""
    addr, net = tid.split(":")
    change = m.get("change") or {}
    return {"addr": addr, "net": int(net), "tid": tid, "ticker": m["symbol"],
            "mcap_usd": m["mcap"], "liquidity_usd": m["liq"],
            "volume_h24": m["vol24"], "price_usd": m["price"],
            "holder_count": m["holders"] or None,
            "change": {"5m": change.get(300), "1h": change.get(3600),
                       "4h": change.get(14400), "24h": change.get(86400)},
            "age_minutes": round(age_minutes(m["created"]), 1)}


def shortlist(fomo: Fomo, ids: list[str]) -> list[dict]:
    """Pass one over everything FOMO knows. No network beyond FOMO itself:
       one call per twenty tokens, and not a single request per token."""
    out = []
    for tid, m in fomo.tokens(ids).items():             # 20 per call
        t = normalise(tid, m)
        if t["net"] in GT_NET:
            out.append(t)
    # turnover ranks the queue. It orders work, it does not decide anything
    out.sort(key=lambda t: (t["volume_h24"] or 0) / max(t["mcap_usd"] or 0, 1),
             reverse=True)
    return out


NO_TRADES = {"buys_h1": None, "sells_h1": None, "buys_h6": None, "sells_h6": None,
             "trades_h24": None}


def trade_counts(t: dict) -> dict:
    """buys and sells per window. FOMO does not return them, DexScreener does.
       Called ONLY for tokens that already cleared the free checks. One per token,
       so this runs on tens, never on the whole universe."""
    try:
        pairs = requests.get(f"{DEX}/{t['addr']}", timeout=20).json().get("pairs") or []
    except Exception:
        return dict(NO_TRADES)
    if not pairs:
        return dict(NO_TRADES)
    x = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0).get("txns") or {}
    w = lambda k: x.get(k) or {}
    h24 = w("h24")
    return {"buys_h1": w("h1").get("buys"), "sells_h1": w("h1").get("sells"),
            "buys_h6": w("h6").get("buys"), "sells_h6": w("h6").get("sells"),
            "trades_h24": (h24["buys"] + h24["sells"]) if "buys" in h24 and "sells" in h24
                          else None}


def authority(v):
    """GT reports Solana authorities as an address, or as 'no' / null when renounced.
       Anything that is not clearly renounced is treated as set."""
    if v is None or v is False:
        return None
    if isinstance(v, str) and v.strip().lower() in ("", "no", "none", "null", "false"):
        return None
    return v


def dossier(t: dict) -> dict:
    """One GT call per token. Fills what the chain actually has, null where it does not."""
    net = GT_NET[t["net"]]
    a = gt_get(f"/networks/{net}/tokens/{t['addr']}/info")["data"]["attributes"]

    d = {**t, "chain": net,
         # GT first, FOMO as the fallback. On Robinhood GT is null and FOMO is all you get.
         "holder_count": (a.get("holders") or {}).get("count") or t["holder_count"],
         "top_10_percent": ((a.get("holders") or {}).get("distribution_percentage")
                            or {}).get("top_10"),
         "developer_holding_percentage": a.get("developer_holding_percentage"),
         "gt_score_details": a.get("gt_score_details"),
         "is_honeypot": a.get("is_honeypot"),
         "mint_authority": authority(a.get("mint_authority")),
         "freeze_authority": authority(a.get("freeze_authority")),
         "description": a.get("description"),
         "x_handle": clean_handle(a.get("twitter_handle"))}

    # Solana only: exact top wallet share, free, off the public RPC
    if t["net"] == 1399811149:
        try:
            d["top_wallet_percent"] = sol_top_wallet(t["addr"])
        except Exception as e:
            log.warning("solana rpc failed %s: %s", t["addr"], e)
            d["top_wallet_percent"] = None          # missing stays missing

    return d


def clean_handle(h):
    """GT returned 'LuffyX100X/status/2102659581109272876' on a Robinhood token.
       Take the first path segment, or treat the account as missing."""
    if not h:
        return None
    h = h.strip()
    for prefix in ("https://", "http://", "www.", "x.com/", "twitter.com/"):
        if h.lower().startswith(prefix):
            h = h[len(prefix):]
    h = h.lstrip("@").split("?")[0].split("/")[0]
    return h if h and h.replace("_", "").isalnum() and len(h) <= 15 else None


# --- Solana: who actually owns the biggest balances

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_P = 2**255 - 19
_D = -121665 * pow(121666, _P - 2, _P) % _P


def b58decode(s: str) -> bytes:
    n = 0
    for c in s:
        n = n * 58 + _B58.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\0" * (len(s) - len(s.lstrip("1"))) + raw


def on_curve(address: str) -> bool:
    """True for a key someone holds (a wallet), False for a program-derived address.
       Pool vaults, bonding curves and lockers are owned by PDAs, which are off the
       ed25519 curve by construction. Same test as Solana's Pubkey::is_on_curve."""
    b = b58decode(address)
    if len(b) != 32:
        return False
    y = int.from_bytes(b, "little") & ((1 << 255) - 1)
    if y >= _P:
        return False
    u, v = (y * y - 1) % _P, (_D * y * y + 1) % _P
    x2 = u * pow(v, _P - 2, _P) % _P                  # x^2 = (y^2 - 1) / (d y^2 + 1)
    if x2 == 0:
        return not b[31] >> 7                         # x = 0 cannot carry a sign bit
    return pow(x2, (_P - 1) // 2, _P) == 1            # a square mod p means a point


def sol_top_wallet(mint: str):
    """Largest share of supply held by one wallet, leaving out accounts a program
       controls. The biggest token account on a fresh launch is almost always the
       pool or the bonding curve, and counting it would kill every token."""
    q = lambda m, p: requests.post(SOL_RPC, json={"jsonrpc": "2.0", "id": 1,
                                                  "method": m, "params": p},
                                   timeout=20).json()["result"]
    supply = float(q("getTokenSupply", [mint])["value"]["amount"])
    top = q("getTokenLargestAccounts", [mint])["value"]
    if not supply or not top:
        return None
    infos = q("getMultipleAccounts", [[a["address"] for a in top],
                                      {"encoding": "jsonParsed"}])["value"]
    held = {}
    for acct, info in zip(top, infos):
        try:
            owner = info["data"]["parsed"]["info"]["owner"]
        except (TypeError, KeyError):
            continue                                  # closed or unparsable, skip it
        if on_curve(owner):                           # wallets only, summed per owner
            held[owner] = held.get(owner, 0.0) + float(acct["amount"])
    return max(held.values()) / supply if held else None


# --- states. One named object per question set, only the fields its questions read.

def market_state(d: dict) -> dict:
    keys = ("ticker", "age_minutes", "holder_count", "change", "price_usd",
            "buys_h1", "sells_h1", "buys_h6", "sells_h6", "trades_h24",
            "liquidity_usd", "mcap_usd", "volume_h24", "intended_ticket_usd")
    return {k: d.get(k) for k in keys}


CHAIN_FIELDS = {
    "solana": ("mint_authority", "freeze_authority", "top_10_percent", "top_wallet_percent",
               "holder_count", "developer_holding_percentage"),
    "bsc": ("is_honeypot", "gt_score_details", "buys_h1", "sells_h1", "buys_h6", "sells_h6",
            "top_10_percent", "holder_count", "liquidity_usd"),
    "robinhood": ("holder_count", "top_10_percent", "is_honeypot", "buys_h1", "sells_h1",
                  "buys_h6", "sells_h6", "trades_h24", "developer_holding_percentage",
                  "price_usd", "volume_h24", "age_minutes"),
}


def chain_state(question_set: str, d: dict) -> dict:
    return {"ticker": d["ticker"], **{k: d.get(k) for k in CHAIN_FIELDS[question_set]}}


def social_state(d: dict) -> dict:
    """What SOCIAL hands the judge. The X block is filled by the bot's X plugin."""
    return {"x_account": d["x_account"],                 # collected by SOCIAL, not here
            "token": {"ticker": d["ticker"], "narrative": d.get("description"),
                      "x_handle_on_chain": d.get("x_handle")}}
