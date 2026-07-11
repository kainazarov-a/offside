#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OFFSIDE · sim.py — синтетический стенд.
Гонит два параллельных «матча»: 1 реальная секунда = 1 игровая минута.
TxLINE реагирует на гол мгновенно, Polymarket догоняет с задержкой 2-6с + шум.
Тот же интерфейс, что и у реальных фидов: engine.on_fixture/on_event/on_tick.
"""
import asyncio
import random

TEAMS = ["Brazil", "France", "Argentina", "Spain", "England", "Germany",
         "Portugal", "Netherlands", "Japan", "Morocco", "USA", "Mexico"]


def _norm(h, d, a):
    s = h + d + a
    return h / s, d / s, a / s


class SimMatch:
    def __init__(self, engine, mid, home, away):
        self.e = engine
        self.m = mid
        self.home, self.away = home, away
        base = random.uniform(0.30, 0.55)
        self.p = dict(home=base, draw=random.uniform(0.20, 0.30),
                      away=1 - base - 0.25)
        self.p["home"], self.p["draw"], self.p["away"] = _norm(
            self.p["home"], self.p["draw"], self.p["away"])
        self.pm = dict(self.p)          # толпа стартует согласной
        self.pm_target = dict(self.p)
        self.pm_delay_until = 0.0
        self.score = [0, 0]
        self.minute = 0

    async def run(self):
        e, m = self.e, self.m
        e.on_fixture(m, self.home, self.away)
        await asyncio.sleep(1)
        e.on_event(m, "kickoff", minute=0, score=self.score)
        loop = asyncio.get_event_loop()
        t_min = 0.0
        while self.minute < 90:
            await asyncio.sleep(0.5)
            t_min += 0.5
            self.minute = int(t_min)
            now = loop.time()

            # случайный гол ~ 3 на матч
            if random.random() < 3.0 / 180:
                team = "home" if random.random() < self.p["home"] / (
                    self.p["home"] + self.p["away"]) else "away"
                self.score[0 if team == "home" else 1] += 1
                jump = random.uniform(0.10, 0.22)
                self.p[team] = min(0.95, self.p[team] + jump)
                other = "away" if team == "home" else "home"
                self.p[other] = max(0.02, self.p[other] - jump * 0.7)
                self.p["draw"] = max(0.02, self.p["draw"] - jump * 0.3)
                self.p["home"], self.p["draw"], self.p["away"] = _norm(
                    self.p["home"], self.p["draw"], self.p["away"])
                e.on_event(m, "goal", team=team, minute=self.minute,
                           score=self.score)
                # толпа проснётся через 2-6 сек
                self.pm_delay_until = now + random.uniform(2.0, 6.0)
                self.pm_target = dict(self.p)
            else:
                # мелкий дрейф TxLINE
                for o in ("home", "draw", "away"):
                    self.p[o] = min(0.97, max(0.02,
                        self.p[o] * (1 + random.gauss(0, 0.004))))
                self.p["home"], self.p["draw"], self.p["away"] = _norm(
                    self.p["home"], self.p["draw"], self.p["away"])
                self.pm_target = dict(self.p)

            # TxLINE тик — мгновенно
            for o in ("home", "draw", "away"):
                e.on_tick("tx", m, o, self.p[o])

            # Polymarket: до дедлайна спит на старой цене, потом экспоненциально догоняет
            if now >= self.pm_delay_until:
                for o in ("home", "draw", "away"):
                    gap = self.pm_target[o] - self.pm[o]
                    self.pm[o] += gap * 0.25 + random.gauss(0, 0.003)
                    self.pm[o] = min(0.97, max(0.02, self.pm[o]))
            for o in ("home", "draw", "away"):
                e.on_tick("pm", m, o, self.pm[o])

        e.on_event(m, "ft", minute=90, score=self.score)


async def run_sim(engine, lanes=2):
    """бесконечный поток матчей в `lanes` параллельных дорожек"""
    async def lane(i):
        n = 0
        while True:
            home, away = random.sample(TEAMS, 2)
            mid = f"SIM{i}-{n}"
            await SimMatch(engine, mid, home, away).run()
            n += 1
            await asyncio.sleep(4)
    await asyncio.gather(*[lane(i) for i in range(lanes)])
