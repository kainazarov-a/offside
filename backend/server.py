#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OFFSIDE · server.py — API + живой стрим + статика фронта.
Запуск (симулятор, без внешних фидов):
    python backend/server.py --sim
Открыть: http://127.0.0.1:8000
"""
import argparse
import asyncio
import json
import os
import sys

from fastapi import FastAPI
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from engine import Bus, Engine  # noqa: E402
import sim as simmod            # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--sim", action="store_true", help="гнать синтетические матчи")
ap.add_argument("--live", action="store_true",
                help="реальные фиды: TxLINE mainnet + Polymarket")
ap.add_argument("--port", type=int, default=8000)
ap.add_argument("--host", default="127.0.0.1",
                help="0.0.0.0 для публичного деплоя")
args, _ = ap.parse_known_args()

bus = Bus()
engine = Engine(bus)


from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def lifespan(app):
    if args.sim:
        asyncio.create_task(simmod.run_sim(engine, lanes=2))
        print("[offside] SIM MODE: два синтетических матча в эфире")
    if args.live:
        from txline import TxLine
        from polymarket import Polymarket

        async def _pm_after(tl):
            fixtures = {fid: {"home": m["home"], "away": m["away"]}
                        for fid, m in tl.meta.items()}
            await Polymarket(engine, fixtures).run()

        asyncio.create_task(TxLine(engine).run(after_bootstrap=_pm_after))

        async def _prewarm():
            # греем пул истории (96ч-свип) заранее: первый клик по Replay/Verify
            # не должен молча ждать холодный свип
            await asyncio.sleep(5)
            try:
                import verify as vf
                pool = await vf.candidates()
                print(f"[server] history pool warmed: {len(pool)} matches",
                      flush=True)
            except Exception as e:
                print(f"[server] prewarm err: {e!r}", flush=True)

        asyncio.create_task(_prewarm())
        print("[offside] LIVE MODE: TxLINE mainnet real-time + Polymarket")
    yield


app = FastAPI(title="OFFSIDE", version="0.2", lifespan=lifespan)


@app.get("/api/state")
async def state():
    return JSONResponse(engine.snapshot())


@app.get("/api/stream")
async def stream():
    q = bus.subscribe()

    async def gen():
        try:
            # немного истории новому клиенту, чтобы экран не был пустым
            for ev in list(bus.hist)[-200:]:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            while True:
                ev = await q.get()
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---------- REPLAY: один прогон на сервер, приватный для запустившего (cid) ----------
_replay_state = {"running": False, "msg": "", "fid": None, "cid": None}
_replay_task = None
REPLAY_MAX_S = 1200          # авто-стоп: забытый реплей гаснет сам через 20 мин


def _replay_cleanup():
    """убрать карточку текущего реплея из движка (фронт получит remove)"""
    if _replay_state.get("fid"):
        engine.remove_match(f"R{_replay_state['fid']}")


async def _stop_replay():
    """остановить текущий прогон и прибрать его карточку"""
    global _replay_task
    t, _replay_task = _replay_task, None
    if t and not t.done():
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass   # CancelledError — BaseException, ловим явно
    # безусловно: finally таски сбрасывает running раньше нас (гонка),
    # поэтому уборку карточки на running не завязываем
    _replay_cleanup()
    _replay_state.update(running=False, msg="stopped")


@app.get("/api/replay/list")
async def replay_list():
    try:
        import replay as rp
        return JSONResponse(await rp.list_replayable())
    except Exception as e:
        return JSONResponse({"error": repr(e)}, status_code=500)


@app.post("/api/replay/start")
async def replay_start(fid: str, speed: float = 30.0, cid: str = ""):
    global _replay_task
    import replay as rp
    if _replay_state["running"]:
        if cid and cid == _replay_state.get("cid"):
            await _stop_replay()      # тот же зритель выбрал другой матч — перезапуск
        else:
            return JSONResponse({"error": "replay busy"}, status_code=409)

    def cb(msg):
        _replay_state["msg"] = msg

    async def _run():
        _replay_state.update(running=True, msg="starting", fid=fid, cid=cid)
        try:
            await asyncio.wait_for(
                rp.run_replay(engine, fid, speed, status_cb=cb),
                timeout=REPLAY_MAX_S)
        except asyncio.TimeoutError:
            cb("авто-стоп по таймеру")
            _replay_cleanup()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            cb(f"error: {e!r}")
        finally:
            _replay_state["running"] = False

    _replay_task = asyncio.create_task(_run())
    return JSONResponse({"ok": True})


@app.post("/api/replay/stop")
async def replay_stop(cid: str = ""):
    if not _replay_state["running"]:
        return JSONResponse({"ok": True, "note": "idle"})
    await _stop_replay()
    return JSONResponse({"ok": True})


@app.get("/api/replay/status")
async def replay_status():
    return JSONResponse({k: _replay_state[k]
                         for k in ("running", "msg", "fid", "cid")})


# ---------- VERIFY: гол -> Merkle-пруф -> программа на Solana ----------
@app.get("/api/verify/candidates")
async def verify_candidates():
    try:
        import verify as vf
        return JSONResponse(await vf.candidates())
    except Exception as e:
        return JSONResponse({"error": repr(e)}, status_code=500)


@app.get("/api/verify/goals")
async def verify_goals(fid: str):
    try:
        import verify as vf
        return JSONResponse(await vf.goals_for(fid))
    except Exception as e:
        return JSONResponse({"error": repr(e)}, status_code=500)


@app.get("/api/verify/prove")
async def verify_prove(fid: str, seq: int, stat: str = "1",
                       expect: int | None = None):
    try:
        import verify as vf
        return JSONResponse(await vf.prove(fid, seq, stat, expect))
    except Exception as e:
        return JSONResponse({"error": repr(e)}, status_code=500)


# статика фронта (в самом конце, чтобы /api/* не перехватывался)
app.mount("/", StaticFiles(directory=os.path.join(ROOT, "frontend"),
                           html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
