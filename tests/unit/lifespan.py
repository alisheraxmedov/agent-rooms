import asyncio


async def run_lifespan(app: object) -> None:
    inbox: asyncio.Queue[dict[str, str]] = asyncio.Queue()
    outbox: asyncio.Queue[dict[str, str]] = asyncio.Queue()
    task = asyncio.create_task(app({"type": "lifespan"}, inbox.get, outbox.put))  # type: ignore[operator]
    await inbox.put({"type": "lifespan.startup"})
    assert (await asyncio.wait_for(outbox.get(), 5))["type"] == "lifespan.startup.complete"
    await inbox.put({"type": "lifespan.shutdown"})
    assert (await asyncio.wait_for(outbox.get(), 5))["type"] == "lifespan.shutdown.complete"
    await asyncio.wait_for(task, 5)
