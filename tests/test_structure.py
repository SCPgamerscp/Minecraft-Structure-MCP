import pytest

from minecraft_structure_mcp import schema, structure
from minecraft_structure_mcp.server import _path, edit_structure


def test_round_trip_entities_blocks_and_large_size(tmp_path):
    root = structure.new([64, 4, 70])
    structure.set_block(root, [60, 1, 50], "minecraft:oak_stairs",
                        {"facing": "north", "half": "bottom", "shape": "inner_left"})
    structure.set_block(root, [1, 0, 1], "minecraft:chest", {"facing": "east"},
                        '{id:"minecraft:chest",Items:[{Slot:0b,id:"minecraft:diamond",Count:2b}]}')
    structure.add_entity(root, [3.5, 1.0, 4.5],
                         '{id:"minecraft:armor_stand",Rotation:[180.0f,0.0f]}')
    file = tmp_path / "large.nbt"
    structure.save(root, file)
    loaded = structure.load(file)
    assert structure.inspect(loaded)["size"] == (64, 4, 70)
    assert "diamond" in structure.inspect(loaded)["blocks"][1]["nbt"]
    assert "armor_stand" in structure.inspect(loaded)["entities"][0]["nbt"]
    assert file.read_bytes()[:2] == b"\x1f\x8b"


def test_transform_and_inverse_with_state_entity_and_rail():
    original = structure.new([5, 3, 7])
    structure.set_block(original, [1, 0, 2], "minecraft:oak_stairs",
                        {"facing": "north", "half": "bottom", "shape": "inner_left"})
    structure.set_block(original, [0, 1, 0], "minecraft:oak_door",
                        {"facing": "east", "half": "lower", "hinge": "left"})
    structure.set_block(original, [3, 2, 2], "minecraft:stone_slab", {"type": "top"})
    structure.set_block(original, [2, 1, 3], "minecraft:rail", {"shape": "north_east"})
    structure.add_entity(original, [1.5, 1.0, 2.5],
                         '{id:"minecraft:painting",Pos:[1.5d,1.0d,2.5d],Rotation:[180.0f,0.0f],TileX:1,TileY:1,TileZ:2,Facing:2b}')
    rotated = structure.transform(original, 1, flip_x=True, flip_y=True)
    assert list(map(int, rotated["size"])) == [7, 3, 5]
    assert list(map(int, rotated["blocks"][0]["pos"])) == [4, 2, 3]
    states = structure.inspect(rotated)["palette"]
    assert states[0]["properties"] == {"facing": "east", "half": "top", "shape": "inner_right"}
    assert states[1]["properties"] == {"facing": "north", "half": "upper", "hinge": "right"}
    assert states[2]["properties"]["type"] == "bottom"
    assert states[3]["properties"]["shape"] == "north_east"
    assert list(map(float, rotated["entities"][0]["pos"])) == [4.5, 2.0, 3.5]
    assert list(map(float, rotated["entities"][0]["nbt"]["Pos"])) == [4.5, 2.0, 3.5]
    assert int(rotated["entities"][0]["nbt"]["Facing"]) == 3


def test_diff_patch_and_preview(tmp_path):
    before = structure.new([2, 2, 3])
    structure.set_block(before, [0, 0, 0], "minecraft:stone")
    after = structure.new([2, 2, 3])
    structure.set_block(after, [1, 0, 1], "minecraft:oak_planks")
    structure.add_entity(after, [1.5, 0, 1.5], '{id:"minecraft:pig"}')
    delta = structure.diff(before, after)
    assert len(delta["changes"]) == 2
    assert delta["entities_after"][0]["block_pos"] == [1, 0, 1]
    structure.patch(before, delta["changes"])
    assert structure.diff(before, after)["changes"] == []
    view = structure.preview(before, 0, width=2, depth=3)
    assert view["rows_north_to_south"][1][1] != " "
    assert "<svg" in structure.render_svg(before, 0, width=2, depth=3)


def test_edit_applies_entity_and_size_diff(tmp_path, monkeypatch):
    import minecraft_structure_mcp.server as server
    monkeypatch.setattr(server, "ROOT", tmp_path)
    original = structure.new([2, 1, 2])
    structure.save(original, tmp_path / "before.nbt")
    updated = structure.new([60, 1, 2])
    structure.set_block(updated, [55, 0, 1], "minecraft:stone")
    structure.add_entity(updated, [55.5, 0, 1.5], '{id:"minecraft:pig"}')
    delta = structure.diff(original, updated)
    edit_structure("before.nbt", "after.nbt", delta["changes"],
                   replace_entities=delta["entities_after"], size=delta["after_size"])
    result = structure.load(tmp_path / "after.nbt")
    assert structure.diff(result, updated)["changes"] == []
    assert structure.diff(result, updated)["entities_changed"] is False


