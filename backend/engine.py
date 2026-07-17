#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OFFSIDE · engine.py — ядро агента. Не знает, откуда данные (sim / TxLINE / Polymarket / replay).

Модель событий (всё публикуется в Bus как dict с полем type):
  fixture  {m, home, away, kickoff}
  tick     {src:'tx'|'pm', m, o:'home'|'draw'|'away', p:0..1}
  event    {m, kind:'kickoff'|'goal'|'ft', team?, minute?, score?}
  signal   {id, m, o, kind:'jump'|'goal', dp, tx, pm, gap}
  lag      {sig, m, gap0_pp, react_ms|null}
  trade    {id, sig, m, o, status:'open'|'closed', entry, exit?, pnl?, reason?}
  stats    {equity, n_trades, wr, lag_p50_ms, signals, brier, resolved}
"""
import asyncio
import collections
import itertools
import statistics
import time

OUTCOMES = ("home", "draw", "away")


class Bus:
    """pub/sub + история для поздних подписчиков"""

    def __init__(self):
        self.subs = set()
        self.hist = collections.deque(maxlen=1200)

    def publish(self, ev: dict):
        ev.setdefault("t", round(time.time(), 3))
        self.hist.append(ev)
        for q in list(self.subs):
            if q.qsize() < 800:
                q.put_nowait(ev)

    def subscribe(self):
        q = asyncio.Queue()
        self.subs.add(q)
        return q

    def unsubscribe(self, q):
        self.subs.discard(q)


class Engine:
    def __init__(self, bus: Bus, cfg: dict | None = None):
        c = cfg or {}
        self.bus = bus
        # --- детерминированные параметры (все в конфиге, судьям это нравится)
        self.jump_pp = c.get("jump_pp", 0.05)        # скачок implied prob за окно
        self.window_s = c.get("window_s", 10.0)
        self.cooldown_s = c.get("cooldown_s", 30.0)
        self.latency_s = c.get("latency_s", 1.5)     # сигнал -> исполнение
        self.spread = c.get("spread", 0.02)          # модель PM-спреда (ask=p+s/2)
        self.fee = c.get("fee", 0.0)                 # Polymarket taker fee
        self.timeout_s = c.get("timeout_s", 120.0)
        self.min_gap_pp = c.get("min_gap_pp", 0.03)  # торгуем только если толпа отстала >= 3пп
        self.stake = c.get("stake", 100.0)           # $ на сделку (бумага)

        self.matches = {}                                        # m -> meta
        self.tx = {}                                             # (m,o) -> prob
        self.pm = {}
        self.series = collections.defaultdict(
            lambda: collections.deque(maxlen=240))               # (src,m,o) -> [(t,p)]
        self.txwin = collections.defaultdict(
            lambda: collections.deque(maxlen=200))               # (m,o) -> [(t,p)] для окна
        self.last_sig_t = collections.defaultdict(float)

        self._sid = itertools.count(1)
        self._tid = itertools.count(1)
        self.signals = collections.deque(maxlen=250)
        self.pending = []                                        # отложенные входы
        self.open_trades = []
        self.closed = collections.deque(maxlen=400)
        self.lag_wait = {}                                       # sig_id -> ctx
        self.lags = collections.deque(maxlen=400)
        self.equity = 0.0
        self.resolved = 0
        self.correct = 0
        self.brier_sum = 0.0
        self._pred = {}                                          # sig_id -> (m, argmax outcome, p)

    # ---------------- входы ----------------
    def on_fixture(self, m, home, away, kickoff=None):
        self.matches[m] = dict(m=m, home=home, away=away, score=[0, 0],
                               minute=0, status="pre", kickoff=kickoff)
        self.bus.publish(dict(type="fixture", m=m, home=home, away=away,
                              kickoff=kickoff))

    def remove_match(self, m):
        """полная уборка карточки (стоп реплея): чистим состояние, шлём remove фронту"""
        self.matches.pop(m, None)
        for d in (self.tx, self.pm, self.txwin, self.last_sig_t):
            for k in [k for k in d if k[0] == m]:
                d.pop(k, None)
        for k in [k for k in self.series if k[1] == m]:
            self.series.pop(k, None)
        for sid in [s for s, c in self.lag_wait.items() if c["m"] == m]:
            self.lag_wait.pop(sid, None)
        self.pending = [p for p in self.pending if p["m"] != m]
        self.open_trades = [t for t in self.open_trades if t["m"] != m]
        self.bus.publish(dict(type="remove", m=m))

    def on_event(self, m, kind, team=None, minute=None, score=None):
        mt = self.matches.get(m)
        if not mt:
            return
        if minute is not None:
            mt["minute"] = minute
        if score is not None:
            mt["score"] = list(score)
        if kind == "kickoff":
            mt["status"] = "live"
        if kind == "ft":
            mt["status"] = "ft"
        self.bus.publish(dict(type="event", m=m, kind=kind, team=team,
                              minute=mt["minute"], score=mt["score"]))
        if kind == "goal" and team in ("home", "away"):
            self._signal(m, team, "goal", dp=None)
        if kind == "ft":
            self._resolve(m)

    def on_tick(self, src, m, o, p, t=None):
        t = t or time.time()
        p = min(max(float(p), 0.001), 0.999)
        key = (m, o)
        if src == "tx":
            self.tx[key] = p
            w = self.txwin[key]
            w.append((t, p))
            self._check_jump(m, o, t)
        else:
            self.pm[key] = p
            self._check_lag_react(m, o, p, t)
        self.series[(src, m, o)].append((round(t, 2), round(p, 4)))
        self.bus.publish(dict(type="tick", src=src, m=m, o=o, p=round(p, 4)))
        self._pump(t)

    # ---------------- детектор ----------------
    def _check_jump(self, m, o, t):
        w = self.txwin[(m, o)]
        if len(w) < 2:
            return
        # значение на границе окна (LOCF)
        p_now = w[-1][1]
        p_then = None
        for tt, pp in reversed(w):
            if t - tt >= self.window_s:
                p_then = pp
                break
        if p_then is None:
            p_then = w[0][1]
        dp = p_now - p_then
        if abs(dp) < self.jump_pp:
            return
        if t - self.last_sig_t[(m, o)] < self.cooldown_s:
            return
        self.last_sig_t[(m, o)] = t
        self._signal(m, o, "jump", dp=dp, t=t)

    def _signal(self, m, o, kind, dp=None, t=None):
        t = t or time.time()
        sid = next(self._sid)
        txp = self.tx.get((m, o))
        pmp = self.pm.get((m, o))
        gap = None if (txp is None or pmp is None) else (txp - pmp)
        sig = dict(type="signal", id=sid, m=m, o=o, kind=kind,
                   dp=None if dp is None else round(dp, 4),
                   tx=None if txp is None else round(txp, 4),
                   pm=None if pmp is None else round(pmp, 4),
                   gap=None if gap is None else round(gap, 4), t=round(t, 3))
        self.signals.append(sig)
        self.bus.publish(sig)
        # предсказание для accuracy: кто фаворит по TxLINE в момент сигнала
        probs = {oo: self.tx.get((m, oo)) for oo in OUTCOMES}
        if all(v is not None for v in probs.values()):
            fav = max(probs, key=probs.get)
            self._pred[sid] = (m, fav, probs[fav])
        # lag-метр: ждём реакции толпы
        if gap is not None and abs(gap) >= 0.005 and pmp is not None and txp is not None:
            self.lag_wait[sid] = dict(m=m, o=o, t0=t, pm0=pmp, tx0=txp, gap0=gap)
        # бумажный вход (латентность честно моделируем)
        direction = None
        if gap is not None and gap >= self.min_gap_pp:
            direction = "buy"      # TxLINE выше толпы -> толпа недооценила -> покупаем у толпы
        elif gap is not None and gap <= -self.min_gap_pp:
            direction = "sell"
        if direction:
            self.pending.append(dict(t_exec=t + self.latency_s, sig=sid, m=m, o=o,
                                     side=direction))

    # ---------------- lag-метр ----------------
    def _check_lag_react(self, m, o, p, t):
        done = []
        for sid, ctx in self.lag_wait.items():
            if ctx["m"] != m or ctx["o"] != o:
                continue
            covered = (p - ctx["pm0"]) / ctx["gap0"] if ctx["gap0"] else 1.0
            if covered >= 0.5:
                rec = dict(type="lag", sig=sid, m=m,
                           gap0_pp=round(ctx["gap0"] * 100, 2),
                           react_ms=int((t - ctx["t0"]) * 1000))
                self.lags.append(rec)
                self.bus.publish(rec)
                done.append(sid)
            elif t - ctx["t0"] > 60:
                rec = dict(type="lag", sig=sid, m=m,
                           gap0_pp=round(ctx["gap0"] * 100, 2), react_ms=None)
                self.lags.append(rec)
                self.bus.publish(rec)
                done.append(sid)
        for sid in done:
            self.lag_wait.pop(sid, None)

    # ---------------- бумажный трейдер ----------------
    def _pump(self, t):
        # отложенные входы
        keep = []
        for pe in self.pending:
            if t < pe["t_exec"]:
                keep.append(pe)
                continue
            key = (pe["m"], pe["o"])
            pmp = self.pm.get(key)
            txp = self.tx.get(key)
            if pmp is None or txp is None:
                continue
            if pe["side"] == "buy":
                entry = min(pmp + self.spread / 2, 0.999)
            else:
                entry = max(pmp - self.spread / 2, 0.001)
            tid = next(self._tid)
            tr = dict(id=tid, sig=pe["sig"], m=pe["m"], o=pe["o"], side=pe["side"],
                      entry=round(entry, 4), t_open=round(t, 3), fair0=round(txp, 4))
            self.open_trades.append(tr)
            self.bus.publish(dict(type="trade", status="open", **tr))
        self.pending = keep

        # сопровождение
        still = []
        for tr in self.open_trades:
            key = (tr["m"], tr["o"])
            pmp = self.pm.get(key)
            txp = self.tx.get(key)
            mt = self.matches.get(tr["m"], {})
            reason = None
            if pmp is None:
                still.append(tr)
                continue
            converged = (tr["side"] == "buy" and pmp >= tr["fair0"] - 0.01) or \
                        (tr["side"] == "sell" and pmp <= tr["fair0"] + 0.01)
            if converged:
                reason = "converged"
            elif t - tr["t_open"] > self.timeout_s:
                reason = "timeout"
            elif mt.get("status") == "ft":
                reason = "ft"
            if not reason:
                still.append(tr)
                continue
            if tr["side"] == "buy":
                exitp = max(pmp - self.spread / 2, 0.001)
                pnl_share = exitp - tr["entry"]
            else:
                exitp = min(pmp + self.spread / 2, 0.999)
                pnl_share = tr["entry"] - exitp
            pnl = round((pnl_share - self.fee) * self.stake, 2)
            self.equity = round(self.equity + pnl, 2)
            tr.update(exit=round(exitp, 4), t_close=round(t, 3), pnl=pnl, reason=reason)
            self.closed.append(tr)
            self.bus.publish(dict(type="trade", status="closed", **tr))
        self.open_trades = still
        self._maybe_stats(t)

    # ---------------- точность сигналов ----------------
    def _resolve(self, m):
        if str(m).startswith("R"):
            # реплей не должен загрязнять боевую точность (acc/Brier)
            for sid, pred in list(self._pred.items()):
                if pred[0] == m:
                    self._pred.pop(sid, None)
            return
        mt = self.matches.get(m)
        if not mt:
            return
        hs, as_ = mt["score"]
        winner = "home" if hs > as_ else ("away" if as_ > hs else "draw")
        for sid, (mm, fav, p) in list(self._pred.items()):
            if mm != m:
                continue
            self.resolved += 1
            hit = 1.0 if fav == winner else 0.0
            self.correct += int(hit)
            self.brier_sum += (p - hit) ** 2
            self._pred.pop(sid, None)

    _last_stats = 0.0

    def _maybe_stats(self, t):
        if t - self._last_stats < 2.0:
            return
        self._last_stats = t
        self.bus.publish(dict(type="stats", **self.stats()))

    def stats(self):
        pnls = [x["pnl"] for x in self.closed]
        wr = round(100 * sum(1 for p in pnls if p > 0) / len(pnls), 1) if pnls else None
        reacts = [x["react_ms"] for x in self.lags if x["react_ms"] is not None]
        return dict(
            equity=self.equity,
            n_trades=len(self.closed),
            open_trades=len(self.open_trades),
            wr=wr,
            avg_pnl=round(statistics.mean(pnls), 2) if pnls else None,
            lag_p50_ms=int(statistics.median(reacts)) if reacts else None,
            lag_n=len(self.lags),
            signals=len(self.signals),
            resolved=self.resolved,
            acc=round(100 * self.correct / self.resolved, 1) if self.resolved else None,
            brier=round(self.brier_sum / self.resolved, 3) if self.resolved else None,
        )

    # ---------------- снапшот для UI ----------------
    def snapshot(self):
        cards = []
        for m, mt in self.matches.items():
            probs = {}
            for o in OUTCOMES:
                probs[o] = dict(
                    tx=self.tx.get((m, o)),
                    pm=self.pm.get((m, o)),
                    spark=list(self.series[("tx", m, o)])[-120:],
                    spark_pm=list(self.series[("pm", m, o)])[-120:],
                )
            cards.append(dict(**mt, probs=probs))
        return dict(
            matches=cards,
            signals=list(self.signals)[-60:],
            lags=list(self.lags)[-120:],
            open_trades=self.open_trades,
            closed=list(self.closed)[-120:],
            stats=self.stats(),
            cfg=dict(jump_pp=self.jump_pp, window_s=self.window_s,
                     latency_s=self.latency_s, spread=self.spread,
                     min_gap_pp=self.min_gap_pp, stake=self.stake),
        )
