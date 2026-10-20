import asyncio
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class Client:
    websocket: object
    user_id: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=64))
    sender: object = None


class ConnectionManager:
    def __init__(self):
        self.clients = {}

    def add(self, websocket, uid):
        cid = str(uuid4())
        client = Client(websocket, uid)
        self.clients[cid] = client
        client.sender = asyncio.create_task(self._send(cid, client))
        return cid

    async def _send(self, cid, client):
        try:
            while True:
                item = await client.queue.get()
                await asyncio.wait_for(client.websocket.send_json(item), timeout=10)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.clients.pop(cid, None)
            try:
                await client.websocket.close(code=1013)
            except Exception:
                pass

    async def remove(self, cid):
        client = self.clients.pop(cid, None)
        if client:
            client.sender.cancel()
            try:
                await client.sender
            except asyncio.CancelledError:
                pass

    async def send(self, cid, payload):
        client = self.clients.get(cid)
        if client:
            try:
                client.queue.put_nowait(payload)
            except asyncio.QueueFull:
                await self.remove(cid)
                await client.websocket.close(code=1013)

    async def broadcast(self, payload, uid=None):
        for cid, client in list(self.clients.items()):
            if uid is None or client.user_id == uid:
                await self.send(cid, payload)

    async def send_to_users(self, payload, users):
        for cid, client in list(self.clients.items()):
            if client.user_id in users:
                await self.send(cid, payload)

    async def disconnect_user(self, uid):
        for cid, client in list(self.clients.items()):
            if client.user_id == uid:
                await self.remove(cid)
                try:
                    await asyncio.wait_for(client.websocket.close(code=1008, reason='Аккаунт удалён администратором.'), timeout=5)
                except Exception:
                    pass

    @property
    def online(self):
        return len({c.user_id for c in self.clients.values()})

    async def close(self):
        for cid in list(self.clients):
            await self.remove(cid)