def test_vanilla_alternate_palettes_are_preserved_and_transformed(tmp_path):
    from copy import deepcopy
    from nbtlib import Compound, List, String
    root = structure.new([3, 1, 4])
    structure.set_block(root, [0, 0, 0], "minecraft:oak_stairs", {"facing": "north"})
    root["palettes"] = List[List[Compound]]([
        List[Compound](root.pop("palette")),
        List[Compound]([structure.state("minecraft:spruce_stairs", {"facing": "south"})]),
    ])
    file = tmp_path / "shipwreck.nbt"
    structure.save(root, file)
    loaded = structure.load(file)
    variant_edit = deepcopy(loaded)
    variant_edit["palettes"][1][0]["Name"] = String("minecraft:birch_stairs")
    variant_delta = structure.diff(loaded, variant_edit)
    assert variant_delta["change_count"] == 1
    assert variant_delta["alternate_palettes_changed"] is True
    assert structure.inspect(loaded)["palette_count"] == 2
    assert structure.inspect(loaded, palette_index=1)["palette"][0]["name"] == "minecraft:spruce_stairs"
    assert "minecraft:spruce_stairs" in structure.preview(loaded, 0, palette_index=1)["legend"].values()
    structure.set_block(loaded, [1, 0, 0], "minecraft:stone")
    assert len(loaded["palettes"][0]) == len(loaded["palettes"][1]) == 2
    rotated = structure.transform(loaded, 1)
    assert rotated["palettes"][0][0]["Properties"]["facing"] == "east"
    assert rotated["palettes"][1][0]["Properties"]["facing"] == "west"
    structure.save(rotated, file)
    assert structure.load(file)["palettes"][1][1]["Name"] == "minecraft:stone"


def test_alternate_palette_diff_is_applicable_and_can_split_shared_states(tmp_path, monkeypatch):
    from copy import deepcopy
    from nbtlib import Compound, List
    import minecraft_structure_mcp.server as server
    monkeypatch.setattr(server, "ROOT", tmp_path)
    before = structure.new([3, 1, 1])
    structure.set_blocks(before, [
        {"pos": [0, 0, 0], "name": "minecraft:oak_stairs"},
        {"pos": [1, 0, 0], "name": "minecraft:oak_stairs"},
    ])
    after = deepcopy(before)
    structure.set_palette_count(after, 2)
    # Two cells initially share one palette index; change the alternate only for one.
    structure.set_blocks(after, [{"pos": [1, 0, 0], "name": "minecraft:oak_stairs",
                                  "variants": [{"name": "minecraft:oak_stairs"},
                                               {"name": "minecraft:spruce_stairs"}]}])
    delta = structure.diff(before, after)
    assert delta["change_count"] == 2  # both cells gain an alternate palette
    assert delta["after_palette_count"] == 2
    assert delta["changes"][1]["after"]["variants"][1]["name"] == "minecraft:spruce_stairs"
    structure.save(before, tmp_path / "before.nbt")
    server.edit_structure("before.nbt", "after.nbt", delta["changes"],
                          palette_count=delta["after_palette_count"])
    result = structure.load(tmp_path / "after.nbt")
    assert structure.diff(result, after)["change_count"] == 0
    assert structure.inspect(result, palette_index=1)["palette_count"] == 2


def test_interactive_3d_preview_escapes_untrusted_names_and_bounds():
    root = structure.new([65, 2, 65])
    structure.set_block(root, [60, 1, 60], "minecraft:stone")
    structure.add_entity(root, [60.5, 1, 60.5], '{id:"minecraft:armor_stand"}')
    page = structure.render_3d_html(root, 50, 0, 50, 16, 2, 16)
    assert "<canvas" in page and '"blocks":[[10,1,10,0]]' in page
    assert '"entities":[[10.5,1.0,10.5,"minecraft:armor_stand"]]' in page
    root["palette"][0]["Name"] = type(root["palette"][0]["Name"])("</script><script>evil()</script>")
    page = structure.render_3d_html(root)
    assert "</script><script>evil()" not in page
    assert "\\u003c/script>" in page
    with pytest.raises(ValueError, match="Viewport"):
        structure.render_3d_html(root, width=97)


