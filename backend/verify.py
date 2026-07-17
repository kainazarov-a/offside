#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OFFSIDE · verify.py — вкладка VERIFY: гол → Merkle-пруф → программа на Solana.
Три задачи:
  candidates()       — какие матчи можно проверять (завершённые 6ч..2нед + живые голы)
  goals_for(fid)     — голы матча из /scores/historical/{fid} (нужен Seq каждого гола)
  prove(fid,seq,...) — Merkle-пакет от TxLINE + НАСТОЯЩИЙ on-chain view()
                       через tools/verify_stat.mjs (официальная схема TxODDS)
"""
import asyncio
import json
import os
import sys
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEC = os.path.join(ROOT, ".secrets", "txline.json")
GLOG = os.path.join(ROOT, "probe_out", "goals_seen.log")
MJS = os.path.join(ROOT, "tools", "verify_stat.mjs")


def _cfg():
    return json.load(open(SEC, encoding="utf-8"))


def _hdrs(cfg):
    return {"Authorization": f"Bearer {cfg['jwt']}",
            "X-Api-Token": cfg["apiToken"],
            "Accept-Encoding": "gzip"}


async def _get(cl, cfg, path):
    """GET с одним ретраем через свежий гостевой JWT на 401"""
    api = cfg["apiBaseUrl"].rstrip("/")
    r = await cl.get(api + path, headers=_hdrs(cfg))
    if r.status_code == 401:
        rr = await cl.post(cfg["apiOrigin"].rstrip("/") + "/auth/guest/start")
        cfg["jwt"] = rr.json()["token"]
        try:
            json.dump(cfg, open(SEC, "w", encoding="utf-8"), indent=2)
        except Exception:
            pass
        r = await cl.get(api + path, headers=_hdrs(cfg))
    return r


def _json(r):
    """толерантный парс: JSON -> NDJSON/SSE построчно -> понятная ошибка.
    Некоторые historical-ответы приходят не массивом, а потоком строк."""
    try:
        return r.json()
    except Exception:
        pass
    out = []
    for line in (r.text or "").splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line.startswith(("event:", "id:", ":")):
            continue
        try:
            d = json.loads(line)
            if isinstance(d, list):
                out.extend(d)
            else:
                out.append(d)
        except json.JSONDecodeError:
            continue
    if out:
        return out
    raise ValueError(f"HTTP {r.status_code}, тело начинается с: {(r.text or '')[:120]!r}")


def _live_goals():
    """голы, пойманные живым адаптером (goals_seen.log) — самые ценные для демо"""
    out = []
    try:
        for line in open(GLOG, encoding="utf-8"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except FileNotFoundError:
        pass
    return out


SWEEP_HOURS = 96          # сколько часов назад метём /fixtures/updates (4 суток)
_cand_cache = {"t": 0.0, "data": None}
_prove_cache = {}         # (fid,seq,stat,expect) -> (t, result); только готовые вердикты


async def _sweep_recent_fixtures(cl, cfg):
    """бесплатный снапшот держит только текущие/будущие фикстуры —
    прошедшие собираем из почасовых /fixtures/updates/{epochDay}/{hour}
    (паттерн из офиц. примера fixture_validation_view_only.ts)"""
    now_ms = int(time.time() * 1000)
    hours = [now_ms - h * 3600000 for h in range(1, SWEEP_HOURS + 1)]

    async def one(ts):
        day, rem = divmod(ts // 1000, 86400)
        hour = rem // 3600
        try:
            r = await _get(cl, cfg, f"/fixtures/updates/{day}/{hour}")
            return _json(r) if r.status_code == 200 else []
        except Exception:
            return []

    out = {}
    for i in range(0, len(hours), 16):          # пачками по 16 параллельно
        batch = await asyncio.gather(*(one(t) for t in hours[i:i + 16]))
        for chunk in batch:
            for f in chunk or []:
                if isinstance(f, dict) and f.get("FixtureId"):
                    out[str(f["FixtureId"])] = f
    return out


async def candidates():
    """матчи для проверки: стартовали 6ч..2нед назад (снапшот + почасовой
    свип прошлого) + живые голы. Кеш 10 мин — свип тяжёлый (~%d запросов).""" % SWEEP_HOURS
    if _cand_cache["data"] is not None and time.time() - _cand_cache["t"] < 600:
        return _cand_cache["data"]
    cfg = _cfg()
    now = time.time() * 1000
    live = _live_goals()
    live_fids = {g["fid"] for g in live}
    pool = {}
    async with httpx.AsyncClient(timeout=15) as cl:
        r = await _get(cl, cfg, "/fixtures/snapshot")
        if r.status_code == 200:
            try:
                for f in _json(r):
                    pool[str(f["FixtureId"])] = f
            except Exception:
                pass
        pool.update(await _sweep_recent_fixtures(cl, cfg))
    fixtures = []
    for fid, f in pool.items():
        st = f.get("StartTime") or 0
        done = now - 14 * 86400e3 < st < now - 6 * 3600e3
        if not (done or fid in live_fids):
            continue
        p1h = bool(f.get("Participant1IsHome", True))
        p1 = f.get("Participant1") or "P1"
        p2 = f.get("Participant2") or "P2"
        fixtures.append(dict(
            fid=fid,
            home=p1 if p1h else p2, away=p2 if p1h else p1,
            p1home=p1h, comp=f.get("Competition"), start=st,
            live_goals=sum(1 for g in live if g["fid"] == fid)))
    fixtures.sort(key=lambda x: -(x["start"] or 0))
    _cand_cache["t"], _cand_cache["data"] = time.time(), fixtures
    return fixtures


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


async def goals_for(fid):
    """история счёта -> список голов с Seq (Seq обязателен для stat-validation)"""
    cfg = _cfg()
    async with httpx.AsyncClient(timeout=30) as cl:
        # p1home — чтобы честно назвать сторону гола (берём из общего пула с Verify)
        p1h = True
        try:
            c = next((x for x in await candidates()
                      if x["fid"] == str(fid)), None)
            if c:
                p1h = bool(c.get("p1home", True))
        except Exception:
            pass
        r = await _get(cl, cfg, f"/scores/historical/{fid}")
        if r.status_code != 200:
            return {"error": f"historical HTTP {r.status_code} "
                             f"(окно 6ч..2нед после матча?)"}
        try:
            msgs = sorted(_json(r), key=lambda d: (d.get("Ts") or 0,
                                                   d.get("Seq") or 0))
        except Exception as e:
            return {"error": f"history parse: {e}"}
    goals, g1, g2, start = [], 0, 0, None
    for d in msgs:
        if start is None and d.get("StartTime"):
            start = d["StartTime"]
        st = d.get("Stats") or {}
        n1, n2 = _int(st.get("1")), _int(st.get("2"))
        if n1 is None or n2 is None:
            continue
        if n1 > g1 or n2 > g2:
            key = "1" if n1 > g1 else "2"
            side = ("home" if p1h else "away") if key == "1" \
                else ("away" if p1h else "home")
            ts = d.get("Ts") or 0
            minute = max(0, min(130, int((ts - (start or ts)) / 60000)))
            hg, ag = (n1, n2) if p1h else (n2, n1)
            goals.append(dict(seq=d.get("Seq"), ts=ts, minute=minute,
                              stat_key=key, value=n1 if key == "1" else n2,
                              side=side, score=[hg, ag],
                              gs=d.get("GameState")))
        # трекаем ТЕКУЩЕЕ значение (не max): после VAR-отката повторный гол
        # той же команды тоже должен попасть в список
        g1, g2 = n1, n2
    return {"fid": str(fid), "p1home": p1h, "goals": goals}


async def prove(fid, seq, stat="1", expect=None):
    """Merkle-пакет (для показа) + настоящий on-chain view через Node.
    Node недоступен -> честно показываем пакет с пометкой."""
    result = {"fid": str(fid), "seq": int(seq), "stat": str(stat)}

    # 1) сам пакет от TxLINE — судья видит корни и размеры пруфов в любом случае
    cfg = _cfg()
    async with httpx.AsyncClient(timeout=30) as cl:
        r = await _get(cl, cfg,
                       f"/scores/stat-validation?fixtureId={fid}"
                       f"&seq={seq}&statKeys={stat}")
    if r.status_code != 200:
        result["error"] = f"stat-validation HTTP {r.status_code}: {r.text[:200]}"
        return result
    try:
        val = _json(r)
    except Exception as e:
        result["error"] = f"stat-validation parse: {e}"
        return result
    ts0 = val["summary"]["updateStats"]["minTimestamp"]
    result["api"] = dict(
        epochDay=int(ts0 // 86400000),
        eventStatRoot=bytes(val["eventStatRoot"]).hex(),
        subTreeRoot=bytes(val["summary"]["eventStatsSubTreeRoot"]).hex(),
        proofNodes=dict(subTree=len(val.get("subTreeProof") or []),
                        mainTree=len(val.get("mainTreeProof") or []),
                        stat=[len(p) for p in (val.get("statProofs") or [])]),
        stats=val.get("statsToProve"),
        updateCount=val["summary"]["updateStats"]["updateCount"])

    # 2) on-chain view() — исполняет сам Txoracle на mainnet (кеш 10 мин на готовые вердикты:
    # авто-демо на вкладке не должно гонять RPC при каждом визите)
    ck = (str(fid), str(seq), str(stat), None if expect is None else str(expect))
    hit = _prove_cache.get(ck)
    if hit and time.time() - hit[0] < 600:
        result["onchain"] = hit[1]
        result["cached"] = True
        return result
    cmd = ["node", MJS, "--fid", str(fid), "--seq", str(seq),
           "--stat", str(stat)]
    if expect is not None:
        cmd += ["--expect", str(expect)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=ROOT,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            so, se = await asyncio.wait_for(proc.communicate(), timeout=90)
        except asyncio.TimeoutError:
            proc.kill()
            result["onchain"] = {"error": "timeout 90s (RPC mainnet?)"}
            return result
        line = (so or b"").decode("utf-8", "replace").strip().splitlines()
        result["onchain"] = json.loads(line[-1]) if line else \
            {"error": (se or b"").decode("utf-8", "replace")[-300:]}
        if result["onchain"].get("ok") is not None:      # кешируем только финальные вердикты
            _prove_cache[ck] = (time.time(), result["onchain"])
    except FileNotFoundError:
        result["onchain"] = {"error": "node не найден в PATH"}
    except Exception as e:
        result["onchain"] = {"error": repr(e)}
    return result


if __name__ == "__main__":
    # ручной прогон: python backend/verify.py <fid> [seq] [stat] [expect]
    async def _main():
        if len(sys.argv) < 2:
            print(json.dumps(await candidates(), ensure_ascii=False, indent=1))
        elif len(sys.argv) == 2:
            print(json.dumps(await goals_for(sys.argv[1]),
                             ensure_ascii=False, indent=1))
        else:
            print(json.dumps(await prove(*sys.argv[1:]),
                             ensure_ascii=False, indent=1))
    asyncio.run(_main())
