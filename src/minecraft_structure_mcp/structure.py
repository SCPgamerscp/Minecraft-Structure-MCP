"""Read, write and edit vanilla Java structure block .nbt files."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from html import escape
import hashlib
from importlib.resources import files
import json
import os
import tempfile

import nbtlib
from nbtlib import Compound, Double, Int, List, String

from .schema import blocks as block_schema, validate


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


def _palettes(root) -> list:
    """Vanilla structures use either palette or alternate palettes, never both."""
    if "palette" in root and "palettes" in root:
        raise ValueError("Structure contains both palette and palettes")
    if "palette" in root:
        return [root["palette"]]
    if "palettes" in root and root["palettes"]:
        return list(root["palettes"])
    raise ValueError("Expected vanilla structure palette or nonempty palettes")


def set_palette_count(root, count: int) -> None:
    """Change variant count while retaining each existing block's palette index."""
    check(root)
    if type(count) is not int or count < 1 or count > 64:
        raise ValueError("palette_count must be between 1 and 64")
    current = _palettes(root)
    if count == 1:
        root["palette"] = List[Compound](deepcopy(current[0]))
        root.pop("palettes", None)
    else:
        root["palettes"] = List[List[Compound]]([
            List[Compound](deepcopy(current[min(i, len(current) - 1)]))
            for i in range(count)
        ])
        root.pop("palette", None)


def check(root) -> tuple[int, int, int]:
    if not isinstance(root, Compound):
        raise ValueError("Root must be an NBT compound")
    size = _size([int(x) for x in root["size"]])
    palettes = _palettes(root)
    length = len(palettes[0])
    if any(len(palette) != length for palette in palettes):
        raise ValueError("All palette variants must have the same number of states")
    for block in root["blocks"]:
        if len(block["pos"]) != 3 or not all(0 <= int(c) < size[i] for i, c in enumerate(block["pos"])):
            raise ValueError("Block position is outside the structure")
        if not 0 <= int(block["state"]) < length:
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
    # A failed write must not truncate an existing structure.
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(fd)
    try:
        root.save(temporary, gzipped=True)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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


def _state_json(block_state: Compound) -> dict:
    name, properties = _key(block_state)
    return {"name": name, "properties": dict(properties)}


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
    palettes = _palettes(root)
    palette = {tuple(_key(variant[i]) for variant in palettes): i
               for i in range(len(palettes[0]))}
    blocks = {tuple(map(int, entry["pos"])): entry for entry in root["blocks"]}
    for change in changes:
        pos = change["pos"]
        if len(pos) != 3 or any(type(c) is not int or not 0 <= c < size[i] for i, c in enumerate(pos)):
            raise ValueError("Block coordinate outside structure")
        key = tuple(pos)
        if change.get("name") is None:
            blocks.pop(key, None)
            continue
        primary = {"name": change["name"], "properties": change.get("properties")}
        variants = change.get("variants")
        if variants is not None:
            if len(variants) != len(palettes):
                raise ValueError("Block variant count does not match structure palette_count")
            if _key(state(**variants[0])) != _key(state(**primary)):
                raise ValueError("The first variant must match name and properties")
        entries = [state(**item) for item in (variants or [primary] * len(palettes))]
        palette_key = tuple(_key(entry) for entry in entries)
        if palette_key not in palette:
            palette[palette_key] = len(palettes[0])
            for variant, entry in zip(palettes, entries):
                variant.append(entry)
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


def inspect(root, offset: int = 0, limit: int = 100, entity_offset: int = 0,
            palette_index: int = 0) -> dict:
    size = check(root)
    if offset < 0 or entity_offset < 0 or limit < 1 or limit > 1000:
        raise ValueError("offsets >= 0 and 1 <= limit <= 1000 required")
    palettes = _palettes(root)
    if not 0 <= palette_index < len(palettes):
        raise ValueError("palette_index outside available variants")
    palette = palettes[palette_index]
    entries = root["blocks"][offset:offset + limit]
    return {"size": size, "data_version": int(root.get("DataVersion", 0)),
            "palette_count": len(palettes), "palette_index": palette_index,
            "block_count": len(root["blocks"]), "entity_count": len(root["entities"]),
            "palette": [{"name": str(p["Name"]), "properties": {k: str(v) for k, v in
                         p.get("Properties", {}).items()}} for p in palette],
            "blocks": [{"pos": list(map(int, b["pos"])), "state": int(b["state"]),
                        **({"nbt": b["nbt"].snbt()} if "nbt" in b else {})} for b in entries],
            "entities": [{"pos": list(map(float, e["pos"])),
                          "block_pos": list(map(int, e["blockPos"])), "nbt": e["nbt"].snbt()}
                         for e in root.get("entities", [])[entity_offset:entity_offset + limit]]}


