import json
import logging
import os

log = logging.getLogger(__name__)


def config_path(path: str) -> str:
    """Return path if it exists, otherwise its committed .example.json fallback."""
    if os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    return f"{root}.example{ext}"


def load_json(path: str, default):
    """Load JSON from path, or return default if the file is missing or unreadable."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Couldn't read %s, using defaults: %s", path, e)
        return default


def save_json(path: str, data):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    # write to a temp file first so a crash mid-write can't leave a half-written file behind
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, path)