def test_split_large_structure_keeps_entities_and_block_entity_payload(tmp_path, monkeypatch):
    from minecraft_structure_mcp import server
    monkeypatch.setattr(server, "ROOT", tmp_path)
    root = structure.new([100, 3, 52])
    structure.set_block(root, [50, 1, 49], "minecraft:chest", nbt='{id:"minecraft:chest",x:50,y:1,z:49}')
    structure.set_block(root, [99, 2, 0], "minecraft:stone")
    structure.add_entity(root, [50.5, 1.0, 49.5],
                         '{id:"minecraft:painting",TileX:50,TileY:1,TileZ:49,Facing:2b}')
    structure.save(root, tmp_path / "large.nbt")
    result = server.split_structure("large.nbt", "pieces")
    assert result["part_count"] == 2
    assert all(max(part["size"]) <= 48 for part in result["parts"])
    chunks = [(part["offset"], structure.load(tmp_path / part["path"])) for part in result["parts"]]
    middle = next(chunk for origin, chunk in chunks if origin == [48, 0, 48])
    assert list(map(int, middle["blocks"][0]["pos"])) == [2, 1, 1]
    assert int(middle["blocks"][0]["nbt"]["x"]) == 2
    assert float(middle["entities"][0]["pos"][0]) == 2.5
    assert int(middle["entities"][0]["nbt"]["TileZ"]) == 1


def test_remote_import_export_and_preview_contents(tmp_path, monkeypatch):
    import base64
    from minecraft_structure_mcp import server
    monkeypatch.setattr(server, "ROOT", tmp_path)
    root = structure.new([65, 2, 1])
    structure.set_block(root, [64, 1, 0], "minecraft:stone")
    original = tmp_path / "original.nbt"
    structure.save(root, original)
    binary = original.read_bytes()
    assert server.import_structure("uploaded.nbt", base64.b64encode(binary).decode())["blocks"] == 1
    first = server.export_structure("uploaded.nbt", length=10)
    second = server.export_structure("uploaded.nbt", offset=first["next_offset"])
    assert base64.b64decode(first["content_base64"]) + base64.b64decode(second["content_base64"]) == binary
    html = server.render_3d_preview("uploaded.nbt", "preview.html", offset_x=60,
                                    width=5, height=2, depth=1, include_html=True)["html"]
    assert "<canvas" in html and (tmp_path / "preview.html").exists()
    svg = server.render_preview("uploaded.nbt", "preview.svg", 1, offset_x=60,
                                width=5, depth=1, include_svg=True)["svg"]
    assert "<svg" in svg
    with pytest.raises(Exception):
        server.import_structure("uploaded.nbt", base64.b64encode(b"bad NBT").decode())
    assert (tmp_path / "uploaded.nbt").read_bytes() == binary


def test_failed_save_keeps_previous_file(tmp_path, monkeypatch):
    root = structure.new([1, 1, 1])
    path = tmp_path / "existing.nbt"
    structure.save(root, path)
    original = path.read_bytes()
    def broken_save(*args, **kwargs):
        raise OSError("disk failed")
    monkeypatch.setattr(root, "save", broken_save)
    with pytest.raises(OSError):
        structure.save(root, path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("facing,expected", [(0, 1), (1, 2), (2, 3), (3, 0)])
def test_hanging_entity_horizontal_facing_turn(facing, expected):
    root = structure.new([2, 2, 2])
    structure.add_entity(root, [0.5, 0.5, 0.5],
                         '{id:"minecraft:painting",Facing:' + str(facing) + 'b}')
    assert int(structure.transform(root, 1)["entities"][0]["nbt"]["Facing"]) == expected


def test_validation_and_path_security():
    assert "minecraft:oak_stairs" in schema.blocks()
    assert len(schema.blocks()) > 1000
    with pytest.raises(ValueError):
        structure.state("minecraft:not_a_real_block")
    with pytest.raises(ValueError):
        structure.state("minecraft:stone", {"facing": "north"})
    with pytest.raises(ValueError):
        structure.state("minecraft:oak_stairs", {"half": "middle"})
    assert structure.new([1000, 1000, 1000])["size"] == [1000, 1000, 1000]
    with pytest.raises(ValueError):
        structure.new([2**31, 1, 1])
    with pytest.raises(ValueError):
        _path("../outside.nbt")


def test_rotation_and_mirrors_keep_all_vanilla_state_values_valid():
    for name, spec in schema.blocks().items():
        original = structure.state(name, {key: values[0] for key, values in spec.items()})
        for turns, flip_x, flip_y, flip_z in (
            (1, False, False, False), (0, True, False, False),
            (0, False, True, False), (1, True, True, True),
        ):
            updated = structure._transform_state(original, turns, flip_x, flip_y, flip_z)
            schema.validate(str(updated["Name"]),
                            {key: str(value) for key, value in updated.get("Properties", {}).items()})
