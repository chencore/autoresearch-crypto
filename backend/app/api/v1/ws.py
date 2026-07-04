from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.evolution_manager import evolution_manager

router = APIRouter()


@router.websocket("/evolve/{run_id}")
async def evolve_ws(websocket: WebSocket, run_id: str) -> None:
    await websocket.accept()
    run = evolution_manager.get_run(run_id)
    if run is None:
        await websocket.send_json(
            {
                "type": "error",
                "code": "not_found",
                "message": f"run not found: {run_id}",
            }
        )
        await websocket.close()
        return

    for event in list(run.event_buffer):
        try:
            await websocket.send_json(event)
        except Exception:
            break

    evolution_manager.subscribe(run_id, websocket)
    try:
        while True:
            if run.status in ("completed", "failed", "stopped"):
                break
            try:
                msg = await asyncio.wait_for(websocket.receive_json(), timeout=1.0)
                if isinstance(msg, dict) and msg.get("action") == "stop":
                    evolution_manager.request_stop(run_id)
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                break
    finally:
        evolution_manager.unsubscribe(run_id, websocket)
        try:
            await websocket.close()
        except Exception:
            pass
