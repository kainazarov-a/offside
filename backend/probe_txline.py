#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OFFSIDE · probe_txline.py v2 — проверка живых эндпоинтов (пути из офиц. доков).
1) /api/fixtures/snapshot   2) odds+scores snapshot по ближайшей фикстуре
3) SSE /api/odds/stream и /api/scores/stream по 20с
Сырьё -> probe_out/*.json.   Запуск: python backend/probe_txline.py
"""
import json
import os
import sys
import time

try:
    import httpx
except ImportError:
    print("pip install httpx  — и перезапусти")
    sys.exit(1)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEC = os.path.join(ROOT, ".secrets", "txline.json")
OUT = os.path.join(ROOT, "probe_out")
os.makedirs(OUT, exist_ok=True)
if not os.path.exists(SEC):
    print("Нет .secrets/txline.json — сначала: node tools/get_token.mjs")
    sys.exit(1)

cfg = json.load(open(SEC, encoding="utf-8"))
API = cfg["apiBaseUrl"].rstrip("/")
HDRS = {
    "Authorization": f"Bearer {cfg['jwt']}",
    "X-Api-Token": cfg["apiToken"] if isinstance(cfg["apiToken"], str) else json.dumps(cfg["apiToken"]),
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
}


def save(name, body):
    p = os.path.join(OUT, f"{name}.json")
    with open(p, "w", encoding="utf-8") as f:
        if isinstance(body, (dict, list)):
            json.dump(body, f, ensure_ascii=False, indent=2)
        else:
            f.write(str(body))


def st_key(f_):
    st = f_.get("StartTime") or f_.get("startTime") or 0
    try:
        return abs(float(st) - time.time() * 1000)
    except Exception:
        return 9e15


with httpx.Client(timeout=20, headers=HDRS) as cl:
    r = cl.get(API + "/fixtures/snapshot")
    print(f"[{r.status_code}] /fixtures/snapshot")
    fx = []
    if r.status_code == 200:
        fx = r.json()
        save("fixtures_snapshot", fx)
        print(f"   фикстур: {len(fx)}")
        for f_ in fx[:6]:
            print("   ", f_.get("FixtureId"), f_.get("Participant1"), "vs",
                  f_.get("Participant2"), "| start:", f_.get("StartTime"),
                  "| comp:", f_.get("CompetitionId"))
    else:
        print("   ", r.text[:300])

    if fx:
        fid = sorted(fx, key=st_key)[0].get("FixtureId")
        print(f"\nближайшая фикстура: {fid}")
        for name, path in [("odds_snapshot", f"/odds/snapshot/{fid}"),
                           ("scores_snapshot", f"/scores/snapshot/{fid}"),
                           ("scores_updates", f"/scores/updates/{fid}")]:
            try:
                r = cl.get(API + path)
                try:
                    body = r.json()
                except Exception:
                    body = r.text[:1500]
                save(name, body)
                head = (json.dumps(body)[:220] if isinstance(body, (dict, list))
                        else str(body)[:220])
                print(f"[{r.status_code}] {path}\n    {head}")
            except Exception as e:
                print(f"[ERR] {path} -> {e!r}")

    for name, path in [("stream_scores", "/scores/stream"),
                       ("stream_odds", "/odds/stream")]:
        print(f"\n[SSE] {path} — слушаю 20с...")
        got = []
        try:
            with cl.stream("GET", API + path,
                           headers={**HDRS, "Accept": "text/event-stream",
                                    "Cache-Control": "no-cache"}) as r:
                print(f"   статус {r.status_code}")
                if r.status_code == 200:
                    t0 = time.time()
                    for line in r.iter_lines():
                        if line:
                            got.append(line)
                            print("   >", line[:200])
                        if len(got) >= 20 or time.time() - t0 > 20:
                            break
            save(name, "\n".join(got) if got else "(тишина 20с — возможно нет живого матча)")
        except Exception as e:
            print(f"   [ERR] {e!r}")

print(f"\nСырьё в {OUT}. Пришли ВЕСЬ вывод этого скрипта.")
