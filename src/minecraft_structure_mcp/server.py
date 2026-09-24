"""MCP entry point. File operations are restricted to STRUCTURE_WORKSPACE."""
import argparse
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import schema, structure


ROOT = Path(os.environ.get("STRUCTURE_WORKSPACE", os.getcwd())).resolve()
mcp = FastMCP("Minecraft Java Structure NBT", host="127.0.0.1", port=8000,
              max_request_body_size=16 * 1024 * 1024,
              instructions="Java 1.20.1 vanilla structure .nbt. Consult minecraft://blocks/1.20.1/schema. Coordinates are relative to the structure origin.")


def _path(path: str) -> Path:
    """Resolve an NBT path inside the configured structure workspace."""
    target = (ROOT / path).resolve()
    if not target.is_relative_to(ROOT) or target.suffix.lower() != ".nbt":
        raise ValueError("Path must be a .nbt file within STRUCTURE_WORKSPACE")
    return target


def _svg_path(path: str) -> Path:
    """Resolve an SVG output path inside the configured structure workspace."""
    target = (ROOT / path).resolve()
    if not target.is_relative_to(ROOT) or target.suffix.lower() != ".svg":
        raise ValueError("Output must be an .svg file within STRUCTURE_WORKSPACE")
    return target


@mcp.resource("minecraft://blocks/1.20.1/schema", mime_type="application/json")
def block_schema() -> str:
    """Complete bundled vanilla Java 1.20.1 block IDs and allowed state values."""
    return json.dumps(schema.blocks(), ensure_ascii=False)


@mcp.resource("minecraft://blocks/1.20.1/{block_id}", mime_type="application/json")
def block_definition(block_id: str) -> str:
    """State keys and allowed values for a single minecraft: block ID."""
    name = block_id if ":" in block_id else f"minecraft:{block_id}"
    schema.validate(name)
    return json.dumps({name: schema.blocks()[name]})


@mcp.tool()
def find_blocks(query: str = "", limit: int = 30) -> dict:
    """Search Java 1.20.1 block IDs and their allowed Block States."""
    return schema.lookup(query, limit)


@mcp.tool()
def analyze_structure(path: str, offset: int = 0, limit: int = 100,
                      entity_offset: int = 0, palette_index: int = 0) -> dict:
    """Read vanilla compressed .nbt, palette, blocks, block-entity SNBT and entity SNBT."""
    return structure.inspect(structure.load(_path(path)), offset, limit, entity_offset, palette_index)


@mcp.tool()
def create_structure(path: str, size: list[int], blocks: list[dict] | None = None,
                     entities: list[dict] | None = None, data_version: int = 3465) -> dict:
    """Create a Java 1.20.1 structure NBT. Blocks: {pos,name,properties?,nbt?}; entities: {pos,nbt,block_pos?}."""
    root = structure.new(size, data_version)
    structure.set_blocks(root, blocks or [])
    for entity in entities or []:
        structure.add_entity(root, entity["pos"], entity["nbt"], entity.get("block_pos"))
    structure.save(root, _path(path))
    return {"path": path, "size": size, "blocks": len(root["blocks"]), "entities": len(root["entities"])}


@mcp.tool()
def edit_structure(source: str, output: str, changes: list[dict],
                   add_entities: list[dict] | None = None,
                   replace_entities: list[dict] | None = None,
                   size: list[int] | None = None) -> dict:
    """Apply block changes to an existing NBT and save a new NBT. Each change: {pos,after:{name,properties?,nbt?}} or {pos,after:null} for removal."""
    root = structure.load(_path(source))
    if size is not None:
        from nbtlib import Int, List
        structure._size(size)
        root["size"] = List[Int]([Int(x) for x in size])
        # Existing out-of-range blocks may be removed by this patch below.
        if not all(0 <= int(v) < size[i] for b in root["blocks"] for i, v in enumerate(b["pos"])):
            root["blocks"][:] = [b for b in root["blocks"] if all(0 <= int(v) < size[i]
                                                         for i, v in enumerate(b["pos"]))]
    structure.patch(root, changes)
    if replace_entities is not None:
        from nbtlib import Compound, List
        root["entities"] = List[Compound]()
        for entity in replace_entities:
            structure.add_entity(root, entity["pos"], entity["nbt"], entity.get("block_pos"))
    for entity in add_entities or []:
        structure.add_entity(root, entity["pos"], entity["nbt"], entity.get("block_pos"))
    structure.save(root, _path(output))
    return {"path": output, "blocks": len(root["blocks"]), "entities": len(root["entities"])}


@mcp.tool()
def diff_structures(before: str, after: str, offset: int = 0, limit: int = 1000) -> dict:
    """Paginated block changes and optional entities_after, reusable as edit_structure changes and replace_entities."""
    return structure.diff(structure.load(_path(before)), structure.load(_path(after)), offset, limit)


@mcp.tool()
def convert_structure(source: str, output: str, quarter_turns: int = 0,
                      flip_x: bool = False, flip_y: bool = False, flip_z: bool = False) -> dict:
    """Mirror on selected axes, then rotate clockwise around Y (quarter_turns 0..3); transform block states and entity coordinates."""
    root = structure.transform(structure.load(_path(source)), quarter_turns, flip_x, flip_y, flip_z)
    structure.save(root, _path(output))
    return {"path": output, "size": list(map(int, root["size"])),
            "blocks": len(root["blocks"]), "entities": len(root["entities"])}


@mcp.tool()
def preview_structure(path: str, y: int, offset_x: int = 0, offset_z: int = 0,
                      width: int = 48, depth: int = 48, palette_index: int = 0) -> dict:
    """Human and AI-readable 2D horizontal slice of a structure with a block-ID legend; page over larger structures."""
    return structure.preview(structure.load(_path(path)), y, offset_x, offset_z, width, depth, palette_index)


@mcp.tool()
def render_preview(path: str, output: str, y: int, offset_x: int = 0,
                   offset_z: int = 0, width: int = 48, depth: int = 48,
                   palette_index: int = 0) -> dict:
    """Save a colored, self-contained SVG layer preview with legend for human review."""
    result = _svg_path(output)
    drawing = structure.render_svg(structure.load(_path(path)), y, offset_x, offset_z,
                                   width, depth, palette_index)
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(drawing, encoding="utf-8")
    return {"path": output, "mime_type": "image/svg+xml"}


def main() -> None:
    """Parse transport options and start the MCP server."""
    parser = argparse.ArgumentParser(description="Minecraft Java structure NBT MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--tls-cert", help="PEM server certificate for HTTPS transport")
    parser.add_argument("--tls-key", help="PEM private key for HTTPS transport")
    args = parser.parse_args()
    mcp.settings.host, mcp.settings.port = args.host, args.port
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error("--tls-cert and --tls-key must be supplied together")
    if args.tls_cert:
        if args.transport != "streamable-http":
            parser.error("TLS is only available for streamable-http")
        import uvicorn
        uvicorn.run(mcp.streamable_http_app(), host=args.host, port=args.port,
                    ssl_certfile=args.tls_cert, ssl_keyfile=args.tls_key)
    else:
        mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
