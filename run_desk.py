"""Set up and run the whole desk from one place. Works on Mac, Windows and Linux.

    python run_desk.py setup          once: key, secret, bank, Telegram, FOMO login
    python run_desk.py start          judge + tunnel + Chrome + the shift, shadow mode
    python run_desk.py start --live   the same, sending real orders
    python run_desk.py check          is everything up and answering
    python run_desk.py seats          what to paste into the Grok Bot seats

Only four things need you: creating the Jev key, logging into FOMO once, creating the
Telegram bot, and pasting the prompts into Grok Bot. This file does the rest.
"""
import getpass, json, os, platform, re, secrets, shutil, signal, subprocess, sys, time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"
PORT = 8080
CDP_PORT = 9222
# Chrome 136+ ignores the debugging port on your normal profile, so the desk gets
# its own. Log into FOMO in it once; it stays logged in.
PROFILE = Path.home() / ".desk-chrome"
FOMO = "https://fomo.family"


# --- .env, without another dependency

def read_env() -> dict:
    out = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return out


def write_env(updates: dict):
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    done = set()
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and k in updates:
            lines[i] = f"{k}={updates[k]}"
            done.add(k)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in done]
    ENV.write_text("\n".join(lines) + "\n")
    if os.name != "nt":
        ENV.chmod(0o600)                       # it holds the key


def load_env():
    for k, v in read_env().items():
        if v:
            os.environ.setdefault(k, v)


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    shown = f" [{default}]" if default and not secret else ""
    v = (getpass.getpass if secret else input)(f"{prompt}{shown}: ").strip()
    return v or default


def say(msg: str):
    print(f"\n== {msg}")


# --- Chrome

