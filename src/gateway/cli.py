from __future__ import annotations

import asyncio
import json
import time


async def request_gateway(socket_path, request: dict) -> dict:
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path), limit=4 * 1024 * 1024)
    except OSError as exc:
        raise RuntimeError("Workspace gateway is unavailable. Start Amber for this workspace first.") from exc
    try:
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        result = json.loads(await asyncio.wait_for(reader.readline(), timeout=10))
        if result.get("error"):
            raise RuntimeError(result["error"])
        return result
    finally:
        writer.close()
        await writer.wait_closed()


async def run_gateway(args, socket_path) -> int:
    if not 0 < args.timeout <= 86400:
        raise RuntimeError("--timeout must be between 0 and 86400 seconds.")
    session = args.session
    seen = 0
    if args.gateway_command == "send":
        if session:
            previous = await request_gateway(socket_path, {"action": "events", "session": session})
            seen = len(previous["events"])
        result = await request_gateway(socket_path, {
            "action": "send", "sender": args.sender, "session": session,
            "messages": args.message, "interval": args.interval,
            "typing_seconds": args.typing_seconds, "reply_to": args.reply_to,
        })
        session = result["session"]
        print(json.dumps(result), flush=True)
        if args.wait == "none":
            return 0
    deadline = time.monotonic() + args.timeout
    received_count = 0
    latest_message_id = None
    while True:
        result = await request_gateway(socket_path, {"action": "events", "session": session})
        fresh = result["events"][seen:]
        seen += len(fresh)
        for row in fresh:
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if row["event"] == "received":
                received_count += 1
                latest_message_id = row["id"]
        if args.gateway_command == "events" and not args.follow:
            return 0
        if any(row["event"] == "error" for row in fresh):
            return 1
        target = "task_complete" if args.wait == "task" else "turn_complete"
        all_received = args.gateway_command == "events" or received_count >= len(args.message)
        if all_received and any(row["event"] == target and (
            target == "task_complete" or args.gateway_command == "events" or row.get("trigger_message_id") == latest_message_id
        ) for row in fresh):
            return 0
        if time.monotonic() >= deadline:
            print(json.dumps({"session": session, "status": "timeout", "work_may_continue": True}), flush=True)
            return 2
        await asyncio.sleep(0.25)
