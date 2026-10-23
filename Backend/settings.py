"""User settings saved in data/settings.json (defaults come from config)."""
import json

from backend import config


def load() -> dict:
    """Load settings, filling missing keys with defaults."""
    cur = dict(config.DEFAULT_SETTINGS)
    try:
        cur.update(json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cur


def save(new: dict) -> dict:
    """Save only known keys. Returns the full settings."""
    cur = load()
    cur.update({k: v for k, v in new.items() if k in config.DEFAULT_SETTINGS})
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.SETTINGS_PATH.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    return cur
