import json
import random
import string
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import uvicorn

app = FastAPI()

# rooms[room_code] = {"clients": {ws: username}, "strokes": [], "chat": []}
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


async def send_to_user(room_code: str, target_username: str, message: dict):
    """Send a message to a specific user in a room (for WebRTC signaling)."""
    if room_code not in rooms:
        return
    data = json.dumps(message)
    for ws, uname in rooms[room_code]["clients"].items():
        if uname == target_username:
            try:
                await ws.send_text(data)
            except:
                pass
            break


@app.get("/create-room")
async def create_room():
    code = generate_code()
    while code in rooms:
        code = generate_code()
    rooms[code] = {"clients": {}, "strokes": [], "chat": []}
    return {"room_code": code}


@app.get("/room-exists/{room_code}")
async def room_exists(room_code: str):
    return {"exists": room_code.upper() in rooms}


@app.websocket("/ws/{room_code}/{username}")
async def websocket_endpoint(ws: WebSocket, room_code: str, username: str):
    room_code = room_code.upper()
    await ws.accept()

    if room_code not in rooms:
        rooms[room_code] = {"clients": {}, "strokes": [], "chat": []}

    room = rooms[room_code]
    room["clients"][ws] = username

    # Send stroke history to new joiner
    for stroke in room["strokes"]:
        try:
            await ws.send_text(json.dumps(stroke))
        except:
            break

    # Send chat history to new joiner
    for msg in room["chat"]:
        try:
            await ws.send_text(json.dumps(msg))
        except:
            break

    # Notify others
    await broadcast_to_room(room_code, {
        "type": "join",
        "username": username,
        "user_count": len(room["clients"]),
        "users": list(room["clients"].values())
    }, exclude_ws=ws)

    # Send full state to new joiner
    await ws.send_text(json.dumps({
        "type": "user_count",
        "user_count": len(room["clients"]),
        "users": list(room["clients"].values())
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

            elif msg_type == "chat":
                # Store and broadcast chat messages
                chat_msg = {
                    "type": "chat",
                    "username": username,
                    "text": message.get("text", ""),
                    "time": message.get("time", "")
                }
                room["chat"].append(chat_msg)
                # Keep only last 100 messages
                if len(room["chat"]) > 100:
                    room["chat"] = room["chat"][-100:]
                await broadcast_to_room(room_code, chat_msg, exclude_ws=ws)
                # Echo back to sender too
                await ws.send_text(json.dumps({**chat_msg, "own": True}))

            # ── WebRTC Signaling ──
            # These messages are forwarded to a specific target user
            elif msg_type in ["webrtc-offer", "webrtc-answer", "webrtc-ice"]:
                target = message.get("target")
                message["from"] = username
                await send_to_user(room_code, target, message)

            elif msg_type == "voice-join":
                # Tell everyone else this user turned on voice
                await broadcast_to_room(room_code, {
                    "type": "voice-join",
                    "username": username
                }, exclude_ws=ws)

            elif msg_type == "voice-leave":
                await broadcast_to_room(room_code, {
                    "type": "voice-leave",
                    "username": username
                }, exclude_ws=ws)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Error: {e}")
    finally:
        room["clients"].pop(ws, None)
        await broadcast_to_room(room_code, {
            "type": "leave",
            "username": username,
            "user_count": len(room["clients"]),
            "users": list(room["clients"].values())
        })
        if not room["clients"]:
            rooms.pop(room_code, None)


app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)