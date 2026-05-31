import logging, os
from logging.handlers import RotatingFileHandler

def setup_logging(file="logs/bot.log", level="INFO", max_bytes=10485760, backup_count=5, **kw):
    os.makedirs(os.path.dirname(file), exist_ok=True)
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not root.handlers:
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter(fmt))
        root.addHandler(ch)
        fh = RotatingFileHandler(file, maxBytes=max_bytes, backupCount=backup_count)
        fh.setFormatter(logging.Formatter(fmt))
        root.addHandler(fh)
    for lib in ("websockets", "httpx", "ccxt", "telegram", "hpack"):
        logging.getLogger(lib).setLevel(logging.WARNING)
