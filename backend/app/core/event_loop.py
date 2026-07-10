"""Windows 本地开发时供 Uvicorn 使用的事件循环工厂。"""

import asyncio


def new_event_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop()
