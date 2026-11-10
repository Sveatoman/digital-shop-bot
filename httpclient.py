"""Общий aiohttp-сеанс: создаётся лениво, закрывается при остановке бота.

Один сеанс на всё приложение вместо нового ClientSession на каждый запрос.
"""
from typing import Optional

import aiohttp

_session: Optional[aiohttp.ClientSession] = None


def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            connector=aiohttp.TCPConnector(limit=10),
        )
    return _session


async def close_session() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()
    _session = None
