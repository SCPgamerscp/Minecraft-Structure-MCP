"""Bundled Java Edition 1.20.1 block registry and block-state definitions."""
import json
from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=1)
def blocks() -> dict[str, dict[str, list[str]]]:
    path = files("minecraft_structure_mcp").joinpath("data/blocks_1_20_1.json")
    return json.loads(path.read_text(encoding="utf-8"))


def validate(name: str, properties: dict[str, str] | None = None) -> None:
    if name not in blocks():
        raise ValueError(f"Unknown Java 1.20.1 block ID: {name}")
    spec = blocks()[name]
    for key, value in (properties or {}).items():
        if key not in spec:
            raise ValueError(f"Unknown state {key!r} for {name}")
        if str(value) not in spec[key]:
            raise ValueError(f"Invalid {name}[{key}={value}]; allowed: {spec[key]}")


def lookup(query: str = "", limit: int = 30) -> dict:
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    matches = [name for name in blocks() if query.lower() in name.lower()]
    return {"version": "1.20.1", "total": len(matches),
            "blocks": {name: blocks()[name] for name in matches[:limit]}}
