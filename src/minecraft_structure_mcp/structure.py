"""Read, write and edit vanilla Java structure block .nbt files."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from html import escape
import hashlib

import nbtlib
from nbtlib import Byte, Compound, Double, Int, List, String

from .schema import validate


def _vec(values, tag=Int):
    if len(values) != 3:
        raise ValueError("Expected three coordinates")
    return List[tag]([tag(v) for v in values])


def _size(size):
    if len(size) != 3 or any(type(x) is not int or x <= 0 for x in size):
        raise ValueError("size must contain three positive integers")
    if any(x > 2**31 - 1 for x in size):
        raise ValueError("NBT Int size exceeds 32-bit signed range")
    return tuple(size)


def new(size: list[int], data_version: int = 3465) -> nbtlib.File:
    size = _size(size)
    root = Compound({"DataVersion": Int(data_version), "size": _vec(size),
                     "palette": List[Compound](), "blocks": List[Compound](),
                     "entities": List[Compound]()})
    return nbtlib.File(root)


def check(root) -> tuple[int, int, int]:
    if not isinstance(root, Compound):
        raise ValueError("Root must be an NBT compound")
    size = _size([int(x) for x in root["size"]])
    palettes = root.get("palette")
    if palettes is None:
        raise ValueError("Expected vanilla structure 'palette' (multi-palette structures unsupported)")
    for block in root["blocks"]:
        if len(block["pos"]) != 3 or not all(0 <= int(c) < size[i] for i, c in enumerate(block["pos"])):
            raise ValueError("Block position is outside the structure")
        if not 0 <= int(block["state"]) < len(palettes):
            raise ValueError("Invalid block palette index")
    return size


def load(path: str | Path) -> nbtlib.File:
    result = nbtlib.load(str(path))
    check(result)
    return result


def save(root: nbtlib.File, path: str | Path) -> None:
    check(root)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    root.save(str(target), gzipped=True)


def state(name: str, properties: dict[str, str] | None = None) -> Compound:
    properties = {str(k): str(v) for k, v in (properties or {}).items()}
    validate(name, properties)
    value = Compound({"Name": String(name)})
    if properties:
        value["Properties"] = Compound({k: String(v) for k, v in sorted(properties.items())})
    return value


def _key(block_state: Compound) -> tuple:
    return (str(block_state["Name"]), tuple(sorted((str(k), str(v)) for k, v in
           block_state.get("Properties", {}).items())))


def _parse_nbt(snbt: str | None) -> Compound | None:
    if snbt is None:
        return None
    result = nbtlib.parse_nbt(snbt)
    if not isinstance(result, Compound):
        raise ValueError("NBT payload must be an SNBT compound")
    return result


def set_block(root, pos: list[int], name: str, properties: dict[str, str] | None = None,
              nbt: str | None = None) -> None:
    set_blocks(root, [{"pos": pos, "name": name, "properties": properties, "nbt": nbt}])


def set_blocks(root, changes: list[dict]) -> None:
    """Apply many block additions/removals in one pass; the last change per cell wins."""
    size = check(root)
    palette = {_key(entry): i for i, entry in enumerate(root["palette"])}
    blocks = {tuple(map(int, entry["pos"])): entry for entry in root["blocks"]}
    for change in changes:
        pos = change["pos"]
        if len(pos) != 3 or any(type(c) is not int or not 0 <= c < size[i] for i, c in enumerate(pos)):
            raise ValueError("Block coordinate outside structure")
        key = tuple(pos)
        if change.get("name") is None:
            blocks.pop(key, None)
            continue
        entry_state = state(change["name"], change.get("properties"))
        palette_key = _key(entry_state)
        if palette_key not in palette:
            palette[palette_key] = len(root["palette"])
            root["palette"].append(entry_state)
        block = Compound({"pos": _vec(pos), "state": Int(palette[palette_key])})
        if change.get("nbt") is not None:
            block["nbt"] = _parse_nbt(change["nbt"])
        blocks[key] = block
    root["blocks"] = List[Compound](blocks.values())


def remove_block(root, pos: list[int]) -> None:
    set_blocks(root, [{"pos": pos, "name": None}])


def add_entity(root, pos: list[float], nbt: str, block_pos: list[int] | None = None) -> None:
    check(root)
    if len(pos) != 3:
        raise ValueError("Entity position needs three coordinates")
    payload = _parse_nbt(nbt)
    if "id" not in payload:
        raise ValueError("Entity NBT needs an id")
    if block_pos is None:
        block_pos = [int(x // 1) for x in pos]
    root["entities"].append(Compound({"pos": _vec(pos, Double),
                                      "blockPos": _vec(block_pos), "nbt": payload}))


def inspect(root, offset: int = 0, limit: int = 100, entity_offset: int = 0) -> dict:
    size = check(root)
    if offset < 0 or entity_offset < 0 or limit < 1 or limit > 1000:
        raise ValueError("offsets >= 0 and 1 <= limit <= 1000 required")
    palette = root["palette"]
    entries = root["blocks"][offset:offset + limit]
    return {"size": size, "data_version": int(root.get("DataVersion", 0)),
            "block_count": len(root["blocks"]), "entity_count": len(root["entities"]),
            "palette": [{"name": str(p["Name"]), "properties": {k: str(v) for k, v in
                         p.get("Properties", {}).items()}} for p in palette],
            "blocks": [{"pos": list(map(int, b["pos"])), "state": int(b["state"]),
                        **({"nbt": b["nbt"].snbt()} if "nbt" in b else {})} for b in entries],
            "entities": [{"pos": list(map(float, e["pos"])),
                          "block_pos": list(map(int, e["blockPos"])), "nbt": e["nbt"].snbt()}
                         for e in root.get("entities", [])[entity_offset:entity_offset + limit]]}


def preview(root, y: int, offset_x: int = 0, offset_z: int = 0,
            width: int = 48, depth: int = 48) -> dict:
    """Top-down slice with a compact symbol legend; air and absent cells are blank."""
    sx, sy, sz = check(root)
    if not 0 <= y < sy or width < 1 or depth < 1 or width > 96 or depth > 96:
        raise ValueError("Invalid layer or viewport (max 96x96)")
    glyphs = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!@#$%^&*"
    index = {}
    legend = {}
    grid = [[" " for _ in range(width)] for _ in range(depth)]
    for block in root["blocks"]:
        x, by, z = map(int, block["pos"])
        if by != y or not offset_x <= x < offset_x + width or not offset_z <= z < offset_z + depth:
            continue
        name = str(root["palette"][int(block["state"])]["Name"])
        if name in ("minecraft:air", "minecraft:cave_air", "minecraft:void_air"):
            continue
        if name not in index:
            symbol = glyphs[len(index)] if len(index) < len(glyphs) else "?"
            index[name] = symbol
            legend[symbol] = name
        grid[z - offset_z][x - offset_x] = index[name]
    return {"layer_y": y, "origin_xz": [offset_x, offset_z],
            "rows_north_to_south": ["".join(row) for row in grid], "legend": legend,
            "axes": "columns increase east (+X); rows increase south (+Z)"}


def render_svg(root, y: int, offset_x: int = 0, offset_z: int = 0,
               width: int = 48, depth: int = 48) -> str:
    """Standalone SVG layer preview for browsers and image viewers."""
    view = preview(root, y, offset_x, offset_z, width, depth)
    colors = {symbol: "#" + hashlib.sha256(name.encode()).hexdigest()[:6]
              for symbol, name in view["legend"].items()}
    cell = 20
    h = depth * cell + 32 + 20 * len(colors)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width*cell}" height="{h}" viewBox="0 0 {width*cell} {h}">',
             '<rect width="100%" height="100%" fill="#17202a"/>']
    for z, row in enumerate(view["rows_north_to_south"]):
        for x, symbol in enumerate(row):
            if symbol in colors:
                parts.append(f'<rect x="{x*cell}" y="{z*cell}" width="19" height="19" fill="{colors[symbol]}"/>')
    parts.append(f'<text x="5" y="{depth*cell+21}" fill="white" font-size="14">Y={y}, X={offset_x}.., Z={offset_z}..</text>')
    for i, (symbol, name) in enumerate(view["legend"].items()):
        yy = depth * cell + 42 + 20*i
        parts.append(f'<rect x="5" y="{yy-12}" width="13" height="13" fill="{colors[symbol]}"/>')
        parts.append(f'<text x="24" y="{yy}" fill="white" font-size="12">{escape(name)}</text>')
    parts.append('</svg>')
    return "".join(parts)


def diff(before, after, offset: int = 0, limit: int = 1000) -> dict:
    check(before)
    check(after)
    if offset < 0 or limit < 1 or limit > 5000:
        raise ValueError("offset >= 0 and 1 <= limit <= 5000 required")
    def mapping(root):
        return {tuple(map(int, b["pos"])): {
            "name": _key(root["palette"][int(b["state"])])[0],
            "properties": dict(_key(root["palette"][int(b["state"])])[1]),
            **({"nbt": b["nbt"].snbt()} if "nbt" in b else {})} for b in root["blocks"]}
    a, b = mapping(before), mapping(after)
    changes = [{"pos": list(pos), "before": a.get(pos), "after": b.get(pos)}
               for pos in sorted(a.keys() | b.keys()) if a.get(pos) != b.get(pos)]
    entities_changed = [e.snbt() for e in before.get("entities", [])] != [e.snbt() for e in after.get("entities", [])]
    entities_after = ([{"pos": list(map(float, e["pos"])),
                        "block_pos": list(map(int, e["blockPos"])), "nbt": e["nbt"].snbt()}
                       for e in after.get("entities", [])] if entities_changed else None)
    return {"before_size": list(map(int, before["size"])),
            "after_size": list(map(int, after["size"])), "change_count": len(changes),
            "changes": changes[offset:offset + limit],
            "entities_changed": entities_changed, "entities_after": entities_after}


def patch(root, changes: list[dict]) -> None:
    set_blocks(root, [{"pos": item["pos"], **(item.get("after") or {"name": None})}
                      for item in changes])


_DIR = {"north": (0, -1), "east": (1, 0), "south": (0, 1), "west": (-1, 0)}


def _direction(value: str, quarter_turns: int, flip_x: bool, flip_z: bool) -> str:
    if value not in _DIR:
        return value
    x, z = _DIR[value]
    if flip_x: x = -x
    if flip_z: z = -z
    for _ in range(quarter_turns): x, z = -z, x
    return next(k for k, v in _DIR.items() if v == (x, z))


def _transform_state(block_state, turns, fx, fy, fz):
    result = deepcopy(block_state)
    p = result.get("Properties")
    if p is None: return result
    for key in ("facing", "horizontal_facing"):
        if key in p:
            old = str(p[key])
            p[key] = String(_direction(old, turns, fx, fz))
            if fy and old in ("up", "down"):
                p[key] = String("down" if old == "up" else "up")
    if "axis" in p and turns % 2 and str(p["axis"]) in ("x", "z"):
        p["axis"] = String("z" if str(p["axis"]) == "x" else "x")
    if "rotation" in p:
        n = int(str(p["rotation"]))
        if fx: n = (-n) % 16
        if fz: n = (8 - n) % 16
        p["rotation"] = String(str((n + 4 * turns) % 16))
    if fy:
        for key in ("half", "type", "vertical_direction"):
            if key in p:
                swaps = {"top": "bottom", "bottom": "top", "upper": "lower", "lower": "upper", "up": "down", "down": "up"}
                p[key] = String(swaps.get(str(p[key]), str(p[key])))
    if fx ^ fz:
        for key in ("hinge", "type"):
            if key in p and str(p[key]) in ("left", "right"):
                p[key] = String("right" if str(p[key]) == "left" else "left")
        if "shape" in p:
            shape = str(p["shape"])
            p["shape"] = String(shape.replace("_left", "_TEMP").replace("_right", "_left").replace("_TEMP", "_right"))
    if "shape" in p and (str(result["Name"]).endswith("_rail") or str(result["Name"]) == "minecraft:rail"):
        shape = str(p["shape"])
        if shape in ("north_south", "east_west"):
            if turns % 2:
                p["shape"] = String("east_west" if shape == "north_south" else "north_south")
        elif shape.startswith("ascending_"):
            p["shape"] = String("ascending_" + _direction(shape[10:], turns, fx, fz))
        elif "_" in shape:
            ends = shape.split("_")
            if len(ends) == 2 and all(end in _DIR for end in ends):
                transformed = {_direction(end, turns, fx, fz) for end in ends}
                for candidate in ("north_east", "north_west", "south_east", "south_west"):
                    if set(candidate.split("_")) == transformed:
                        p["shape"] = String(candidate)
                        break
    # Directional connection keys (fences, walls, redstone wire).
    connections = {k: p.pop(k) for k in list(p) if k in _DIR}
    for key, value in connections.items():
        p[_direction(key, turns, fx, fz)] = value
    return result


def transform(root, quarter_turns: int = 0, flip_x: bool = False,
              flip_y: bool = False, flip_z: bool = False):
    """Mirrors first, then rotates clockwise about Y; normalizes into positive coordinates."""
    sx, sy, sz = check(root)
    if type(quarter_turns) is not int or quarter_turns not in (0, 1, 2, 3):
        raise ValueError("quarter_turns must be 0, 1, 2 or 3")
    result = deepcopy(root)
    def point(values, continuous=False):
        x, y, z = values
        x = sx - x if flip_x and continuous else sx - 1 - x if flip_x else x
        y = sy - y if flip_y and continuous else sy - 1 - y if flip_y else y
        z = sz - z if flip_z and continuous else sz - 1 - z if flip_z else z
        dx, dz = sx, sz
        for _ in range(quarter_turns):
            x, z = (dz - z, x) if continuous else (dz - 1 - z, x)
            dx, dz = dz, dx
        return (x, y, z)
    result["size"] = _vec([sz, sy, sx] if quarter_turns % 2 else [sx, sy, sz])
    result["palette"] = List[Compound]([_transform_state(p, quarter_turns, flip_x, flip_y, flip_z)
                                       for p in root["palette"]])
    for block in result["blocks"]:
        block["pos"] = _vec(point(list(map(int, block["pos"]))))
        # Structure NBT usually omits BE world coordinates; adjust if they are present.
        payload = block.get("nbt")
        if payload and all(k in payload for k in ("x", "y", "z")):
            payload["x"], payload["y"], payload["z"] = map(Int, point([int(payload[k]) for k in ("x", "y", "z")]))
    for entity in result.get("entities", []):
        entity["pos"] = _vec(point(list(map(float, entity["pos"])), True), Double)
        if "blockPos" in entity:
            entity["blockPos"] = _vec(point(list(map(int, entity["blockPos"]))))
        nbt = entity.get("nbt")
        if nbt:
            if "Rotation" in nbt and len(nbt["Rotation"]) >= 2:
                yaw = float(nbt["Rotation"][0])
                if flip_x: yaw = -yaw
                if flip_z: yaw = 180 - yaw
                nbt["Rotation"][0] = type(nbt["Rotation"][0])((yaw + 90 * quarter_turns) % 360)
                if flip_y: nbt["Rotation"][1] = type(nbt["Rotation"][1])(-float(nbt["Rotation"][1]))
            if all(k in nbt for k in ("TileX", "TileY", "TileZ")):
                nbt["TileX"], nbt["TileY"], nbt["TileZ"] = map(Int, point([int(nbt[k]) for k in ("TileX", "TileY", "TileZ")]))
            if "Facing" in nbt and int(nbt["Facing"]) in (2, 3, 4, 5):
                dirs = {2: "north", 3: "south", 4: "west", 5: "east"}
                inverse = {v: k for k, v in dirs.items()}
                nbt["Facing"] = type(nbt["Facing"])(inverse[_direction(dirs[int(nbt["Facing"])], quarter_turns, flip_x, flip_z)])
    check(result)
    return result