def preview(root, y: int, offset_x: int = 0, offset_z: int = 0,
            width: int = 48, depth: int = 48, palette_index: int = 0) -> dict:
    """Top-down slice with a compact symbol legend; air and absent cells are blank."""
    sx, sy, sz = check(root)
    palettes = _palettes(root)
    if not 0 <= palette_index < len(palettes):
        raise ValueError("palette_index outside available variants")
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
        name = str(palettes[palette_index][int(block["state"])]["Name"])
        if name in ("minecraft:air", "minecraft:cave_air", "minecraft:void_air"):
            continue
        if name not in index:
            symbol = glyphs[len(index)] if len(index) < len(glyphs) else "?"
            index[name] = symbol
            legend[symbol] = name
        grid[z - offset_z][x - offset_x] = index[name]
    return {"layer_y": y, "palette_index": palette_index, "palette_count": len(palettes),
            "origin_xz": [offset_x, offset_z],
            "rows_north_to_south": ["".join(row) for row in grid], "legend": legend,
            "axes": "columns increase east (+X); rows increase south (+Z)"}


def render_svg(root, y: int, offset_x: int = 0, offset_z: int = 0,
               width: int = 48, depth: int = 48, palette_index: int = 0) -> str:
    """Standalone SVG layer preview for browsers and image viewers."""
    view = preview(root, y, offset_x, offset_z, width, depth, palette_index)
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
    parts.append(f'<text x="5" y="{depth*cell+21}" fill="white" font-size="14">Y={y}, variant={palette_index}, X={offset_x}.., Z={offset_z}..</text>')
    for i, (symbol, name) in enumerate(view["legend"].items()):
        yy = depth * cell + 42 + 20*i
        parts.append(f'<rect x="5" y="{yy-12}" width="13" height="13" fill="{colors[symbol]}"/>')
        parts.append(f'<text x="24" y="{yy}" fill="white" font-size="12">{escape(name)}</text>')
    parts.append('</svg>')
    return "".join(parts)


def render_3d_html(root, offset_x: int = 0, offset_y: int = 0, offset_z: int = 0,
                   width: int = 48, height: int = 48, depth: int = 48,
                   palette_index: int = 0) -> str:
    """Offline interactive 3D viewport; coordinates and block IDs remain inspectable."""
    size = check(root)
    palettes = _palettes(root)
    if not 0 <= palette_index < len(palettes):
        raise ValueError("palette_index outside available variants")
    if any(type(v) is not int or v < 0 for v in (offset_x, offset_y, offset_z)):
        raise ValueError("Offsets must be nonnegative integers")
    if any(type(v) is not int or not 1 <= v <= 96 for v in (width, height, depth)):
        raise ValueError("Viewport dimensions must each be between 1 and 96")
    origin = (offset_x, offset_y, offset_z)
    span = (width, height, depth)
    names = [str(p["Name"]) for p in palettes[palette_index]]
    air = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
    blocks = []
    for b in root["blocks"]:
        pos = tuple(map(int, b["pos"]))
        state_id = int(b["state"])
        if (names[state_id] not in air and
                all(origin[i] <= pos[i] < origin[i] + span[i] for i in range(3))):
            blocks.append([*(pos[i] - origin[i] for i in range(3)), state_id])
    if len(blocks) > 120_000:
        raise ValueError("3D viewport contains over 120000 blocks; reduce width/height/depth")
    entities = []
    for entry in root.get("entities", []):
        pos = tuple(map(float, entry["pos"]))
        if all(origin[i] <= pos[i] < origin[i] + span[i] for i in range(3)):
            entities.append([*(pos[i] - origin[i] for i in range(3)),
                             str(entry.get("nbt", {}).get("id", "unknown"))])
    payload = {"size": list(size), "origin": origin, "span": span,
               "paletteIndex": palette_index, "paletteCount": len(palettes),
               "names": names, "blocks": blocks, "entities": entities}
    # Escape '<' so untrusted NBT names cannot terminate the script element.
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")
    template = files("minecraft_structure_mcp").joinpath("data/preview_3d.html").read_text(encoding="utf-8")
    return template.replace("/*STRUCTURE_DATA*/", data)


