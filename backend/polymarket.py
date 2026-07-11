#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OFFSIDE · polymarket.py — толпа (Polymarket, публичные API, без ключей).

Три контура, работают параллельно и не умирают:
  1) autodiscover (Gamma) — каждые 5 мин ищет рынки под наши фикстуры
     (France–Spain листингуется позже — подхватим на лету) + ручной
     оверрайд config/match_map.json.
  2) WS market channel (CLOB) — best_bid/best_ask в реальном времени →
     midpoint с точностью мс. Известный отказ: сервер может «молча
     замолчать» (py-clob-client #292) → сторож 120с + реконнект.
  3) REST midpoint поллер — базовая линия ~2.5с/цикл; живёт всегда,
     страхует WS. Дубли не мешают: тик с неизменной ценой отсекаем.

Пробный запуск: python backend/polymarket.py --probe
"""
import asyncio
import json
import os
import sys
import time

import httpx

try:
    import websockets
except ImportError:        # мс-точность появится после: pip install websockets
    websockets = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config", "match_map.json")
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
DISCOVER_EVERY_S = 300     # повторный autodiscover (поздние листинги)
WS_SILENCE_S = 120         # молчание WS дольше — принудительный реконнект


def _tokens_of(market):
    raw = market.get("clobTokenIds")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    return raw or []


async def discover(cl, home, away):
    """ищем событие с рынками на оба названия команд; возвращаем кандидатов"""
    out = []
    try:
        r = await cl.get(GAMMA + "/events",
                         params={"closed": "false", "limit": "500",
                                 "order": "volume24hr", "ascending": "false"})
        if r.status_code != 200:
            return out
        for ev in r.json():
            title = (ev.get("title") or "").lower()
            if home.lower() in title and away.lower() in title:
                mk = []
                for m in ev.get("markets") or []:
                    toks = _tokens_of(m)
                    mk.append(dict(
                        q=m.get("question") or m.get("groupItemTitle"),
                        token_yes=toks[0] if toks else None))
                out.append(dict(title=ev.get("title"), slug=ev.get("slug"),
                                markets=mk))
    except Exception as e:
        print(f"[pm] discover err: {e!r}", flush=True)
    return out


def guess_map(cands, home, away):
    """из кандидатов пытаемся собрать {home,draw,away} -> token"""
    for ev in cands:
        got = {}
        for m in ev["markets"]:
            q = (m["q"] or "").lower()
            if not m["token_yes"]:
                continue
            if "draw" in q or "ничья" in q:
                got["draw"] = m["token_yes"]
            elif home.lower() in q and away.lower() not in q:
                got["home"] = m["token_yes"]
            elif away.lower() in q and home.lower() not in q:
                got["away"] = m["token_yes"]
        if len(got) == 3:
            return got, ev["title"]
    return None, None


class Polymarket:
    def __init__(self, engine, fixtures=None):
        """fixtures: {fid: {"home":..,"away":..}} — от TxLine.meta"""
        self.e = engine
        self.fixtures = fixtures or {}
        self.map = {}
        self.tok2mo = {}       # token -> (fid, outcome)
        self.gen = 0           # растёт при изменении карты (WS переподписка)
        self.last_mid = {}     # token -> последний отправленный mid
        self.n_ws = 0
        self.n_poll = 0
        if os.path.exists(CFG):
            try:
                self.map = json.load(open(CFG, encoding="utf-8"))
            except Exception as e:
                print(f"[pm] config broken: {e!r}", flush=True)
        self._reindex()

    def _reindex(self):
        self.tok2mo = {tok: (fid, o) for fid, toks in self.map.items()
                       for o, tok in toks.items()}

    # ---------- единая точка выдачи тика в движок ----------
    def _tick(self, tok, mid, src):
        mo = self.tok2mo.get(tok)
        if mo is None or not (0.0 < mid < 1.0):
            return
        if self.last_mid.get(tok) == mid:
            return
        self.last_mid[tok] = mid
        self.e.on_tick("pm", mo[0], mo[1], mid)
        if src == "ws":
            self.n_ws += 1
        else:
            self.n_poll += 1

    # ---------- контур 1: авто-поиск рынков (периодический) ----------
    async def _autodiscover(self, cl):
        added = 0
        for fid, m in self.fixtures.items():
            if fid in self.map:
                continue
            cands = await discover(cl, m["home"], m["away"])
            got, title = guess_map(cands, m["home"], m["away"])
            if got:
                self.map[fid] = got
                added += 1
                print(f"[pm] auto-map {fid}: {title}", flush=True)
        if added:
            os.makedirs(os.path.dirname(CFG), exist_ok=True)
            json.dump(self.map, open(CFG, "w", encoding="utf-8"), indent=2)
            self._reindex()
            self.gen += 1
            print(f"[pm] карта сохранена: {CFG}", flush=True)

    async def _discover_loop(self, cl):
        while True:
            try:
                await self._autodiscover(cl)
                if not self.map:
                    print("[pm] рынков пока нет — повторю поиск через 5 мин",
                          flush=True)
            except Exception as e:
                print(f"[pm] autodiscover err: {e!r}", flush=True)
            await asyncio.sleep(DISCOVER_EVERY_S)

    # ---------- контур 2: REST midpoint (базовая линия) ----------
    async def _poll_loop(self, cl):
        fails = 0
        announced = False
        while True:
            if not self.map:
                await asyncio.sleep(5)
                continue
            if not announced:
                print(f"[pm] поллер запущен: {len(self.map)} матчей", flush=True)
                announced = True
            for fid, toks in list(self.map.items()):
                for o, tok in toks.items():
                    try:
                        r = await cl.get(CLOB + "/midpoint",
                                         params={"token_id": tok})
                        if r.status_code == 200:
                            self._tick(tok, float(r.json().get("mid")), "poll")
                            fails = 0
                    except Exception:
                        fails += 1
                        if fails % 50 == 1:
                            print("[pm] midpoint errors...", flush=True)
                    await asyncio.sleep(0.12)
            await asyncio.sleep(0.8)

    # ---------- контур 3: WS market channel (мс-точность) ----------
    def _mid_from(self, tok, bb, ba):
        try:
            bb, ba = float(bb), float(ba)
        except (TypeError, ValueError):
            return
        if bb <= 0 or ba <= 0 or bb > ba:
            return
        self._tick(tok, round((bb + ba) / 2, 4), "ws")

    def _on_ws_event(self, d):
        et = d.get("event_type")
        if et == "best_bid_ask":
            self._mid_from(d.get("asset_id"),
                           d.get("best_bid"), d.get("best_ask"))
        elif et == "price_change":
            for pc in d.get("price_changes") or []:
                self._mid_from(pc.get("asset_id"),
                               pc.get("best_bid"), pc.get("best_ask"))
        elif et == "book":
            bids = [float(x["price"]) for x in d.get("bids") or []
                    if x.get("price")]
            asks = [float(x["price"]) for x in d.get("asks") or []
                    if x.get("price")]
            if bids and asks:
                self._mid_from(d.get("asset_id"), max(bids), min(asks))

    async def _ws_loop(self):
        if websockets is None:
            print("[pm] websockets не установлен → живём на REST-поллере "
                  "(мс-точность: pip install websockets)", flush=True)
            return
        fails = 0
        while True:
            if not self.map:
                await asyncio.sleep(5)
                continue
            mygen = self.gen
            toks = list(self.tok2mo.keys())
            try:
                async with websockets.connect(WS_URL, max_size=2 ** 22) as ws:
                    await ws.send(json.dumps({"assets_ids": toks,
                                              "type": "market",
                                              "custom_feature_enabled": True}))
                    print(f"[pm] ws connected: {len(toks)} токенов", flush=True)
                    fails = 0
                    last = time.time()
                    last_ping = 0.0
                    while True:
                        if self.gen != mygen:
                            print("[pm] ws: карта выросла — переподписка",
                                  flush=True)
                            break
                        if time.time() - last > WS_SILENCE_S:
                            raise RuntimeError(f"тишина > {WS_SILENCE_S}с")
                        if time.time() - last_ping > 10:
                            await ws.send("PING")
                            last_ping = time.time()
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=10)
                        except asyncio.TimeoutError:
                            continue
                        last = time.time()
                        if not msg or msg == "PONG":
                            continue
                        try:
                            data = json.loads(msg)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        for d in (data if isinstance(data, list) else [data]):
                            if isinstance(d, dict):
                                self._on_ws_event(d)
            except asyncio.CancelledError:
                return
            except Exception as e:
                fails += 1
                w = min(30, 3 * fails)
                print(f"[pm] ws reconnect #{fails} через {w}с: {e!r}",
                      flush=True)
                await asyncio.sleep(w)

    # ---------- пульс в консоль ----------
    async def _pulse(self):
        while True:
            await asyncio.sleep(120)
            if self.map:
                print(f"[pm] пульс: ws-тиков {self.n_ws}, "
                      f"poll-тиков {self.n_poll}, матчей {len(self.map)}",
                      flush=True)

    async def run(self):
        while True:            # супервизор: контур толпы не умирает
            try:
                async with httpx.AsyncClient(timeout=10) as cl:
                    await asyncio.gather(self._discover_loop(cl),
                                         self._poll_loop(cl),
                                         self._ws_loop(),
                                         self._pulse())
            except asyncio.CancelledError:
                return
            except Exception as e:
                print(f"[pm] контур упал, рестарт через 5с: {e!r}", flush=True)
                await asyncio.sleep(5)


# ---------- пробный режим ----------
async def _probe():
    fxp = os.path.join(ROOT, "probe_out", "fixtures_snapshot.json")
    if not os.path.exists(fxp):
        print("нет probe_out/fixtures_snapshot.json — сначала probe_txline")
        return
    fx = json.load(open(fxp, encoding="utf-8"))
    async with httpx.AsyncClient(timeout=15) as cl:
        for f in fx:
            p1h = bool(f.get("Participant1IsHome", True))
            home = f["Participant1"] if p1h else f["Participant2"]
            away = f["Participant2"] if p1h else f["Participant1"]
            print(f"\n=== {f['FixtureId']}: {home} vs {away} ({f.get('Competition')}) ===")
            cands = await discover(cl, home, away)
            if not cands:
                print("   Polymarket: ничего не нашёл")
                continue
            for ev in cands[:3]:
                print(f"   событие: {ev['title']}  (slug: {ev['slug']})")
                for m in ev["markets"][:8]:
                    print(f"      - {m['q']}  token={str(m['token_yes'])[:20]}...")
            got, title = guess_map(cands, home, away)
            print(f"   авто-карта: {'OK -> ' + title if got else 'не собралась (нужен ручной map)'}")

if __name__ == "__main__":
    if "--probe" in sys.argv:
        asyncio.run(_probe())
    else:
        print("используй --probe, либо импортируй Polymarket из server.py")
