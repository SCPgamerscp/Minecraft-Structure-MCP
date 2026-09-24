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

Windows PowerShell で直接起動する場合:

```powershell
py -m pip install -e .
$env:STRUCTURE_WORKSPACE = "C:\minecraft-structures"
py -m minecraft_structure_mcp.server
```

ローカル HTTP:

```bash
minecraft-structure-mcp --transport streamable-http --host 127.0.0.1 --port 8000
# MCP URL: http://127.0.0.1:8000/mcp
```

HTTPS 公開時は TLS 証明書と32文字以上の共有トークンが必須です。クライアントの HTTP ヘッダーに `Authorization: Bearer <トークン>` を設定します。認証なしの HTTP はループバックアドレスにしかバインドできません。MCP ツールは許可したディレクトリのファイルを編集できます。共有トークンを安全に管理できないクライアントを使う場合は、ループバックで起動して認証付きリバースプロキシを利用してください。

```bash
export STRUCTURE_MCP_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
minecraft-structure-mcp --transport streamable-http --host 0.0.0.0 --port 8443 \
  --public-url https://your-host.example:8443/mcp \
  --tls-cert /path/to/fullchain.pem --tls-key /path/to/privkey.pem
# MCP URL: https://your-host.example:8443/mcp（Bearer ヘッダー付き）
```

`STRUCTURE_WORKSPACE` 未指定時は起動時の作業ディレクトリです。NBT、SVG、HTML の読み書きはこのディレクトリ内に限定されます。シンボリックリンクによるディレクトリ外へのアクセスも拒否します。

## ツールと Resource

| 名前 | 用途 |
| --- | --- |
| `find_blocks` | 1.20.1 のブロックIDと許可された Block States を検索 |
| `analyze_structure` | `.nbt` のサイズ、パレット、ブロック、ブロックエンティティ、エンティティを取得（ページ指定可） |
| `create_structure` | 新規 `.nbt` を作成（ブロック・エンティティを一括指定可） |
| `import_structure` / `export_structure` | HTTPS クライアントから NBT をアップロード／分割した base64 でダウンロード |
| `edit_structure` | 既存 `.nbt` に差分適用、サイズ変更、エンティティ追加・置換 |
| `diff_structures` | 座標ごとのブロック差分とエンティティ差分を生成 |
| `convert_structure` | X/Y/Z 反転後に Y 軸回り 90° 単位で回転 |
| `preview_structure` | AI と人間が読める、凡例付きの水平断面表示 |
| `render_preview` | 凡例付きの SVG 断面画像を保存 |
| `render_3d_preview` | 回転・拡大・高さ切替が可能な単体 HTML 3D プレビューを保存 |
| `split_structure` | 48以下の構造物NBTに分割し、相対配置オフセットを返す |

MCP Resource `minecraft://blocks/1.20.1/schema` に全ブロックの定義、`minecraft://blocks/1.20.1/oak_stairs` に個別の Block States 定義があります。全件 Resource が大きい場合は `find_blocks` か個別 Resource を使ってください。収録されているのは Java 版バニラの 1,003 ブロックです。Mod で追加したブロックの新規生成は検証対象外ですが、既存NBT内のパレットは読み込んで保持できます。

`palette` と、難破船などに使われる複数候補の `palettes` の両形式に対応します。解析とプレビューでは `palette_index` で候補を選べます。複数パレット構造の編集では既存候補を維持し、新しいブロック状態を全候補へ追加します。代替候補を個別に編集するときはブロックに `variants: [{"name": "minecraft:...", "properties": {}}, ...]` を候補数だけ指定します。

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

`diff_structures` の `changes` は `edit_structure` の `changes` に渡せます。`change_count` が `limit` より多いときは `offset` を進めて続きを取得します。エンティティが変わったときは `entities_after` を `replace_entities` に、サイズが変わったときは `after_size` を `size` に渡してください。パレット候補数が変わったときは `after_palette_count` を `palette_count` に渡します。各ブロック差分は `{"pos": [x,y,z], "after": {"name": "minecraft:stone"}}`、削除は `{"pos": [x,y,z], "after": null}` です。候補ごとの違いも `after.variants` に入り、座標単位で適用できます。使用されていないパレット項目だけの変更は `alternate_palettes_changed` で通知します。

### 座標と方向

- 座標は構造物内の相対座標です。X は東、Y は上、Z は南が正です。
- 反転を先に適用し、`quarter_turns: 1` は上から見て時計回り 90°、`2` は 180°、`3` は 270° です。変換後の座標は 0 以上に正規化されます。
- 階段の `facing`・`half`・左右の `shape`、ドアの `facing`・`half`・`hinge`、スラブの `type`、線路の `shape`、柵などの接続方向、看板の `rotation`、ブロックの `axis` を変換します。ブロックエンティティの SNBT は保持し、埋め込まれた `x/y/z` があれば相対座標として変換します。エンティティの位置、回転、絵画の `TileX/Y/Z` と `Facing` も変換します。
- 特殊なブロック・エンティティの方向固有タグすべてを自動変換するわけではありません。変換後はプレビューとゲーム内で確認してください。

48×48×48 のサイズ制限はサーバー側には設けていません。NBT の `size` は 32 ビット整数として保存されます。構造物ブロックで扱う必要がある場合は `split_structure` が48以下の部品を作成します。返された `offset` の位置に各部品を置いてください。分割時はブロックエンティティや絵画の座標も部品の相対座標に変換します。プレビューは最大 96×96 の水平断面、3D は各軸最大96の範囲をオフセットで切り出します。3D はブロックを色分けした立方体として描画し、階段などの細かい形状やテクスチャは再現しません。

HTTPS クライアントから使う場合、`import_structure` は圧縮済みNBT（最大16 MiB）を受け取ります。`export_structure` は `offset` と `length` で分割取得できます。`render_preview(include_svg=true)`、`render_3d_preview(include_html=true)` は描画内容をMCPの返答にも含めるため、サーバーのファイルに直接アクセスできないクライアントでもプレビューを保存できます。

Minecraft 1.20.1 のバニラ構造物（村と8候補パレットの難破船）を読み、NBTの往復保存と回転後の再読込を確認しています。ゲーム内での実配置は、利用するワールド／データパックで最終確認してください。

## 開発

```bash
python -m pip install -e . pytest
python -m pytest -q
```

ブロックIDと State 値の同梱データは [PrismarineJS minecraft-data](https://github.com/PrismarineJS/minecraft-data) の Java 1.20 データ（1.20.1 の対応データ）から抽出したものです。元プロジェクトは README で MIT License と記載しています。構造物NBTの処理は `nbtlib`、MCP 通信は公式 Python SDK の `mcp` を利用します。
