import json
import random
import string
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI()

# rooms[room_code] = {"clients": {ws: username}, "strokes": [...]}
rooms = {}


def generate_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))


async def broadcast_to_room(room_code: str, message: dict, exclude_ws=None):
    if room_code not in rooms:
        return
    data = json.dumps(message)
    dead = []
    for ws in list(rooms[room_code]["clients"]):
        if ws == exclude_ws:
            continue
        try:
            await ws.send_text(data)
        except:
            dead.append(ws)
    for ws in dead:
        rooms[room_code]["clients"].pop(ws, None)


@app.get("/create-room")
async def create_room():
    code = generate_code()
    while code in rooms:
        code = generate_code()
    rooms[code] = {"clients": {}, "strokes": []}
    return {"room_code": code}


@app.get("/room-exists/{room_code}")
async def room_exists(room_code: str):
    return {"exists": room_code.upper() in rooms}


@app.websocket("/ws/{room_code}/{username}")
async def websocket_endpoint(ws: WebSocket, room_code: str, username: str):
    room_code = room_code.upper()
    await ws.accept()

    # Create room if needed
    if room_code not in rooms:
        rooms[room_code] = {"clients": {}, "strokes": []}

    room = rooms[room_code]
    room["clients"][ws] = username

    # Send stroke history to new joiner
    for stroke in room["strokes"]:
        try:
            await ws.send_text(json.dumps(stroke))
        except:
            break

    # Notify others someone joined
    await broadcast_to_room(room_code, {
        "type": "join",
        "username": username,
        "user_count": len(room["clients"])
    }, exclude_ws=ws)

    # Send user count to the new joiner too
    await ws.send_text(json.dumps({
        "type": "user_count",
        "user_count": len(room["clients"])
    }))

    try:
        while True:
            data = await ws.receive_text()
            message = json.loads(data)
            msg_type = message.get("type")

            if msg_type == "draw":
                room["strokes"].append(message)
                await broadcast_to_room(room_code, message, exclude_ws=ws)

            elif msg_type == "clear":
                room["strokes"].clear()
                await broadcast_to_room(room_code, message, exclude_ws=ws)

            elif msg_type == "undo":
                stroke_id = message.get("stroke_id")
                room["strokes"] = [s for s in room["strokes"] if s.get("stroke_id") != stroke_id]
                await broadcast_to_room(room_code, message, exclude_ws=ws)

            elif msg_type == "status":
                await broadcast_to_room(room_code, message, exclude_ws=ws)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Error: {e}")
    finally:
        room["clients"].pop(ws, None)
        await broadcast_to_room(room_code, {
            "type": "leave",
            "username": username,
            "user_count": len(room["clients"])
        })
        # Clean up empty rooms
        if not room["clients"]:
            rooms.pop(room_code, None)


# Serve frontend
app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)