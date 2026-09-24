"""MCP entry point. File operations are restricted to STRUCTURE_WORKSPACE."""
import argparse
import base64
import binascii
import gzip
import hashlib
import ipaddress
import io
import json
import os
from pathlib import Path
import secrets
import tempfile
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import schema, structure


ROOT = Path(os.environ.get("STRUCTURE_WORKSPACE", os.getcwd())).resolve()
mcp = FastMCP("Minecraft Java Structure NBT", host="127.0.0.1", port=8000,
              max_request_body_size=16 * 1024 * 1024,
              instructions="Java 1.20.1 vanilla structure .nbt. Consult minecraft://blocks/1.20.1/schema. Coordinates are relative to the structure origin.")


class BearerGate:
    """Require a shared bearer secret on every HTTP request to the MCP app."""

    def __init__(self, app, token: str):
        self.app = app
        self.expected = ("Bearer " + token).encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            supplied = next((v for k, v in scope.get("headers", []) if k.lower() == b"authorization"), b"")
            if not secrets.compare_digest(supplied, self.expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                        (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b"Authentication required"})
                return
        await self.app(scope, receive, send)


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _path(path: str) -> Path:
    target = (ROOT / path).resolve()
    if not target.is_relative_to(ROOT) or target.suffix.lower() != ".nbt":
        raise ValueError("Path must be a .nbt file within STRUCTURE_WORKSPACE")
    return target


def _svg_path(path: str) -> Path:
    target = (ROOT / path).resolve()
    if not target.is_relative_to(ROOT) or target.suffix.lower() != ".svg":
        raise ValueError("Output must be an .svg file within STRUCTURE_WORKSPACE")
    return target


def _html_path(path: str) -> Path:
    target = (ROOT / path).resolve()
    if not target.is_relative_to(ROOT) or target.suffix.lower() != ".html":
        raise ValueError("Output must be an .html file within STRUCTURE_WORKSPACE")
    return target


def _directory(path: str) -> Path:
    target = (ROOT / path).resolve()
    if not target.is_relative_to(ROOT):
        raise ValueError("Directory must be within STRUCTURE_WORKSPACE")
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
def import_structure(path: str, content_base64: str) -> dict:
    """Upload a local compressed or plain Java structure NBT to the HTTP MCP workspace (max 16 MiB)."""
    target = _path(path)
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("content_base64 is not valid base64") from exc
    if not raw or len(raw) > 16 * 1024 * 1024:
        raise ValueError("Upload must contain 1 byte to 16 MiB of data")
    if raw.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                if len(stream.read(64 * 1024 * 1024 + 1)) > 64 * 1024 * 1024:
                    raise ValueError("Decompressed NBT exceeds 64 MiB")
        except (EOFError, OSError) as exc:
            raise ValueError("Invalid gzip NBT") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".upload_", suffix=".nbt", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
        root = structure.load(temporary)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"path": path, "size": list(map(int, root["size"])),
            "blocks": len(root["blocks"]), "entities": len(root["entities"])}


@mcp.tool()
def export_structure(path: str, offset: int = 0, length: int = 65536) -> dict:
    """Download a structure NBT as paginated base64 chunks for a remote MCP client."""
    target = _path(path)
    if offset < 0 or not 1 <= length <= 1_048_576:
        raise ValueError("offset >= 0 and 1 <= length <= 1048576 required")
    total = target.stat().st_size
    if offset > total:
        raise ValueError("offset is past end of file")
    with target.open("rb") as stream:
        stream.seek(offset)
        data = stream.read(length)
    return {"path": path, "offset": offset, "next_offset": offset + len(data),
            "total_bytes": total, "content_base64": base64.b64encode(data).decode("ascii"),
            "sha256_chunk": hashlib.sha256(data).hexdigest()}


