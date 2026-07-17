#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OFFSIDE · replay.py — прогон завершённого матча через тот же движок.
Источники: /api/scores/historical/{fid} (полная последовательность событий,
доступно для матчей 6ч..2нед назад) + /api/odds/updates/{день}/{час}/{интервал}
(5-минутные интервалы). Скорость xN. Идеален для демо и для судей.
"""
import asyncio
import os

import httpx

from txline import TxLine
from verify import _cfg as _vcfg, _get as _vget, _json as _vjson

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fx_meta(fx):
    p1h = bool(fx.get("Participant1IsHome", True))
    home = fx["Participant1"] if p1h else fx["Participant2"]
    away = fx["Participant2"] if p1h else fx["Participant1"]
    return dict(p1home=p1h, home=home, away=away,
                start=fx.get("StartTime") or 0, status="pre", goals=[0, 0])


async def list_replayable():
    """единый источник с VERIFY: снапшот + почасовой свип прошлого (verify.candidates),
    иначе бесплатный снапшот теряет завершённые матчи и список пустеет"""
    import verify as _vf
    return [dict(fid=c["fid"], home=c["home"], away=c["away"],
                 comp=c.get("comp"), start=c.get("start"))
            for c in await _vf.candidates()]


async def run_replay(engine, fid, speed=30.0, status_cb=None):
    """тянет историю, сортирует по Ts, кормит движок с ускорением x{speed}"""
    cfg = _vcfg()

    def say(msg):
        print(f"[replay] {msg}", flush=True)
        if status_cb:
            status_cb(msg)

    async with httpx.AsyncClient(timeout=30) as cl:
        # мета фикстуры: сначала снапшот, если выпал — общий пул с VERIFY
        # (все запросы через verify._get: 401 лечится свежим гостевым JWT сам)
        say("читаю фикстуру")
        r = await _vget(cl, cfg, "/fixtures/snapshot")
        fx = next((f for f in (r.json() if r.status_code == 200 else [])
                   if str(f["FixtureId"]) == str(fid)), None)
        if fx:
            meta0 = _fx_meta(fx)
        else:
            import verify as _vf
            c = next((x for x in await _vf.candidates()
                      if x["fid"] == str(fid)), None)
            if not c:
                say(f"фикстура {fid} не найдена ни в снапшоте, ни в свипе")
                return
            meta0 = dict(p1home=bool(c.get("p1home", True)),
                         home=c["home"], away=c["away"],
                         start=c.get("start") or 0, status="pre", goals=[0, 0])
        rfid = f"R{fid}"             # своя карточка: реплей не трогает live-матч
        tx = TxLine.__new__(TxLine)      # используем ЕГО же трансформы
        tx.e = engine
        tx.meta = {rfid: meta0}
        tx.seen_actions = set()
        m = tx.meta[rfid]
        engine.on_fixture(rfid, m["home"], m["away"], kickoff=m["start"])

        def _replay_minute(mm):
            """минута из времени ЛЕНТЫ, не из настоящего wall-clock"""
            ts0 = mm.get("start") or 0
            cur = getattr(tx, "_replay_ts", ts0)
            return max(0, min(130, int((cur - ts0) / 60000)))
        tx._minute = _replay_minute

        # история счёта
        say("качаю историю счёта")
        r = await _vget(cl, cfg, f"/scores/historical/{fid}")
        if r.status_code != 200:
            say(f"historical scores: HTTP {r.status_code} (окно 6ч..2нед?)")
            return
        try:
            scores = _vjson(r)
        except Exception as e:
            say(f"history parse: {e}")
            return
        say(f"событий счёта: {len(scores)}")

        # история одсов: 5-мин интервалы от старта-15мин до старта+150мин
        say("качаю историю одсов")
        odds = []
        t0 = int(m["start"]) - 15 * 60000
        for k in range(0, 44):           # 44 интервала x5мин = 220 мин (ОТ+пенальти)
            ts = t0 + k * 5 * 60000
            day, rem = divmod(ts // 1000, 86400)
            hour, rem2 = divmod(rem, 3600)
            interval = rem2 // 300
            try:
                r = await _vget(cl, cfg, f"/odds/updates/{day}/{hour}/{interval}")
                if r.status_code == 200:
                    odds += [d for d in _vjson(r)
                             if str(d.get("FixtureId")) == str(fid)]
            except Exception:
                pass
        say(f"одс-апдейтов: {len(odds)}")

        feed = sorted(
            [("s", s.get("Ts") or 0, s) for s in scores] +
            [("o", o.get("Ts") or 0, o) for o in odds],
            key=lambda x: x[1])
        if not feed:
            say("пустая история")
            return
        say(f"старт реплея x{speed}: {m['home']} vs {m['away']}")
        prev = feed[0][1]
        try:
            for kind, ts, d in feed:
                dt = max(0.0, (ts - prev) / 1000.0) / max(speed, 1.0)
                if dt > 0:
                    await asyncio.sleep(min(dt, 5.0))
                prev = ts
                tx._replay_ts = ts
                d = {**d, "FixtureId": rfid}  # перенаправляем в реплей-карточку
                (tx.on_score if kind == "s" else tx.on_odds)(d)
        except asyncio.CancelledError:
            say("реплей остановлен")
            raise
        say("реплей завершён")
