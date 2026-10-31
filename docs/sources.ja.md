# データ源と一覧の作り方(`genmodelfit update`)

[English](sources.md) · **日本語**

このページは、`genmodelfit update` が一覧(モデルの情報)をどのデータ源から、どんな規則で作るかを説明する。
特に、ライセンス・言語・無検閲・除外の決め方を書いている。
読む人: メンテナ、コントリビューター、一覧の根拠を確かめたいアプリ開発者。

## 目次

- [データ源](#データ源)
- [テンプレートの選び方](#テンプレートの選び方)
- [ライセンス](#ライセンス)
- [言語・動かし方・説明と、手で足すモデル](#言語動かし方説明と手で足すモデル)
- [無検閲(uncensored)のモデル](#無検閲uncensoredのモデル)
- [除外](#除外)
- [Civitai: 使わない](#civitai-使わない)
- [確認の印(review)](#確認の印review)
- [上流の形が変わったとき・ネットに繋がらないとき・一部を取得できなかったとき](#上流の形が変わったときネットに繋がらないとき一部を取得できなかったとき)
- [テンプレートの `size` は一式の合計か](#テンプレートの-size-は一式の合計か)

`genmodelfit update` は、下のデータ源から一覧を作り直して、キャッシュ(`db.cached_db_path()`)か `--output` の
場所に書き出す。Python からは `genmodelfit.update.build_database(fetch=...)`(書き出しはしない)。
同梱の `src/genmodelfit/data/db.json` は、実際に `update` を走らせた結果(ネットに繋がらないときの控え)。

持つのはモデルの**情報**だけで、重みは取らない・配らない。

## データ源

| データ源 | URL | 条件 | 使うもの |
|---|---|---|---|
| ComfyUI 公式テンプレートの一覧 | `https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/index.json` | MIT | 分類・名前・タグ・`openSource`・`size`・`usage`・`date`・`minComfyUIVersion`・`requiresCustomNodes`・`io.outputs` |
| テンプレートごとの JSON | `…/templates/<name>.json` | MIT | 全ノード(サブグラフ `definitions.subgraphs[].nodes` の中も)の `properties.models`(`name`・`url`・`hash`・`hash_type`・`directory`) |
| Hugging Face Hub API | `https://huggingface.co/api/models/{repo_id}?blobs=true`(版が main 以外なら `/revision/{rev}`) | HF の利用規約(公開 API を匿名で) | `cardData.license` / `license_name` / `license_link`(無ければ `license:` タグ)、`gated`、`siblings[].size` と `lfs.sha256`、`cardData.base_model`、`cardData.language`(`card_languages`。下の「言語」)、`downloads`、`likes`、`lastModified`、`tags` |
| 重みの見出し・`config.json`・`/api/models/<repo>` の `safetensors` | 本体のファイル(Range 要求)・同じフォルダの `config.json`・Hub API | 同上 | 精度(下の「ファイル」の精度の決め方。重み本体は落とさない) |
| Hugging Face の検索 | `https://huggingface.co/api/models?filter=…&pipeline_tag=…&search=…&sort=downloads&direction=-1&limit=100` | 同上 | 無検閲のモデル、テンプレートに無い読み上げ・効果音のモデルの候補 |
| モデルカード | `https://huggingface.co/{repo_id}/raw/main/README.md` | 同上 | 除外の判定にだけ使う(保存しない) |
| ファイルの URL(HEAD) | テンプレートなどに書かれた `https://huggingface.co/…/resolve/…` | 同上 | 匿名の HEAD(転送は基本的に追わない。名前が変わったリポジトリの 307 だけ 1 回追う。[gate.ja.md](gate.ja.md))の応答コードだけ(`files[].head_status`。[gate.ja.md](gate.ja.md)) |
| ComfyUI の最新のリリース | `https://api.github.com/repos/Comfy-Org/ComfyUI/releases/latest` | GitHub の公開 API(認証なし、1 回) | `tag_name`(`sources.comfyui.latest_release`。gate の版の比較に使う) |

使わないもの: willitrunai.com などのサイトのデータ(CLAUDE.md)、Civitai(下)。

取得の決まり:

- `User-Agent: genmodelfit/<版> (+https://github.com/laxshmate/genmodelfit)` を名乗る。
- 同時に 8 本まで。
- 時間切れは接続 10 秒・読み込み 60 秒。
- やり直し: 接続の失敗・HTTP 500・502・503・504 は合わせて 4 回まで試す。

上限(rate limit):

- HF の匿名の API は **5 分あたり 500 回まで**(応答の `ratelimit-policy: "fixed window";"api";q=500;w=300`)。
- 1 回の update で HF に 500〜600 回問い合わせる。
- 429 を受けたら `Retry-After` の秒数だけ全スレッドで待つ(`sources.huggingface.rate_limited` に回数、`rate_limit_waited_s` に待った秒数)。このため update は数分かかることがある。
- 1 つの要求で 429 をやり直すのは 30 回まで(`fetch.MAX_RATE_LIMITED`)。これは上の 4 回とは別に数える。
- 待つ時間の合計には上限がある(既定 900 秒、`--rate-limit-budget` で変える)。超えたら以後の HF への要求はすぐ失敗にし、一部が欠けた扱い(下の「一部を取得できなかったとき」)で止まる。
- トークンは使わない。

## テンプレートの選び方

ローカルで動くものだけを取る:

- 名前が `api_` で始まるもの(クラウドの API を呼ぶテンプレート)は外す
- `openSource` が **false** のものは外す。`openSource` が**無い**ものはローカル扱い(古いテンプレートは書いていない)
- `size` が 0 か無いものは外す(重みを持たない。Chatterbox の読み上げなど、カスタムノードが自分で取るものもここで外れる)
- 分類 `LLM`・`Node Basics`・`Vector` は生成モデルではないので外す

一覧の ID はテンプレート名そのまま。テンプレートの JSON が読めないものは警告にして飛ばし、
**ローカルのテンプレートの 30% を超えて読めなければ**上流の形が変わったとみなして止める。

### 種類(modality)

| 分類 | modality |
|---|---|
| Image / Image Tools | image |
| Video / Video Tools | video |
| 3D Model | 3d |
| Audio | 名前・題・タグに `tts` / `text to speech` / `speech` / `voice cloning` → **tts**。名前・題・タグ・説明に `sound effect(s)` / `sfx` / `foley` があり `music` / `song` / `instrumental` が無い → **sfx**。それ以外 → **music** |
| それ以外(Character & Fashion・Brand & Design・Product & Ads) | `io.outputs[].mediaType`(video → video、audio → music、image → image)。無ければタグ(`Image to 3D` / `Text to 3D` → 3d、`Video` を含む → video、それ以外 → image) |

手で決めたもの(名前・タグから決められないもの): `audio_stable_audio_example`(Stable Audio Open 1.0)→ sfx。
モデルカードの用途が効果音・環境音(field recordings)中心のため。

### tasks

タグ → 略号。

| タグ | task | タグ | task |
|---|---|---|---|
| Text to Image | t2i | Image to Video | i2v |
| Image Edit / Style Reference / Layer Decompose | i2i | Text to Video | t2v |
| Inpainting | inpaint | Video Edit / Video to Video / Character Replacement | v2v |
| Outpainting | outpaint | FLF2V | flf2v |
| ControlNet | control | Reference to Video | ref2v |
| Image Upscale / Video Upscale | upscale | Audio to Video | a2v |
| Remove Background | matting | Lip Sync | lipsync |
| Relight | relight | Motion Control | motion |
| Depth / Normal / Albedo Estimation, Element Segmentation | estimate | Frame Interpolation | interp |
| Image to 3D | i23d | Text to 3D | t23d |
| Text to Audio / Text to Music | t2a | Music Cover / Audio Editing | a2a |
| Voice Cloning | vc | Text to Speech | tts |

当てはまるタグが無ければ種類の既定(image → t2i、video → t2v、3d → i23d、tts → tts、音 → t2a)。
ただし分類が **Image Tools / Video Tools**(道具)のものは、生成ではないので既定を **`tool`** にする
(`Depth`・`Pose Map` など表に無いタグの道具を t2i / t2v と言わないため)。
さらにタグによらず、道具の分類で題が深度・法線・線画・ポーズ・形状の地図(`Depth Map: …`、`Depth Estimation: …`、
`Lineart Map: …`)や切り分け(Segmentation)のものは **`estimate`**、題が音の分離(`Audio Separation`)のものは
**`separate`** にする(`tasks.tool_task_from_title`。タグが ControlNet / Text to Image でも、生成ではない前処理なので)。
分類によらず、題が MoGe で始まるもの・Geometry Estimation のものも **`estimate`**(MoGe は写真から形を推す単眼の形状推定。
メッシュを出すが生成ではない)。

上流のタグの誤り・ゆれ(`Depth` と `Depth Estimation` が混在する、道具に `Text to Image` が付いているなど)は直さず、
そのまま対応表に当てる(表に足すのは名前がはっきりしたものだけ)。

### ファイル

`properties.models` を URL で重複を除いて全部入れる。役割(role)は `directory` から:
`diffusion_models` / `unet` / `checkpoints` → main、`text_encoders` / `clip` → text_encoder、`vae` → vae、
`clip_vision` → clip_vision、`loras` → lora、それ以外(`latent_upscale_models`・`model_patches` など)→ other。

sha256 はテンプレートの `hash` が `hash_type: SHA256` ならそれ、無ければ HF の `lfs.sha256`。
大きさは HF の `siblings[].size`。API の一覧に無いファイル(HF の URL だが別の版など)だけ HEAD で
`x-linked-size` / `content-length` を見る(`--skip-head` で飛ばす)。Civitai の URL には HEAD も送らない。

**一式がそろっているか**(`files_complete`。[schema.ja.md](schema.ja.md)): 次のどれかなら `false`。

- 本体(role main。`diffusion_models` / `unet` / `checkpoints`)が書かれていない
  (カスタムノードが自分で重みを取るテンプレート、MoGe など `geometry_estimation` に本体を置くもの、ファイルが 0 個のもの)
- ファイルの大きさの合計が、index.json の `size` の **50% 未満**
- HF のリポジトリで、本体が GGUF だけでテキストエンコーダ・VAE が無い(GGUF の写しは本体だけを置くことが多い)

`false` のときは、テンプレートの index の `size` を **`size_floor_bytes`** に入れる(ファイルは消さない)。見積もりは
`max(ファイルの合計, size_floor_bytes)`(`ModelEntry.estimate_size_bytes`)を使い、一式が欠けた一覧を小さく見ない。
HF のものは index の size が無いので `size_floor_bytes` は null(自動のおすすめ(#3)では「大きさが分からない」と同じ扱い)。

精度(precision)は次の順に決め、出どころを `precision_source` に残す(#30。[schema.ja.md](schema.ja.md)「Models」)。
見出しで読めた精度は、後の出どころで上書きしない。

1. `header`: 本体のファイルの見出し(safetensors の JSON・GGUF の頭。Range 要求で読む。`headers.py`)
2. `filename`: 本体のファイル名(nvfp4 / fp4 / nf4 / fp8 / int8 / int4 / bf16 / fp16 / fp32 / gguf-<量子化>)
3. `hf_api` / `config`: 本体を置いた HF のリポジトリ(`repo_precision.py`)。Hub API(`/api/models/<repo>`、匿名)の
   `safetensors.parameters` で一つの dtype が 9 割以上、かつ要素の数 × 1 要素のバイト数がこの項目の本体の
   safetensors(同じリポジトリの分割も足す)の大きさと 5% 以内で合えばそれ(gated でも読める。見出しは 401 で読めない)。
   無ければ本体と同じフォルダの `config.json` の `torch_dtype` / `dtype`(API の一覧にあって gated でないときだけ取る)。
   1 つずつ順に、間を 0.3 秒空けて送り、429 は Retry-After を守る。結果は `<cache>/repo_precision.json`
4. `workflow`: 公式テンプレートの本体の読み込みノード(`workflow.loader_precision`)。UNETLoader の `weight_dtype`・
   WanVideoModelLoader の `quantization` が fp8_* なら fp8、無ければ読み込むファイル名の精度の語、無ければ精度の指定
   (GIMM-VFI の `fp32`、WanVideo の `base_precision` など)。`default`・`auto` は何も言わない。LoRA・CLIP・VAE の
   読み込みノードは見ない。カスタムノードが自分で重みを取るテンプレート(ファイルの一覧が無いもの)にも使える

2026-10-20 に作った一覧(当時の同梱)では、無検閲を除く精度の不明は 25 → 10 件(見出し 3・Hub API 4・ワークフロー 8)。残る 10 件:
Civitai のチェックポイント(realismByStableYogi。名前に精度が無く、HF ではないので見出しも読まない)の 2 件、
読み込みノードが精度を言わないテンプレート 4 件(LivePortrait は `auto`、AnimateDiff の Purz は本体の一覧が無く
チェックポイントの名前に精度が無い、Lineart・OpenPose は前処理のノードが重みを自分で取る)、
.pth / .ckpt の HF のモデル 3 件(Kokoro・MeloTTS は config.json に dtype が無い、tts-hifigan は config.json が無い。
Hub API の safetensors の集計は .safetensors だけ)、csm-1b(Hub API は F32 で 62.1 億バイトぶんだが、本体の
2 つの分割の合計は 71.3 億バイトで合わない)。

パラメータ数(params_b)はファイル名・題の `14B` `1.3B` のような表記から推す(無ければ null)。

### 任意の部品

0.12.0 で足した(#81)。テンプレートの既定の値で読み込まれない部品のファイルに `optional`(と `optional_replaces`)を付ける
(`optional.optional_files`。update とテンプレートの読み直しが毎回作り直す。形は [schema.ja.md](schema.ja.md)「Models」、
見積もりへの効き方は [accuracy.ja.md](accuracy.ja.md)「ファイルの数え方」)。ComfyUI のコードは import せず、v0.43.0(426f5ee)の
動きをテンプレートの JSON の上でまねる。

1. **出発点**: ComfyUI の出力のノード(`OUTPUT_NODE`。型の表 `optional.OUTPUT_NODE_TYPES` は v0.43.0 の nodes.py・comfy_extras・
   comfy_api_nodes から読んだもの。ComfyUI は出力のノードを、出力がつながっていても全部実行する: execution.py:1183-1185)と、
   出力がどこにもつながっていないノード(表に無い拡張の出力のノードの代わり。ファイルを読むノード・メモ・Primitive は除く)。
   どのサブグラフの中でも探す。
2. **さかのぼり**: 出発点から入力のリンクを逆にたどる。サブグラフの実体は出口(-20)から中へ、中の入口(-10)から外の実体の
   入力へ出る。出口が見つからない壊れたサブグラフは、中の全部が使われるとみなす(安全側)。
3. **無効・素通し**: mode 2(無効)のノードはたどらない。mode 4(素通し)のノードは自分のファイルを数えず、入力はたどる
   (画面が API の prompt に入れないのと同じ)。サブグラフの実体の mode は、一番上の階層の実体のものだけが中身すべてに効く
   (入れ子の実体の mode は中身に効かない。画面の動きに合わせた)。
4. **スイッチ**: If/Else Switch(`ComfySwitchNode`)は、`switch` の値が決まっている(ノードの入力欄、サブグラフの実体の入力欄、
   Primitive のノード)ときだけ、選ばれない側をたどらない(lazy の入力。comfy_execution/graph.py:153-175、
   comfy_extras/nodes_logic.py:146-150)。Soft Switch(`ComfySoftSwitchNode`)は、選んだ側がつながっていない
   (または prompt に入らないノードにつながる)ならもう一方をたどる(nodes_logic.py:178-188)。値が決まらなければ両方たどる。
5. **分け方**: lazy の枝もたどる検証(execution.py:946-969。一覧に無いファイルは「Value not in list」: execution.py:1061-1085)で
   届かないファイルは `inactive`、実行では届かないが検証では届くファイルは `switch_off`。1 つのファイルを読むノードが複数あれば、
   どれか 1 つが使われれば任意にしない。
6. **入れ替え**: スイッチを 1 つずつ逆にして、読み込まなくなる既定の部品があれば `optional_replaces` に入れる
   (例: Pixal3D / Trellis2 は Trellis2 の本体が Pixal3D の本体の代わり)。無効のノードの部品は、本体の役割なら既定の本体の
   代わり(`kind: replace`)、ほかは足すだけ(`kind: add`)とみなす。
7. つながりの線を使わずに値を渡す拡張のノード(`SetNode` / `GetNode`・`Anything Everywhere`)があるテンプレート、出発点の無い
   テンプレートは分けない(全部を数える)。

同梱の一覧(テンプレート 278 件。分け方は Comfy-Org/workflow_templates の 0cc0fba の JSON で作った。ファイルの組は同梱の一覧と
全件同じ)では、任意の部品があるのは 66 件(`inactive` のあるもの 22 件・`switch_off` のあるもの 44 件)。多くは既定で切った
高速化の LoRA で、ほかに Qwen-Image 2.1 の指示の書き直し(`qwen3.5_9b_…_pe_*`、8.8 GB)・LTX-2 の distilled の本体・
Wan VACE 14B の 1.3B の枝・HunyuanVideo 1.5 720p の 1080p の仕上げ・FLUX.2 Klein の素通しにした本体など。

## ライセンス

一覧はソース非公開の商用アプリ(AI生成キット)の自動のおすすめに使う。**寛容の側に間違えることが一番悪い**ので、
確かめられないとき・表記が食い違うときは `license: null`(不明)にし、`license_evidence` に
`license_unverified: <理由>` を残す。決め方を変えたら `update.LICENSE_POLICY`(今は 5。4 は #28 で出どころの表と VOID の系統、5 は 0.1.1 のレビューで
表の上書きの行・系統の不明への戻し・同じ段階の中の順を足して上げた)を上げる。

### 段階

`hf.license_tier`。一式は一番厳しいもの。

| 段階 | 例 |
|---|---|
| 6 不明 | null |
| 5 非商用・研究用 | cc-by-nc*・名前に non-commercial / nc / research を含むもの(flux-1-dev-non-commercial-license・qwen-research・sai-nc-community・nsclv1 など) |
| 4 独自・条件付き | other(tencent-hunyuan-community・stable-audio-community・ltx-2-community-license-agreement・fair-ai-public-license など)・gpl 系・下に当たらないもの全部 |
| 3 用途の制限 | openrail 系(openrail++・creativeml-openrail-m)・llama*・gemma・名前に `ethical-restrictions` / `use-restrictions` を含むもの(寛容なライセンスにカードの本文が用途の制限を足したもの。Irodori-TTS の `mit-with-ethical-restrictions`) |
| 2 表示の義務 | cc-by-4.0 など・odc-by |
| 1 寛容 | apache-2.0・mit・bsd 系・isc・zlib・unlicense・cc0-1.0・bsl-1.0 など(`hf.PERMISSIVE`) |

`other` は `license_name`(独自ライセンスの名前)で 4 と 5 を分ける。同じ段階の中は名前の順(結果に影響しない)。

### 1 ファイルの決め方

`hf.HfClient.resolve_file`。

0. **本家を確かめられない部品**(`hf.UNVERIFIED_COMPONENTS`): どのリポジトリの中にあっても不明
   (`rt_detr_v4`。下の「部品の表」の後)
1. **部品の表**(`hf.COMPONENTS`): 再配布(持ち主が `Comfy-Org` / `comfyanonymous` / `Kijai`)の中の、ファイル名で本家が
   決まる部品(テキストエンコーダ・VAE など)は、表の本家のライセンス。再配布のリポジトリの表記や base_model は
   本体のものなので部品には当てない(HiDream の `llama_3.1_8b_instruct` が mit になっていた)。載せたのは、本家の
   ライセンスを HF の API で確かめ、複数の再配布で sha256 が同じ(手を加えていない写し)と確かめたもの
   (#14 で足した行は、本家のカードを確かめたもの)。寛容な行は再配布以外の持ち主の同名のファイルには当てない
   (改変されていることがある)。**寛容でない行・本家が申請を求める行(DINOv3・SAM 3・Llama・Gemma 3 など)は、
   どのリポジトリの中にあっても当てる**(改変しても規約は軽くならない。VAST-AI/TripoSplat の `dino_v3_vit_h` が
   mit になっていた)。申請を求める行は `license_evidence` に `upstream_gated: <本家>` を残し、gate の `gated` が保留にする
2. **再配布の本体**: `cardData.base_model`(と `base_model:…` のタグ)から、ファイルに対応する本家を選ぶ
   - 1 つならそれ。複数なら、ファイル名と語(英数字の塊。精度・形式の語と、`14b` `720p` `v2` のような大きさ・版の語は
     除く)が一番多く重なるもの(同点は全部を見て一番厳しいもの)
   - **どれとも重ならない・base_model が無い(表 `hf.REPACKAGED_UPSTREAM` にも無い)→ 不明**(再配布側の表記は使わない)
   - 本家が**読めない(401 / 403 / 404)・ライセンスの記載が無い → 不明**
3. **base_model の道**: 本家(再配布でなければ置き場所のリポジトリ)から `base_model` を **12 段まで**たどる
   (`base_model` は 1 つでも一覧でもよい。`base_model:finetune:` / `quantized:` / `adapter:` / `merge:` のタグも見る)。
   道のどこかが読めない・記載が無い・12 段を超える → 不明。**自分の表記が、元(base_model)の一番厳しいものより寛容なら
   不明**(派生は元より寛容にならない。例: Quiho の apache-2.0 は元の krea/Krea-2-Turbo の other より寛容)。
   同じか厳しければ自分の表記
4. **系統の表**(`hf.FAMILIES`): リポジトリ名・ファイル名・たどった base_model の名前で元のモデルの系統が分かるとき、
   表記が系統のライセンスより寛容なら不明(例: AtomicChat の Qwen-Image-2.1 は base_model に Qwen3-VL しか書いていない
   が、名前で Qwen-Image-2.1(qwen-research)と分かる)。当たった系統は `license_evidence` に `family:<名前>`
5. **HF で見つけた無検閲のモデル**で、base_model も系統も分からなければ不明(無検閲化は元のモデルの派生なので、
   本人の表記だけでは確かめられない)。無検閲でないもの(読み上げなど)は本人の表記

3 と 4 の間で、さらに次の 2 つを見る(#14 のレビュー):

- **元のライセンスを読めない系統**(`hf.LINEAGES`): 名前(4 と同じ)が SDPose・Lotus・Stable Diffusion 2・VOID なら不明
  (下の「元のライセンスを読めない系統」)
- **別の系統の部品**(`hf.FOREIGN_MARKERS`): ファイル名に別のモデルの印(dino・sam・siglip・clip・t5・qwen・llama・
  gemma・mistral・glm・birefnet・depth-anything・moge・detr・flux・wan・trellis・hunyuan・stable-diffusion・lumina・
  ltx・cogvideo・whisper・wav2vec)があり、その印が置き場所のリポジトリ・ライセンスを読んだ道の名前に出てこなければ、
  ファイル名そのものに部品の表か系統の表の行が当たるときだけ通す(部品の表の行なら、置き場所の表記より厳しいか
  同じときはその本家のライセンス)。当たらなければ不明。リポジトリの表記を、中に同梱された別のモデルの部品に付けない

ファイルには `license` / `license_name` / `license_url`、`license_from`(ライセンスを読んだリポジトリ。部品の表なら表の本家)、
`license_path`(たどった道: 置き場所 → base_model … → 決め手)、`license_evidence`、`repo_license`(置き場所の表記が
決め手と違うときの置き場所の表記)を入れる。HF 以外のファイル(Civitai など)は不明。

上の決め方で不明になったファイルには、最後に**出どころの表**(`license_sources.SOURCES`。下の「出どころの表」)を当てる。
表の行のうち**上書きの行**(`override=True`)は、自動で決まっていても当てる(0.1.1 のレビュー)。自動の決め方が
cardData の寛容な表記だけを見て本文の規約を見落とすもの(ChronoEdit の LoRA: cardData は apache-2.0、本文は
「Governing Terms: … NVIDIA Open Model License」。nvidia/ChronoEdit-14B-Diffusers の lora/ と sha256 が同じ)と、
違う元をたどるもの(InfiniteTalk → MeiGen-AI/InfiniteTalk、Wan 2.1 I2V 480P → Wan-AI/Wan2.1-I2V-14B-480P)だけ。
前に表で決めたファイル(`license_source:<行>` の印)は、表の行を直したら当て直す。
元のライセンスを読めない系統(`hf.LINEAGES`。VOID など)のファイルは、自動で決まっていても不明に戻す
(`license_sources.enforce_lineage`。gate と同じ判定なので、一覧のデータと gate の答えがそろう)。
一式のライセンス(`ModelEntry.license`)は一番厳しいファイルのもの。同じ段階(寛容など)の中では義務の多いもの
(apache-2.0 > bsd-3-clause > mit・bsd-2-clause …。`hf.OBLIGATION_RANK`)を出す(apache-2.0 と mit の一式が
「mit」と出ていた)。ファイルごとの値は JSON の `licenses` に全部ある。

### 部品の表

2026-10-20 に確認。再配布の中のファイルだけに当てる(行に「どこでも」とあるものを除く)。

| ファイル名 | 本家 | ライセンス | sha256 の確認 |
|---|---|---|---|
| `llama_3.1_8b_instruct*` | [meta-llama/Llama-3.1-8B-Instruct](https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct) | llama3.1 | (1 か所) |
| `t5xxl_{fp16,fp8_e4m3fn_scaled,…}` | [google/t5-v1_1-xxl](https://huggingface.co/google/t5-v1_1-xxl) | apache-2.0 | HiDream と flux_text_encoders で同じ |
| `umt5_xxl_*`・`umt5-xxl-enc-*` | [google/umt5-xxl](https://huggingface.co/google/umt5-xxl) | apache-2.0 | Wan 2.1 と Wan-Animate-2 で同じ |
| `clip_l` | openai/clip-vit-large-patch14(HF に記載なし。[GitHub の LICENSE](https://github.com/openai/CLIP/blob/main/LICENSE)) | mit | |
| `clip_g`・`clip_vision_g` | [laion/CLIP-ViT-bigG-14-laion2B-39B-b160k](https://huggingface.co/laion/CLIP-ViT-bigG-14-laion2B-39B-b160k) | mit | |
| `clip_vision_h`・`CLIP-ViT-H-14-laion2B-s32B-b79K` | [laion/CLIP-ViT-H-14-laion2B-s32B-b79K](https://huggingface.co/laion/CLIP-ViT-H-14-laion2B-s32B-b79K) | mit | 2 か所で同じ |
| `sigclip_vision_patch14_384` | [google/siglip-so400m-patch14-384](https://huggingface.co/google/siglip-so400m-patch14-384) | apache-2.0 | 2 か所で同じ |
| `qwen_2.5_vl_7b_fp8_scaled` | [Qwen/Qwen2.5-VL-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct) | apache-2.0 | Qwen-Image と HunyuanVideo 1.5 で同じ |
| `qwen_2.5_vl_fp16`(OmniGen2。7.5 GB = 3B) | [Qwen/Qwen2.5-VL-3B-Instruct](https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct) | other(qwen-research) | |
| `qwen_3_4b`・`qwen_3_4b_fp8_mixed` | [Qwen/Qwen3-4B](https://huggingface.co/Qwen/Qwen3-4B) | apache-2.0 | FLUX.2 klein 4B と Z-Image で同じ |
| `qwen3vl_8b_fp8_scaled` | [Qwen/Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct) | apache-2.0 | Comfy-Org/Qwen3-VL と Boogu で同じ |
| `gemma_3_12B_it*` | [google/gemma-3-12b-it](https://huggingface.co/google/gemma-3-12b-it) | gemma | |
| `wav2vec2_large_english*` | [jonatasgrosman/wav2vec2-large-xlsr-53-english](https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-english) | apache-2.0 | |
| `ae`(FLUX.1 の VAE) | [black-forest-labs/FLUX.1-schnell](https://huggingface.co/black-forest-labs/FLUX.1-schnell) | apache-2.0 | HiDream・Lumina 2・Z-Image で同じ |
| `flux2-vae` | [black-forest-labs/FLUX.2-VAE](https://huggingface.co/black-forest-labs/FLUX.2-VAE) | apache-2.0 | flux2-dev・ERNIE・Lens で同じ |
| `flux2-vae_small` | [black-forest-labs/FLUX.2-small-decoder](https://huggingface.co/black-forest-labs/FLUX.2-small-decoder) | apache-2.0 | |
| `wan_2.1_vae`・`Wan2_1_VAE_*` | [Wan-AI/Wan2.1-T2V-14B](https://huggingface.co/Wan-AI/Wan2.1-T2V-14B) | apache-2.0 | 3 か所で同じ |
| `wan2.2_vae` | [Wan-AI/Wan2.2-TI2V-5B](https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B) | apache-2.0 | |
| `qwen_image_vae` | [Qwen/Qwen-Image](https://huggingface.co/Qwen/Qwen-Image) | apache-2.0 | 5 か所で同じ |
| `RealESRGAN_x4plus*` | xinntao/Real-ESRGAN([GitHub の LICENSE](https://github.com/xinntao/Real-ESRGAN/blob/master/LICENSE)) | bsd-3-clause | |
| `dino_v3*`・`dinov3*`(どこでも。申請が要る) | [facebook/dinov3-vitl16-pretrain-lvd1689m](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m)(gated: manual。[原文](https://ai.meta.com/resources/models-and-libraries/dinov3-license)) | other(dinov3-license) | (本家のカード) |
| `sam3*`(どこでも。申請が要る) | [facebook/sam3](https://huggingface.co/facebook/sam3)(gated: manual) | other(sam-license) | (本家のカード) |
| `depth_anything_v2_vit{b,l,g}`(どこでも) | [depth-anything/Depth-Anything-V2-Large](https://huggingface.co/depth-anything/Depth-Anything-V2-Large) | cc-by-nc-4.0 | (本家のカード) |
| `depth_anything_v2_vits` | [depth-anything/Depth-Anything-V2-Small](https://huggingface.co/depth-anything/Depth-Anything-V2-Small) | apache-2.0 | (本家のカード) |
| Lumina 2 系の一式(`lumina_2.safetensors`・`…all_in_one`)(どこでも) | Gemma 2 2B を含む([neta-art/Neta-Lumina](https://huggingface.co/neta-art/Neta-Lumina)・[NetaYume のカード](https://huggingface.co/duongve/NetaYume-Lumina-Image-2.0)) | gemma | (カード) |
| `dinov2*` | [facebook/dinov2-large](https://huggingface.co/facebook/dinov2-large) | apache-2.0 | (本家のカード) |
| `sam_vit_*`・`sam2*_hiera*` | [facebook/sam-vit-huge](https://huggingface.co/facebook/sam-vit-huge)・[facebook/sam2.1-hiera-large](https://huggingface.co/facebook/sam2.1-hiera-large) | apache-2.0 | (本家のカード) |
| `siglip*` | [google/siglip-so400m-patch14-384](https://huggingface.co/google/siglip-so400m-patch14-384)・[google/siglip2-so400m-patch16-512](https://huggingface.co/google/siglip2-so400m-patch16-512) | apache-2.0 | (本家のカード) |
| `birefnet` | [ZhengPeng7/BiRefNet](https://huggingface.co/ZhengPeng7/BiRefNet) | mit | (本家のカード) |
| `moge_1_vitl*`・`moge_2_vitl_normal*` | [Ruicheng/moge-vitl](https://huggingface.co/Ruicheng/moge-vitl)・[Ruicheng/moge-2-vitl-normal](https://huggingface.co/Ruicheng/moge-2-vitl-normal) | mit | (本家のカード) |
| `trellis_2_{shape,texture}_vae*` | [microsoft/TRELLIS.2-4B](https://huggingface.co/microsoft/TRELLIS.2-4B) | mit | (本家のカード。Pixal3D はこれを土台にしている) |
| `qwen_{0.6b,1.7b,4b}_ace15` | [ACE-Step/Ace-Step1.5](https://huggingface.co/ACE-Step/Ace-Step1.5)(Qwen3-Embedding-0.6B と 5Hz の LM を含む。元の [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)・[Qwen3-1.7B](https://huggingface.co/Qwen/Qwen3-1.7B) は apache-2.0) | mit | (本家のカード) |

本家を確かめられない部品(`hf.UNVERIFIED_COMPONENTS`。どこにあっても不明):

| ファイル名 | 理由 |
|---|---|
| `rt_detr_v4*` | RT-DETRv4(https://github.com/RT-DETRs/RT-DETRv4)。コードは Apache-2.0 だが、重みは DINOv3 を教師にした蒸留で作られ、重みのライセンスの記載が無い。Comfy-Org/SDPose の中にあるが SDPose の一部ではない(Comfy-Org/SDPose の README が本家を別に挙げる) |

部品の表に入れなかったもの(確かめきれなかったもの。どれも置き場所の表記のほうが厳しい): Qwen-Image-2.1 の
`qwen3vl_8b_int8_convrot`(写しが 1 か所だけ)、FLUX.2 dev の `mistral_3_small_flux2_*`、Stable Audio 3 の `t5gemma_b_b_ul2`、
NewBie の `gemma_3_4b_it_bf16`、FLUX.2 klein 9B の `qwen_3_8b*`、Krea-2・Mage-Flow の `qwen3vl_4b_*`、HunyuanImage 2.1 の
`qwen_2.5_vl_7b`(bf16)、HunyuanVideo の `llava_llama3`。このうち `mistral_3_small_flux2_*`・`t5gemma_b_b_ul2`・
`gemma_3_4b_it_*`・`qwen_3_8b*`・Krea-2 の `qwen3vl_4b_*`・HunyuanImage 2.1 の `qwen_2.5_vl_7b` は、下の「出どころの表」で決める
(置き場所とファイル名の組で当てる)。

### 系統の表

名前で当てる。表記がこれより寛容なら不明。

| 系統 | 名前の例 | ライセンス | 確かめた場所 |
|---|---|---|---|
| flux.1-dev | flux1-dev・flux.1dev・FLUX.1-Krea-dev・flux1-dev-kontext | other(flux-1-dev-non-commercial-license) | https://huggingface.co/black-forest-labs/FLUX.1-dev |
| flux.1-schnell | flux1-schnell | apache-2.0 | https://huggingface.co/black-forest-labs/FLUX.1-schnell |
| flux.2-dev | flux2_dev | other(flux-non-commercial-license) | https://huggingface.co/black-forest-labs/FLUX.2-dev |
| flux.2-klein-9b | klein-9b・klein-base-9b | other(flux-non-commercial-license) | https://huggingface.co/black-forest-labs/FLUX.2-klein-9B |
| flux.2-klein-4b | klein-4b | apache-2.0 | https://huggingface.co/black-forest-labs/FLUX.2-klein-4B |
| qwen-image-2.1 | Qwen-Image-2.1・qwen_image_2.1 | other(qwen-research) | https://huggingface.co/Qwen/Qwen-Image-2.1 |
| qwen-image | Qwen-Image・qwen_image_edit・2512 | apache-2.0 | https://huggingface.co/Qwen/Qwen-Image |
| krea-2 | Krea-2・krea2 | other(krea-2-community-license) | https://huggingface.co/krea/Krea-2-Turbo |
| sdxl | sdxl・sd_xl・stable-diffusion-xl | openrail++ | https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0 |
| sd1.5 | sd15・sd-1.5・stable-diffusion-v1-5 | creativeml-openrail-m | https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5 |
| sd3.5 | sd3.5・stable-diffusion-3.5 | other(stabilityai-ai-community) | https://huggingface.co/stabilityai/stable-diffusion-3.5-large |
| illustrious-noobai | Illustrious・NoobAI | other(fair-ai-public-license-1.0-sd) | https://huggingface.co/OnomaAIResearch/Illustrious-xl-early-release-v0 ・ https://huggingface.co/Laxhar/noobai-XL-1.1 |
| hunyuan | HunyuanVideo・Hunyuan3D・HunyuanImage | other(tencent-hunyuan-community) | https://huggingface.co/tencent/HunyuanVideo ・ tencent/Hunyuan3D-2 ・ tencent/HunyuanVideo-1.5 |
| stable-audio | stable-audio-open・stable_audio_3 | other(stable-audio-community) | https://huggingface.co/stabilityai/stable-audio-open-1.0 ・ stabilityai/stable-audio-3-medium-base |
| ltx-2 | LTX-2・ltx-2.3・LTXV2 | other(ltx-2-community-license-agreement) | https://huggingface.co/Lightricks/LTX-2 |
| ltx-video | LTX-Video・ltxv | other(名前の記載なし) | https://huggingface.co/Lightricks/LTX-Video |
| wan | Wan2.1・wan2_2 | apache-2.0 | https://huggingface.co/Wan-AI/Wan2.1-T2V-14B ・ Wan-AI/Wan2.2-T2V-A14B |
| hidream | HiDream(本体の DiT だけ。テキストエンコーダは部品の表) | mit | https://huggingface.co/HiDream-ai/HiDream-I1-Full |
| z-image | Z-Image・z_image | apache-2.0 | https://huggingface.co/Tongyi-MAI/Z-Image-Turbo |
| chroma | Chroma | apache-2.0 | https://huggingface.co/lodestones/Chroma |
| marigold-v1 | marigold(v2 を除く) | openrail++ | https://huggingface.co/prs-eth/marigold-depth-v1-1(v2 の huawei-bayerlab/marigold-v2-0 は Qwen-Image-Edit-2509 から作った apache-2.0) |
| qwen3-vl | Qwen3-VL・qwen3vl | apache-2.0 | https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct ・ -4B ・ -2B ・ -32B |

### 元のライセンスを読めない系統

`hf.LINEAGES`。名前が出たら不明。

| 系統 | 名前の例 | 根拠 |
|---|---|---|
| sdpose | SDPose・sdpose_wholebody | https://huggingface.co/teemosliang/SDPose-Wholebody のカード「U-Net backbone initialized with Stable Diffusion v2 weights」(表記は mit) |
| lotus | lotus-depth・lotus-normal | https://github.com/EnVision-Research/Lotus の学習の設定が stabilityai/stable-diffusion-2-base から始める(表記は apache-2.0) |
| sd2 | sd2・sd21・stable-diffusion-2 | 元の stabilityai/stable-diffusion-2・-2-base は 2026-10-20 に匿名で読めない(401)。写しの表記は openrail++ |
| void-model | void_pass1・netflix/void-model | https://huggingface.co/netflix/void-model のカード「VOID is built on CogVideoX-Fun-V1.5-5b-InP」。元の https://huggingface.co/alibaba-pai/CogVideoX-Fun-V1.5-5b-InP の LICENSE は The CogVideoX License(5B の Transformer。商用は登録・月 100 万アクセスの上限)。VOID の表記は apache-2.0(#28) |

系統の表は「少なくともこれだけ厳しい」という下限で、ライセンスを決めるのには使わない(寛容な系統に当たっても、
派生の表記が確かめられたことにはならない。例: Wan の系統でも、Comfy-Org/Wan-Dancer の base_model(Wan-AI/Wan2.2-Dancer-14B)は
読めないので自動では不明。本当の本家 Wan-AI/Wan-Dancer-14B は下の出どころの表で確かめた)。

### 出どころの表

`license_sources.SOURCES`。#28、2026-10-20 に確認。

再配布のリポジトリ(Comfy-Org・Kijai など)は base_model を書いていない・ファイルと合わない base_model を書いている
ことが多く、上の決め方では不明になる(2026-10-20 に作った一覧(当時の同梱)で、無検閲を除く 299 項目のうち 113 項目)。そこで、
不明になったファイルについて、人が**一次資料**で出どころをたどり、確かめられたものだけを表にした。

たどり方の決まり(寛容の側に間違えない):

1. 一次資料は、元のモデルの HF のカード(cardData の license と本文)・LICENSE・公式の GitHub の LICENSE と README・
   公式のコードだけ。再配布の README は「元はどれか」を知る手がかりにだけ使い、そこに書いてあるライセンスは使わない
   (例: Comfy-Org/Lens の README は FLUX.2-dev を apache-2.0 と書いているが、本当は非商用)
2. 「この置き場所のこのファイルは、この元の重み」と次のどれかで確かめる: sha256 が元のファイルと同じ / 公式の README・
   コードが置き場所かファイルを名指しする / 公式のコードが元のリポジトリから手を加えずに読む / safetensors のヘッダ
   (Range 要求で読む)で重みの名前・形が元と同じ / 再配布の README が元を挙げ、大きさが合う
3. 元のライセンスは書いてあるとおり。カードの cardData と本文が食い違うときは厳しいほう(ChronoEdit は cardData が
   apache-2.0 だが、本文が NVIDIA Open Model License)。独自のライセンスは `other` と名前で持つ
4. 派生(LoRA・抽出・量子化)は、元の元(base_model)のライセンスより寛容にならないことを確かめる。元より寛容な表記しか
   無いもの(USO・ObjectRemovalFluxFill・LTX-2.3-Transition・MiniMax-H3 Turbo)・LoRA の置き場所にライセンスの記載が
   無いもの(Anima の公式 LoRA・ByteZSzn の Flux2TurboComfyv2)は表に入れない(不明のまま)
5. 寛容なライセンスの行は、根拠の文にそのライセンス(apache-2.0 / Apache License 2.0 など)が書いてあるものだけ
   (tests/test_license_sources.py が検査)
6. 行は「置き場所のリポジトリ」と「ファイル名」の組で当てる。同じ名前でも別の置き場所のファイルには当てない。
   本家を確かめられない部品・元を読めない系統の名前のファイルには当てない

当てたファイルには `license_from`(元)・`license_url`・`license_path`(置き場所 → … → 元)と、`license_evidence` に
`license_source:<行>` と根拠の文(URL・書いてあること・確かめた日)を入れる。`update` は自動で決めたあと不明のものに
だけ当てる。同梱の一覧には、ネットに触らずに `license_sources.apply_license_sources(db)` で当てられる。
`python -m genmodelfit.license_sources` は、当てたときに不明・auto の項目が何件変わるかを出す(一覧は書き換えない)。

| 置き場所 | ファイル名 | ライセンス | 元(一次資料) | 確かめ方 |
|---|---|---|---|---|
| Kijai/WanVideo_comfy | `Wan21_CausVid_14B_T2V_lora_rank*` | cc-by-nc-4.0 | [lightx2v/Wan2.1-T2V-14B-CausVid](https://huggingface.co/lightx2v/Wan2.1-T2V-14B-CausVid) | Kijai の README「CausVid LoRAs are experimental extractions from the CausVid finetunes」と元のリンク |
| Kijai/WanVideo_comfy | `Wan21_CausVid_bidirect2_T2V_1_3B_lora_rank*` | cc-by-nc-4.0 | [tianweiy/CausVid](https://huggingface.co/tianweiy/CausVid) | 同上(「CausVid 1.3B」) |
| Kijai/WanVideo_comfy | `Wan21_T2V_14B_lightx2v_cfg_step_distill_lora_rank*` | apache-2.0 | [lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill](https://huggingface.co/lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill)(LICENSE.txt は Apache 2.0) | Kijai の README「CFG and Step distill 14B」 |
| Kijai/WanVideo_comfy・Comfy-Org/Wan-Animate-2 | `lightx2v_I2V_14B_480p_cfg_step_distill_rank*_bf16` | apache-2.0 | [lightx2v/Wan2.1-I2V-14B-480P-StepDistill-CfgDistill-Lightx2v](https://huggingface.co/lightx2v/Wan2.1-I2V-14B-480P-StepDistill-CfgDistill-Lightx2v) | Kijai の Lightx2v/README.md「Extracted from …/distill_models」。Comfy-Org の写しは sha256 が同じ |
| Kijai/WanVideo_comfy | `lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank*_bf16` | apache-2.0 | [lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill-Lightx2v](https://huggingface.co/lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill-Lightx2v) | Kijai の Lightx2v/README.md「Extracted from …/distill_model.safetensors」 |
| Kijai/WanVideo_comfy | `WanAnimate_relight_lora_fp16` | apache-2.0 | [Wan-AI/Wan2.2-Animate-14B](https://huggingface.co/Wan-AI/Wan2.2-Animate-14B) の relighting_lora | ヘッダで 960 個の重みの形が同じ(名前の付け方と F32→F16 だけ違う) |
| Kijai/WanVideo_comfy_fp8_scaled | `Wan21-WanMove_fp8_scaled_e4m3fn_KJ` | apache-2.0 | [Ruihang/Wan-Move-14B-480P](https://huggingface.co/Ruihang/Wan-Move-14B-480P) | 公式 https://github.com/ali-vilab/Wan-Move の README が Kijai のこのフォルダを挙げる |
| Comfy-Org/Wan-Animate-2 | `wan_animate_2(_distill)_*` | apache-2.0 | [Wan-AI/Wan2.2-Animate-2-14B](https://huggingface.co/Wan-AI/Wan2.2-Animate-2-14B) | 再配布の README の元 |
| Comfy-Org/MiniMax-Music-3 | `minimax_music3_*` | other(minimax-music3-community-license) | [MiniMaxAI/MiniMax-Music3 の LICENSE](https://huggingface.co/MiniMaxAI/MiniMax-Music3/blob/main/LICENSE)(UI への表示・年 2,000 万ドル超は許可) | 再配布の README の元 |
| Comfy-Org/HunyuanVideo_repackaged・Kijai/HunyuanVideo_comfy | `hunyuan_video_t2v_720p_*`・`hunyuan_video_vae_*` | other(tencent-hunyuan-community) | [tencent/HunyuanVideo](https://huggingface.co/tencent/HunyuanVideo) | 再配布の README の元 |
| Comfy-Org/NewBie-image-Exp0.1_repackaged | `gemma_3_4b_it_*` | gemma(元は申請が要る) | [google/gemma-3-4b-it](https://huggingface.co/google/gemma-3-4b-it) | 元の NewBie-AI/NewBie-image-Exp0.1 のカード「We use Gemma3-4B-it as the primary text encoder」 |
| Comfy-Org/NewBie-image-Exp0.1_repackaged | `jina_clip_v2_*` | cc-by-nc-4.0 | [jinaai/jina-clip-v2](https://huggingface.co/jinaai/jina-clip-v2) | 同じカード「Jina CLIP v2」 |
| Kijai/LTXV2_comfy | `ltx-2-19b-*_transformer_only_*`・`ltx-2-19b-embeddings_connector_*` | other(ltx-2-community-license-agreement) | [Lightricks/LTX-2](https://huggingface.co/Lightricks/LTX-2) | Kijai の README「extracted from their respective original models」 |
| Comfy-Org/ltx-2 | `gemma-3-12b-it-abliterated_lora_rank*` | gemma | [mlabonne/gemma-3-12b-it-abliterated](https://huggingface.co/mlabonne/gemma-3-12b-it-abliterated) | 再配布の cardData の base_model |
| Comfy-Org/HunyuanImage_2.1_ComfyUI | `qwen_2.5_vl_7b` | apache-2.0 | [Qwen/Qwen2.5-VL-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct) | 公式 https://github.com/Tencent-Hunyuan/HunyuanImage-2.1/blob/master/ckpts/checkpoints-download.md が `hf download Qwen/Qwen2.5-VL-7B-Instruct` |
| circlestone-labs/Anima | `qwen_3_06b_base` | apache-2.0 | [Qwen/Qwen3-0.6B-Base](https://huggingface.co/Qwen/Qwen3-0.6B-Base) | sha256 が同じ |
| Comfy-Org/Wan_2.2_ComfyUI_Repackaged | `chrono_edit_14B_*`・`chronoedit_distill_lora` | other(nvidia-open-model-license)(上書きの行) | [nvidia/ChronoEdit-14B-Diffusers](https://huggingface.co/nvidia/ChronoEdit-14B-Diffusers)(本文「Governing Terms: … NVIDIA Open Model License Agreement」) | 本体は再配布の README の元。LoRA は sha256 が元の lora/chronoedit_distill_lora.safetensors と同じ |
| Comfy-Org/Wan_2.1_ComfyUI_repackaged | `wan2.1_infiniteTalk_{multi,single}_fp16` | apache-2.0(上書きの行) | [MeiGen-AI/InfiniteTalk](https://huggingface.co/MeiGen-AI/InfiniteTalk)(「The models in this repository are licensed under the Apache 2.0 License」) | 再配布の README の元(自動の決め方は別の元をたどっていた) |
| Comfy-Org/Wan_2.1_ComfyUI_repackaged・Kijai/WanVideo_comfy_fp8_scaled | `wan2.1_i2v_480p_14B_*`・`Wan2_1-I2V-14B-480p_fp8_e4m3fn_scaled_KJ` | apache-2.0(上書きの行) | [Wan-AI/Wan2.1-I2V-14B-480P](https://huggingface.co/Wan-AI/Wan2.1-I2V-14B-480P)(LICENSE.txt。「licensed under the Apache 2.0 License」) | 再配布の README の元。Kijai のものはヘッダに img_emb があり VACE の層が無い(自動の決め方は 720P・VACE をたどっていた) |
| Comfy-Org/ERNIE-Image | `ministral-3-3b` | apache-2.0 | [baidu/ERNIE-Image](https://huggingface.co/baidu/ERNIE-Image) の text_encoder(Ministral 3 3B。mistralai の Ministral-3-3B は全部 apache-2.0) | 再配布の README の元・text_encoder/config.json |
| ByteZSzn/Flux.2-Turbo-ComfyUI・Comfy-Org/flux2-dev | `Flux_2-Turbo-LoRA_comfyui` | other(flux-dev-non-commercial-license) | [fal/FLUX.2-dev-Turbo](https://huggingface.co/fal/FLUX.2-dev-Turbo) | sha256 が fal の comfy/ のファイルと同じ |
| Comfy-Org/flux2-dev | `mistral_3_small_flux2_*` | apache-2.0 | [mistralai/Mistral-Small-3.2-24B-Instruct-2506](https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506) | 公式のコード black-forest-labs/flux2 の text_encoder.py(コミット 50fe516)がこれをそのまま読む |
| Comfy-Org/flux2-klein-9B・Comfy-Org/vae-text-encorder-for-flux-klein-9b | `qwen_3_8b*` | apache-2.0 | [Qwen/Qwen3-8B](https://huggingface.co/Qwen/Qwen3-8B) | 公式のコード util.py の klein-9b が Qwen3-8B を読む。大きさが分割の合計と合う |
| black-forest-labs/flux-3-image-klein-comfy | `qwen3.5_9b_flux3_prompt_upsampler_*` | other(flux-kommunity-license) | 置き場所(BFL 自身)の cardData | 本家自身のリポジトリ。元の Qwen3.5(apache-2.0)より厳しい |
| Comfy-Org/MOSS-Image | `qwen3.5_9b_*` | apache-2.0 | [OpenMOSS-Team/MOSS-Image-Base](https://huggingface.co/OpenMOSS-Team/MOSS-Image-Base) の text_encoder(Qwen3.5-9B。Qwen/Qwen3.5-9B も apache-2.0) | 再配布の base_model・text_encoder/config.json |
| Kijai/SUPIR_pruned | `SUPIR-v0[QF]_*` | other(supir-non-commercial) | https://github.com/Fanghua-Yu/SUPIR の「Non-Commercial Use Only Declaration」 | 公式の重みの名前 |
| Comfy-Org/stable-audio-3 | `t5gemma_b_b_ul2` | gemma(元は申請が要る) | [google/t5gemma-b-b-ul2](https://huggingface.co/google/t5gemma-b-b-ul2) | 公式 Stability-AI/stable-audio-tools の docs/conditioning.md |
| OpenMOSS-Team/MOSS-Audio-Tokenizer | `model-0000[12]-of-00002` | apache-2.0 | 置き場所自身のカード(`license: apache-2.0`) | MOSS-TTS v1.5 の processor が既定で読む(0.14.0、#142) |
| ResembleAI/chatterbox | `ve.pt`・`s3gen.pt`・`conds.pt` | mit | 置き場所自身のカード(`license: mit`) | 公式の src/chatterbox/mtl_tts.py が読む(0.14.0、#142) |
| Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice・-0.6B-CustomVoice | `speech_tokenizer/model.safetensors` | apache-2.0 | [Qwen/Qwen3-TTS-Tokenizer-12Hz](https://huggingface.co/Qwen/Qwen3-TTS-Tokenizer-12Hz)(`license: apache-2.0`) | sha256 が同じ(0.14.0、#142) |
| openbmb/VoxCPM2 | `audiovae.pth` | apache-2.0 | 置き場所自身のカード(`license: apache-2.0`) | 公式の src/voxcpm/model/voxcpm2.py が読む(0.14.0、#142) |
| FunAudioLLM/Fun-CosyVoice3-0.5B-2512 | `llm.pt`・`flow.pt`・`hift.pt`・`campplus.onnx`・`speech_tokenizer_v3.onnx` | apache-2.0 | 置き場所自身のカード(`license: apache-2.0`) | 公式の cosyvoice/cli/cosyvoice.py が読む(0.14.0、#142) |
| k2-fsa/OmniVoice | `audio_tokenizer/model.safetensors`(パス全体で当てる) | other(boson-higgs-audio-2-community-license) | [bosonai/higgs-audio-v2-tokenizer](https://huggingface.co/bosonai/higgs-audio-v2-tokenizer)(同じフォルダの LICENSE「BOSON HIGGS AUDIO 2 COMMUNITY LICENSE AGREEMENT」。Llama 3 の規約が元で、年間 100,000 人を超えると別の許諾) | sha256 が同じ(0.14.0、#142) |
| Serveurperso/Qwen3-TTS-GGUF | `qwen-tokenizer-12hz-*.gguf` | apache-2.0 | [Qwen/Qwen3-TTS-Tokenizer-12Hz](https://huggingface.co/Qwen/Qwen3-TTS-Tokenizer-12Hz)(`license: apache-2.0`) | 置き場所のカード「Audio codec : Qwen3-TTS-Tokenizer-12Hz (Qwen team), Apache 2.0」(0.14.0、#142) |
| Comfy-Org/void-model | `cogvideox_vae` | apache-2.0 | [zai-org/CogVideoX-2b](https://huggingface.co/zai-org/CogVideoX-2b)「including its corresponding Transformers module and VAE module … Apache 2.0」 | sha256 が alibaba-pai/CogVideoX-Fun-V1.5-5b-InP の VAE と同じ。2b と 5b の VAE は同じ |
| Comfy-Org/HuMo_ComfyUI | `whisper_large_v3_fp16` | apache-2.0 | [openai/whisper-large-v3](https://huggingface.co/openai/whisper-large-v3) | sha256 が同じ |
| Comfy-Org/PixelDiT | `gemma_2_2b_it_elm_*` | gemma(元は申請が要る) | [google/gemma-2-2b-it](https://huggingface.co/google/gemma-2-2b-it) | 元の nvidia/PixelDiT-1300M-1024px のカード「Text encoder: Gemma-2-2B-IT」 |
| Comfy-Org/PixelDiT | `pid_flux1_*` | other(nsclv1) | [nvidia/PiD](https://huggingface.co/nvidia/PiD)(非商用) | 再配布の base_model |
| Comfy-Org/Wan-Dancer | `wan2.2_dancer_14b_{global,local}_*` | apache-2.0 | [Wan-AI/Wan-Dancer-14B](https://huggingface.co/Wan-AI/Wan-Dancer-14B) | 公式 https://github.com/Wan-Video/Wan-Dancer の README の HF のリンク |
| Comfy-Org/Krea-2 | `krea2_style_reference` | other(krea-2-community-license) | [ostris/krea2_turbo_style_reference](https://huggingface.co/ostris/krea2_turbo_style_reference) | sha256 が同じ |
| Comfy-Org/Krea-2 | `krea2_darkbrush` | other(krea-2-community-license) | [krea/Krea-2-LoRA-darkbrush](https://huggingface.co/krea/Krea-2-LoRA-darkbrush) | 再配布の README の元。大きさ・ヘッダが同じ |
| Comfy-Org/Krea-2 | `qwen3vl_4b_*` | other(krea-2-community-license) | [krea/Krea-2-Turbo](https://huggingface.co/krea/Krea-2-Turbo) の text_encoder | 元が同じ大きさのテキストエンコーダを自分のライセンスで配る(Qwen3-VL の apache-2.0 より厳しい) |
| k2-fsa/OmniVoice | `model.safetensors` | other(cc-by-nc。版の記載なし) | https://github.com/k2-fsa/OmniVoice の README「The pre-trained model is licensed under the CC-BY-NC」 | 公式の README |
| circlestone-labs/Anima-Official-LoRAs | `anima-*`(`anima-turbo-lora-v0.2` など) | other(circlestone-labs-non-commercial-v1.0) | [LICENSE.md](https://huggingface.co/circlestone-labs/Anima-Official-LoRAs/blob/main/LICENSE.md)(2026-05-26 のコミット c221e431c8) | 見出し「CircleStone Labs Non-Commercial License v1.0」、2.b「for Non-Commercial Purposes」だけ。カードのメタデータは空(#30 で確認) |

確かめきれず不明のままにしたもの: Comfy-Org/Mage-Flow(本家 microsoft/Mage-Flow が 401、GitHub も無い)、Comfy-Org/Lens
(microsoft/Lens が 401。GitHub は MIT だが重みの記載が無い。gpt-oss も microsoft/Lens の text_encoder から読むので
openai/gpt-oss-20b そのままとは言えない)、FILM の `film_net_fp16`(重みは GitHub の外で、重みのライセンスの記載が無い)、
HunyuanVideo の `llava_llama3_*`(xtuner/llava-llama-3-8b にライセンスの記載が無い)、ByteZSzn の `Flux2TurboComfyv2`、
lightx2v/Minimax-h3-Turbo、Kokoro(元の StyleTTS2-LJSpeech に記載が無い)、SDPose・Lotus・
RT-DETRv4(上の系統・部品)、Civitai のファイル。
本家の持ち主への記載のお願いの下書きは [license_requests.ja.md](license_requests.ja.md)(出すのはメンテナの確認の後)。

**匿名で読めない(401)リポジトリの確かめ直し**(#30): `update` は Hub API が 401 を返したリポジトリ(非公開・消えた)を
`sources.huggingface.unauthorized` に並べる。`update --inspect-only` はそのリポジトリにもう一度 API を送り、
`sources.huggingface.unauthorized_recheck`(`unauthorized`・`private_or_missing`・`missing`・`readable`・`failed`・`checked_at`)に書く。
毎週の更新(#19・#42。メンテナの PC の起動スクリプト(タスク スケジューラ)が作り直し・試験・PR を行い、確認待ちの判断だけ
Claude Code のスキル genmodelfit-weekly に任せる。weekly-data.yml は手動の予備。[release.ja.md](release.ja.md))の PR の差分で、Mage-Flow・Lens などが公開されたら分かる(読めるようになったものは、次の作り直しでライセンスが入る)。
匿名の API は、非公開のリポジトリにも無いリポジトリにも 401 を返す(無い名前 this-org-zzz/does-not-exist-123 でも 401)
ので匿名では見分けられない。無いと確かめた名前だけを `repo_precision.KNOWN_MISSING` に書き、
`unauthorized_missing`(確かめ直しでは `missing`)に分ける。
2026-10-20 の時点: 非公開か無い = Wan-AI/Wan2.2-Dancer-14B、microsoft/Lens、microsoft/Mage-Flow。無い = google/gemma-3-12b
(Google の公開の一覧は gemma-3-12b-it・-pt と QAT 版だけ。-it / -pt の付け忘れの名前。どのカードの base_model から来たかは
記録が無い)。

表を 2026-10-20 に作った一覧(当時の同梱)に当てると、無検閲を除く 299 項目のライセンス不明は 113 → 46(ファイルは 75 → 29)、
auto(HEAD の期限を見ないとき)は 83 → 99。新しく auto になる 16 項目は、すべてのファイルのライセンスを人が確かめた
(tests/test_license_sources.py の `JUSTIFIED_AUTO`。ここに無い項目が auto になったら試験が落ちる)。

### 一式のライセンス

**すべてのファイル**(本体・テキストエンコーダ・VAE・LoRA・clip_vision・アップスケーラ・コントロールネットなど、
テンプレートが使うもの全部)の中で一番厳しいもの。ファイルが 1 つも書かれていなければ不明。`license_name` は
一番厳しいファイルのもの、`license_url` はそのファイルのライセンスの記載の場所。`license_evidence` には、ファイルの
`license_unverified` の理由をまとめる(10 件まで)。

## 言語・動かし方・説明と、手で足すモデル

0.5.0 で足した(#36)。

アプリが一覧だけで画面を作れるように、`ModelEntry` に任意の項目 `languages` / `runtime` / `description` を足した
(形は [schema.ja.md](schema.ja.md)「ModelEntry」。`schema_version` は 1 のまま。古い版は知らないキーを捨てるので読める)。
決めるのは `appinfo.apply_app_fields`(update が毎回当てる。`update --fields-only` でこれだけやり直せる。
そのときカードは読み直す。`--offline` なら通信せず、一覧に保存した `card_languages` を使う)。

### 言語

項目 `languages`。

当てるたびに材料から作り直す(前の値を引き継がない。表を狭めたり手の表から行を消したりすると、次の当て直しで直る)。
材料と優先順:

1. **手の表** `src/genmodelfit/data/overrides.json`(人が実機か一次資料で確かめたもの)。書いた種類(`prompt` / `speech`)はこれ。
   手のモデルは `src/genmodelfit/data/manual.json` に書いた値
2. **HF のカードの `language`**(本体のファイルの置き場所と、その元 `license_from` のカード。言語のあった最初のもの)。
   読めたものは項目 `card_languages` に保存して持つ(`[]` = 読んだが言語が無い、null = まだ読んでいない)。
   **カードが言語を書いていれば、それが上限**: 読み上げは `speech` = カードの値。指示の文は、文字を読む部品が
   すべて英語だけのもの(CLIP・T5)ならカードの言語のうち `en` だけ、それ以外はカードの値。
   **音楽はカードの言語のうち `en` だけを指示に使い、`speech`(歌の言語)はカードから決めない**(0.6.0、#41)。
   音楽のカードの `language` は歌詞の言語のことが多く、指示の言語とは限らない
   (https://huggingface.co/ACE-Step/ACE-Step-v1-3.5B のカードは 17 言語で、「Performance varies by language」は歌の話。
   歌の入らない `audio_ace_step_1_t2a_instrumentals` の指示にも 17 言語が付いていた)。歌の言語は手の表で書く
3. **カードが無い(言語が書いていない)ときだけ、文字を読む部品の表**: 表に当たれば `["en"]`。
   テンプレートの既定の値で読み込まない部品([「任意の部品」](#任意の部品)。0.12.0、#81)は材料にしない
   (カードを読む本体も、既定で読み込む本体だけ)。
   多言語の部品でも、表だけでは `ja` を付けない(部品が読める言語と、モデルが学習した説明文の言語は違う。
   Wan・Qwen-Image・HunyuanVideo 1.5 は多言語の部品を使うが、カードは `en` `zh`。Kandinsky 5・Anima のように
   日本語で確かめた資料が無いものに `ja` を付けないため)

決まらなければキーを付けない(当て推量しない)。`[]` は「その入出力が無い」と手で決めたもの
(画像から 3D は指示の文を取らない・歌の入らない音楽の `speech`)。指示の文を取る作業(`appinfo.PROMPT_TASKS`:
`t2i` `i2i` `t2v` `i2v` `v2v` `flf2v` `ref2v` `a2v` `t2a` `inpaint` `outpaint` `control`)が無いモデルには表を当てない。
コードは ISO 639-1 の 2 文字(カードの 3 文字コードは `eng→en` などに直す。`multilingual`・`und` は捨てる)。

文字を読む部品の表(`appinfo.TEXT_ENCODERS`。ファイル名の先頭。2026-10-21 にカードの原文を確かめた):

| 部品のファイル名 | 結果 | 根拠 |
|---|---|---|
| `umt5*` | 多言語 | https://huggingface.co/google/umt5-xxl のカードの `language` に `multilingual` と `ja` `zh` `en` ほか 100 言語超 |
| `qwen_3_*` | 多言語 | https://huggingface.co/Qwen/Qwen3-4B 「Support of 100+ languages and dialects」 |
| `qwen3.5*` | 多言語 | https://huggingface.co/Qwen/Qwen3.5-9B 「Expanded support to 201 languages and dialects」 |
| `qwen_2.5_vl*` `qwen2.5-vl*` | 多言語 | 元の LLM https://huggingface.co/Qwen/Qwen2.5-7B-Instruct 「over 29 languages, including … Japanese」(VL のカード自体には言語の記述が無い) |
| `clip_l*` `clip_g*` | 英語だけ | https://huggingface.co/openai/clip-vit-large-patch14 「not been purposefully trained in or evaluated on any languages other than English」 |
| `t5xxl*` `t5-base*` | 英語だけ | https://huggingface.co/google/t5-v1_1-xxl (`language: en`、C4) |
| 本体に CLIP が入った系統 `sd` | 英語だけ | 上と同じ CLIP |

「多言語」はカードの言語を狭めない、「英語だけ」はカードの言語を `en` に狭める、という意味で使う。
部品が複数あるとき: 多言語が 1 つでもあれば多言語、すべて英語の部品なら英語だけ、表に無い部品(Gemma・Mistral・Llama・
Qwen3-VL・gpt-oss など。カードに言語の記述が無い、または確かめていない)が混じれば決めない(カードがあればカードのまま)。

手の表 `src/genmodelfit/data/overrides.json` は 35 件(言語・説明の 16 件と、HF の読み上げの動かし方だけの 19 件。動かし方は「動かし方」の節。MeloTTS-English は言語の行に動かし方を足した)。言語・説明の 16 件のうち、アプリが実機で確かめた 9 件(AI生成キットの catalog.json の `notes` と実機の確認に合わせた):
Z-Image Turbo 2 件・Wan 2.1/2.2 は `prompt` が `en` `ja` `zh`(「日本語の指示もある程度理解しますが、英語で書くとより正確」
「日本語・英語・中国語の指示を理解します」。Z-Image のカードは `en` だけだが、部品の Qwen3 で日本語の指示も通ることを実機で確かめた)、
SDXL は `en` のみ(「日本語はほとんど理解しません」)、ACE-Step 1.5 は `prompt` が `en`(「曲の雰囲気は英語で書くとより正確」)・
`speech` が `en` `ja`(「日本語の歌詞にも対応」。カードは「50+ languages」で一覧が無いので、確かめた 2 つだけ)、
Stable Audio 3 Small の効果音は `prompt` が `en` で `speech` は `[]`(歌は入らない)、Hunyuan3D 2.0/2.1 は `prompt` が `[]`(画像から)。
残りの 2 件はカードの `language` の誤りを直す行(行の `_note` に根拠。update は `_note` を読まない):
Wan2.2 S2V(https://huggingface.co/Wan-AI/Wan2.2-S2V-14B のカードが `es` だけ。同じ Wan2.2 の
https://huggingface.co/Wan-AI/Wan2.2-I2V-A14B のカードに合わせて `en` `zh`)と MeloTTS-English
(https://huggingface.co/myshell-ai/MeloTTS-English のカードが `ko`。英語の声なので `en`)。
もう 1 件は歌の言語(0.6.0、#41): ACE-Step v1 の歌入りの `audio_ace_step_1_t2a_song` の `speech` は、
よく歌える 10 言語 `en` `zh` `ru` `es` `ja` `de` `fr` `pt` `it` `ko`(https://huggingface.co/ACE-Step/ACE-Step-v1-3.5B の
カードが案内する https://github.com/ace-step/ACE-Step の README「Supports 19 languages with top 10 well-performing languages
including: English, Chinese, Russian, Spanish, Japanese, German, French, Portuguese, Italian, Korean」)。指示は音楽の規則で
`en`。歌の入らない `audio_ace_step_1_t2a_instrumentals` には付けない。
残りの 4 件は Qwen-Image 2.1(0.12.0、#81): 指示の書き直しの部品(`qwen3.5_9b_…_pe_*`)を任意の部品にして言語の材料から外すと、
文字を読む部品が表に無い `qwen3vl_8b` だけになり、言語が決まらなくなる。そこで 4 件とも `prompt` に `en` を書いた(根拠は
公式テンプレートの既定の指示が英語であること。テンプレートは一次資料)。`image_qwen_image_2_1_image_edit` だけ `ja` も足した:
オーナーが実機(RTX 3070 8GB、公式の `qwen_image_2.1_int8_convrot`、このテンプレートと同じノード構成)で日本語の指示を翻訳せずに
渡して正しく編集できた(Issue #81 の 2026-10-26 の追記)。Turbo の編集と文章から画像は日本語で試していないので付けない。
`zh` は Qwen-Image 2.1 のカード(https://huggingface.co/Qwen/Qwen-Image-2.1 、2026-10-28 に確認)に言語の記述が無いので付けない。

手のモデル `src/genmodelfit/data/manual.json` の 4 件: つくよみちゃんの `speech` は `ja` `en`: カード(https://huggingface.co/ayousanz/piper-plus-tsukuyomi-chan)の
`language` は 6 言語(ja en zh es fr pt)だが、アプリが実機で確かめたのは日本語と英語だけなのでそれだけにした。
Irodori-TTS は `ja`(カードの `language: ja`)。Stable Audio 3 Small の音楽は `prompt` が `en`・`speech` が `[]`。
Supertonic 3 は `speech` がカードの 31 言語(カードの `language` と本文の「Supported Languages」の表が同じ)。

### 動かし方

項目 `runtime`。

公式テンプレート由来は `comfyui`。HF 由来は、読み上げ以外で ComfyUI の系統(`comfy_family`)に当たったものだけ `comfyui`。
HF 由来の読み上げは仕組みがモデルごとに違うので、規則では決めず、手の表(`overrides.json` の `runtime`)に書いたものだけ
(0.14.0、#142。カードの使い方・`library_name`・公式の README で確かめ、行の `_note` に根拠。2026-10-31 に一覧の 20 件すべて):
`cosyvoice` `audio-cpp` `transformers` `qwen-tts` `chatterbox` `qwentts-cpp` `parler-tts` `sglang-omni` `higgs-audio-v3-studio` `kokoro` `omnivoice` `vibevoice` `melotts` `voxcpm` `vieneu` `speechbrain`。手の表に無い読み上げは null。手のモデルは書いた値(`piper` `irodori-tts` `supertonic` `comfyui`)。
`comfyui` でも null でもない値のモデルは、見積もりが `.onnx` などの ComfyUI で使わない形式のファイルも数える
(`estimate.select_files`。つくよみちゃんの重みは onnx)。それ以外のモデルの数え方は変えていない。

### 説明

項目 `description`。

`src/genmodelfit/data/overrides.json` と `src/genmodelfit/data/manual.json` の手で書いたものだけ(日本語は AI生成キットの catalog.json の `summary`、
英語はそれを訳したもの)。手の表から行を消すと、次の当て直しで説明も消える。
GPU なしの PC で動くかは書かない(判定と食い違わないように。genmodelfit は今、SDXL・Stable Audio 3 Small を
`--vram 0` で Too tight と判定する。CPU での判定は https://github.com/laxshmate/genmodelfit/issues/39)。

### 手で足すモデル

`source: "manual"`、`src/genmodelfit/data/manual.json`。

人気順の収集で拾えないモデル。ID は `manual:<名前>`。`files` は `url` `sha256` `size_bytes` `directory` `role` と、
ファイルごとの `license` `license_name` `license_from` `license_url` `license_evidence` を書く(`appinfo.manual_entries`)。
update は、ほかのモデルと同じ道に乗せる: 一式のライセンスは `set_entry_license`(一番厳しいもの)、`files_complete` と
精度(見出しを読む)、ComfyUI の系統(`families.assign`)、HEAD の確認(`check_heads`)。さらに `license_from` のカードを引き、
書いた値と食い違えば**厳しいほう**にして警告する(`appinfo.verify_manual_licenses`。カードを読めなければ書いた値のまま警告)。
`directory` は 1 段の置き場所(ComfyUI のモデルは ComfyUI の置き場所、読み上げは `tts`)。サブフォルダは
ほかのモデルと同じく `name` に書く(Irodori-TTS の `tokenizer/tokenizer.json`・`dacvae/weights.pth`)。
`download` は `<置き場所>/<directory>/<name>` に置く(`tts/tokenizer/tokenizer.json`。`downloader.safe_relpath` は
`directory` に `/` を許さないので、そこには書かない)。
HF の収集が手のモデルと同じリポジトリ(本体のファイルの `license_from` と `homepage`)を拾ったら、その `hf:` の項目は
一覧に入れない(`appinfo.merge_manual`。部品の T5Gemma や、元のモデルの Irodori-TTS v4 は除かない)。
`runtime` が `comfyui` 以外の手のモデル(piper・irodori-tts・supertonic)は、判定(gate)の `official_workflow`・`custom_nodes`・
`comfyui_version` の代わりに `app_runtime` で保留にする(この一覧では動かし方を確かめられない。[gate.ja.md](gate.ja.md))。
書いた値がカードより厳しい段階なら、本文の制限を読んで厳しくしたものとして警告しない(Irodori-TTS)。
`license` がカードと同じで、カードに `license_name` が無ければ、書いた `license_name` は本文(LICENSE)で細かくしたものとして、警告も書き換えもしない(0.14.0、#127。Supertonic 3 はカードが `openrail` だけで、LICENSE の本文が Open RAIL-M なので `openrail` / `openrail-m`。AI生成キットの許可リストは名前まで見る)。根拠の LICENSE の URL は `license_evidence` に書く。

最初の 3 件の出典(2026-10-21 に HF の API とカードの原文で確かめた。sha256 と大きさは Hub API の `lfs` と一致。
LFS でない小さいファイルは実際に落として sha256 を計算した):

| ID | ライセンス | 確かめた場所 |
|---|---|---|
| `manual:tsukuyomi-chan-piper-plus` | `other` / `tsukuyomi-chan-corpus`(つくよみちゃんコーパス利用規約。SPDX に無い) | https://huggingface.co/ayousanz/piper-plus-tsukuyomi-chan のカードの `license: other`・`license_name: tsukuyomi-chan-corpus`・`license_link: https://tyc.rei-yumesaki.net/material/corpus/#terms3`。クレジット表記と出力音声の用途の禁止事項がある(アプリの `credit` / `conditions`。スキーマには持たない) |
| `manual:irodori-tts-v4.1-small` | `other` / `mit-with-ethical-restrictions`(用途の制限付きの段。声のコーデックは `mit`) | https://huggingface.co/Aratako/Irodori-TTS-v4.1-Small のカードの cardData は `license: mit`・本文「released under MIT」だが、同じカードの「Ethical Restrictions」(https://huggingface.co/Aratako/Irodori-TTS-v4.1-Small#ethical-restrictions。実在の人の声を同意なくまねない・ディープフェイクや偽情報の音声をつくらない)が用途を制限するので、寛容の段に入れない(gate の許可リストの `mit` にも当たらない)。声のコーデック https://huggingface.co/Aratako/Semantic-DACVAE-Japanese-32dim も `license: mit`(元の facebook/dacvae-watermarked は `apache-2.0`) |
| `manual:stable-audio-3-small-music` | `other` / `stable-audio-community`、文字を読む部品 `t5gemma_b_b_ul2` は `gemma` | https://huggingface.co/stabilityai/stable-audio-3-small-music のカード(`license: other`・`license_name: stable-audio-community`、`gated: auto`)。同じリポジトリに `LICENSE_GEMMA.md` がある。URL は gated でない https://huggingface.co/Comfy-Org/stable-audio-3 の複製(`stable_audio_3_small_music.safetensors`・`t5gemma_b_b_ul2.safetensors`)。T5Gemma の記載は、同じファイルを使う `audio_stable_audio_3_medium` と同じ(`license_sources` の `stable-audio-3-t5gemma`) |
| `manual:supertonic-3`(0.14.0、#127。2026-10-31 に確かめた) | `openrail` / `openrail-m`(BigScience Open RAIL-M。用途の制限付きの段。名前は LICENSE の本文から) | https://huggingface.co/Supertone/supertonic-3 の版 `3cadd1ee6394` のカード(`license: openrail`、本文「The accompanying model is released under the OpenRAIL-M License」。サンプルのコードは MIT)と、同じ版の `LICENSE`(Attachment A の使用制限: 同意のないなりすまし・ディープフェイクの禁止など)。ファイルの URL は版を固定した `resolve/<版>/…`(main だと中身が変わったときに sha256 が合わなくなる)。16 個・合計 401,276,744 バイト。`.onnx` の 4 個は Range で先頭 8 MB を読み、重みがすべて float32 だったので見出しの精度 `fp32` を書いた(カードの「about 99M parameters」と合計の大きさも 1 個 4 バイトで合う)。`config.json` はダウンロード数を数えるための飾りで、SDK が使わないので入れない。開発元の GitHub(supertone-inc/supertonic)はアーカイブ済み(「This repository is archived. Development and support have ended.」)で、更新・修正は出ない。重みは supertone-oss-archive/supertonic-3 にも同じ LFS の oid で置かれている(`license_evidence` にも書いた) |

手のモデルは、HF のリポジトリがあれば人気の順位(`popularity`)の Hugging Face の仲間に入るが、全期間のダウンロード数(`downloads_all_time`)を持たないので順位は 0(一番下。[scoring.ja.md](scoring.ja.md)「人気」)。

### 手で足す部品

`src/genmodelfit/data/manual.json` の `components`、0.6.0、#40。

HF の収集はリポジトリの本体(と同じリポジトリの部品)だけを拾うので、別のリポジトリにある部品が抜けることがある。
`components` は「モデルの ID → 足すファイル」の表で、update(作り直し・`--heads-only`・`--fields-only`)が
`appinfo.merge_manual` の中で毎回当てる(同じ URL のファイルがあれば足さない。何度当てても同じ)。
**ライセンスは表に書かず、部品の出どころの表(`license_sources.py`、上の「出どころの表」)の行で決める**(行の名前の型に `/` があれば、リポジトリの中のパス全体に当てる。同じリポジトリの `model.safetensors` と `audio_tokenizer/model.safetensors` を分けるため。0.14.0、#142。後から足す部品の URL は版に固定する)
(行が無ければ不明。寛容な側に誤らない)。そのモデルがその部品を使う根拠は `evidence` に書き、
ファイルの `license_evidence` の最後に `manual_component: <根拠>` として残す。ID は変えない(使う側の ID を保つ)。

| ID | 足すファイル | ライセンス | 確かめた場所(2026-10-21) |
|---|---|---|---|
| `hf:stabilityai/stable-audio-3-small-sfx` | https://huggingface.co/Comfy-Org/stable-audio-3 の `text_encoders/t5gemma_b_b_ul2.safetensors`(sha256 `1e1eba25…`、1,187,264,003 バイト。ComfyUI 用の T5Gemma) | `gemma`(`license_sources` の `stable-audio-3-t5gemma`) | https://huggingface.co/stabilityai/stable-audio-3-small-sfx の Hub API: ファイルに `t5gemma-b-b-ul2/model.safetensors`(sha256 `9b05ea5a…` = https://huggingface.co/google/t5gemma-b-b-ul2 の `model.safetensors` と同じ。google/t5gemma-b-b-ul2 は `license: gemma`)と `LICENSE_GEMMA.md` があり、利用申請の文(`extra_gated_prompt`)に「This model also includes components redistributed under the Gemma Terms of Use (https://ai.google.dev/gemma/terms)」。Stability AI の https://github.com/Stability-AI/stable-audio-tools/blob/main/docs/conditioning.md「This uses a T5Gemma (https://huggingface.co/google/t5gemma-b-b-ul2) encoder」 |
| `hf:OpenMOSS-Team/MOSS-TTS-v1.5`(0.14.0、#142。以下 2026-10-31) | https://huggingface.co/OpenMOSS-Team/MOSS-Audio-Tokenizer の `model-00001-of-00002.safetensors`・`model-00002-of-00002.safetensors`(合計 7,098,461,728 バイト。音声トークナイザ) | `apache-2.0`(`moss-audio-tokenizer`) | MOSS-TTS v1.5 の `processing_moss_tts.py`(版 cdd3b911)の `from_pretrained` は、指定が無ければ `OpenMOSS-Team/MOSS-Audio-Tokenizer` を読む |
| `hf:ResembleAI/chatterbox` | 同じリポジトリの `s3gen.pt`(1,057,165,844 バイト)・`ve.pt`・`conds.pt` | `mit`(`chatterbox-parts`) | https://github.com/resemble-ai/chatterbox の `src/chatterbox/mtl_tts.py`(コミット 5de7a54a)の `from_pretrained` / `from_local` が読む。一覧の本体 `t3_mtl23ls_v3.safetensors` を使うには `t3_model="v3"` が要る(省くと v2) |
| `hf:Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`・`-0.6B-CustomVoice` | 同じリポジトリの `speech_tokenizer/model.safetensors`(682,293,092 バイト) | `apache-2.0`(`qwen3-tts-speech-tokenizer`) | https://github.com/QwenLM/Qwen3-TTS の `modeling_qwen3_tts.py`(コミット 022e286b)の `from_pretrained` が `speech_tokenizer/` を落として読む(無ければ止まる) |
| `hf:openbmb/VoxCPM2` | 同じリポジトリの `audiovae.pth`(376,951,122 バイト) | `apache-2.0`(`voxcpm2-audiovae`) | https://github.com/OpenBMB/VoxCPM の `src/voxcpm/model/voxcpm2.py`(コミット cce58a5b)が読む(無ければ止まる)。雑音除去の ZipEnhancer は ModelScope にあり、カードの例は `load_denoiser=False` なので足さない |
| `hf:FunAudioLLM/Fun-CosyVoice3-0.5B-2512` | 同じリポジトリの `llm.pt`・`flow.pt`・`hift.pt`・`speech_tokenizer_v3.onnx`・`campplus.onnx`(合計 4,434,743,215 バイト。URL は版 29e01c4e に固定) | `apache-2.0`(`cosyvoice3-parts`) | https://github.com/FunAudioLLM/CosyVoice の `cosyvoice/cli/cosyvoice.py`(コミット 074ca6dc)の `CosyVoice3` が読む。`CosyVoice-BlankEN` は Qwen の設定とトークナイザで、`llm.pt` がその重みを上書きするので、見積もりは LLM を二重に数えて多めに出ることがある |
| `hf:k2-fsa/OmniVoice` | 同じリポジトリの `audio_tokenizer/model.safetensors`(805,665,628 バイト。版 c5fdb5cc に固定) | `other` / `boson-higgs-audio-2-community-license`(`omnivoice-audio-tokenizer`)。一式は本体の `cc-by-nc` のまま | https://github.com/k2-fsa/OmniVoice の `omnivoice/models/omnivoice.py`(コミット 08be0b4c)が `audio_tokenizer/` を読む |
| `hf:Serveurperso/Qwen3-TTS-GGUF` | 同じリポジトリの `qwen-tokenizer-12hz-Q8_0.gguf`(291,150,624 バイト。版 b7ee2e8c に固定) | `apache-2.0`(`qwentts-cpp-tokenizer`) | カード「The tokenizer is shared across every talker」。一覧の talker が Q8_0 なので Q8_0 |

ほかの Stable Audio 3 の項目(`audio_stable_audio_3_medium`・`_medium_base`・`manual:stable-audio-3-small-music`)は
もとから同じ部品を持つ(tests/test_appinfo.py がすべての Stable Audio 3 の項目に `gemma` の T5Gemma があることを検査する)。

## 無検閲(uncensored)のモデル

CLAUDE.md の決まり: 無検閲のモデルも一覧から外さず、`content: "uncensored"` と根拠(`content_evidence`)を付ける。

**テンプレート**: ID・題・タグに `uncensored` / `nsfw` / `abliterated` / `heretic` / `unfiltered` があれば `name:<語>`、使うファイルの HF
リポジトリに `not-for-all-audiences` タグがあれば `hf-tag:not-for-all-audiences (<repo>)`。
(2026-10-20 時点で当てはまるテンプレートは 0 件)

**Hugging Face の検索**: pipeline_tag(text-to-image・image-to-image・text-to-video・image-to-video・text-to-speech・
text-to-audio・image-to-3d・text-to-3d)ごとに、`filter=not-for-all-audiences` と `search=uncensored|nsfw|abliterated|heretic|unfiltered`
で、ダウンロード数の多い順に 100 件ずつ取る。新しく伸びているものを拾うため、`filter=not-for-all-audiences` と
テンプレートで足りない種類の検索は `sort=trendingScore` と `sort=likes30d` でも取る。どの検索も
`expand[]=downloadsAllTime` で全期間のダウンロード数を受け取る(返らなければ直近 30 日の数で代え、その候補の数を
`sources.huggingface-search.all_time_missing` に残す)。trending の検索を先に並べ、ダウンロード数の順の検索にも
出たものは trending だけのものとして扱わない(下限は 10,000)。根拠(`hf-tag:not-for-all-audiences` か名前の `name:<語>`)の
あるものだけを候補にし、次の順に絞る:

1. 除外の判定(下)に当たるものを外す(数だけ数える)
2. **全期間のダウンロード数が 10,000 未満**のものを外す(`skip_downloads`)。trendingScore・likes30d の検索だけで
   見つけたもので、**本当に伸びているもの**(検索の結果の trendingScore が 0.5 以上で、直近 30 日のダウンロード数が
   100 以上)だけは 1,000 以上で入れる。llmfit の `scripts/scrape_hf_models.py`(`min_downloads=10000`、
   trendingScore・likes30d での発見)にならった(2026-10-20 に利用者が決めた)。一覧に「誰も使っていないモデル」を入れないため。
   伸びている条件は 2026-10-21 に足した(#19。likes30d の順で、古くて使われていないもの(直近 30 日 5 件など)が
   1,000 の下限で入っていた)
   - **無検閲は直近 30 日のダウンロード数も 100 以上**(`uncensored_inactive`。#19)
3. 試験用・作りかけの名前(`tiny-random`・`tiny-dummy`・`-random-init`・`ci-random-`・`test-` で始まる)を外す(`test_stub`)。
   `merge` / `mergekit` のタグか、**リポジトリ名に merge / mergekit の語**のある混ぜ合わせを外す(`merge`。llmfit と同じ。
   名前は #19 で足した。「mix」は入れない)
4. LoRA・LyCORIS・アダプタ・埋め込み(名前か `lora` / `peft` / `adapter` / `qlora` タグ)を外す。本体が無いと動かず、適否は本体で決まるため
5. 部品だけのもの(名前に text-encoder・VL・CLIP・VAE・GPTQ・AWQ・MLX など)を外す
6. 同じリポジトリ名の写し(持ち主違い)は一番ダウンロード数の多いものだけ。名前は末尾の量子化・形式の語
   (`-GGUF`・`-Q8_0`・`_K_M`・`-fp8`・`-nf4`・`-int8`・`_diffusers`・`-Quantized` など)を取り、英数字だけにして比べる
   (`update.copy_name`。#19。例: `Wai-NSFW-Illustrious-v140-Q8-GGUF` は `waiNSFWIllustrious_v140` の写し)。
   持ち主ごとに種類あたり 2 件まで
7. 種類ごとに**上位 30 件**まで(一覧を大きくしすぎない)。リポジトリの情報とモデルカードを取り、カードで
   除外に当たるもの・重みが選べないもの・本体が 30 MB 未満のもの(差分や中身の無いリポジトリ)を外す
8. **同じ重みの写し**(名前が違う)を外す: 選んだファイルの sha256 の組が同じもの、または元のモデル(たどった
   base_model・当たった系統)が重なり、合計の大きさが ±1% 以内のもの。ダウンロード数の多いほうを残す
   (`sources.huggingface-search.skipped.duplicate_weights`)

ライセンスは上の「ライセンス」の規則で確かめる(本人の表記をそのまま使わない。無検閲化したもので元が分からなければ不明)。

ID は `hf:<owner>/<repo>`、`source: "huggingface"`、`discovered_from` に見つけた検索の URL、`downloads`(直近 30 日)/
`downloads_all_time`(全期間)/ `likes` を入れる。
同梱の一覧も 2026-10-20 にこの規則へそろえた(HF の 64 件から、全期間 10,000 未満の 16 件と merge のタグの 2 件を外して 46 件。
無検閲は 39 → 24 件)。2026-10-21 に #19 の規則で作り直した([release.ja.md](release.ja.md))。件数は一覧の `sources.counts`・
`sources.huggingface-search` を見る(2026-10-21T06:20Z の同梱の一覧では HF 48 件、無検閲 26 件)。

**利用者のテンプレート**: index.json の `username` が `ComfyUI` でないテンプレート(2026-10-20 時点で 32 件。hellorob・
PurzBeats など)は `Template.username` に持ち、`community: true` とする。同じ重みの公式のワークフローがあれば表の代表にしない
(ワークフローの一覧には残す)。公式の仲間がいない利用者のテンプレートはそのまま 1 つのモデルとして出すが、並べ方では
公式のものの後にする([schema.ja.md](schema.ja.md)「`recommend` の並べ方」)。
`recommend` の並べ方の人気の順位は、テンプレートの使用回数(`usage`)と HF の全期間のダウンロード数を混ぜず、同じ種類・同じ出どころの中の百分位にして使う(0.11.0。それまでの「HF のダウンロード数を 100 で割って同じ目盛りにする」換算はやめた。[scoring.ja.md](scoring.ja.md)「人気」)。

**テンプレートで足りない種類**: ローカルの読み上げのテンプレートは 0 件(Chatterbox は `size` 0)、効果音は 1 件
(Stable Audio Open)しかないので、HF の人気順で補う(`content` は根拠が無ければ `sfw`):
読み上げ `pipeline_tag=text-to-speech` の上位 20 件、効果音 `pipeline_tag=text-to-audio` で `search=sfx|foley|sound|
audioldm|stable-audio-open` のうち効果音と判定したもの(名前・タグに sound effect / sfx / foley、または
MMAudio・AudioLDM・Stable Audio Open の系統で music の語が無いもの)の上位 10 件。

### HF のリポジトリから使うファイルを選ぶ規則

`hf.select_weight_files`。

見積もり(#4)はファイルの合計を使うので、**動かすのに要る 1 組だけ**を入れる(同じ部品の別の版を重ねない)。

1. 重みの拡張子(`.safetensors` `.gguf` `.ckpt` `.bin` `.pth` `.pt` `.sft`)で、大きさの分かるものだけ。
   `optimizer` `scheduler` `safety_checker` `feature_extractor` `onnx` `flax` `voices/` `samples/` などは除く
2. リポジトリ直下に**単一ファイルの本体**があれば、diffusers の形があってもそちらだけを使う(ComfyUI が読む形)
3. diffusers の形だけ(`model_index.json` があり直下に本体が無い)なら、部品のフォルダごとに 1 版:
   `transformer` / `transformer_2` / `unet` / `prior` / `decoder` → main、`text_encoder*` → text_encoder、`vae` → vae、
   `image_encoder` → clip_vision。safetensors を優先し、精度は fp16 → bf16 → 印の無い既定の版。分割(`-00001-of-00003`)はまとめて取る
4. それ以外は、ファイル名で役割(本体・テキストエンコーダ・VAE。`mmproj`・tokenizer・codec・LoRA は使わない)に分け、
   役割ごとに 1 版を選ぶ:
   - GGUF: Q8_0 → Q4_K_M → Q5_K_M → Q6_K → Q4_K_S → Q5_K_S の順、どれも無ければ一番小さいもの
   - それ以外: safetensors を優先(.bin / .ckpt / .pth は safetensors が無いときだけ)、fp32 の版は他が無いときだけ、
     残りから一番大きいもの(完全な本体)
   - 本体は、リポジトリ名と語が重なるファイルがあればその中から選ぶ(別のモデルの同梱を避ける)
   - テキストエンコーダ・VAE は、本体と同じフォルダ・直下・役割名のフォルダ(`text_encoders` `vae` など)にあるものだけ
   - 直下に本体が無く、本体が役割名でないフォルダ 4 つ以上に分かれているリポジトリは、別々のモデルの詰め合わせと
     みなして入れない
5. 本体が選べなければ一覧に入れない。`precision` は選んだ本体のファイル名から

規則で選ぶので、複数のモデルを詰め合わせたリポジトリなどでは外れることがある。

## 除外

未成年の性的表現を目的とするものを除く。

`src/genmodelfit/safety.py`。外したものは一覧に載せず、`sources.excluded` に**数だけ**を残す(名前も残さない)。

| 種類 | 語 | どこに当てるか |
|---|---|---|
| STRONG | loli(s)・lolicon(s)・loli con・lolita(s)・shota(s)・shotacon(s)・shota con・jailbait・preteen(s)・pedo・pedophile・pedophilia・paedophile・paedophilia・toddlercon・underage、ロリ・ロリコン・ロリータ・ショタ・ショタコン・児童ポルノ | すべてのモデルの ID・名前・タグ、モデルカード・説明 |
| CONTEXT | child・children・kid(s)・minor(s)・teen(s)・teenage(r/rs)・schoolgirl(s)・school girl(s)・schoolboy(s)・school boy(s)・little girl(s) / boy(s)・young girl(s) / boy(s)・toddler・infant・cub・jk、幼女・児童・女子小学生・女子中学生・小学生・中学生、JC・JS(大文字のときだけ) | 無検閲のモデルの ID・名前・タグにあれば外す。カードでは、同じ文に性的な語(sex・nude・naked・porn・erotic・nsfw・explicit・lewd・hentai・topless・エロ・ヌード・裸・性的・アダルト・R18 など)があるとき(無検閲の印の無いモデルにも当てる) |
| DATASET | e621(学習データに未成年の性的表現の分類を含むことが知られている画像の集まり。2026-10-21 メンテナの判断) | 無検閲のモデルの ID・名前・タグ・モデルカードにあれば外す(目的が未成年でなくても、出力に含まれうるため) |

- 語は英数字の境界で照合する(`minor` は `minority`、`kid` は `kidney`、`teen` は `fifteen` に当たらない。
  `Loli-XL` や `shota_v2` には当たる)。カタカナの語は前後がカタカナでないときだけ(ロリ は カロリー・ロリポップに
  当たらない)。`js` は JavaScript(`transformers.js` のタグなど)と紛らわしいので大文字の `JS` だけ
- **否定・禁止は、その語を直接受けているときだけ**数えない: 否定の語(no / not / never / without / do not / prohibited
  など)の後ろ 8 語以内で、間に句読点(, ; : . など)や「filters / limits / restrictions / censorship / safety」などの
  区切りの語が無いとき。語の後ろの禁止(「… are strictly prohibited」「…は禁止」)も同じ。
  例: 「Do not generate sexual content involving minors.」は数えない。「Uncensored, no filters: generates nude teen girls.」
  の no は filters を受けているだけなので teen を数える(以前は文全体を無視していた)
- **生成の禁止**(do not use … to generate / must not create など、禁止の語が生成の動詞を受けている形)は、「,」で区切った
  列挙(「illegal content, non-consensual imagery, or sexual content involving minors」)全体に効かせ、文の終わり
  (. ; : ! ?)までの 30 語を数えない。犯罪の名前としての言及(「child sexual abuse material」。安全対策の説明に出る)は
  語の当たりから除く。2026-10-20 の実データで、この 2 つの形の注意書きを持つ成人向けモデル 2 件を誤って外していたので直した
- 「Do not use it to generate:」のように**禁止の形**(do not / must not / never / prohibited …)の行が「:」で終わるときは、
  続く箇条書き(禁止の一覧)も数えない(「No filters:」のような否定だけの行には当てない)
- 公式テンプレート(無検閲の印が無いもの)の名前には STRONG の語だけを当てる("children's book" などで外さないため)

2026-10-20 の実行で外れた件数は `sources.excluded` を見る(名前は残さない)。作例のプロンプトに child を含むもの・
注意書きに minors を含む正しい成人向けのモデルが外れないことは、テスト(`tests/test_review_fixes.py`)で確かめている。

## Civitai: 使わない

Civitai の利用規約(https://civitai.com/content/tos 、lastmod 2026-10-06)を読んで、使わないと決めた。

- 11.4: 自動のアクセスは「公開 API などを**自分の有効な認証情報で**、上限の範囲で」使うときだけ許される。
  利用者ごとの API キーが前提で、ツールが匿名で集めて配ることは想定されていない
- 6.1: サービスの利用許諾は「個人的・非商用の利用に限る」
- 7: サービスの「compilation, information, data」は Civitai の Materials。11.10 で Materials の販売・譲渡を禁止

genmodelfit の一覧はソース非公開の商用アプリ(AI生成キット)が同梱して配る前提なので、Civitai から集めた情報を
入れることは「はっきり許されている」とは言えない。テンプレートにある Civitai の URL(5 つ、のべ 6 か所)は URL だけを
そのまま持ち、大きさ・ライセンスは不明のまま(HEAD も送らない)。

## 確認の印(review)

update(作り直し・`--heads-only`・`--fields-only`・`--inspect-only`)は前の一覧と比べ、次の項目を確認待ち(`pending`)にする:
新しい項目・寛容な側へのライセンスの変化・必要量の減少・新しく無検閲・**ファイルの変化**(`files_changed`、0.9.0。ファイルの増減、
URL(クエリも比べる。`#` から後だけ除く)・`repo_id` の変化、大きさの変化(大きくなった・小さくなった・分かった・分からなくなった)、
値のあった sha256 の変化・消失。前が空で今入った sha256 だけは付けない)。`review reject` で外した項目(`src/genmodelfit/data/review_rejected.txt`)は
作り直しても入れない。確認待ちは gate の `reviewed` で保留になり、配る一覧(data-latest)には入らない。詳しくは [schema.ja.md](schema.ja.md)「review」・
[release.ja.md](release.ja.md)「毎週のスキル(genmodelfit-weekly、#42)」。

## 上流の形が変わったとき・ネットに繋がらないとき・一部を取得できなかったとき

- index.json が一覧でない・分類に `templates` が無い・テンプレートに `name` が無い → `UpstreamFormatError`
  で止まり、終了コード 1。キャッシュはそのまま
- テンプレートの JSON に `nodes` が無い・読めない → 1 件ずつ警告(`sources.warnings`)。30% を超えたら止まる
- 一覧が空になった → 止まる
- ネットに繋がらない(index.json が取れない、テンプレートの JSON が全部取れない、HF の 30% を超えて繋がらない)
  → 前のキャッシュを残して終了コード 1。`recommend` などはキャッシュ(無ければ同梱の一覧)で動き続ける
- 実測(`measurements.json`)のあるモデルが上流から消えたときは、前の一覧(キャッシュ、無ければ同梱)から引き継いで
  警告を出す(読めない・型の合わない項目を含むキャッシュは使わず同梱へ戻る。0.9.1、#63)
- **一部を取得できなかったとき**(テンプレートの JSON が 1 件でも読めない、HF の一時的な失敗(つながらない・429・5xx。
  401 / 403 / 404 は「不明」として扱い数えない)、429 の待ち時間の上限を超えた、HF の検索が失敗した):
  `sources.partial: true` と `sources.partial_counts`(`templates_failed`・`hf_repos_failed_transient`・`entries_affected`・
  `hf_search_failed`・`rate_limit_budget_exhausted`・`carried_over`)を入れる。影響を受けた一覧の 1 件(読めなかった
  テンプレート、失敗したリポジトリのファイルを持つもの)は、前の一覧に同じ ID があればその値を使う。ただし前の一覧が
  同じ `sources.license_policy` で作られたときだけ(古い決め方の寛容すぎるライセンスを持ち込まない)。検索で見つかる
  はずだった HF のモデルは引き継がない
- 一部が欠けたとき、`update` は**書き出さずに終了コード 1** で止まる(前のキャッシュはそのまま)。欠けたままでよければ
  `--allow-partial` を付ける(書き出して終了コード 0。`sources.partial` は true)

## テンプレートの `size` は一式の合計か

実際に比べて確かめた結果。

2026-10-20 の `update` の結果で、ファイルの大きさがすべて分かるテンプレート **263 件**について、index.json の
`size` と、テンプレートが使うファイル(HF の `siblings[].size`)の合計を比べた(当時の `sources.size_check`。今の値は
同梱の一覧の `sources.size_check` を見る)。

| 比べた結果 | 件数 |
|---|---:|
| バイト単位で一致 | 0 |
| ±2% 以内 | 179(68%) |
| ±10% 以内 | 214(81%) |
| size が合計より 10% 以上小さい | 22 |
| size が合計より 10% 以上大きい | 27 |
| size が 0.01 GiB の倍数(手で丸めた値) | 258 |

抜き出し(上 10 件は実測のあるものと代表、下 3 件は外れの例):

| テンプレート | index の size | ファイルの合計 | 比(size / 合計) |
|---|---:|---:|---:|
| image_z_image_turbo_int8 | 12,133,282,611(11.30 GiB) | 12,168,299,735(11.33 GiB) | 0.997 |
| image_z_image_turbo | 20,830,591,386(19.40 GiB) | 20,690,152,836(19.27 GiB) | 1.007 |
| sdxl_simple_example | 13,013,750,907(12.12 GiB) | 13,014,060,264(12.12 GiB) | 1.000 |
| image_sdxl_simple | 6,979,321,856(6.50 GiB) | 6,938,078,334(6.46 GiB) | 1.006 |
| flux_schnell | 17,233,556,275(16.05 GiB) | 17,236,328,572(16.05 GiB) | 1.000 |
| text_to_video_wan | 9,824,737,690(9.15 GiB) | 9,828,025,775(9.15 GiB) | 1.000 |
| video_wan2_2_5B_ti2v | 18,146,236,826(16.90 GiB) | 18,144,966,705(16.90 GiB) | 1.000 |
| audio_ace_step_1_5_checkpoint | 10,737,418,240(10.00 GiB) | 10,025,478,736(9.34 GiB) | 1.071 |
| audio_stable_audio_example | 5,690,831,667(5.30 GiB) | 5,745,535,406(5.35 GiB) | 0.990 |
| 3d_hunyuan3d_image_to_model | 4,928,474,972(4.59 GiB) | 4,928,151,562(4.59 GiB) | 1.000 |
| 3d_hunyuan3d-v2.1 | 4,928,474,972(4.59 GiB) | 7,365,943,290(6.86 GiB) | 0.669 |
| flux_fill_outpaint_example | 10,372,346,020(9.66 GiB) | 34,174,211,972(31.83 GiB) | 0.304 |
| templates_shane_change_any_objects | 10,737,418,240(10.00 GiB) | 46,495,526,446(43.30 GiB) | 0.231 |

結論:

- `size` は**一式(テンプレートが使うモデルファイル全部)の合計を GiB で丸めて手で書いた値**とみてよい
  (大半が ±2% に入り、ほぼ全部が 0.01 GiB 刻み)。ただしバイトの一致は 0 件で、目安にしかならない
- 外れの中身を見たもの:
  - 別のテンプレートの値の写し: `3d_hunyuan3d-v2.1` は 2.0 と同じ 4.59 GiB だが、2.1 の本体は 6.86 GiB
  - size が小さすぎる: `flux_fill_outpaint_example` は本体の FLUX.1 Fill(23.8 GB)だけで size の 9.66 GiB を超える。
    `templates_shane_change_any_objects` も本体が fp16 の 34.7 GB(size は 10 GiB。小さい精度を前提にした値かもしれない)
  - size が大きすぎる: カスタムノードが自分で重みを取り、`properties.models` に本体が書かれていないもの
    (`video-wan21_scail`・`templates-wan2_1_infinitetalk_music` は clip_vision の 1.26 GB しか書かれていない)
- このため genmodelfit は**ファイルの大きさの合計を使い、`size` はファイルが 1 つも書かれていないときの控えにだけ使う**
  (`ModelEntry.total_size_bytes`)。本体(role main)が書かれていない・合計が size の半分未満のテンプレートは
  `files_complete: false` にし、index の size を `size_floor_bytes` に入れて、見積もりが小さく見ないようにした(上の「ファイル」)
