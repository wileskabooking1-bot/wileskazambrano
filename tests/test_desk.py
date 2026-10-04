import time
import pytest
import requests
from fastapi.testclient import TestClient
from typesafe_sdk import Choice, Noul, Score, SystemOneResponse

import book, collect, main, pick as pick_mod
from filter import free_kill, trade_kill, chain_kill, soft_kill
from questions import SETS, PICK


@pytest.fixture(autouse=True)
def clean_book():
    book.DB.execute("DELETE FROM position"); book.DB.execute("DELETE FROM bench")
    book.DB.commit()


def tok(**kw):
    t = {"addr": "Mint111", "net": 1399811149, "tid": "Mint111:1399811149",
         "ticker": "AAA", "mcap_usd": 300_000, "liquidity_usd": 50_000,
         "volume_h24": 600_000, "price_usd": 0.001, "holder_count": 300,
         "change": {"5m": 0.01, "1h": 0.2, "4h": 0.4, "24h": 0.6}, "age_minutes": 60}
    t.update(kw)
    return t


def noul(v): return {"type": "noul", "noul": v}
def choice(c, probs, conf=0.8): return {"type": "choice", "choice": c, "probabilities": probs, "confidence": conf}
def score(v): return {"type": "score", "score": v, "confidence": 0.8, "legend": {}, "probabilities": {}}


GOOD_MARKET = {"shape": choice("crowd", {"crowd": 0.9, "one_buyer": 0.05, "fading": 0.03, "too_early": 0.02}),
               "liquidity_fits_ticket": noul(0.9), "momentum_already_spent": noul(0.2)}
GOOD_SOL = {"authority_risk": choice("renounced", {"renounced": 1.0}),
            "concentration_is_exit_risk": noul(0.1), "dev_still_loaded": noul(0.1)}


# --- questions
def test_sets_are_sdk_objects():
    for name, qs in SETS.items():
        if callable(qs):
            qs = qs({"candidates": [{"ticker": "A", "summary": "x"}, {"ticker": "B", "summary": "y"}]})
        for q in qs.values():
            assert isinstance(q, (Choice, Noul, Score))
            q.model_dump()


# --- filter
def test_free_kill():
    assert free_kill(tok()) is None
    assert free_kill(tok(age_minutes=5)) == "age"
    assert free_kill(tok(liquidity_usd=None)) == "liquidity"      # null is not fine
    assert free_kill(tok(mcap_usd=9_000_000)) == "mcap"


def test_trade_kill():
    assert trade_kill({"trades_h24": None}) == "no_pair"
    assert trade_kill({"trades_h24": 500, "sells_h1": 0, "buys_h1": 30}) == "no_sells"
    assert trade_kill({"trades_h24": 500, "sells_h1": 10, "buys_h1": 30}) is None


def test_chain_kill_honeypot_on_base_too():
    assert chain_kill({"chain": "base", "is_honeypot": True}) == "honeypot"
    assert chain_kill({"chain": "solana", "mint_authority": "Abc", "freeze_authority": None}) == "authority_open"
    assert chain_kill({"chain": "solana", "mint_authority": None, "freeze_authority": None,
                       "top_10_percent": "45.0"}) is None


def test_authority_normalised():
    assert collect.authority("no") is None
    assert collect.authority(None) is None
    assert collect.authority("7xKX...") == "7xKX..."


def test_clean_handle():
    assert collect.clean_handle("LuffyX100X/status/2102659581109272876") == "LuffyX100X"
    assert collect.clean_handle("https://x.com/@proj?s=1") == "proj"
    assert collect.clean_handle("not a handle!") is None


def test_soft_kill():
    assert soft_kill({**GOOD_MARKET, **GOOD_SOL}) is None
    assert soft_kill({**GOOD_MARKET, "concentration_is_exit_risk": noul(0.7)}) == "concentration_is_exit_risk"
    weak = {**GOOD_MARKET, "shape": choice("crowd", {"crowd": 0.4})}
    assert soft_kill(weak) == "shape_weak"
    assert soft_kill({"effort": score(0.5)}) == "effort"


# --- pick
def survivor(ticker, addr="Mint111", **ans):
    d = {**tok(ticker=ticker, addr=addr), "chain": "solana"}
    return d, {**GOOD_MARKET, **GOOD_SOL, **ans}


