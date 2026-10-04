"""The Grok Bot side of main.py: the five things main() needs from the desk.

Everything here is configured from the environment, and every piece degrades to
logging when it is not configured, so the shadow week runs with nothing but BANK_USD.

    BANK_USD            free cash. Replace bank() with your venue balance when you have one.
    SOCIAL_URL          endpoint that takes {"x_handle": ...} and returns SOCIAL's x_account
                        block (or null). Unset: no social read, the token carries the gap.
    SEATS_URL           where a finished order is POSTed for SIZE -> FILLS -> RISK.
    TELEGRAM_BOT_TOKEN  plus TELEGRAM_CHAT_ID: one line per cycle, trade or no trade.
    SHADOW_LOG          jsonl file for the shadow week, default shadow.jsonl.
"""
import json, logging, os, time
import requests

log = logging.getLogger("desk.seats")


class Desk:
    def __init__(self):
        self.secret = os.environ.get("DESK_SECRET", "")
        self.social_url = os.environ.get("SOCIAL_URL")
        self.seats_url = os.environ.get("SEATS_URL")
        self.tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.tg_chat = os.environ.get("TELEGRAM_CHAT_ID")
        self.shadow_path = os.environ.get("SHADOW_LOG", "shadow.jsonl")

    def _auth(self):
        return {"Authorization": f"Bearer {self.secret}"}

    def bank(self) -> float:
        return float(os.environ.get("BANK_USD", "0"))

    def read_x(self, handle: str):
        """The exact on-chain handle, never a search. Missing is missing."""
        if not self.social_url:
            return None
        try:
            r = requests.post(self.social_url, json={"x_handle": handle},
                              headers=self._auth(), timeout=60)
            r.raise_for_status()
            return r.json().get("x_account")
        except Exception as e:
            log.warning("SOCIAL read failed for %s: %s", handle, e)
            return None

    def log_shadow(self, order, stats):
        row = {"at": time.time(), "order": order, "stats": stats}
        with open(self.shadow_path, "a") as f:
            f.write(json.dumps(row, default=str) + "\n")
        log.info("shadow order %s %s", order["token"]["ticker"], order["model"])

    def report(self, order, stats):
        if order:
            t = order["token"]
            line = (f"ORDER {t['ticker']} ({t['chain']}) size_factor {order['size_factor']} "
                    f"conf {order['confidence']} model {order['model']}")
        elif stats.get("held"):
            line = f"holding {stats['held']} for {stats['minutes']} min, no scan"
        elif stats.get("stand_down"):
            line = f"STAND DOWN: {stats['stand_down']}"
        else:
            line = (f"no trade. seen {stats.get('seen', 0)}, benched {stats.get('benched', 0)}, "
                    f"free {stats.get('free')}, trade {stats.get('trade')}, "
                    f"chain {stats.get('chain')}, soft {stats.get('soft')}")
        log.info(line)
        if self.tg_token and self.tg_chat:
            try:
                requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                              json={"chat_id": self.tg_chat, "text": line}, timeout=15)
            except Exception as e:
                log.warning("telegram failed: %s", e)

    def send_to_seats(self, order):
        """SIZE, then FILLS, then RISK. The seats work it top to bottom."""
        if not self.seats_url:
            log.error("live order with no SEATS_URL set: %s", json.dumps(order, default=str))
            return
        r = requests.post(self.seats_url, json=order, headers=self._auth(), timeout=30)
        r.raise_for_status()