@mcp.tool()
def edit_structure(source: str, output: str, changes: list[dict],
                   add_entities: list[dict] | None = None,
                   replace_entities: list[dict] | None = None,
                   size: list[int] | None = None,
                   palette_count: int | None = None) -> dict:
    """Apply block changes to an existing NBT and save a new NBT. Each change: {pos,after:{name,properties?,nbt?}} or {pos,after:null} for removal."""
    root = structure.load(_path(source))
    if palette_count is not None:
        structure.set_palette_count(root, palette_count)
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
                   palette_index: int = 0, include_svg: bool = False) -> dict:
    """Save a colored, self-contained SVG layer preview with legend for human review."""
    result = _svg_path(output)
    drawing = structure.render_svg(structure.load(_path(path)), y, offset_x, offset_z,
                                   width, depth, palette_index)
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(drawing, encoding="utf-8")
    return {"path": output, "mime_type": "image/svg+xml",
            **({"svg": drawing} if include_svg else {})}


@mcp.tool()
def render_3d_preview(path: str, output: str, offset_x: int = 0,
                      offset_y: int = 0, offset_z: int = 0, width: int = 48,
                      height: int = 48, depth: int = 48, palette_index: int = 0,
                      include_html: bool = False) -> dict:
    """Create a standalone interactive HTML 3D preview with rotation, zoom, Y layers and entity markers."""
    target = _html_path(output)
    page = structure.render_3d_html(structure.load(_path(path)), offset_x, offset_y,
                                    offset_z, width, height, depth, palette_index)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    return {"path": output, "mime_type": "text/html",
            **({"html": page} if include_html else {})}


@mcp.tool()
def split_structure(path: str, output_dir: str, chunk_size: list[int] | None = None) -> dict:
    """Split an oversized NBT into <=48-block templates and return each relative placement offset."""
    directory = _directory(output_dir)
    chunks = structure.split(structure.load(_path(path)), chunk_size or [48, 48, 48])
    directory.mkdir(parents=True, exist_ok=True)
    manifest = []
    for origin, chunk in chunks:
        name = "chunk_" + "_".join(map(str, origin)) + ".nbt"
        target = directory / name
        structure.save(chunk, target)
        manifest.append({"path": str(target.relative_to(ROOT)), "offset": list(origin),
                         "size": list(map(int, chunk["size"])),
                         "blocks": len(chunk["blocks"]), "entities": len(chunk["entities"])})
    return {"source": path, "parts": manifest, "part_count": len(manifest)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Minecraft Java structure NBT MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--tls-cert", help="PEM server certificate for HTTPS transport")
    parser.add_argument("--tls-key", help="PEM private key for HTTPS transport")
    parser.add_argument("--public-url", help="External HTTPS MCP URL, e.g. https://example.com:8443/mcp")
    args = parser.parse_args()
    mcp.settings.host, mcp.settings.port = args.host, args.port
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error("--tls-cert and --tls-key must be supplied together")
    if args.transport == "stdio":
        if args.tls_cert:
            parser.error("TLS is only available for streamable-http")
        mcp.run(transport=args.transport)
        return
    token = os.environ.get("STRUCTURE_MCP_TOKEN", "")
    if token and len(token) < 32:
        parser.error("STRUCTURE_MCP_TOKEN must contain at least 32 characters")
    if not _is_loopback(args.host) and (not token or not args.tls_cert):
        parser.error("A non-loopback HTTP server requires TLS and STRUCTURE_MCP_TOKEN")
    if not _is_loopback(args.host) and not args.public_url:
        parser.error("A non-loopback server requires --public-url for Host validation")
    if args.public_url:
        parsed = urlparse(args.public_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path != "/mcp" or parsed.query or parsed.fragment:
            parser.error("--public-url must be an HTTPS URL ending in /mcp")
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", parsed.netloc],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*",
                             f"https://{parsed.netloc}"],
        )
    import uvicorn
    app = mcp.streamable_http_app()
    if token:
        app = BearerGate(app, token)
    uvicorn.run(app, host=args.host, port=args.port,
                ssl_certfile=args.tls_cert, ssl_keyfile=args.tls_key)


if __name__ == "__main__":
    main()