def split(root, chunk_size: list[int] | tuple[int, int, int] = (48, 48, 48)) -> list[tuple[tuple[int, int, int], nbtlib.File]]:
    """Partition an oversized template into placeable-size NBT templates."""
    size = check(root)
    tile = _size(chunk_size)
    if any(v > 48 for v in tile):
        raise ValueError("Each chunk dimension must be at most 48")
    capacity = 1
    for i in range(3):
        capacity *= (size[i] + tile[i] - 1) // tile[i]
    if capacity > 10_000:
        raise ValueError("Structure would require more than 10000 pieces")
    groups: dict[tuple[int, int, int], dict[str, list]] = {}
    def get_group(key):
        return groups.setdefault(key, {"blocks": [], "entities": []})
    for block in root["blocks"]:
        pos = tuple(map(int, block["pos"]))
        key = tuple(pos[i] // tile[i] for i in range(3))
        get_group(key)["blocks"].append(block)
    for entity in root.get("entities", []):
        block_pos = tuple(map(int, entity.get("blockPos", [int(v // 1) for v in entity["pos"]])))
        anchor = tuple(min(size[i] - 1, max(0, block_pos[i])) for i in range(3))
        key = tuple(anchor[i] // tile[i] for i in range(3))
        get_group(key)["entities"].append(entity)
    result = []
    for index, group in sorted(groups.items()):
        origin = tuple(index[i] * tile[i] for i in range(3))
        length = tuple(min(tile[i], size[i] - origin[i]) for i in range(3))
        header = {key: deepcopy(value) for key, value in root.items()
                  if key not in ("size", "blocks", "entities")}
        header["size"] = _vec(length)
        header["blocks"] = List[Compound]()
        header["entities"] = List[Compound]()
        chunk = nbtlib.File(Compound(header), root_name=root.root_name)
        for original in group["blocks"]:
            entry = deepcopy(original)
            entry["pos"] = _vec([int(entry["pos"][i]) - origin[i] for i in range(3)])
            payload = entry.get("nbt")
            if payload and all(axis in payload for axis in ("x", "y", "z")):
                for i, axis in enumerate(("x", "y", "z")):
                    payload[axis] = type(payload[axis])(int(payload[axis]) - origin[i])
            chunk["blocks"].append(entry)
        for original in group["entities"]:
            entry = deepcopy(original)
            entry["pos"] = _vec([float(entry["pos"][i]) - origin[i] for i in range(3)], Double)
            if "blockPos" in entry:
                entry["blockPos"] = _vec([int(entry["blockPos"][i]) - origin[i] for i in range(3)])
            payload = entry.get("nbt")
            if payload:
                if "Pos" in payload and len(payload["Pos"]) == 3:
                    for i in range(3):
                        payload["Pos"][i] = type(payload["Pos"][i])(float(payload["Pos"][i]) - origin[i])
                if all(axis in payload for axis in ("TileX", "TileY", "TileZ")):
                    for i, axis in enumerate(("TileX", "TileY", "TileZ")):
                        payload[axis] = type(payload[axis])(int(payload[axis]) - origin[i])
            chunk["entities"].append(entry)
        check(chunk)
        result.append((origin, chunk))
    return result


def diff(before, after, offset: int = 0, limit: int = 1000) -> dict:
    check(before)
    check(after)
    if offset < 0 or limit < 1 or limit > 5000:
        raise ValueError("offset >= 0 and 1 <= limit <= 5000 required")
    def mapping(root):
        palettes = _palettes(root)
        def entry(block):
            variants = [_state_json(p[int(block["state"])]) for p in palettes]
            return {**variants[0],
                    **({"variants": variants} if len(variants) > 1 else {}),
                    **({"nbt": block["nbt"].snbt()} if "nbt" in block else {})}
        return {tuple(map(int, block["pos"])): entry(block) for block in root["blocks"]}
    a, b = mapping(before), mapping(after)
    changes = [{"pos": list(pos), "before": a.get(pos), "after": b.get(pos)}
               for pos in sorted(a.keys() | b.keys()) if a.get(pos) != b.get(pos)]
    entities_changed = [e.snbt() for e in before.get("entities", [])] != [e.snbt() for e in after.get("entities", [])]
    entities_after = ([{"pos": list(map(float, e["pos"])),
                        "block_pos": list(map(int, e["blockPos"])), "nbt": e["nbt"].snbt()}
                       for e in after.get("entities", [])] if entities_changed else None)
    variants_changed = (len(_palettes(before)) != len(_palettes(after)) or
                        [[_key(state) for state in palette] for palette in _palettes(before)[1:]] !=
                        [[_key(state) for state in palette] for palette in _palettes(after)[1:]])
    return {"before_size": list(map(int, before["size"])),
            "after_size": list(map(int, after["size"])),
            "before_palette_count": len(_palettes(before)),
            "after_palette_count": len(_palettes(after)),
            "change_count": len(changes),
            "changes": changes[offset:offset + limit],
            "entities_changed": entities_changed, "entities_after": entities_after,
            "alternate_palettes_changed": variants_changed}


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
                opposite = "down" if old == "up" else "up"
                allowed = block_schema().get(str(result["Name"]), {}).get(key, [])
                p[key] = String(opposite if opposite in allowed else old)
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
        for key in ("face", "attachment"):
            if key in p:
                p[key] = String({"floor": "ceiling", "ceiling": "floor"}.get(str(p[key]), str(p[key])))
        if "hanging" in p:
            p["hanging"] = String("false" if str(p["hanging"]) == "true" else "true")
        if "up" in p and "down" in p:
            p["up"], p["down"] = p["down"], p["up"]
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
    if "orientation" in p:
        parts = str(p["orientation"]).split("_")
        if len(parts) == 2:
            first, second = (_direction(v, turns, fx, fz) for v in parts)
            if fy:
                first = {"up": "down", "down": "up"}.get(first, first)
                second = {"up": "down", "down": "up"}.get(second, second)
            candidate = first + "_" + second
            # Jigsaw blocks have no horizontal_down state; retain a valid
            # orientation where vertical reflection cannot be expressed.
            allowed = block_schema().get(str(result["Name"]), {}).get("orientation", [])
            if candidate not in allowed and fy:
                candidate = first + "_up"
            if candidate in allowed:
                p["orientation"] = String(candidate)
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
    for original, target in zip(_palettes(root), _palettes(result)):
        target[:] = [_transform_state(p, quarter_turns, flip_x, flip_y, flip_z) for p in original]
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
            if "Pos" in nbt and len(nbt["Pos"]) == 3:
                converted = point(list(map(float, nbt["Pos"])), True)
                for i in range(3):
                    nbt["Pos"][i] = type(nbt["Pos"][i])(converted[i])
            if "Rotation" in nbt and len(nbt["Rotation"]) >= 2:
                yaw = float(nbt["Rotation"][0])
                if flip_x: yaw = -yaw
                if flip_z: yaw = 180 - yaw
                nbt["Rotation"][0] = type(nbt["Rotation"][0])((yaw + 90 * quarter_turns) % 360)
                if flip_y: nbt["Rotation"][1] = type(nbt["Rotation"][1])(-float(nbt["Rotation"][1]))
            if all(k in nbt for k in ("TileX", "TileY", "TileZ")):
                nbt["TileX"], nbt["TileY"], nbt["TileZ"] = map(Int, point([int(nbt[k]) for k in ("TileX", "TileY", "TileZ")]))
            if "Facing" in nbt and int(nbt["Facing"]) in (0, 1, 2, 3):
                dirs = {0: "south", 1: "west", 2: "north", 3: "east"}
                inverse = {v: k for k, v in dirs.items()}
                nbt["Facing"] = type(nbt["Facing"])(inverse[_direction(dirs[int(nbt["Facing"])], quarter_turns, flip_x, flip_z)])
    check(result)
    return result