def test_pick_labels_unique_and_maps_back():
    s = [survivor("AAA", "Mint111"), survivor("AAA", "Mint222")]
    seen = {}
    def judge(qs, state):
        seen["state"] = state
        names = [c["ticker"] for c in state["candidates"]]
        assert len(set(names)) == 2
        PICK(state)                                         # builds without error
        return {"model": "jev-1.13.0", "answers": {
            "best": choice(names[1], {names[0]: 0.2, names[1]: 0.8}, conf=0.8),
            "worth_trading_at_all": noul(0.9)}}
    o = pick_mod.pick(judge, s)
    assert o["token"]["address"] == "Mint222"
    assert o["size_factor"] == 0.6                          # no X account
    assert o["model"] == "jev-1.13.0"


def test_pick_stands_down_on_worth():
    def judge(qs, state):
        return {"model": "m", "answers": {"best": choice("AAA", {"AAA": 0.9}, 0.9),
                                          "worth_trading_at_all": noul(0.3)}}
    assert pick_mod.pick(judge, [survivor("AAA"), survivor("BBB")]) is None


# --- book
def test_bench_by_reason():
    book.sit("x:1", "honeypot"); book.sit("y:1", "age")
    assert book.benched("x:1") and book.benched("y:1")
    assert not book.benched("z:1")


# --- main
class FakeDesk:
    def __init__(self): self.shadow = []
    def read_x(self, h): return None
    def log_shadow(self, o, s): self.shadow.append(o)


def wire(monkeypatch, tokens):
    monkeypatch.setattr(main, "universe", lambda: [t["tid"] for t in tokens])
    monkeypatch.setattr(main, "shortlist", lambda f, ids: [dict(t) for t in tokens])
    monkeypatch.setattr(main, "trade_counts", lambda t: {"buys_h1": 50, "sells_h1": 20, "buys_h6": 300,
                                                          "sells_h6": 150, "trades_h24": 900})
    monkeypatch.setattr(main, "dossier", lambda t: {**t, "chain": "solana", "mint_authority": None,
                                                   "freeze_authority": None, "top_10_percent": 30,
                                                   "top_wallet_percent": 0.02, "x_handle": None,
                                                   "description": None})


def test_run_once_shadow_single_survivor(monkeypatch):
    wire(monkeypatch, [tok()])
    calls = []
    def judge(qs, state):
        calls.append((qs, set(state)))
        return {"model": "jev-1.13.0", "answers": GOOD_MARKET if qs == "market" else GOOD_SOL}
    desk = FakeDesk()
    order, stats = main.run_once(None, judge, desk, 1000, shadow=True)
    assert order is None and len(desk.shadow) == 1
    assert desk.shadow[0]["size_factor"] == 0.6
    assert [c[0] for c in calls] == ["market", "solana"]
    assert "description" not in calls[0][1]                # trimmed state
    assert book.held() is None                             # shadow never takes the book


def test_run_once_live_takes_book_then_skips(monkeypatch):
    wire(monkeypatch, [tok()])
    judge = lambda qs, st: {"model": "m", "answers": GOOD_MARKET if qs == "market" else GOOD_SOL}
    order, _ = main.run_once(None, judge, FakeDesk(), 1000, shadow=False)
    assert order and book.held()["ticker"] == "AAA"
    order, stats = main.run_once(None, judge, FakeDesk(), 1000, shadow=False)
    assert order is None and stats["held"] == "AAA"


def test_run_once_stands_down_when_judge_unreachable(monkeypatch):
    wire(monkeypatch, [tok()])
    def judge(qs, st): raise requests.ConnectionError("down")
    with pytest.raises(main.StandDown):
        main.run_once(None, judge, FakeDesk(), 1000)


# --- judge service
def test_judge_endpoint(monkeypatch):
    import desk_api, judge as judge_mod
    async def fake(state, questions):
        return SystemOneResponse.model_validate({
            "model": "jev-1.13.0", "usage": {"input_tokens": 10, "output_tokens": 2},
            "answers": {"urgent": {"type": "noul", "noul": 0.95}}})
    monkeypatch.setattr(judge_mod.client, "system_one", fake)
    c = TestClient(desk_api.app)
    h = {"Authorization": "Bearer test-secret"}
    assert c.post("/judge", json={"question_set": "market", "state": {}}).status_code == 401
    assert c.post("/judge", json={"question_set": "nope", "state": {}}, headers=h).status_code == 422
    assert c.post("/judge", json={"question_set": "pick", "state": {}}, headers=h).status_code == 422
    r = c.post("/judge", json={"question_set": "market", "state": {"x": 1}}, headers=h)
    assert r.status_code == 200 and r.json()["answers"]["urgent"]["noul"] == 0.95
    book.take({"token": {"ticker": "A", "address": "a", "network_id": 1}})
    assert c.post("/book/release", headers=h).json()["released"]["ticker"] == "A"
    assert book.held() is None
