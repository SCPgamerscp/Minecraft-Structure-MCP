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
                         '{id:"minecraft:painting",Rotation:[180.0f,0.0f],TileX:1,TileY:1,TileZ:2,Facing:2b}')
    rotated = structure.transform(original, 1, flip_x=True, flip_y=True)
    assert list(map(int, rotated["size"])) == [7, 3, 5]
    assert list(map(int, rotated["blocks"][0]["pos"])) == [4, 2, 3]
    states = structure.inspect(rotated)["palette"]
    assert states[0]["properties"] == {"facing": "east", "half": "top", "shape": "inner_right"}
    assert states[1]["properties"] == {"facing": "north", "half": "upper", "hinge": "right"}
    assert states[2]["properties"]["type"] == "bottom"
    assert states[3]["properties"]["shape"] == "north_east"
    assert list(map(float, rotated["entities"][0]["pos"])) == [4.5, 2.0, 3.5]
    assert int(rotated["entities"][0]["nbt"]["Facing"]) == 5


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
