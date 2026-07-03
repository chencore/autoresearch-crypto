from fastapi import APIRouter, WebSocket

router = APIRouter()


@router.websocket("/{topic}")
async def ws_placeholder(websocket: WebSocket, topic: str) -> None:
    await websocket.accept()
    await websocket.send_json({"status": "todo"})
    await websocket.close()
