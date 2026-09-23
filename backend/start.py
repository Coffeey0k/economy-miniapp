"""
Точка запуска для деплоя на Render.

Запускает ОДНОВРЕМЕННО, в одном процессе:
1. Самого бота (long polling, как раньше через run.sh) — bot.main()
2. Backend-API для мини-аппа (main:app) — через встроенный сервер uvicorn

Оба работают с одним и тем же файлом paradise.db на диске — поэтому
данные обновляются в реальном времени в обе стороны: бот меняет базу,
мини-апп сразу это видит, и наоборот.

Render запускает ровно один процесс на сервис — поэтому бот и API нельзя
запускать отдельными командами, их нужно объединить в один, что этот
файл и делает.
"""

import asyncio
import os

import uvicorn

import bot as bot_module          # bot.py — твой существующий бот
from main import app as api_app   # main.py — backend для мини-аппа


async def run_api():
    port = int(os.environ.get("PORT", 8000))
    config = uvicorn.Config(api_app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    await asyncio.gather(
        bot_module.main(),  # запускает бота (long polling + фоновые задачи)
        run_api(),          # запускает API мини-аппа
    )


if __name__ == "__main__":
    asyncio.run(main())
