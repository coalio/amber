from __future__ import annotations

import asyncio
import fcntl
import json
from pathlib import Path

from src.receiver.gateway import GatewayReceiver
from src.state.gateway import GatewayStore


class GatewayServer:
    def __init__(self, socket_path: Path, receiver: GatewayReceiver, store: GatewayStore) -> None:
        self.socket_path = socket_path
        self.receiver = receiver
        self.store = store
        self._server = None
        self._lock_file = None

    async def start(self) -> None:
        # an exclusive lease makes stale socket cleanup safe across process restarts
        self._lock_file = self.socket_path.with_suffix(".lock").open("a")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock_file.close()
            raise RuntimeError("Another runtime owns this workspace gateway.") from exc
        self.socket_path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._handle, path=self.socket_path, limit=65536)
        self.socket_path.chmod(0o600)

    async def close(self) -> None:
        await self.receiver.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self.socket_path.unlink(missing_ok=True)
        if self._lock_file is not None:
            self._lock_file.close()

    async def _handle(self, reader, writer) -> None:
        # frame one bounded request and always close the local connection
        try:
            request = json.loads(await asyncio.wait_for(reader.readline(), timeout=5))
            response = await self._request(request)
        except (ValueError, TypeError, KeyError, RuntimeError, asyncio.TimeoutError) as exc:
            response = {"error": str(exc)}
        writer.write((json.dumps(response) + "\n").encode())
        try:
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _request(self, request: dict) -> dict:
        # keep wire operations separate from ingress authorization and normalization
        if not isinstance(request, dict):
            raise RuntimeError("Gateway request must be an object.")
        if request.get("action") == "events":
            return {"events": self.store.read(str(request["session"]))}
        if request.get("action") != "send":
            raise RuntimeError("Unknown gateway operation.")
        return await self.receiver.submit(request)
