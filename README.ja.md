# genmodelfit

[English](https://github.com/laxshmate/genmodelfit/blob/main/README.md) · **日本語**

画像・動画・音楽・効果音・読み上げ・3D の生成モデルのうち、どれがあなたの PC で快適に動くかを判定するツールです。

[![PyPI](https://img.shields.io/pypi/v/genmodelfit)](https://pypi.org/project/genmodelfit/)
[![Python](https://img.shields.io/pypi/pyversions/genmodelfit)](https://pypi.org/project/genmodelfit/)
[![License: MPL-2.0](https://img.shields.io/pypi/l/genmodelfit)](https://github.com/laxshmate/genmodelfit/blob/main/LICENSE)

![VRAM 8 GiB・メモリ 32 GiB の PC での uvx genmodelfit search flux と recommend](https://raw.githubusercontent.com/laxshmate/genmodelfit/main/docs/images/demo.ja.gif)

genmodelfit 0.13.1 で `uvx genmodelfit search flux` と `uvx genmodelfit recommend` を動かしたところです。一覧は毎週
更新するので、並ぶモデルと件数は変わります。この GIF の GPU は、VRAM 8 GiB の架空の Ada 世代です(実在の PC ではありません)。

LLM 向けに同じことをする [llmfit](https://github.com/AlexsJones/llmfit) に着想を得ています。状態: アルファ版
([CHANGELOG.md](https://github.com/laxshmate/genmodelfit/blob/main/CHANGELOG.md))。

## すぐ試す

**入れずに試す。** [uv](https://docs.astral.sh/uv/getting-started/installation/) があれば、コマンド 1 つで動きます。
uv が専用の一時的な環境で動かします。Python が無ければ uv が用意します。お使いの Python や PATH には何も足しません。

```
uvx genmodelfit recommend         # この PC に合うモデルを種類ごとに出す
uvx genmodelfit system            # この PC で見つけたもの
```

この README のコマンドはすべて、`genmodelfit` を `uvx genmodelfit` に替えれば同じように動きます。
uvx は前に取った版を使い続けることがあります。`uvx genmodelfit@latest …` なら、新しい版が出ていないかを先に確かめます。

### 入れ方

```
pipx install genmodelfit        # 1 回だけ pipx ensurepath を実行し、端末を開き直す
uv tool install genmodelfit     # 1 回だけ uv tool update-shell を実行し、端末を開き直す
pip install genmodelfit
```

`pip install` のあとでコマンドが見つからないときは、入れたのと同じ Python で `python -m genmodelfit …` を実行します
(pipx・uv tool では動きません)。pip の「not on PATH」の警告など、詳しく:
[docs/commands.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/commands.ja.md#genmodelfit-が見つからないとき)。

実測で協力するなら `genmodelfit share`([実測で協力する](https://github.com/laxshmate/genmodelfit/blob/main/README.ja.md#実測で協力する))。

## できること・しないこと

- 一覧にある各モデルが、この PC で動くか・どのくらい余裕があるかを**判定**します(VRAM・メモリ・GPU の世代から)。
- 種類ごと(image 画像・video 動画・music 音楽・sfx 効果音・tts 読み上げ・3d)に、良いものから**おすすめ**します。
- ライセンスを見せて確認したうえで、モデルのファイルを**ダウンロード**します。
- 生成は**しません**。モデルを動かすのは [ComfyUI](https://github.com/comfyanonymous/ComfyUI)(またはモデルが指定する
  実行環境)です。genmodelfit が持つのはモデルの**情報だけ**で、モデル本体は持ちません。

llmfit との違い: llmfit は LLM だけが対象です([llmfit#41](https://github.com/AlexsJones/llmfit/issues/41))。
生成モデルは、精度・解像度・コマ数・メモリへの逃がし方で、必要なメモリが数倍変わります。

### 動く環境

- Windows・Linux・macOS、Python 3.10 以上(`uvx` なら uv が Python を用意します)。
- GPU: NVIDIA(CUDA)。使える NVIDIA の GPU が無いときは、AMD(ROCm)・Intel(XPU)・Apple silicon(MPS)を
  各社の対応表で判定します。内蔵 GPU は共有メモリで判定し、それ以外の PC は CPU だけとして判定します。
  対象の GPU と OS の版: [docs/hardware.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/hardware.ja.md#nvidia-以外の-gpu0702223)。

## 使い方

```
genmodelfit system                                # 1. PC を確かめる
genmodelfit recommend                             # 2. 種類ごとのおすすめ
genmodelfit recommend -m image --task edit        #    1 つの種類・用途だけ
genmodelfit fit                                   # 3. 動くものを全部、1 つの表で
genmodelfit info "Z-Image-Turbo"                  # 4. 1 つのモデルを詳しく
genmodelfit download "Z-Image-Turbo" --list       # 5. 落とさずにファイルを見る
genmodelfit download "Z-Image-Turbo"              #    ComfyUI に落とす
genmodelfit update --from-release                 # 6. 署名付きの最新の一覧を取る
```

1. `system`: GPU・VRAM・メモリ・ページファイル・ディスクの空きと、モデルの一覧が新しいかを出します。
2. `recommend`: この PC で動くモデルを、種類ごとに良い順で出します。`-m` で種類を、`--task` で種類の中の用途を
   選べます(一覧は `genmodelfit types`)。
3. `fit`: この PC で動くモデル(Perfect / Good / Marginal)を全部出します。`--all` で補助ツールと動かないものも、
   `--sort score` で種類をまたいだ順位、`--fit good` で 1 つの段階だけ。
4. `info`: ファイル・ライセンス・動かし方ごとのメモリ・限界と、Verified になる/ならない理由を出します。
5. `download`: 先に確認してから ComfyUI のフォルダへ落とし、どのファイルも sha256 で確かめます。`--comfyui-dir`
   が無ければ ComfyUI を探し、見つけた場所を出します。
6. `update --from-release`: 一覧は毎週作り直しています。リンクの確認から 30 日を過ぎたモデルは、新しい一覧を
   取るまで Verified でなくなります。

### ほかの PC で判定する

`--vram` と `--ram`(GiB)で、検出した大きさを置き換えます。例: `genmodelfit recommend --vram 12 --ram 32`。
変わるのは量だけです。使える NVIDIA の GPU があれば、その世代のままです(理由の文には「<GPU 名> の世代」と出ます)。無ければ `--vram` は、世代の分からない
NVIDIA の GPU として判定します。RTX 30 系以降(30・40・50 系)なら世代で見積もりは変わらないので、GPU を買う前の
確かめにも使えます。`--width`・`--height`・`--frames`・`--length`・`--precision` で、設定を決めて判定できます。

### 表示の言語

出力の既定は英語です。日本語にするには、どのコマンドでも `--lang ja` を付けるか、`genmodelfit config set lang ja`
で 1 回保存します。列の名前・段階の名前・動かし方の印は、どちらの言語でも英語のままです。
詳しく(環境変数・Python・折り返し): [docs/commands.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/commands.ja.md#表示の言語)。

## 結果の読み方

### 例

VRAM 8 GiB・メモリ 32 GiB の PC での `genmodelfit recommend --lang ja`(種類ごとに一番上の 1 件。`COLUMNS=100`、
GIF と同じ架空の GPU)。[tools/screenshots.py](https://github.com/laxshmate/genmodelfit/blob/main/tools/screenshots.py)
の `--readme` でこのブロックを作り直せます:

```
$ genmodelfit recommend --vram 8 --ram 32 -n 1 --lang ja
PC: VRAM 8.0 GiB と仮定(--vram)、メモリ 32.0 GiB(--ram)
おすすめ: 83 件中 6 件(隠した 221 件: 無検閲 26・補助ツール 34・ライセンス不明 28・動かない 133。ほ
    かに 25 件のワークフローをモデルにまとめた)
== 画像 (image): 32 件中 上位 1 件(続きは -m image)
Type  Status      Score Model                      Use       Prec  VRAM Mem% Mode    Disk License
image ● Good        100 Anima Base 1.0             gen,edit  bf16   5.7  71% GPU~     5.4 other
== 動画 (video): 12 件中 上位 1 件(続きは -m video)
Type  Status      Score Model                      Use       Prec  VRAM Mem% Mode    Disk License
video ● Good         96 Wan 2.2 5B TI2V            gen       fp16   6.1  76% GPU+RAM 16.9 apache-2.0
== 音楽 (music): 10 件中 上位 1 件(続きは -m music)
Type  Status      Score Model                      Use       Prec  VRAM Mem% Mode    Disk License
music ● Good         96 ACE-Step 1.5 Turbo         gen       bf16   6.0  76% GPU~     9.3 mit
…
```

最初の行は PC と、隠したモデルの数と理由です。表のあとに、印の説明が続きます。`fit` は同じ列を 1 つの表で出します。

### 段階

llmfit と同じく、動かし方で使うメモリ(GPU なら VRAM、CPU ならメモリ)の使用率で決めます。動かし方によっては
上限があります。

![段階: 60% まで Perfect、85% まで Good、98% まで Marginal、それより上は Too tight。GPU~ と GPU+RAM は Good まで、CPU は Marginal まで](https://raw.githubusercontent.com/laxshmate/genmodelfit/main/docs/images/fit-levels.ja.png)

| 段階 | 使用率 | 動かし方による上限 |
|---|---|---|
| **Perfect** | 60% 以下 | 全部 GPU に載るとき(`GPU`)だけ。余裕があるという意味で、速さの保証ではない |
| **Good** | 85% 以下 | `GPU~`・`GPU+RAM` はここまで。`CPU` でも、実時間に動く読み上げと 1 回の計算で終わる補助ツールはここまで |
| **Marginal** | 98% 以下 | `CPU` の生成と、メモリが少し足りないときはここまで |
| **Too tight** | 98% 超 | 動かない |
| **?** | — | 判定していない(ダウンロードの大きさが分からない) |

`recommend` の並びは、動くものが先で、Perfect/Good(`GPU+RAM`・`CPU` は後)→ Marginal です。その種類の GPU が
2 機種以上で動いた記録があれば、PC の行に「✓ 確認済みの種類」と出ます(動作を確かめた種類という意味)。
実測で段階を決める条件も含めた決まり: [docs/scoring.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/scoring.ja.md)。

### 列

| 列 | 意味 |
|---|---|
| Score | 0〜100。メモリの余裕(Mem% 70% までは 100)。速さ・品質・人気は入れない |
| Use | `gen`・`edit`・`i2v` など(`genmodelfit types`) |
| Prec | 載る中で一番良い精度(fp16・bf16・fp8・int8 など) |
| VRAM | その動かし方で要る GiB。`m` = 実測 |
| Mem% | 一番きついメモリの使用率(VRAM。CPU ならメモリ)。`RAM` = PC のメモリが足りない |
| Mode | `GPU` 全部 GPU、`GPU~` 部品を順に載せ替え、`GPU+RAM` メモリへ逃がす(遅い)、`CPU`(遅い)、`-` 動かない |

`Type`・`Status`・`Model`・`Disk`(ダウンロードの GiB)・`License`(`other` = 自分で原文を読む)は名前のとおりです。
端末が狭いときは、`Disk`、次に `Mem%` から外します。すべての列の詳しい意味:
[docs/commands.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/commands.ja.md#表の列)。

### ライセンス不明・無検閲のモデルは、既定で出しません

ライセンスの分からないモデルと、無検閲のモデルは、既定では一覧に出しません。何も付けずに使えば、こうしたモデルを
知らずに選ばずに済みます。隠した件数は、結果の最後に「注意」として出ます。
出すときは、次のオプションを付けます。

- `--any-license`: ライセンスの分からないモデルも出す
- `--uncensored`: 無検閲のモデルも出す(`Censored` の列も出す。無検閲だけなら `--content uncensored`)

`uncensored` は、安全フィルタを外したモデルや、成人向けの出力を想定したモデルの印です。既定では Verified に
なりません。未成年の性的表現を目的とするモデルは、一覧から除外しています
([docs/sources.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/sources.ja.md))。

### 見積もりの確かさ

0.13.1 の時点では、見積もりは 1 種類の NVIDIA の GPU の少数の実測で較正し、別の GPU の実測で照合しています。
`m` の無い値は目安として見てください。ComfyUI は空きがあるほど VRAM を多く使うので、大きい GPU で測ったピークは
最低限の必要量ではありません。件数と誤差: [docs/accuracy.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/accuracy.ja.md)。

## 実測で協力する

いろいろな GPU の実測が集まるほど、見積もりが良くなります。コマンド 1 つで、git は要りません
(NVIDIA・AMD・Intel・Apple の GPU。GitHub のアカウントが要ります):

```
genmodelfit share
```

![genmodelfit share: ComfyUI を見つける、計画を見せる、測る、送る中身を全部見せる、プルリクエストか Issue で送る](https://raw.githubusercontent.com/laxshmate/genmodelfit/main/docs/images/share-flow.ja.png)

1. ComfyUI を見つけます(起動中のもの。無ければ見つけたものを起動し、終わったら止めます)。
2. 測ると役に立つ、入っているモデルを合計 5 分ほど選び、計画を見せて 1 回だけ尋ねます。
3. 1 つずつ測り、結果をこの PC に保存します。
4. 送るファイルを全部見せて、送り方を尋ねます: **1)** プルリクエスト(PR。GitHub にログイン)、**2)** Issue
   (ログインなし。ボットが PR を作る)、**q)** 送らない(あとで `share --no-measure` で送れる)。
5. メンテナがマージすると、次の版から、同じ GPU を使う人みんなの判定に使われます。

`genmodelfit measure` は、選んだ 1 つを測るだけで、何も送りません。出す候補は、ライセンスが分かるものだけです
(`share --any-license` ならライセンス不明のものも含めます)。

実測の使われ方: NVIDIA で ComfyUI の動的 VRAM(今の既定)のときの実測は、見積もりの照合だけに使います。
動的 VRAM でない実測(AMD・Intel・Apple、または動的 VRAM を切った NVIDIA)は、その GPU の見積もりを直し、2 人の
実測がそろうと段階も決めます。✓ には、その種類の GPU で 2 機種以上の記録が要ります。

すべての手順・キー・`--dry-run`・設定を変えた測定・git を使う人向けの `bench`:
[CONTRIBUTING.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/CONTRIBUTING.ja.md)。

## 送るもの・送らないもの

**通信。** 判定のコマンドと Python の関数は**ネットにつなぎません**(すべてのソケットをふさいだ試験で確かめて
います。Python で通信するのは `update_from_release` と `download` だけ)。通信するコマンドは次だけです。

- `update`: 公開の情報(GitHub・Hugging Face の API・モデルの URL への HEAD)。`--from-release` は `db.json` と
  署名だけ。`download`: モデルのファイル。
- `download`・`measure`・`share`: この PC で起動中の ComfyUI(127.0.0.1 の 8188・8000)と、その ComfyUI だけ。
  `measure`・`share` は GitHub から公式のテンプレートと入力の画像を取ることもあります。起動した ComfyUI は
  127.0.0.1 でだけ待ちます。`bench` は `--url` で指定した ComfyUI とだけ話します。
- `share`: 測る前は、もうある GitHub のトークンを api.github.com で確かめるだけです。同意したあとで、ログインして
  PR を開きます(github.com・api.github.com)。`share --issue` はブラウザでフォームを開くだけです。ログインの
  トークンは本人だけが読める形で保存します。

`~/.netrc` の資格情報は付けません。テレメトリもありません。勝手に送ることはありません。

**`share` が送るもの**(送る前にすべて見せます):

- GPU の名前・VRAM・会社と backend・世代と compute capability(NVIDIA)・ドライバの版。AMD・Intel の GPU では
  PCI の型番 ID(`1002:744c` のような機種の番号。同じ機種ならどのカードでも同じ。差し場所ではない)
- OS の種類・GiB に丸めたメモリ・ComfyUI と genmodelfit の版・一覧の版・日付(時刻は入れない)
- モデル・設定・結果・最大 VRAM / メモリ・時間とその内訳(キュー・読み込み・サンプリング・デコード)・モデルの
  載せ方(`offload`)・測り方・モデルのファイルの指紋・最大 VRAM が GPU の総量を超えて共有メモリに逃げた印
- その測定に対する genmodelfit の見積もり(あなたの、そのモデル・その GPU の実測は使わずに作ったもの)
- 1 回に 1 つの失敗の記録のファイル: モデル・失敗の種類・足りないノードの名前・エラーの種類の名前・失敗した
  ノードの種類

**送らないもの:** PC 名・ユーザー名・パス・GPU の UUID・PCI の差し場所・シリアル番号・IP アドレス・CPU 名・正確な
時刻・プロンプト・あなたの入力・生成した画像や音・エラーの本文・自分で止めた測定やほかのプログラムに乱された測定・
この PC にファイルが無いモデル。

コマンドごとの表: [docs/schema.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/schema.ja.md#side-effects--通信と書き込み)。
ファイルの形: [docs/schema.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/schema.ja.md#contrib)。

## アプリから使う(Python / JSON)

各コマンドは `--json` を付けると JSON で出します([docs/schema.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/schema.ja.md))。
Python では次のとおりです。

```python
import genmodelfit as gmf

for f in gmf.recommend(modality="image", task="edit", limit=5):
    print(f.model.id, f.estimate.fit, f.to_dict()["memory_required_gb"])

if gmf.data_status().expired:  # 一覧は Verified(auto)を出せるほど新しいか
    db = gmf.update_from_release()  # 署名付きの一覧(通信するのはこれと gmf.download だけ)

fit = gmf.judge_model("image_sdxl_simple", gpu_name="NVIDIA GeForce RTX 3060", vram_gb=12, driver="560.94")
print(fit.to_dict()["fit_level"])
```

互換を守るのは `gmf.__all__` の名前だけです。ほか(AMD・Intel の `system_from`・`load_db`・セッション):
[docs/schema.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/schema.ja.md#python-api-stable--公開の範囲)。

## 参考

### コマンド

| コマンド | 内容 |
|---|---|
| `system [--all-gpus]` | この PC と、一覧の新しさ |
| `recommend [-m TYPE] [--task USE] [-n N] [--auto-only]` | 種類ごとのおすすめ |
| `fit [-m TYPE] [--fit LEVEL] [--sort KEY] [--all]` | 動くモデルを全部、1 つの表で |
| `search <text>` | ID・名前に文字を含むモデルを、この PC 向けに判定して出す |
| `types` | 6 つの種類と、その中の用途(`--task` の名前)と件数 |
| `info <名前か ID>` | ファイル・ライセンス・見積もり・限界・Verified の条件 |
| `download <名前か ID> [--list] [--comfyui-dir DIR]` | ファイルを落とす([ダウンロード](https://github.com/laxshmate/genmodelfit/blob/main/README.ja.md#ダウンロード)) |
| `update [--from-release]` | 一覧を上流から作り直す、または署名付きの一覧を取り込む |
| `gate` | Verified のモデルの数と、ほかが保留の理由 |
| `diff [OLD] [NEW] [--all]` | 2 つの一覧の違い([一覧の保守](https://github.com/laxshmate/genmodelfit/blob/main/README.ja.md#一覧の保守)) |
| `config [show \| set KEY VALUE \| unset KEY \| path]` | 設定: `download` の ComfyUI のフォルダと表示の言語([docs/download.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/download.ja.md#genmodelfit-config)) |
| `measure [名前か ID]` | ComfyUI で 1 つのモデルを実測して保存する(何も送らない) |
| `share [ファイル]` | 数分測って、結果を GitHub の PR で送る(`--issue` は Issue のフォーム) |

保守する人向け: `review`・`bench`・`contrib`。すべてのコマンドとオプション・環境変数・終了コード:
[docs/commands.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/commands.ja.md)(`--help` と同じ内容)。

### Verified(自動でおすすめに出す条件)

`info` が `自動のおすすめ(Verified): yes`(JSON の `auto`)と出すのは、次を**すべて**満たすときだけです:
ライセンスが商用利用できる許可リストにある、gated が無い、公式の ComfyUI ワークフロー、すべての URL を 30 日以内に
確かめた、sha256 が分かる、内容が `sfw`、確認待ちでない。表には出しません。詳しくは [docs/gate.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/gate.ja.md)。

### ダウンロード

一覧にある https の URL だけを使い、sha256 を確かめ、Ctrl+C のあとは続きから落とし、置き場所の外には書きません。
gated は落としません。無検閲とライセンス不明のファイルは、同意があるときだけです([docs/download.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/download.ja.md))。

### データ源

[ComfyUI のワークフローテンプレート](https://github.com/Comfy-Org/workflow_templates)(MIT)、
[Hugging Face Hub の API](https://huggingface.co/docs/hub/api)(llmfit と同じくダウンロード数 10,000 以上)、
少しだけ手で足したモデル。Civitai は使いません([docs/sources.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/sources.ja.md))。

### 一覧の保守

一覧は毎週作り直します。新しいモデル・変わったモデルは、人が承認する(`genmodelfit review`)まで `pending` のままで
Verified にしません。署名するのは、人が確認したデータだけです([docs/release.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/release.ja.md))。
`genmodelfit diff` は一覧の違いを出します([docs/commands.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/commands.ja.md#genmodelfit-diff))。
README の画像: `tools/screenshots.py`・`tools/diagrams.py`・`tools/demo_gif.py`。

### 文書の一覧

文書の一覧と、それぞれ誰向けか: [docs/README.ja.md](https://github.com/laxshmate/genmodelfit/blob/main/docs/README.ja.md)。

## ライセンス

[Mozilla Public License 2.0](https://github.com/laxshmate/genmodelfit/blob/main/LICENSE)(MPL-2.0)。同梱のデータも同じです。
genmodelfit を直さずに使うだけなら、ソース非公開のアプリにも組み込めます。genmodelfit のファイルを直して配るときは、
そのファイルを MPL-2.0 で公開してください。アプリ(例: PyInstaller の exe)に同梱して配るときは、ライセンスの表示を
残し、ソースの入手先(https://github.com/laxshmate/genmodelfit、MPL-2.0 §3.2)を利用者に知らせてください。
`certifi`(MPL-2.0)も同じです。データの出典は [THIRD_PARTY_NOTICES](https://github.com/laxshmate/genmodelfit/blob/main/THIRD_PARTY_NOTICES)。
モデルのライセンスは表示するだけで、利用を許諾するものではありません。