def chrome_path() -> str | None:
    if os.environ.get("CHROME_PATH"):
        return os.environ["CHROME_PATH"]
    system = platform.system()
    if system == "Darwin":
        cands = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    elif system == "Windows":
        cands = [os.path.join(os.environ.get(v, ""), r"Google\Chrome\Application\chrome.exe")
                 for v in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData")]
    else:
        cands = [shutil.which(n) or "" for n in
                 ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")]
    return next((c for c in cands if c and os.path.exists(c)), None)


def cdp_up() -> bool:
    import requests
    try:
        requests.get(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2)
        return True
    except Exception:
        return False


def open_chrome(url: str = FOMO):
    if cdp_up():
        return None
    path = chrome_path()
    if not path:
        sys.exit("Chrome not found. Install Google Chrome, or set CHROME_PATH in .env.")
    PROFILE.mkdir(exist_ok=True)
    proc = subprocess.Popen([path, f"--remote-debugging-port={CDP_PORT}",
                             f"--user-data-dir={PROFILE}", "--no-first-run", url],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        if cdp_up():
            return proc
        time.sleep(1)
    sys.exit("Chrome started but its debugging port never answered. Close every Chrome "
             "window using the desk profile and try again.")


# --- checks

def check_key(key: str) -> bool:
    from typesafe_sdk import Noul, TypeSafeAuthenticationError, TypeSafeClient
    try:
        r = TypeSafeClient(api_key=key).system_one(
            state="payouts have been failing for 3 days",
            questions={"urgent": Noul(instructions="This conveys urgency")})
        print(f"   key works. model {r.model}, urgent {r.answers['urgent'].noul:.2f}")
        return True
    except TypeSafeAuthenticationError:
        print("   401: that key is wrong or revoked. Make a new one.")
    except Exception as e:
        print(f"   could not reach TypeSafe: {e}")
    return False


def check_fomo() -> bool:
    from fomo_api import Fomo
    try:
        tok = Fomo(f"http://127.0.0.1:{CDP_PORT}").token()
        print(f"   FOMO login found ({len(tok)} char bearer).")
        return True
    except Exception as e:
        print(f"   {e}")
        return False


def telegram_chat(token: str) -> str | None:
    import requests
    for _ in range(60):
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10).json()
        if not r.get("ok"):
            print(f"   Telegram refused the token: {r.get('description')}")
            return None
        for u in reversed(r.get("result", [])):
            chat = (u.get("message") or u.get("channel_post") or {}).get("chat")
            if chat:
                return str(chat["id"])
        time.sleep(2)
    return None


# --- commands

def setup():
    if sys.version_info < (3, 10):
        sys.exit("Python 3.10 or newer is required.")
    say("Installing requirements")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r",
                    str(ROOT / "requirements.txt")], check=True)
    env = read_env()
    up = {}

    say("1/5 Jev key")
    key = env.get("TYPESAFE_API_KEY", "")
    if key.startswith("ts-") and check_key(key):
        pass
    else:
        print("   Opening console.typesafe.ai. Sign in, open Keys, create one and copy it\n"
              "   the moment it appears. You will not see it again.")
        webbrowser.open("https://console.typesafe.ai")
        while True:
            key = ask("   paste the key (input hidden)", secret=True)
            if key and check_key(key):
                up["TYPESAFE_API_KEY"] = key
                break

    say("2/5 Desk secret (what the bots get instead of the key)")
    if not env.get("DESK_SECRET"):
        up["DESK_SECRET"] = secrets.token_hex(24)
        print("   generated.")
    else:
        print("   already set.")
    up["JUDGE_URL"] = f"http://127.0.0.1:{PORT}/judge"      # main.py runs on this machine
    up["CHROME_CDP"] = f"http://127.0.0.1:{CDP_PORT}"

    say("3/5 Bank")
    up["BANK_USD"] = ask("   free cash in USD the desk may size from", env.get("BANK_USD", "1000"))

    say("4/5 Telegram (where you watch the desk)")
    if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        print("   already set.")
    elif ask("   set it up now? y/n", "y").lower().startswith("y"):
        print("   In Telegram: message @BotFather, send /newbot, follow it, copy the token.")
        webbrowser.open("https://t.me/BotFather")
        tg = ask("   paste the bot token", secret=True)
        print("   Now open your new bot in Telegram and send it any message. Waiting...")
        chat = telegram_chat(tg) if tg else None
        if chat:
            import requests
            requests.post(f"https://api.telegram.org/bot{tg}/sendMessage",
                          json={"chat_id": chat, "text": "desk connected"}, timeout=10)
            up["TELEGRAM_BOT_TOKEN"], up["TELEGRAM_CHAT_ID"] = tg, chat
            print("   connected. You should see 'desk connected' in Telegram.")
        else:
            print("   no message arrived. Run setup again to retry; the desk works without it.")

    write_env(up)
    load_env()
    os.environ.update(up)

    say("5/5 FOMO login")
    print(f"   A Chrome window with its own desk profile is opening on {FOMO}.\n"
          "   Log in there once. Leave that window open whenever the desk runs.")
    open_chrome()
    input("   press Enter once you are logged in... ")
    while not check_fomo():
        input("   not logged in yet. Log in in that window, then press Enter... ")
    print("   Run `python fomo_api.py <addr>:<netId>` with any token you know and check\n"
          "   the field names against _row() in fomo_api.py before the first cycle.")

    say("Setup done")
    if not shutil.which("cloudflared"):
        print("   One install left, for the bots to reach the judge:\n" + CLOUDFLARED_HELP)
    print("   Next: python run_desk.py start")


CLOUDFLARED_HELP = """   cloudflared is not installed.
     mac:      brew install cloudflared
     windows:  winget install --id Cloudflare.cloudflared
     linux:    https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"""

TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def start_tunnel(procs: list) -> str | None:
    if not shutil.which("cloudflared"):
        print(CLOUDFLARED_HELP + "\n   Without it the bots cannot reach the judge. "
              "The shift still runs.")
        return None
    p = subprocess.Popen(["cloudflared", "tunnel", "--url", f"http://localhost:{PORT}"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    procs.append(p)
    deadline = time.time() + 45
    while time.time() < deadline:
        line = p.stdout.readline()
        if (m := TUNNEL_RE.search(line or "")):
            return m.group(0)
    print("   tunnel did not report an address in 45s.")
    return None


def wait_judge() -> bool:
    import requests
    for _ in range(30):
        try:
            if requests.get(f"http://127.0.0.1:{PORT}/docs", timeout=2).ok:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def start(live: bool):
    load_env()
    for k in ("TYPESAFE_API_KEY", "DESK_SECRET"):
        if not os.environ.get(k):
            sys.exit(f"{k} is missing. Run: python run_desk.py setup")
    procs = []

    def stop(*_):
        for p in reversed(procs):
            p.terminate()
        print("\ndesk stopped.")
        sys.exit(0)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    say("Judge")
    procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "desk_api:app",
                                   "--host", "127.0.0.1", "--port", str(PORT)],
                                  cwd=ROOT, env=os.environ.copy()))
    if not wait_judge():
        stop()
    print(f"   up on http://127.0.0.1:{PORT}")

    say("Tunnel")
    public = start_tunnel(procs)
    if public:
        write_env({"PUBLIC_JUDGE_URL": f"{public}/judge"})
        print(f"   bots use JUDGE_URL={public}/judge  (python run_desk.py seats)\n"
              "   This address changes on every restart. Update the seats when it does,\n"
              "   or set up a named Cloudflare tunnel for a fixed one.")

    say("Chrome / FOMO")
    open_chrome()
    if not check_fomo():
        print("   Log into FOMO in the desk Chrome window. The shift retries every cycle.")

    say(f"Shift ({'LIVE' if live else 'shadow'})")
    if live and not os.environ.get("SEATS_URL"):
        print("   WARNING: --live without SEATS_URL. Orders will be logged, not delivered.")
    procs.append(subprocess.Popen([sys.executable, "main.py"] + (["--live"] if live else []),
                                  cwd=ROOT, env=os.environ.copy()))
    print("   running. Ctrl+C stops everything.")
    while True:
        for p in procs:                        # judge, tunnel, shift: any one dying
            if p.poll() is not None:           # leaves the desk half up, so stop all
                print(f"   {' '.join(map(str, p.args[:3]))} exited. Stopping the desk.")
                stop()
        time.sleep(5)


