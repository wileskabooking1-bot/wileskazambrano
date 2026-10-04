# book.py
import os, sqlite3, time

DB = sqlite3.connect(os.environ.get("DESK_DB", "desk.db"), check_same_thread=False)
DB.executescript("""
CREATE TABLE IF NOT EXISTS position(
  id INTEGER PRIMARY KEY CHECK (id = 1),
  ticker TEXT, addr TEXT, net INT, opened_at REAL);
CREATE TABLE IF NOT EXISTS bench(
  tid TEXT PRIMARY KEY, reason TEXT, until REAL);
CREATE TABLE IF NOT EXISTS watch(
  tid TEXT PRIMARY KEY, first_seen REAL);
CREATE TABLE IF NOT EXISTS seen(
  tid TEXT, at REAL, holders REAL, price REAL);
CREATE INDEX IF NOT EXISTS seen_tid ON seen(tid, at);
""")

# how long a rejection stands, by what fired it
BENCH_MINUTES = {
    # facts that will not change while this token exists
    "honeypot": 100_000, "authority_open": 100_000,
    "top_wallet": 100_000, "sell_side": 100_000,
    # slow to change
    "recycled_account": 360, "account_is_the_project": 360,
    # can change as the float moves
    "top_10": 90, "holders": 90, "dev_still_loaded": 90,
    "concentration_is_exit_risk": 90,
    # can change inside the hour, keep it short or you miss the token maturing
    "shape": 25, "shape_weak": 25, "momentum_already_spent": 25,
    "liquidity_fits_ticket": 25, "liquidity": 25, "volume": 25,
    "trades": 25, "mcap": 25, "age": 20,
}
DEFAULT_BENCH = 45


def held():
    r = DB.execute("SELECT ticker, opened_at FROM position WHERE id=1").fetchone()
    return {"ticker": r[0], "minutes": (time.time() - r[1]) / 60} if r else None


def take(order):
    t = order["token"]
    DB.execute("INSERT OR REPLACE INTO position VALUES (1,?,?,?,?)",
               (t["ticker"], t["address"], t["network_id"], time.time()))
    DB.commit()


def release():
    """RISK calls this the moment a close is filled. Nothing else calls it."""
    DB.execute("DELETE FROM position")
    DB.commit()


def benched(tid: str) -> bool:
    r = DB.execute("SELECT until FROM bench WHERE tid=?", (tid,)).fetchone()
    return bool(r and r[0] > time.time())


def sit(tid: str, reason: str):
    mins = BENCH_MINUTES.get(reason, DEFAULT_BENCH)
    DB.execute("INSERT OR REPLACE INTO bench VALUES (?,?,?)",
               (tid, reason, time.time() + mins * 60))
    DB.commit()


# --- history. The FOMO batch is one snapshot. Growth needs two, so every cycle
#     records holders and price for every listed token, and the shape question gets
#     the difference as a field. Arithmetic stays in code.

WATCH_HOURS = 6         # keep re-listing a token this long after first sight
WATCH_MAX   = 200       # 10 FOMO calls at 20 per call
KEEP_HOURS  = 2         # snapshots older than this are never read
WINDOWS     = {"15m": 15, "1h": 60}


def watchlist() -> list[str]:
    """Tokens first seen recently, minus those benched on a permanent fact.
       new_pools only shows the latest launches, so without this a token is
       listed once and never again, and there is never a second snapshot."""
    now = time.time()
    rows = DB.execute("""
        SELECT w.tid FROM watch w LEFT JOIN bench b ON b.tid = w.tid
        WHERE w.first_seen > ? AND (b.until IS NULL OR b.until < ?)
        ORDER BY w.first_seen DESC LIMIT ?""",
        (now - WATCH_HOURS * 3600, now + WATCH_HOURS * 3600, WATCH_MAX)).fetchall()
    return [r[0] for r in rows]


def history(t: dict) -> dict:
    """Holder and price change over matched windows, from earlier snapshots.
       A window is null until a snapshot between 2/3 and 2x its length exists."""
    now = time.time()
    out = {}
    for name, mins in WINDOWS.items():
        r = DB.execute("""
            SELECT at, holders, price FROM seen
            WHERE tid = ? AND at BETWEEN ? AND ?
            ORDER BY ABS(at - ?) LIMIT 1""",
            (t["tid"], now - mins * 120, now - mins * 40, now - mins * 60)).fetchone()
        if r is None:
            out[name] = None
            continue
        at, h0, p0 = r
        h1, p1 = t.get("holder_count"), t.get("price_usd")
        out[name] = {"minutes": round((now - at) / 60, 1),
                     "holders_then": h0, "holders_now": h1,
                     "holders_pct": round((h1 - h0) / h0, 4) if h0 and h1 is not None else None,
                     "price_pct": round((p1 - p0) / p0, 4) if p0 and p1 is not None else None}
    return out


def record(tokens: list[dict]):
    """Call once per cycle, after history() has read the older snapshots."""
    now = time.time()
    DB.executemany("INSERT OR IGNORE INTO watch VALUES (?,?)",
                   [(t["tid"], now) for t in tokens])
    DB.executemany("INSERT INTO seen VALUES (?,?,?,?)",
                   [(t["tid"], now, t.get("holder_count"), t.get("price_usd"))
                    for t in tokens])
    DB.execute("DELETE FROM seen WHERE at < ?", (now - KEEP_HOURS * 3600,))
    DB.execute("DELETE FROM watch WHERE first_seen < ?", (now - WATCH_HOURS * 3600,))
    DB.commit()
