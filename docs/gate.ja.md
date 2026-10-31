# 自動でおすすめに出してよいかの判定(gate)

[English](gate.md) · **日本語**

`auto: true`(`info` の Verified)の規則を説明するページです。人が確かめずに PC 初心者へ出してよいモデルを、どう決めているかをまとめています。
一覧を使うアプリの開発者と、規則や一覧を変えるコントリビューター向けです。

## 目次

- [使い方](#使い方)
- [条件](#条件)
- [許可リスト(既定)](#許可リスト既定)
- [ファイルのライセンスの読み方](#ファイルのライセンスの読み方)
  - [レビューで見つかった、auto を間違えていたもの(#14、2026-10-20)](#レビューで見つかったauto-を間違えていたもの142026-10-20)
- [URL の確かめ方(`url_reachable`)](#url-の確かめ方url_reachable)
  - [確認の期限(`max_head_age_days`)](#確認の期限max_head_age_days)
- [ComfyUI の版(`comfyui_version`)](#comfyui-の版comfyui_version)
- [無検閲のモデル(`content`)](#無検閲のモデルcontent)
- [自動で出せるもの](#自動で出せるもの)

使う側(AI生成キット)は、**人が確かめずに**おすすめのモデルを PC 初心者に出す。
そのため判定は保守的にし、分からないもの(ライセンス不明・HEAD 未確認・版不明など)はすべて「保留」にする。

- 全部の条件を満たしたものだけ `auto: true`。
- 保留でも一覧・`recommend`(`--auto-only` を付けないとき)・`fit`・`info` には今までどおり出る。
- **auto を間違えて出すことが一番悪い**。迷うものは保留にする。

実装は `src/genmodelfit/gate.py`。

- 結果は一覧(db.json)に持たず、毎回計算する。
- 一覧に持つのは `update` が記録した HEAD の結果・ComfyUI の最新の版・確認の印(`review`)だけ。

## 使い方

```powershell
genmodelfit gate [--json] [--modality image] [--only-auto | --only-held]   # 条件ごとの件数
genmodelfit recommend --auto-only --vram 8 --comfyui-version 0.40.0         # 自動で出せるものだけ
genmodelfit info image_z_image_turbo --comfyui-version 0.40.0               # 条件ごとの結果と理由
# 方針の差し替え(recommend / fit / info / gate に共通)
--comfyui-version 0.40.0                # 使う ComfyUI の版(アプリは必ず渡す。下の「ComfyUI の版」)
--license-allowlist mit,apache-2.0      # または 1 行に 1 つ書いたファイル(# から後は注釈)
--max-head-age-days 30                  # HEAD の確認をこの日数まで信じる(既定 30。none で期限なし)
--allow-uncensored-auto                 # 無検閲のモデルも自動で出してよい
```

`recommend` / `fit` / `info` / `search` / `diff` の `--limit` / `-n` は 1 以上(0 や負の数はエラーで終了コード 2)。

```python
from genmodelfit.db import load
from genmodelfit.gate import GatePolicy, evaluate_offline

db = load()
policy = GatePolicy(comfyui_version="0.40.0")  # アプリが同梱・導入する ComfyUI の版
r = evaluate_offline(db.get("image_z_image_turbo"), policy, sources=db.sources)
r.auto, r.held_by, r.to_dict()
```

`to_dict()` の形:

```json
{"auto": true, "held_by": ["<落ちた条件の id>"], "comfyui_version": "<比べた版>",
 "checks": [{"id": "", "passed": true, "detail": ""}]}
```

- `checks` は下の表の順に並ぶ。ComfyUI のモデル(`runtime` が `comfyui` か null)は `app_runtime` を除く 10 個。
- `runtime` が `comfyui` でも null でもないモデルは、`official_workflow`・`custom_nodes`・`comfyui_version` の位置に `app_runtime` が 1 つ入って 8 個。
- `held_by[0]`(最初に落ちた条件)は `gate --only-held` に `held:<id>` で出る。`info` は条件ごとに [ok] / [NG] を出す。`recommend` / `fit` の表には Verified の列はもう無い(#84)。
- `detail` は人が読む文で、表示の言語(`en` / `ja`。`--lang`・`GENMODELFIT_LANG`)で出る(文言は変わりうる。機械は `id` と `passed` を見る)。

## 条件

| id | 通る条件 | 保留になる例 |
|---|---|---|
| `license` | すべてのファイル(LoRA・アップスケーラ・画像の読み取り部なども)のライセンスが許可リストにある。ライセンスは下の「ファイルのライセンスの読み方」で決める | `other`・openrail 系・非商用・コミュニティライセンス・llama / gemma / DINOv3 の規約・不明 |
| `gated` | どのファイルも `gated: false`。部品の本家が利用申請(規約への同意)を求めるもの(DINOv3・SAM 3)も保留 | `true`、不明(`null`)、本家が申請を求める部品 |
| `official_workflow` | ComfyUI 公式テンプレート(`source: comfyui-template`)で `openSource` が false でない | Hugging Face だけの項目 |
| `custom_nodes` | テンプレートの `requiresCustomNodes` が空 | カスタムノードが要る、テンプレートが無い |
| `comfyui_version` | テンプレートの `minComfyUIVersion` ≦ 使う版(数字で比べる。`0.3.9 < 0.3.10`) | 版が読めない・記載なし・使う版が不明 |
| `app_runtime` | (`runtime` が `comfyui` でも null でもないモデルだけ。`official_workflow`・`custom_nodes`・`comfyui_version` の代わり)通らない。動かす仕組み(piper・irodori-tts・supertonic)はこの一覧では確かめられないので、使う側が確かめる(0.5.0、#36) | `src/genmodelfit/data/manual.json` に手で足したモデルのつくよみちゃん(runtime `piper`)・Irodori-TTS(runtime `irodori-tts`)・Supertonic 3(runtime `supertonic`)と、手の表で動かし方を書いた Hugging Face の読み上げ(`hf:Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` の `qwen-tts` など。0.14.0、#142) |
| `url_reachable` | すべてのファイルの URL に、匿名の HEAD が 200 / 302 / 307(`/api/resolve-cache` への転送)を返した記録があり、その確認が `max_head_age_days`(既定 30 日)より新しい | 401(gated)・404・記録なし(未確認)・URL なし・確認が古い・確認の日時が無い |
| `sha256` | すべてのファイルに 64 桁の 16 進の sha256 がある | Civitai のファイルなど |
| `content` | 無検閲(`content: uncensored`)でない | 無検閲(`--allow-uncensored-auto` で外せる) |
| `size_known` | すべてのファイルの大きさが分かり、ファイルの一覧が完全(`files_complete` が false でない) | 大きさ不明、ファイルの記載なし |
| `reviewed` | 確認の印(`review`)が確認待ち(`pending`)でない。印の無い古い一覧(0.5.0 まで)は通る。いつも最後の条件(0.6.0、#42。[schema.ja.md](schema.ja.md)「review」) | `update`(作り直し・`--heads-only`・`--fields-only`・`--inspect-only`)が付けた新しい項目・寛容な側へのライセンスの変化・必要量の減少・新しく無検閲・ファイルの変化(`files_changed`。URL(クエリも)・sha256 の変化や消失・大きさの増減・ファイルの増減・repo_id。0.9.0。[sources.ja.md](sources.ja.md)「確認の印(review)」)(`genmodelfit review approve` で根拠を付けて確かめるまで) |

- ファイルが 1 つも書かれていない項目は、ファイルについての条件がすべて保留になる(空なら通る、にはしない)。
- `openSource` の記載が無いテンプレートは、ローカルで動くテンプレートとして通す。
  - 対象は SDXL・FLUX.1 schnell・Wan 2.1 などの古いもの。2026-10-21T06:20Z の同梱の一覧で 51 件。
  - `update` も同じ扱い。
  - `api_` で始まる API のテンプレートと `openSource: false` は `update` が一覧に入れていない。

## 許可リスト(既定)

`apache-2.0`・`mit`・`bsd-2-clause`・`bsd-3-clause`・`cc0-1.0`・`unlicense`。

選び方: **商用で使えて、使い方の制限(用途の禁止の一覧など)・売上や利用者数の基準・申請・表示の義務が無い**もの(`hf.license_tier` の「1 寛容」の段)。

- これらの条件(著作権表示・ライセンス文を残す)は、重みを**再配布するとき**にかかる。
- genmodelfit と使う側のアプリは重みを配らず、利用者の PC が Hugging Face から直接取るので、アプリ側で守ることが残らない。

入れないもの:

- `cc-by-*`(`cc-by-4.0` など): 一覧の決め方(#13)では「2 表示の義務」の段。
  - 表示(帰属)を出せるアプリは `--license-allowlist apache-2.0,mit,bsd-2-clause,bsd-3-clause,cc0-1.0,unlicense,cc-by-4.0` のように足して使う。
  - #14 の最初の版では既定に入れていたが、#13 の段と合わせて外した(2026-10-20 の一覧に cc-by-4.0 のファイルは無い)。
- `openrail` / `openrail++` / `creativeml-openrail-m` などの RAIL: 用途の制限(Attachment A)を利用者に引き継ぐ義務がある。人が条件を読んで、アプリの規約に入れる必要がある。
- 非商用(`cc-by-nc-*`・`flux-1-dev-non-commercial-license` など): 販売するアプリでは使えない。
- 売上・利用者数の基準つきのコミュニティライセンス(`stabilityai-ai-community`・`stable-audio-community`・`tencent-hunyuan-community`・`ltx-2-community-license`・MiniMax のものなど): 基準を超えたら別の契約が要り、表示や地域の条件が付くものもある。
- `llama*`・`gemma`・`dinov3-license`: 独自の規約(利用者数の基準・用途の制限・表示)。
- `other`(上のどれでもない独自のもの)・不明: 読んでいないので通さない。
  - `other` は `license_name` で照合する。
  - 人が読んで問題ないと判断したもの(例: `mit-and-apache-2.0`)は許可リストに名前を足せば通せる。

## ファイルのライセンスの読み方

一覧の `license` は、置き場所のリポジトリの表記をそのまま持つことがある。

- リポジトリの中には別のモデルの部品(テキストエンコーダ・画像の読み取り部・検出器など)が入っていて、本当のライセンスはリポジトリの表記と違うことがある。
  - 例: VAST-AI/TripoSplat(表記 mit)の `dino_v3_vit_h` は DINOv3 の規約。
  - 例: Comfy-Org/SDPose の `rt_detr_v4-x-hgnet` は SDPose ではなく RT-DETRv4。
- カードの表記が元のモデルより寛容なことがある。
  - 例: SDPose のカードは mit だが、U-Net は Stable Diffusion 2 から作られている。

gate は一覧を書き換えずに、判定の中で次の順に読む(どれかで決まったらそこで止める):

1. **ファイル名が制限付きの部品**なら、どのリポジトリの中にあっても、一覧の値より優先してその規約で読む(保留):

   | ファイル名 | 読み方 | 根拠 |
   |---|---|---|
   | `llama` / `llava` | llama3.1 | https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct |
   | `gemma` 1〜3・`t5gemma` | gemma | https://ai.google.dev/gemma/terms。Gemma 4 は Apache-2.0(https://ai.google.dev/gemma/docs/gemma_4_license)なので当てない |
   | `dino_v3`・`dinov3` | other(dinov3-license)、本家が申請を求める | https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m(gated: manual)。原文 https://ai.meta.com/resources/models-and-libraries/dinov3-license は配るときに規約の写しと「Built with DINOv3」の表示を求め、用途を制限する |
   | `sam3`・`sam3.1` | other(sam-license)、本家が申請を求める | https://huggingface.co/facebook/sam3(gated: manual) |
   | `depth_anything_v2_vit{b,l,g}` | cc-by-nc-4.0 | https://huggingface.co/depth-anything/Depth-Anything-V2-Large |
   | Lumina 2 系の一式(`lumina_2.safetensors`・`…lumina…all_in_one`・`NetaYume…all_in_one`) | gemma | テキストエンコーダ Gemma 2 2B を含む(https://huggingface.co/neta-art/Neta-Lumina・https://huggingface.co/duongve/NetaYume-Lumina-Image-2.0 のカード) |

   ほかに、`hf.COMPONENTS` の寛容でない行・申請を求める行(Qwen2.5-VL-3B の qwen-research など)も同じ扱い。
2. **本家を確かめられない部品**(`hf.UNVERIFIED_COMPONENTS`)は不明: `rt_detr_v4`。
   RT-DETRv4(https://github.com/RT-DETRs/RT-DETRv4)のコードは Apache-2.0 だが、重みは DINOv3 を教師にした蒸留で作られ、重みのライセンスの記載が無い。
3. **元のライセンスを読めない系統**(`hf.LINEAGES`)は不明。
   名前はファイル名と、ライセンスを読んだ道(`license_from`・`license_path`。再配布のリポジトリの名前は除く)で見る:

   | 系統 | 根拠 |
   |---|---|
   | `sdpose` | https://huggingface.co/teemosliang/SDPose-Wholebody のカード「U-Net backbone initialized with Stable Diffusion v2 weights」(表記は mit) |
   | `lotus`(lotus-depth / lotus-normal) | https://github.com/EnVision-Research/Lotus の学習の設定が `stabilityai/stable-diffusion-2-base` から始める(https://huggingface.co/jingheya/lotus-depth-d-v1-1 の表記は apache-2.0) |
   | `sd2`(sd2・sd21・stable-diffusion-2) | Stable Diffusion 2 の元(stabilityai/stable-diffusion-2・-2-base)は 2026-10-20 に匿名で読めない(401)。写し https://huggingface.co/sd2-community/stable-diffusion-2-base の表記は openrail++ |
   | `void-model`(void_pass・netflix/void-model) | https://huggingface.co/netflix/void-model のカード「VOID is built on CogVideoX-Fun-V1.5-5b-InP」。元の LICENSE は The CogVideoX License(VOID の表記は apache-2.0。#28) |

   系統の表(`hf.FAMILIES`。SDXL・SD1.5・Marigold v1(openrail++)など)より寛容な値も不明。
4. ライセンスを**再配布側の表記からしか読めていない**(`license_from` が再配布のリポジトリ)なら、確かめられないとして保留。
   ただし出どころの分かる部品は、その本家のライセンスで読む:

   | ファイル名 | ライセンス | 本家 |
   |---|---|---|
   | `umt5_xxl…` | apache-2.0 | https://huggingface.co/google/umt5-xxl |
   | `t5xxl…`・`t5-v1_1…`・`t5-base…` | apache-2.0 | https://huggingface.co/google/t5-v1_1-xxl |
   | `clip_l…` | mit | https://github.com/openai/CLIP |
   | `clip_g…` | mit | https://huggingface.co/laion/CLIP-ViT-bigG-14-laion2B-39B-b160k |
   | `clip_vision_h…` | mit | https://huggingface.co/laion/CLIP-ViT-H-14-laion2B-s32B-b79K |
   | `wan_2.1_vae…`・`Wan2_1_VAE…` | apache-2.0 | https://huggingface.co/Wan-AI/Wan2.1-T2V-14B |

5. **別の系統の部品**: ファイル名に別のモデルの印(`hf.FOREIGN_MARKERS`: dino・sam・siglip・clip・t5・qwen・llama・gemma・mistral・birefnet・depth-anything・moge・detr・flux・wan・trellis・hunyuan・stable-diffusion・lumina・ltx など)があり、その印が置き場所のリポジトリ・ライセンスを読んだ道の名前に出てこないとき。
   - **本家を確かめた行**(`hf.COMPONENTS`・`hf.FAMILIES`)がファイル名に当たるときだけ通す。部品の表の行なら、その本家のライセンス。
   - 当たらなければ不明。
   - 例: TripoSplat の `flux2-vae` は部品の表の FLUX.2-VAE(apache-2.0)。Pixal3D の `trellis_2_shape_vae` は microsoft/TRELLIS.2-4B(mit)。JoyAI の `qwen3vl_8b_joyimage_edit` は系統の表の Qwen3-VL(apache-2.0)。
6. それ以外は一覧の値(本家から読んだもの)。

`update` も同じ表(部品の表・確かめられない部品・系統・別の系統の部品)で決め、確かめられないものは `null` にする([sources.ja.md](sources.ja.md) の「ライセンス」)。
gate のこの読み方は、一覧が古い決め方で作られていても判定で落とせるようにする二重の守り。

### レビューで見つかった、auto を間違えていたもの(#14、2026-10-20)

最初の版は 93 件を auto にしていたが、次の 10 件は間違いだった(カードと食い違ったまま auto になっていた)。
今は一覧の決め方(update)と gate の両方で保留になる。`tests/test_gate_real.py` に回帰のテストがある。

| モデル | 間違っていた理由 |
|---|---|
| `3d_pixal3d_multi_views`・`3d_pixal3d_trellis2_image_to_model` | Comfy-Org/Pixal3D の `dino_v3_L_naf_fp32` に、本家 TencentARC/Pixal3D の mit が付いていた(DINOv3 の規約・申請が要る) |
| `3d_triposplat_image_to_gaussian_splat` | VAST-AI/TripoSplat の `dino_v3_vit_h` に、リポジトリの mit が付いていた |
| `utility_sdpose_ood_image_to_pose`・`utility_sdpose_multi_person`・`utility_sdpose_multi_person_video`・`utility_sdpose_ood_video_to_pose_map` | SDPose のカードは mit だが Stable Diffusion 2 から作られている。同じリポジトリの `rt_detr_v4` も SDPose の mit になっていた |
| `image_lotus_depth_v1_1`・`image_qwen_image_instantx_controlnet`(Lotus を含む) | Lotus のカードは apache-2.0 だが stable-diffusion-2-base から学習している |
| `image_netayume_lumina_t2i` | 一式のファイル `NetaYumev35_pretrained_all_in_one` はテキストエンコーダ Gemma 2 2B を含む(レビューの後の見直しで見つけた) |

## URL の確かめ方(`url_reachable`)

`genmodelfit update` が、すべてのファイルの Hugging Face の URL に匿名の HEAD を送り、応答コードを `files[].head_status`、日時を `head_checked_at` に記録する。

- 同じ URL は 1 回だけ、同時に 8 本まで。429 は `Retry-After` を守って待つ。
- Civitai など HF 以外には送らない(未確認として保留)。

転送は基本的に追わない。

- 例外: **307 で転送先が `/api/resolve-cache/…` でないもの**(リポジトリの名前が変わったもの)。無いファイルでも 307 が返るので、転送先に 1 回だけ HEAD を送ってその応答を記録する(`fetch.follow_rename`)。
- その先がまた別の転送なら未確認(`null`)。
- このため一覧に残る 307 は `/api/resolve-cache` への転送だけで、gate は 200・302・307 を「取れる」とみなす。
- 応答の内訳は一覧の `sources.head_check` で分かる。
  - 2026-10-21T06:20Z の同梱の一覧では、HEAD を送った 487 URL のうち 302 が 461・307 が 3・401 が 23。Civitai の 6 か所には送らない。
  - 2026-10-20 の一覧で 307 だった 6 件は、名前の変わったリポジトリで、転送先はどれも 302 だった。

2026-10-20 に実際に送って確かめた応答:

| ファイル | 応答 | 転送先 | 記録 |
|---|---|---|---|
| LFS(Xet)のファイル(重みのほぼ全部。小さい safetensors も) | 302 | `https://us.aws.cdn.hf.co/xet-bridge-us/…`。`x-linked-size`・`x-linked-etag`(sha256)付き | 302 |
| LFS でない小さいファイル(`model_index.json` など) | 307 | `/api/resolve-cache/models/…`(相対) | 307 |
| リポジトリの名前が変わったもの(`Comfy-Org/flux2-klein` など) | 307 | 新しいリポジトリの `/resolve/…`(相対) | 転送先の応答(302・404 など) |
| gated のリポジトリ | 401 | なし | 401 |
| 無いファイル | 404 | なし | 404 |

HEAD は HF の "resolvers" の枠(応答の `ratelimit-policy: "fixed window";"resolvers";q=3000;w=300`。API の 500 回 / 5 分とは別)で数えられるので、1 回の update で使い切ることは無い。

### 確認の期限(`max_head_age_days`)

上流ではファイルが消えたり、リポジトリが gated になったりする。

- HEAD の確認は既定で **30 日**まで信じる。
- それより古い(または日時の記録が無い)ファイルがあれば `url_reachable` を保留にする(detail: 日本語なら「確認が古い」、英語なら "check too old")。
- `GatePolicy(max_head_age_days=…)` / `--max-head-age-days N`(`none` で期限なし)で変えられる。

**同梱の一覧(`src/genmodelfit/data/db.json`)の HEAD の確認の日時**は `genmodelfit system --json` の `database.status`(`heads_checked_oldest`・`expires_at`。[schema.ja.md](schema.ja.md)「Data status / 一覧の新しさ」)で分かる。

- `expires_at` を過ぎると、同梱の一覧だけでは auto が 0 件になる(2026-10-21T06:20Z の同梱の一覧なら 2026-11-20T06:20Z)。
- 使う側のアプリは、定期的に(30 日以内に)`genmodelfit update --from-release`(署名つきの配布。勧める)か、次の HEAD のやり直しを実行する:

```powershell
genmodelfit update --heads-only [--db PATH] [--output PATH]   # 既定はキャッシュの一覧を読んでキャッシュに書く
```

キャッシュが読めない・型が合わないときは、同梱の一覧を読んで書く(0.9.1、#63)。

## ComfyUI の版(`comfyui_version`)

**使う側のアプリは、自分が導入・同梱する ComfyUI の版を必ず渡す**(`--comfyui-version` / `GatePolicy(comfyui_version=…)`)。
例: AI生成キット は ComfyUI v0.40.0 を導入するので `GatePolicy(comfyui_version="0.40.0")`。
渡さないと、利用者の ComfyUI より新しい版が要るテンプレートを auto にしてしまうことがある。

渡さないときの既定:

- ライブラリ(`evaluate_offline`): `sources` に記録された版を使う。`sources` も渡さなければ `comfyui_version` は保留。
- CLI: 一覧に `update` が記録した最新のリリース(`sources.comfyui.latest_release`)。

`update` の記録のしかた:

- `https://api.github.com/repos/Comfy-Org/ComfyUI/releases/latest` を認証なしで 1 回だけ取り、`tag_name` を記録する(2026-10-20 は `v0.42.1`)。
- 取れなければ `null` と警告。
- `update --heads-only` で取れなかったときは、前に記録した版を残して警告を出す。

版の比べ方:

- 数字の桁で比べる(足りない桁は 0)。
- 数字が同じなら、プレリリース(`0.4.0-rc1`・`0.4.0rc1`・`0.4.0.dev3`)はリリース(`0.4.0`)より前とみなす。
- 必要な版の側でも使う版の側でも同じ(使う版が `0.4.0-rc1` なら、`0.4.0` が要るテンプレートは保留)。
- `+build` の後ろは見ない。
- 読めない印(`0.4.0-foo`)は版を読めないとして保留。

## 無検閲のモデル(`content`)

自動のおすすめは初心者向けなので、無検閲のモデルは既定で保留にする。

- 一覧から外すわけではない。
- `recommend --content uncensored`・`fit`・`info` などの一覧・絞り込みには今までどおり出る。

## 自動で出せるもの

件数は `genmodelfit gate --only-auto --comfyui-version <版>` で確かめる。

- 2026-10-21T06:20Z の同梱の一覧で、0.40.0 なら 95 件(画像 57・動画 29・音楽 9)、記録の v0.42.1 なら 99 件。3D・読み上げ・効果音は 0。
- 2026-10-20 の時点では 83 件。レビューの指摘を直したあと、そのすべてのファイルについて、ライセンスと根拠(部品の表・系統の表の行、または本家のカード)を人が確かめた(PR #14 の説明に一覧)。
- その後に増えた分は、tests/test_license_sources.py の `JUSTIFIED_AUTO`([sources.ja.md](sources.ja.md)「出どころの表」)。

確かめた中で注意が要るもの:

- `black-forest-labs/FLUX.1-schnell` は HF で gated(`gated: auto`。同意を押せばすぐ取れる)。ライセンスは apache-2.0 で、一覧のファイルは gated でない置き場所(Comfy-Org など)から取るので通している。部品の本家の gated を一般には見ていない(部品の表で申請が要ると書いた DINOv3・SAM 3 だけ)。
- `video_wan_ati` の本体(Kijai/WanVideo_comfy)は、update が本家を Wan2.1-VACE と選んでいるが、本当の本家は https://huggingface.co/bytedance-research/ATI(apache-2.0、base_model は Wan2.1-I2V-14B-480P)。結果は同じ apache-2.0。
