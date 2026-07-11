#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OFFSIDE · txline.py — боевой адаптер TxLINE (mainnet, level 12 real-time).
Фикстуры + StablePriceDemargined 1X2 (чистые вероятности) + счёт из Stats.
Гол = прирост Stats["1"]/["2"] (официальная кодировка: 1=голы P1, 2=голы P2).
401 -> авто-обновление гостевого JWT (apiToken живёт отдельно).
"""
import asyncio
import json
import os
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEC = os.path.join(ROOT, ".secrets", "txline.json")
ALOG = os.path.join(ROOT, "probe_out", "actions_seen.log")
GLOG = os.path.join(ROOT, "probe_out", "goals_seen.log")

PRE = {"scheduled", "ns", "not_started", "notstarted", "pre"}
FT = {"f", "fet", "fpe", "ended", "finished", "final", "ft"}
# probe-факт: у полного матча в снапшоте и стриме MarketPeriod = null (→ "")
FT_PERIODS = {"", "ft", "mt", "fulltime", "full_time", "full time",
              "full-time", "full", "match", "regular", "reg",
              "regular_time", "90"}


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


class TxLine:
    def __init__(self, engine):
        self.e = engine
        self.cfg = json.load(open(SEC, encoding="utf-8"))
        self.api = self.cfg["apiBaseUrl"].rstrip("/")
        self.origin = self.cfg["apiOrigin"].rstrip("/")
        self.meta = {}          # fid -> p1home/home/away/start/status/goals
        self.seen_actions = set()

    # ---------- заголовки/авторизация ----------
    def hdrs(self, sse=False):
        h = {"Authorization": f"Bearer {self.cfg['jwt']}",
             "X-Api-Token": self.cfg["apiToken"],
             "Accept-Encoding": "gzip",
             "Accept": "text/event-stream" if sse else "application/json"}
        if sse:
            h["Cache-Control"] = "no-cache"
        return h

    async def refresh_jwt(self, cl):
        r = await cl.post(self.origin + "/auth/guest/start")
        self.cfg["jwt"] = r.json()["token"]
        try:
            json.dump(self.cfg, open(SEC, "w", encoding="utf-8"), indent=2)
        except Exception:
            pass
        print("[txline] JWT обновлён", flush=True)

    # ---------- маппинг исходов ----------
    def outcome_of(self, fid, name):
        m = self.meta.get(fid)
        if not m:
            return None
        if name == "draw":
            return "draw"
        if name == "part1":
            return "home" if m["p1home"] else "away"
        if name == "part2":
            return "away" if m["p1home"] else "home"
        return None

    def _ft_ok(self, d):
        """только рынок ПОЛНОГО матча (отсекаем таймы/овертаймы), с самологом"""
        if not hasattr(self, "_mp_seen"):
            self._mp_seen = set()
        mp = str(d.get("MarketPeriod") or "").strip().lower()
        if mp not in FT_PERIODS:
            if mp not in self._mp_seen:
                self._mp_seen.add(mp)
                print(f"[txline] отсекаю 1X2 период {mp!r}", flush=True)
            return False
        if d.get("MarketParameters"):
            return False
        return True

    # ---------- обработчики ----------
    def on_odds(self, d):
        if d.get("Bookmaker") != "TXLineStablePriceDemargined":
            return
        if d.get("SuperOddsType") != "1X2_PARTICIPANT_RESULT":
            return
        if not self._ft_ok(d):
            return
        fid = str(d.get("FixtureId"))
        if fid not in self.meta:
            return
        for n, pc in zip(d.get("PriceNames") or [], d.get("Pct") or []):
            o = self.outcome_of(fid, n)
            if o is None:
                continue
            try:
                p = float(pc) / 100.0
            except (TypeError, ValueError):
                continue
            self.e.on_tick("tx", fid, o, p)

    def on_score(self, d):
        fid = str(d.get("FixtureId"))
        m = self.meta.get(fid)
        if not m:
            return
        act = (d.get("Action") or "").lower()
        if act and act not in self.seen_actions:
            self.seen_actions.add(act)
            try:
                os.makedirs(os.path.dirname(ALOG), exist_ok=True)
                with open(ALOG, "a", encoding="utf-8") as f:
                    f.write(act + "\n")
            except Exception:
                pass

        gs = str(d.get("GameState") or "").lower()
        if gs:
            if gs in PRE:
                st = "pre"
            elif gs in FT or "finish" in gs or "ended" in gs:
                st = "ft"
            else:
                st = "live"
            if st == "live" and m["status"] == "pre":
                m["status"] = "live"
                self.e.on_event(fid, "kickoff", minute=0, score=m["goals"])
            elif st == "ft" and m["status"] != "ft":
                m["status"] = "ft"
                self.e.on_event(fid, "ft", minute=self._minute(m), score=m["goals"])

        stats = d.get("Stats") or {}
        g1, g2 = _int(stats.get("1")), _int(stats.get("2"))
        if g1 is not None and g2 is not None:
            hg, ag = (g1, g2) if m["p1home"] else (g2, g1)
            if [hg, ag] != m["goals"]:
                team = None
                if hg > m["goals"][0]:
                    team = "home"
                elif ag > m["goals"][1]:
                    team = "away"
                m["goals"] = [hg, ag]
                if team:
                    self._log_goal(fid, d)
                    self.e.on_event(fid, "goal", team=team,
                                    minute=self._minute(m), score=m["goals"])

    def _log_goal(self, fid, d):
        """сырьё для VERIFY: /scores/stat-validation требует fixtureId+Seq гола"""
        try:
            os.makedirs(os.path.dirname(GLOG), exist_ok=True)
            with open(GLOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(dict(fid=fid, seq=d.get("Seq"),
                                        ts=d.get("Ts"), stats=d.get("Stats"),
                                        gs=d.get("GameState")),
                                   ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _minute(self, m):
        if not m["start"]:
            return 0
        return max(0, min(130, int((time.time() * 1000 - m["start"]) / 60000)))

    # ---------- старт ----------
    async def bootstrap(self, cl):
        r = await cl.get(self.api + "/fixtures/snapshot", headers=self.hdrs())
        if r.status_code == 401:
            await self.refresh_jwt(cl)
            r = await cl.get(self.api + "/fixtures/snapshot", headers=self.hdrs())
        r.raise_for_status()
        for f in r.json():
            fid = str(f["FixtureId"])
            p1h = bool(f.get("Participant1IsHome", True))
            home = f["Participant1"] if p1h else f["Participant2"]
            away = f["Participant2"] if p1h else f["Participant1"]
            self.meta[fid] = dict(p1home=p1h, home=home, away=away,
                                  start=f.get("StartTime") or 0,
                                  status="pre", goals=[0, 0])
            self.e.on_fixture(fid, home, away, kickoff=f.get("StartTime"))
            print(f"[txline] fixture {fid}: {home} vs {away}"
                  f" ({f.get('Competition')})", flush=True)
        for fid in list(self.meta):
            try:
                r = await cl.get(f"{self.api}/odds/snapshot/{fid}",
                                 headers=self.hdrs())
                if r.status_code != 200:
                    continue
                best = None
                for d in r.json():
                    if (d.get("SuperOddsType") == "1X2_PARTICIPANT_RESULT"
                            and d.get("Bookmaker") == "TXLineStablePriceDemargined"
                            and self._ft_ok(d)
                            and (best is None or d.get("Ts", 0) >= best.get("Ts", 0))):
                        best = d
                if best:
                    self.on_odds(best)
            except Exception as e:
                print(f"[txline] odds snapshot {fid} err: {e!r}", flush=True)

    async def _stream(self, cl, path, handler, tag):
        fails = 0
        while True:
            try:
                async with cl.stream("GET", self.api + path,
                                     headers=self.hdrs(sse=True),
                                     timeout=httpx.Timeout(30, read=None)) as r:
                    if r.status_code == 401:
                        await self.refresh_jwt(cl)
                        continue
                    if r.status_code != 200:
                        raise RuntimeError(f"status {r.status_code}")
                    print(f"[txline] {tag} stream: connected", flush=True)
                    fails = 0
                    async for line in r.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw:
                            continue
                        try:
                            d = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(d, dict) and "FixtureId" in d:
                            handler(d)
            except asyncio.CancelledError:
                return
            except Exception as e:
                fails += 1
                w = min(30, 3 * fails)
                print(f"[txline] {tag} reconnect #{fails} через {w}с: {e!r}",
                      flush=True)
                await asyncio.sleep(w)

    async def run(self, after_bootstrap=None):
        async with httpx.AsyncClient(timeout=25) as cl:
            while True:    # ночь без присмотра: bootstrap обязан подняться сам
                try:
                    await self.bootstrap(cl)
                    break
                except Exception as e:
                    print(f"[txline] bootstrap retry через 10с: {e!r}",
                          flush=True)
                    await asyncio.sleep(10)
            if after_bootstrap:
                asyncio.create_task(after_bootstrap(self))

            async def clocker():
                while True:
                    await asyncio.sleep(30)
                    for fid, m in self.meta.items():
                        if m["status"] == "live":
                            self.e.on_event(fid, "clock",
                                            minute=self._minute(m),
                                            score=m["goals"])

            await asyncio.gather(
                self._stream(cl, "/odds/stream", self.on_odds, "odds"),
                self._stream(cl, "/scores/stream", self.on_score, "scores"),
                clocker())