def check():
    import requests
    load_env()
    ok = True
    say("Judge")
    try:
        h = {"Authorization": f"Bearer {os.environ['DESK_SECRET']}"}
        r = requests.post(f"http://127.0.0.1:{PORT}/judge", headers=h, timeout=30, json={
            "question_set": "market", "state": {
                "ticker": "TEST", "age_minutes": 42, "holder_count": 310,
                "change": {"5m": 0.04, "1h": 0.22, "24h": 0.61}, "buys_h1": 540,
                "sells_h1": 120, "liquidity_usd": 48000, "mcap_usd": 310000,
                "volume_h24": 610000, "intended_ticket_usd": 900}})
        if r.status_code == 502:
            raise RuntimeError(f"judge is up, but Jev did not answer: {r.json().get('detail')}")
        r.raise_for_status()
        a = r.json()
        probs = a["answers"]["shape"]["probabilities"]
        good = (a["answers"]["shape"]["choice"] in probs
                and abs(sum(probs.values()) - 1) < 0.02
                and a["model"] != "jev-latest")
        print(f"   {'ok' if good else 'WRONG'}: model {a['model']}, "
              f"shape {a['answers']['shape']['choice']}")
        ok &= good
        held = requests.get(f"http://127.0.0.1:{PORT}/book/held", headers=h, timeout=5).json()
        print(f"   book: {held['held'] or 'flat'}")
    except Exception as e:
        print(f"   not answering: {e}")
        ok = False
    say("FOMO")
    ok &= cdp_up() and check_fomo()
    if not cdp_up():
        print("   desk Chrome is not running.")
    say("Tunnel")
    pub = read_env().get("PUBLIC_JUDGE_URL")
    if pub:
        try:
            code = requests.post(pub, json={}, timeout=10).status_code
            print(f"   {pub} answers ({code}, 401/422 is expected without a secret)")
        except Exception as e:
            print(f"   {pub} unreachable: {e}")
            ok = False
    else:
        print("   no tunnel recorded. Bots cannot reach the judge.")
    say("ALL GOOD" if ok else "SOMETHING IS DOWN, see above")


def seats():
    env = read_env()
    url = env.get("PUBLIC_JUDGE_URL", "<start the desk first: python run_desk.py start>")
    print(f"""
Give every judging seat (SCAN, VET, SOCIAL, CHIEF) these two values:
  JUDGE_URL   = {url}
  DESK_SECRET = {env.get('DESK_SECRET', '<run setup>')}
and for RISK:
  DESK_URL    = {url.rsplit('/judge', 1)[0]}
Never give any seat TYPESAFE_API_KEY.

Paste, in this order:
  SCAN, VET, SOCIAL, CHIEF   prompts/handoff.md on top, then their own prompt
  SOCIAL                     prompts/social.md
  CHIEF                      prompts/chief.md
  SIZE                       prompts/size.md
  FILLS                      prompts/fills.md
  RISK                       prompts/risk.md

Then, from a bot's own terminal, run the curl in prompts/handoff.md.""")


if __name__ == "__main__":
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "setup":
        setup()
    elif cmd == "start":
        start("--live" in sys.argv)
    elif cmd == "check":
        check()
    elif cmd == "seats":
        seats()
    else:
        print(__doc__)
