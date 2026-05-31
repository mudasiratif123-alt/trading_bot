import asyncio
import signal
import logging
from bot import SMCBot

logger = logging.getLogger(__name__)

async def main():
    bot = SMCBot()
    loop = asyncio.get_running_loop()

    def shutdown():
        logger.info("Shutdown signal received.")
        asyncio.create_task(bot.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown)
        except NotImplementedError:
            pass

    try:
        await bot.start()
    except KeyboardInterrupt:
        await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
