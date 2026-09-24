# Minecraft Structure MCP

Minecraft Java Edition 1.20.1 のバニラ構造物 `.nbt` を Python で解析・生成・編集する MCP サーバーです。ローカルの stdio と、ローカル HTTP／HTTPS 対応クライアント向けの Streamable HTTP を提供します。

## インストールと起動

```bash
python -m pip install -e .
mkdir -p structures
export STRUCTURE_WORKSPACE="$(pwd)/structures"
minecraft-structure-mcp                         # stdio
```

MCP クライアントの stdio 設定例（`/absolute/path/...` を実際のパスに変更）:

```json
{
  "mcpServers": {
    "minecraft-structure": {
      "command": "python",
      "args": ["-m", "minecraft_structure_mcp.server"],
      "env": {"STRUCTURE_WORKSPACE": "/absolute/path/structures"}
    }
  }
}
```

ローカル HTTP:

```bash
minecraft-structure-mcp --transport streamable-http --host 127.0.0.1 --port 8000
# MCP URL: http://127.0.0.1:8000/mcp
```

HTTPS で接続する場合は証明書と鍵を指定できます。外部の AI クライアントを接続させるときは、認証を行うリバースプロキシを前に置き、認証済みの接続だけを MCP に通してください。MCP 本体には認証機能を設定していないため、公開アドレスへ直接バインドしないでください。MCP ツールは許可したディレクトリのファイルを編集できます。

```bash
minecraft-structure-mcp --transport streamable-http --host 127.0.0.1 --port 8443 \
  --tls-cert /path/to/fullchain.pem --tls-key /path/to/privkey.pem
# MCP URL: https://your-host.example:8443/mcp
```

`STRUCTURE_WORKSPACE` 未指定時は起動時の作業ディレクトリです。NBT と SVG の読み書きはこのディレクトリ内に限定されます。シンボリックリンクによるディレクトリ外へのアクセスも拒否します。

## ツールと Resource

| 名前 | 用途 |
| --- | --- |
| `find_blocks` | 1.20.1 のブロックIDと許可された Block States を検索 |
| `analyze_structure` | `.nbt` のサイズ、パレット、ブロック、ブロックエンティティ、エンティティを取得（ページ指定可） |
| `create_structure` | 新規 `.nbt` を作成（ブロック・エンティティを一括指定可） |
| `edit_structure` | 既存 `.nbt` に差分適用、サイズ変更、エンティティ追加・置換 |
| `diff_structures` | 座標ごとのブロック差分とエンティティ差分を生成 |
| `convert_structure` | X/Y/Z 反転後に Y 軸回り 90° 単位で回転 |
| `preview_structure` | AI と人間が読める、凡例付きの水平断面表示 |
| `render_preview` | 凡例付きの SVG 断面画像を保存 |

MCP Resource `minecraft://blocks/1.20.1/schema` に全ブロックの定義、`minecraft://blocks/1.20.1/oak_stairs` に個別の Block States 定義があります。全件 Resource が大きい場合は `find_blocks` か個別 Resource を使ってください。収録されているのは Java 版バニラの 1,003 ブロックです。Mod で追加したブロックの新規生成は検証対象外ですが、既存NBT内のパレットは読み込んで保持できます。

`palette` と、難破船などに使われる複数候補の `palettes` の両形式に対応します。解析とプレビューでは `palette_index` で候補を選べます。複数パレット構造の編集では既存候補を維持し、新しいブロック状態を全候補へ追加します。差分の `alternate_palettes_changed` は代替候補自体の変更を通知しますが、`edit_structure` の `changes` はブロック座標の差分のみを適用します。

### 入力例

`create_structure`:

```json
{
  "path": "temple.nbt",
  "size": [64, 12, 64],
  "blocks": [
    {"pos": [0, 0, 0], "name": "minecraft:stone_bricks"},
    {"pos": [1, 0, 0], "name": "minecraft:oak_stairs", "properties": {"facing": "north", "half": "bottom"}},
    {"pos": [2, 0, 0], "name": "minecraft:chest", "nbt": "{id:\"minecraft:chest\",Items:[]} "}
  ],
  "entities": [
    {"pos": [3.5, 1.0, 3.5], "nbt": "{id:\"minecraft:armor_stand\",Rotation:[0.0f,0.0f]}"}
  ]
}
```

`diff_structures` の `changes` は `edit_structure` の `changes` に渡せます。`change_count` が `limit` より多いときは `offset` を進めて続きを取得します。エンティティが変わったときは `entities_after` を `replace_entities` に、サイズが変わったときは `after_size` を `size` に渡してください。差分の各ブロックは `{"pos": [x,y,z], "after": {"name": "minecraft:stone"}}`、削除は `{"pos": [x,y,z], "after": null}` です。

### 座標と方向

- 座標は構造物内の相対座標です。X は東、Y は上、Z は南が正です。
- 反転を先に適用し、`quarter_turns: 1` は上から見て時計回り 90°、`2` は 180°、`3` は 270° です。変換後の座標は 0 以上に正規化されます。
- 階段の `facing`・`half`・左右の `shape`、ドアの `facing`・`half`・`hinge`、スラブの `type`、線路の `shape`、柵などの接続方向、看板の `rotation`、ブロックの `axis` を変換します。ブロックエンティティの SNBT は保持し、埋め込まれた `x/y/z` があれば相対座標として変換します。エンティティの位置、回転、絵画の `TileX/Y/Z` と `Facing` も変換します。
- 特殊なブロック・エンティティの方向固有タグすべてを自動変換するわけではありません。変換後はプレビューとゲーム内で確認してください。

48×48×48 のサイズ制限はサーバー側には設けていません。NBT の `size` は 32 ビット整数として保存されます。Minecraft ゲーム側の構造物ブロック UI／設置方法固有の上限や、巨大な一括設置時の負荷は別途考慮してください。プレビューは最大 96×96 の断面を `offset_x` / `offset_z` で分割して表示します。

現段階のプレビューは水平断面の SVG です。3D 表示と、Minecraft 1.20.1 のゲーム本体での実配置テストは含まれません。

## 開発

```bash
python -m pip install -e . pytest
python -m pytest -q
```

ブロックIDと State 値の同梱データは [PrismarineJS minecraft-data](https://github.com/PrismarineJS/minecraft-data) の Java 1.20 データ（1.20.1 の対応データ）から抽出したものです。元プロジェクトは README で MIT License と記載しています。構造物NBTの処理は `nbtlib`、MCP 通信は公式 Python SDK の `mcp` を利用します。
