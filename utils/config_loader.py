import os, yaml

def load_config(path="config/config.yaml") -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    cfg["telegram"]["bot_token"] = os.getenv("TELEGRAM_TOKEN", cfg["telegram"]["bot_token"])
    cfg["telegram"]["chat_id"]   = os.getenv("TELEGRAM_CHAT_ID", cfg["telegram"]["chat_id"])
    return cfg
