"""FOMO has no public API. This reads your own logged-in session out of Chrome.

Setup, once: `python run_desk.py setup` opens Chrome for you. By hand:
    1. Start Chrome with remote debugging AND its own profile. Chrome 136+ ignores the
       debugging port on your normal profile, so --user-data-dir is required:
         mac:     /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222 --user-data-dir=$HOME/.desk-chrome
         linux:   google-chrome --remote-debugging-port=9222 --user-data-dir=$HOME/.desk-chrome
         windows: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir=%USERPROFILE%\\.desk-chrome
    2. Open fomo.family in that window, log in once, leave the tab open.
    3. python fomo_api.py <addr>:<netId>      prints the raw response for one token.
       Check it against _row() below before the first real run.

The Privy bearer lives about an hour. token() re-reads it from the open tab; Privy
refreshes it in the page on its own as long as the tab stays open.
"""
import base64, json, os, sys, time
import requests

API = "https://prod-api.fomo.family/proxy/filterTokens"
CDP = os.environ.get("CHROME_CDP", "http://127.0.0.1:9222")
BATCH = 20

# the change windows normalise() reads, keyed by seconds
CHANGE_KEYS = {300: "change5m", 3600: "change1", 14400: "change4", 43200: "change12",
               86400: "change24"}


def _jwt_exp(token: str) -> float:
    try:
        body = token.split(".")[1]
        body += "=" * (-len(body) % 4)
        return float(json.loads(base64.urlsafe_b64decode(body))["exp"])
    except Exception:
        return 0.0


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _row(raw: dict) -> dict:
    """One FOMO token row -> the keys collect.normalise() reads. If FOMO renames a
       field, this is the only place that changes."""
    return {"symbol": raw.get("symbol") or raw.get("ticker"),
            "mcap": _num(raw.get("marketCap")),
            "liq": _num(raw.get("liquidity")),
            "vol24": _num(raw.get("volume24")),
            "price": _num(raw.get("priceUSD")),
            "holders": raw.get("holders"),
            "change": {s: _num(raw.get(k)) for s, k in CHANGE_KEYS.items()},
            "created": raw.get("createdAt")}


def _rows_of(body) -> list[dict]:
    """filterTokens has been seen wrapped as a bare list, {data: [...]} and
       {results: [...]}. Take whichever arrived."""
    if isinstance(body, list):
        return body
    for k in ("data", "results", "tokens", "items"):
        v = body.get(k) if isinstance(body, dict) else None
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            inner = _rows_of(v)
            if inner:
                return inner
    return []


def _tid_of(raw: dict) -> str | None:
    tok = raw.get("token") if isinstance(raw.get("token"), dict) else raw
    addr = tok.get("address") or raw.get("address")
    net = tok.get("networkId") or raw.get("networkId")
    return f"{addr}:{net}" if addr and net is not None else None


class Fomo:
    def __init__(self, cdp: str = CDP):
        self.cdp = cdp
        self._bearer, self._exp = None, 0.0

    # --- the bearer, read out of the logged-in tab
    def _from_chrome(self) -> str:
        import websocket                                  # pip install websocket-client
        tabs = requests.get(f"{self.cdp}/json", timeout=5).json()
        tab = next((t for t in tabs if t.get("type") == "page"
                    and "fomo.family" in t.get("url", "")), None)
        if tab is None:
            raise RuntimeError("no fomo.family tab open in Chrome. Open it and log in.")
        # no Origin header: Chrome 111+ refuses debugger connections that send one
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=10,
                                         suppress_origin=True)
        try:
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                "params": {"expression": "localStorage.getItem('privy:token')",
                                           "returnByValue": True}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == 1:
                    break
        finally:
            ws.close()
        val = ((msg.get("result") or {}).get("result") or {}).get("value")
        if not val:
            raise RuntimeError("no privy:token in the fomo.family tab. Log in again.")
        return json.loads(val) if val.startswith('"') else val

    def token(self) -> str:
        """Call at the top of every cycle. Re-reads the bearer when it has under
           five minutes left, so it never expires halfway through a funnel."""
        if not self._bearer or self._exp - time.time() < 300:
            self._bearer = self._from_chrome()
            self._exp = _jwt_exp(self._bearer)
        return self._bearer

    def _post(self, chunk: list[str]):
        h = {"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json",
             "Origin": "https://fomo.family", "Referer": "https://fomo.family/"}
        r = requests.post(API, json=chunk, headers=h, timeout=30)
        if r.status_code == 401:                          # expired early, re-read once
            self._bearer = None
            h["Authorization"] = f"Bearer {self.token()}"
            r = requests.post(API, json=chunk, headers=h, timeout=30)
        r.raise_for_status()
        return r.json()

    def raw(self, ids: list[str]):
        return self._post(ids[:BATCH])

    def tokens(self, ids: list[str]) -> dict[str, dict]:
        """['<addr>:<netId>', ...] -> {tid: row}, twenty per request."""
        out = {}
        for i in range(0, len(ids), BATCH):
            chunk = ids[i:i + BATCH]
            want = {c.lower(): c for c in chunk}          # EVM rows may come back checksummed
            rows = _rows_of(self._post(chunk))
            for raw in rows:
                tid = _tid_of(raw)
                tid = want.get(tid.lower(), tid) if tid else None
                if tid is None and len(rows) == len(chunk):
                    tid = chunk[rows.index(raw)]          # same order as requested
                if tid is None:
                    continue
                row = _row(raw)
                if row["symbol"] is not None:
                    out[tid] = row
        return out


if __name__ == "__main__":
    print(json.dumps(Fomo().raw(sys.argv[1:]), indent=2)[:5000])
