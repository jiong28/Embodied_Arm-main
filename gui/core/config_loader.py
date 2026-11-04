import os
import json


def _find_config_dir():
    env_dir = os.environ.get("HORIZONARM_CONFIG_DIR", "").strip()
    if env_dir and os.path.isdir(env_dir):
        return env_dir
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project_root, "config")


def _load_motor_config():
    config = {
        "motor_reducer_ratios": {str(i): 1.0 for i in range(1, 7)},
        "motor_directions": {str(i): 1 for i in range(1, 7)},
    }
    try:
        config_path = os.path.join(_find_config_dir(), "motor_config.json")
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if "motor_reducer_ratios" in loaded:
                    config["motor_reducer_ratios"].update(loaded["motor_reducer_ratios"])
                if "motor_directions" in loaded:
                    config["motor_directions"].update(loaded["motor_directions"])
    except Exception:
        pass
    return config


_config_cache = None


def get_config():
    global _config_cache
    if _config_cache is None:
        _config_cache = _load_motor_config()
    return _config_cache


def get_reducer_ratios():
    cfg = get_config()
    return {int(k): v for k, v in cfg["motor_reducer_ratios"].items()}


def get_directions():
    cfg = get_config()
    return {int(k): v for k, v in cfg["motor_directions"].items()}


def reload():
    global _config_cache
    _config_cache = None
    return get_config()
