"""
Proceso independiente del scheduler — corre UNA sola instancia como servicio Docker separado.
No importar desde server.py.
"""
import asyncio
import logging
import sys
from dotenv import load_dotenv
import os

load_dotenv()

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

from scheduler import scheduler_loop

if __name__ == "__main__":
    logging.info("[scheduler_main] Iniciando scheduler como proceso independiente...")
    asyncio.run(scheduler_loop())
