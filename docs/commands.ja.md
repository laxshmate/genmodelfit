# コマンド一覧

[English](commands.md) · **日本語**

`genmodelfit` のすべてのコマンドとオプションを 1 ページにまとめたものです。コマンドごとに、何をするか・オプション・
読むものと書くもの・終了コードがあります。同じ文は `genmodelfit <コマンド> --help` でも出ます。

## よくある使い方

| したいこと | コマンド | 詳しく |
|---|---|---|
| 入れる | `pipx install genmodelfit`(か `uv tool install genmodelfit` / `pip install genmodelfit`)。入れずに試すなら `uvx genmodelfit recommend` | [README「入れ方」](../README.ja.md#入れ方) |
| 入れたのに `genmodelfit` が「見つからない」 | pipx・uv tool なら `pipx ensurepath`・`uv tool update-shell` を 1 回実行して端末を開き直す。pip なら、入れたのと同じ Python で `python -m genmodelfit recommend` | [下](#genmodelfit-が見つからないとき) |
| この PC で動くモデルを知る | `genmodelfit recommend` | [README「すぐ試す」](../README.ja.md#すぐ試す) |
| 1 つの種類のモデルを探す(例は音楽) | `genmodelfit recommend -m music`(か `genmodelfit search <語>`) | [`recommend`](#genmodelfit-recommend)・[`types`](#genmodelfit-types) |
| 動くものを全部 1 つの表で見る | `genmodelfit fit` | [`fit`](#genmodelfit-fit) |
| 1 つのモデルを調べる(ファイル・ライセンス・見積もり) | `genmodelfit info "Z-Image-Turbo"` | [`info`](#genmodelfit-info) |
| モデルを ComfyUI に入れる(初めて) | `genmodelfit download "Z-Image-Turbo" --list` で確かめ、`--list` を外して実行 | [download.ja.md](download.ja.md) |
| 落とす場所を変える | `genmodelfit config set comfyui-dir D:/ComfyUI`(1 回だけなら `--comfyui-dir D:/ComfyUI`) | [download.ja.md「genmodelfit config」](download.ja.md#genmodelfit-config) |
| ComfyUI が見つからない(`download`・`measure`) | `--comfyui-dir D:/ComfyUI` を付ける(か `genmodelfit config set comfyui-dir D:/ComfyUI` で一度決める) | [download.ja.md「置き場所の決め方」](download.ja.md#置き場所の決め方) |
| 最新の一覧を取る | `genmodelfit update --from-release` | [`update`](#genmodelfit-update) |
| ほかの PC として判定する | `genmodelfit recommend --vram 12 --ram 32` | [README「ほかの PC で判定する」](../README.ja.md#ほかの-pc-で判定する) |
| スクリプト・アプリから使う | `--json` を付ける。落とすときは `--yes` と置き場所(`--comfyui-dir`、か `genmodelfit config`・`GENMODELFIT_COMFYUI_DIR`)も要る。置き場所が決まらないと落とさない(終了コード 1) | [schema.ja.md](schema.ja.md) |
| 表示の言語を変える | `genmodelfit config set lang ja`(1 回だけなら `--lang ja`) | [表示の言語](#表示の言語) |
| モデルを実測して送る | `genmodelfit share`(ComfyUI を見つけ、無ければ起動し、数分測って PR を開く) | [CONTRIBUTING.ja.md「かんたんな送り方」](../CONTRIBUTING.ja.md#かんたんな送り方git-は要らない) |

## 用語

| 用語 | 意味 |
|---|---|
| 段階(`Status`) | モデルがこの PC のメモリ(GPU なら VRAM、CPU ならメモリ)をどれだけ使うか: `perfect` は 60% まで、`good` は 85% まで、`marginal` は 98% まで、`too_tight` は 98% を超える(動かない)、`unknown` / `?` は判定していない(大きさが分からない)。動かし方による上限もある: [README「段階」](../README.ja.md#段階)。 |
| runnable | `perfect`・`good`・`marginal` のどれか(`fit` が既定で出すもの)。 |
| 逃がす(offload) | 重みの一部を VRAM ではなくメモリに置き、動かしながら GPU へ移すこと(`GPU+RAM`)。小さい GPU でも動くが遅い。「逃がさずに」は `GPU` か `GPU~` のこと。 |
| 動かし方(`Mode`) | `GPU` 全部 GPU に載せる、`GPU~` 部品を順に GPU へ載せ替える、`GPU+RAM` 重みの一部をメモリへ逃がす(遅い)、`CPU`(遅い)、`-` 動かない。[README「列」](../README.ja.md#列) |
| Score | 0〜100 のメモリの余裕(使用率 70% までは 100)。速さや品質ではない。 |
| モデル / ワークフロー | ワークフローは ComfyUI のテンプレート(か Hugging Face の項目)1 つで、`image_z_image_turbo_int8` のような ID を持つ。モデルは、いくつかのワークフローが共有する重み(系統 + 大きさ)で、ワークフローどうしは精度か使い方だけが違う。表はモデルごとに 1 行(`--all-workflows` ならワークフローごと)。`info` と `download` はどちらでも受ける。 |
| 用途(`--task`) | 種類の中でワークフローがすること: `gen`・`edit`・`i2v` など(`genmodelfit types` が並べる)。 |
| 一覧(`db.json`) | genmodelfit が判定するモデルの一覧。パッケージに 1 つ入っていて、`update --from-release` は署名つきの新しい一覧をキャッシュに置き、それからはそちらを使う(`--no-cache` で同梱、`--db PATH` でファイル)。 |
| HEAD の確認 | ダウンロードの URL のそれぞれに、名乗らずに HTTP の HEAD を送った結果(一覧に記録してある)。30 日(`--max-head-age-days`)より古い確認は期限切れで、そのモデルは `genmodelfit update --from-release` を実行するまで保留になる。日付は `system` で分かる。 |
| Verified / auto / 保留 | Verified(JSON では `auto`)は、gate の条件(ライセンスが許可リストにある・公式のワークフロー・HEAD の確認が新しい・sha256 が分かる・`sfw`・確認済み など)をすべて満たしたもので、アプリが人に尋ねずにおすすめに出してよい。満たさないものは保留(`held_reasons`)。[README「Verified」](../README.ja.md#verified自動でおすすめに出す条件)・[gate.ja.md](gate.ja.md) |
| 内容(`sfw` / `uncensored`) | `uncensored` は、安全フィルタを外したモデルや成人向けの出力を想定したモデルの印。`--uncensored`(両方)か `--content uncensored`(それだけ)を付けたときだけ出す。[README「ライセンス不明・無検閲のモデルは、既定で出しません」](../README.ja.md#ライセンス不明無検閲のモデルは既定で出しません) |
| GB | いつも GiB(1024³ バイト)。`--vram`・`--ram`・`--budget` も同じ。 |

## このページの読み方

- コマンドの節には、目的・使い方の行・何をするか・そのコマンドだけのオプション・ほかのコマンドと共通のオプション
  ([共通のオプション](#共通のオプション)に 1 回だけ載せる)・前提・読むものと書くもの・終了コード・例があります。
- 使い方の行の `[...]` は省けること、`A | B` はどちらか 1 つ、`...` は繰り返せることを表します。
- 「値」はオプションの後ろに書くもの(`--vram GB`)で、値の無いオプションは付けるだけのスイッチです。「既定」は、
  そのオプションを省いたときに使うものです。
- `-h` / `--help` と `--lang` はどのコマンドでも使えるので、下に 1 回だけ載せます。
- オプションは紛れない長さに略せます(`--modality` を `--mod`)。ただし同意のオプション `--uncensored`・
  `--any-license`・`--include-unknown-license`・`--content` は略さずに書きます(`recommend --unc` は使い方の誤りで、
  終了コード 2)。
- 同じ名前でもコマンドによって働きが違うオプションがあります。
  - `--precision`: `recommend`・`fit`・`search`・`info` では見積もりをその精度に換算する。`download` では落とす
    ワークフロー(ファイル)を選ぶ。
  - `--content`: 一覧を出すコマンドでは表示を絞る。`download` では名前で探すモデルを決め、`uncensored` / `all` は
    無検閲のモデルを落とす同意にもなる。`--uncensored` は `--content all` と同じで、`--content sfw` か
    `--content uncensored` と一緒に使うと使い方の誤り(終了コード 2)。
  - `--all`: `fit` / `search` では全部を出す。`diff` では保守用の内容を足す。`review list` では確認済みも出す。
    `share` では送ったことにした実測も送り直す、`contrib share` では一覧に出す。
  - `--limit` / `-n`: `recommend` / `fit` / `search` では行の数、`diff` では節ごとの行の数、`measure` では出す提案の数。
  - `--output` は `update`・`bench`・`contrib` で、`--force` は `update`・`bench` で、それぞれ意味が違う(各コマンドを見る)。
- `--json` を付けると、どのコマンドもプログラム向けの JSON を出します。形は [schema.ja.md](schema.ja.md) にあります。

## 全体のオプション

| オプション | 値 | 説明 |
|---|---|---|
| `--version` |  | 版を出して終わる |
| `--lang` | `{en,ja}` | 表示の言語(既定は環境変数 GENMODELFIT_LANG、`genmodelfit config` の lang、無ければ en) |

## 共通のオプション

次のオプションは、使えるどのコマンドでも同じ意味です。コマンドの節には、そのコマンドだけのオプションを載せ、
ここへリンクします。

| オプション | 値 | 既定 | 説明 | コマンド |
|---|---|---|---|---|
| `--json` |  |  | JSON で出す | すべてのコマンド |
| `--vram` | `GB` | この PC から調べる | GPU の VRAM を GB(GiB)で決める(例: 8。0 なら GPU なしとして判定)。別の PC として判定するときに | `system`・`recommend`・`fit`・`search`・`info`・`download` |
| `--ram` | `GB` | この PC から調べる | メインメモリを GB(GiB)で決める(例: 32)。別の PC として判定するときに | `system`・`recommend`・`fit`・`search`・`info`・`download` |
| `--db` | `PATH` | update で取った一覧、無ければ同梱の一覧 | この一覧のファイル(db.json)を使う | `system`・`recommend`・`fit`・`search`・`types`・`info`・`download`・`measure`・`gate`・`bench` |
| `--no-cache` |  |  | update で取った一覧を使わず、同梱のデータを使う | `system`・`recommend`・`fit`・`search`・`types`・`info`・`download`・`measure`・`share`・`gate`・`review list`・`bench` |
| `--license-allowlist` | `LIST\|FILE` | [gate.ja.md](gate.ja.md) の許可リスト | 自動のおすすめに許すライセンス(「mit,apache-2.0」か、1 行に 1 つ書いたファイル)。既定は [gate.ja.md](gate.ja.md)。空の文字("")は指定なしと同じ | `system`・`recommend`・`fit`・`search`・`info`・`update`・`download`・`gate` |
| `--comfyui-version` | `VER` | 一覧が記録した最新のリリース | 使う ComfyUI の版。アプリは自分の版を必ず渡す(既定は update が記録した最新のリリース) | `system`・`recommend`・`fit`・`search`・`info`・`update`・`download`・`gate` |
| `--max-head-age-days` | `DAYS` | `30` | HEAD の確認をこの日数まで信じる(既定 30。none で期限なし) | `system`・`recommend`・`fit`・`search`・`info`・`update`・`download`・`gate` |
| `--allow-uncensored-auto` |  |  | 無検閲のモデルも自動のおすすめ(Verified)に出してよい(表示の絞り込み・download の同意とは別) | `system`・`recommend`・`fit`・`search`・`info`・`update`・`download`・`gate` |
| `--modality`, `-m` | `TYPE[,TYPE]` |  | 種類で絞る(カンマ区切り可): image, video, music, sfx, tts, 3d | `recommend`・`fit`・`search` |
| `--content` | `{all,sfw,uncensored}` | `sfw` | 内容の印で絞る: sfw(既定)/ uncensored(無検閲だけ)/ all(両方。`--uncensored` と同じ)。`--uncensored` と一緒に使えるのは all だけ。略さずに書く | `recommend`・`fit`・`search`・`info` |
| `--uncensored` |  |  | 無検閲のモデルも含める(`--content all` と同じ) | `recommend`・`fit`・`search`・`types`・`info` |
| `--width` | `PX` | 種類ごと: image 1024、video 832 | 画像・動画の幅(ピクセル)で判定する。省いた設定は種類ごとの既定 | `recommend`・`fit`・`search`・`info` |
| `--height` | `PX` | 種類ごと: image 1024、video 480 | 画像・動画の高さ(ピクセル)で判定する | `recommend`・`fit`・`search`・`info` |
| `--frames` | `N` | 種類ごと: video 81 | 動画のコマ数で判定する | `recommend`・`fit`・`search`・`info` |
| `--length` | `SEC` | 種類ごと: music 30 秒、sfx 10 秒、tts 10 秒 | 音楽・効果音・読み上げの長さ(秒)で判定する | `recommend`・`fit`・`search`・`info` |
| `--precision` | `PREC` | 一覧にある精度のまま | 見積もりで本体の重みをこの精度に換算する(fp32・bf16・fp16・fp8・int8・q8・q6・q5・nf4・q4。元の精度が分かるときだけ)。download の `--precision`(落とすワークフローを選ぶ)とは別 | `recommend`・`fit`・`search`・`info` |
| `--limits` |  |  | この PC の GPU で逃がさずに good になる最大の解像度・コマ数・長さも出す(JSON の limits) | `recommend`・`fit`・`search` |
| `--license` | `LIST` |  | すべてのファイルのライセンスがこの中にあるものだけ(例: apache-2.0,mit。不明は合わない) | `recommend`・`fit`・`search` |
| `--any-license`, `--include-unknown-license` |  |  | ライセンスの分からないファイルを含むモデルも出す(既定は出さない。info `<ID>` では常に出る。前からの書き方 `--include-unknown-license` も同じ) | `recommend`・`fit`・`search` |
| `--task` | `USE[,USE]` |  | 用途で絞る(gen / edit / i2v / lipsync / motion / voice / upscale / tool、または t2i・i2i・inpaint などの tasks。一覧は `genmodelfit types`) | `recommend`・`fit`・`search` |
| `--all-workflows` |  |  | 同じモデル(精度・使い方だけ違うもの)をまとめず、ワークフローごとに 1 行ずつ出す | `recommend`・`fit`・`search` |

## この PC を判定するコマンドの前提

`system`・`recommend`・`fit`・`search`・`info` の前提は次のとおりです。

- 用意するものは無い。
- GPU とメモリは調べて決める(`--vram` / `--ram` で置き換える)。
- GPU は NVIDIA なら nvidia-smi、ほかの AMD / Intel / Apple は WMI(Windows)・lspci と sysfs(Linux)・system_profiler(macOS)で調べる。
- 一覧は同梱のものを使う(`genmodelfit update` で新しい一覧を取ってあればそちら。`--no-cache` で同梱)。

## `genmodelfit` が見つからないとき

- pipx と uv tool は、1 回だけ `pipx ensurepath`・`uv tool update-shell` を実行すると `genmodelfit` を PATH に
  通します。そのあと端末を開き直します。
- `pip install` で入れたなら、入れたのと同じ Python で `python -m genmodelfit …` を実行します(Windows では
  `py -m genmodelfit …` も可)。pipx と uv tool は専用の環境に入れるので、この方法では動きません。
- pip が *「WARNING: The script genmodelfit.exe is installed in '…\Scripts' which is not on PATH」* と出すことが
  あります。python.org の Python を「Add python.exe to PATH」なしで入れた、Microsoft Store の Python、ユーザー単位で
  入れた(`--user`)、仮想環境を有効にしていない、のどれかでよく出ます。その場所を PATH に足すか、
  `python -m genmodelfit` を使います。
- PATH を変える前に開いた端末には反映されないので、新しい端末を開きます。

## 表の列

`recommend`・`fit`・`search` の表の列:

| 列 | 意味 |
|---|---|
| `Type` | image・video・music・sfx・tts・3d |
| `Status` | 段階([用語](#用語)) |
| `Score` | 0〜100。メモリの余裕。`Mem%` が 70% 以下なら 100、超えると下がる。速さ・品質・人気は入れない([scoring.ja.md](scoring.ja.md#score)) |
| `Censored` | `Y` 検閲あり / `N` 無検閲。`--uncensored`(か `--content all` / `uncensored`)のときだけ出る |
| `Model` | 同じ重みを 1 行にまとめたモデル。長い名前は真ん中を `…` で詰める |
| `Use` | `gen`・`edit`・`i2v` など(`genmodelfit types`)。行自身の用途(`--task` で合ったもの)を先頭に、そのモデルのほかのワークフローの用途を重複なく並べる(`gen,edit`。収まらなければ `gen,…`)。ワークフローの一覧は `info`、別々の行は `--all-workflows` |
| `Prec` | 載る中で一番良い精度(fp16・bf16・fp8・int8 など) |
| `VRAM` | その動かし方で要る GiB。`m` = 実測(メンテナ・この PC の実測、または同じ GPU のほかの利用者の実測の中央値。出どころの数は `info`・JSON の `sample_count`) |
| `Mem%` | 一番きついメモリの使用率。GPU なら VRAM、CPU ならメモリ、`GPU+RAM` は大きいほう。`RAM` = PC のメモリが足りない |
| `Mode` | `GPU` 全部 GPU、`GPU~` 部品を順に載せ替え、`GPU+RAM` メモリへ逃がす(遅い)、`CPU`(遅い)、`-` 動かない(`GPU req` は使える GPU が無いので動かない、`RAM` はメモリ不足) |
| `Disk` | ダウンロードの大きさ(GiB) |
| `License` | ファイルのライセンス(`other` = 自分で原文を読む) |

`?` = 分からない(精度・ライセンス・大きさ。判定に使っていない)。`-` = 当てはまらない(動かない・CPU なので VRAM が
無い など)。端末が狭いときに外す列の順は、[表示の言語](#表示の言語)にあります。

## 表示の言語

出力の既定は英語です。日本語にするには、次のどれかを使います。

- どのコマンドでも `--lang ja` を付ける(例 `genmodelfit recommend --lang ja`)
- 環境変数 `GENMODELFIT_LANG=ja` を置く
- `genmodelfit config set lang ja` で保存する(コマンドラインだけ)
- Python で `gmf.set_language("ja")` を呼ぶ

優先は `--lang`、環境変数、保存した設定(コマンドライン)か `set_language`(Python)の順です。OS の言語は見ません。
変わるのは文だけです: 理由・注意・エラー・表の見出しと説明・`--help`。JSON のキー・ID・コード・判定は、どちらの
言語でも同じです。英語の出力には日本語が出ません。日本語の出力には英語だけの文が出ません(1 行に 2 つの言語の文を
並べない)。

どちらの言語でも英語のまま出すもの: 表の列の名前(`Type`・`Status`・`Score`・`Model`・`Use`・`Prec`・`VRAM`・`Mem%`・
`Mode`・`Disk`・`License`・`Censored`)、段階の名前(`Perfect`・`Good`・`Marginal`・`Too tight`)、動かし方の印
(`GPU`・`GPU~`・`GPU+RAM`・`CPU`)、単位、ID・ライセンスの ID・オプションの名前など機械が読む値。表の幅をそろえ、
`--fit`・`--sort` などに書く名前と同じにするためです。日本語の出力では、表の下の説明がこれらを日本語で説明します。
種類の見出しは `== 画像 (image): …` のように、`-m` に書く名前も出します。

長い行は、全角を 2 桁で数えて折り返します。ファイル名・URL・オプション・単位つきの数の途中では切りません(1 つで
幅より長ければ、その行だけはみ出す)。行の頭に「、。)」を、行の終わりに「(「」を置きません。端末が狭いときは、
表の列を `Disk`・`Mem%`・`License`・`Prec`・`Use` の順に外します(外した列は `info` か `--json` で見る)。

## 環境変数

| 変数 | 使うところ | 内容 |
|---|---|---|
| `GENMODELFIT_LANG` | すべてのコマンド | 表示の言語。`en` か `ja`。`ja_JP.UTF-8` のような書き方も受ける。これに勝つのは `--lang` だけ。 |
| `GENMODELFIT_COMFYUI_DIR` | `download` | ファイルを置く ComfyUI のフォルダ。`--comfyui-dir` / `--output-dir` には負け、`genmodelfit config` と自動で探すのには勝つ。 |
| `GENMODELFIT_COMFYUI_SEARCH` | `download`・`measure`・`share` | `0`・`off`・`no`・`false` で、ComfyUI を自動で探すのを止める(アプリ・スクリプト・試験用)。 |
| `GENMODELFIT_COMFYUI_START` | `measure`・`share` | `0`・`off`・`no`・`false` で、起動中の ComfyUI が無いときに見つけた ComfyUI を起動しない(`--no-start` と同じ)。 |
| `GENMODELFIT_CONFIG_DIR` | `config` と、保存した設定を読むすべてのコマンド | 設定ファイル `config.json` のフォルダ(既定は[ファイルの置き場所](#ファイルの置き場所))。 |
| `GENMODELFIT_CACHE_DIR` | `update`・`measure`・`share`・`bench`・`contrib` と、キャッシュした一覧を読むすべてのコマンド | キャッシュのフォルダ(既定は[ファイルの置き場所](#ファイルの置き場所))。 |
| `GENMODELFIT_SIGNING_KEY` | `python -m genmodelfit.signing sign` だけ(`genmodelfit` コマンドでは使わない) | データのリリースに署名する秘密鍵(PEM)。保守する人向け([release.ja.md](release.ja.md))。 |
| `GITHUB_TOKEN`・`GH_TOKEN` | `share` | PR を送るのにそのまま使う GitHub のトークン(scope `public_repo` が要る)。保存したログインとデバイス認証に勝つ。 |
| `GENMODELFIT_GH_CLIENT_ID` | `share` | デバイス認証に使う GitHub の OAuth アプリの client id。空にするとデバイス認証を使わない(`GITHUB_TOKEN`・`GH_TOKEN` だけ)。 |

通信は、プロキシと証明書の環境変数(`HTTPS_PROXY`・`REQUESTS_CA_BUNDLE` など)にも従います。`~/.netrc` の資格情報は送りません。

## 保存できる設定(`genmodelfit config`)

`genmodelfit config set キー 値` は、設定のフォルダの `config.json` に設定を保存します。読むのはコマンドラインだけで、
Python の関数は読みません。キーは `comfyui-dir`(フォルダのパス)と `lang`(`en` か `ja`)です。
同じ設定を決める場所が複数あるときは、次の上のものが勝ちます。

1. コマンドラインのオプション(`--lang`・`--comfyui-dir`・`--output-dir`)
2. 環境変数(`GENMODELFIT_LANG`・`GENMODELFIT_COMFYUI_DIR`)
3. 保存した設定(`genmodelfit config`)
4. 既定(`lang` は `en`。ComfyUI のフォルダは、ちょうど 1 つ見つかったときだけ自動で使う)

`genmodelfit config` は、各設定とどこから来た値かを出します。詳しくは [download.ja.md](download.ja.md)。

## ファイルの置き場所

| | Windows | macOS | Linux |
|---|---|---|---|
| 設定のフォルダ(`config.json`) | `%LOCALAPPDATA%\genmodelfit` | `~/Library/Application Support/genmodelfit` | `~/.config/genmodelfit`(か `$XDG_CONFIG_HOME/genmodelfit`) |
| キャッシュのフォルダ | `%LOCALAPPDATA%\genmodelfit\Cache` | `~/Library/Caches/genmodelfit` | `~/.cache/genmodelfit`(か `$XDG_CACHE_HOME/genmodelfit`) |

この PC の設定ファイルの場所は `genmodelfit config path` で分かります。`GENMODELFIT_CONFIG_DIR`・`GENMODELFIT_CACHE_DIR`
でフォルダを変えられます。設定のフォルダには `share` の GitHub のログイン(`github_token`。本人だけが読める。
`genmodelfit share --logout` で消す)も入ります。キャッシュには、`update` で取った一覧(`db.json`)、HEAD とファイルの見出しの確認の結果、
`measure`・`bench` の結果(`measurements/`。この PC の判定にも使う)、提出の下書きと失敗の記録(`submissions/`。`share` が送るもの)、
`share` で送った印(`submissions_shared.json`)、`measure`・`share` が起動した ComfyUI の出力(`comfyui-<日付>.log`)、
測るあいだの `measure.lock`(同時に測るのは 1 つの `measure` か `share` だけ)が入ります。
キャッシュのフォルダは消してもかまいません(同梱の一覧に戻ります。保存した結果も消えます)。

## 終了コード

| コード | 意味 |
|---|---|
| 0 | 成功。引数なしの `genmodelfit` は使い方を出して 0 で終わります。 |
| 1 | 失敗: モデルが無い・断った・聞かれて断った・通信の誤り・読めないファイル・検査に通らなかった、など。何が失敗かは、各コマンドの節に書いてあります。 |
| 2 | 使い方の誤り: 知らないオプション・正しくない値・足りない引数・略した同意のオプション・同時に使えないオプション。 |
| 3 | `contrib aggregate --report --fail-on-breach` だけ: 誤差が閾値を超えた。 |
| 130 | Ctrl+C で止めた。`download` はどの OS でも 130 を返し、途中まで落とした `.part` のファイルを残します。同じコマンドをもう一度実行すれば続きから落とします。ほかのコマンドは Python の決まりどおりに止まります: Linux・macOS では 130、Windows では 3221225786(0xC000013A。PowerShell の `$LASTEXITCODE` では -1073741510)。`bench`・`measure` は送った仕事を取り消して(genmodelfit が起動した ComfyUI も止めて)から 1 で終わります。`share` は測るのをやめ、そこまでのものを送るかに進んでから 130 で終わります。`measure`・`share` の質問で Ctrl+C を押すと 130 です。 |

`--json` のときも失敗は 1 で終わり、文は JSON の `error` にあります。

## コマンド

- [`genmodelfit system`](#genmodelfit-system) — この PC の GPU・メモリ・空きと、一覧の新しさを出す
- [`genmodelfit recommend`](#genmodelfit-recommend) — おすすめ順に出す
- [`genmodelfit fit`](#genmodelfit-fit) — 動くモデルの判定を表で出す(`--all` で全部)
- [`genmodelfit search`](#genmodelfit-search) — ID・名前の一部で探して、この PC での判定を出す
- [`genmodelfit types`](#genmodelfit-types) — 大きな種類と、その中の用途(`--task` の名前)と数
- [`genmodelfit info`](#genmodelfit-info) — 1 つのモデルの詳細
- [`genmodelfit update`](#genmodelfit-update) — 新しい一覧を取る(利用者は `--from-release`。オプションなしは上流から作り直す)
- [`genmodelfit download`](#genmodelfit-download) — モデルのファイルを落とす(同意・sha256・続きから・ComfyUI のフォルダ)
- [`genmodelfit measure`](#genmodelfit-measure) — 測ると役に立つモデルを選んで、ComfyUI で実測する(無ければ起動する。何も送らない)
- [`genmodelfit share`](#genmodelfit-share) — 入っているモデルを数分測り、送っていない実測といっしょに GitHub の PR で送る(`--issue` は Issue のフォームで送る)
- [`genmodelfit config`](#genmodelfit-config) — 設定(download の ComfyUI のフォルダ・表示の言語)を見る・変える
  - [`genmodelfit config show`](#genmodelfit-config-show) — 今の設定と出どころ(サブコマンドを省いたときはこれ)
  - [`genmodelfit config set`](#genmodelfit-config-set) — 値を保存する
  - [`genmodelfit config unset`](#genmodelfit-config-unset) — 値を消す
  - [`genmodelfit config path`](#genmodelfit-config-path) — 設定ファイルの場所
- [`genmodelfit gate`](#genmodelfit-gate) — 自動でおすすめに出せるかの集計
- [`genmodelfit diff`](#genmodelfit-diff) — 2 つの一覧(db.json)の違い(増減・ライセンス・自動のおすすめ・HEAD の失敗)
- [`genmodelfit review`](#genmodelfit-review) — 確認待ち(新しい項目・ライセンスの変化など)の一覧・確かめる・外す(保守する人向け)
  - [`genmodelfit review list`](#genmodelfit-review-list) — 確認待ちの項目と理由
  - [`genmodelfit review approve`](#genmodelfit-review-approve) — 根拠(一次資料の URL)を付けて確認済みにする(一覧のファイルを書き換える)
  - [`genmodelfit review reject`](#genmodelfit-review-reject) — 一覧から外し、update でも入れないように記録する
- [`genmodelfit bench`](#genmodelfit-bench) — 保守する人向け: 動いている ComfyUI で実測する(ふつうは measure)
- [`genmodelfit contrib`](#genmodelfit-contrib) — 保守する人向け: 実測の提出を作る・検査する・まとめる(送るのは measure と share)
  - [`genmodelfit contrib make`](#genmodelfit-contrib-make) — bench が保存した結果から提出用の JSON を作る
  - [`genmodelfit contrib show`](#genmodelfit-contrib-show) — 送る中身と個人の情報の点検を見せる
  - [`genmodelfit contrib share`](#genmodelfit-contrib-share) — `genmodelfit share --issue` と同じ(前からのスクリプトのため)
  - [`genmodelfit contrib validate`](#genmodelfit-contrib-validate) — 形・値・個人の情報・重複を検査する(CI でも使う)
  - [`genmodelfit contrib intake`](#genmodelfit-contrib-intake) — GitHub の実測の Issue を検査し、PR に入れるファイルを作る(measurement-intake.yml が使う)
  - [`genmodelfit contrib aggregate`](#genmodelfit-contrib-aggregate) — 同じモデル・設定・GPU ごとにまとめ、精度を点検する

## `genmodelfit system`

この PC の GPU・メモリ・空きと、一覧の新しさを出す

**使い方**

```text
genmodelfit system [-h] [--lang {en,ja}] [--json] [--vram GB] [--ram GB] [--db PATH] [--no-cache]
                   [--license-allowlist LIST|FILE] [--comfyui-version VER]
                   [--max-head-age-days DAYS] [--allow-uncensored-auto] [--disk-path PATH]
                   [--all-gpus]
```

この PC(GPU・メモリ・ディスクの空き)と、使っている一覧がまだ新しいか(一覧の欄: 出どころ・HEAD の確認の日付・期限切れか・いつまで有効か・自動のおすすめの数)を出す。

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `--disk-path` | `PATH` | ホームのフォルダ | ディスクの空きを調べる場所(フォルダ) |
| `--all-gpus` |  |  | NVIDIA 以外も含めて画面の部品(GPU)をすべて出す(Windows は約 1 秒かかる。判定には使わない) |

- [共通のオプション](#共通のオプション)も使えます: `--json`・`--vram`・`--ram`・`--db`・`--no-cache`・`--license-allowlist`・`--comfyui-version`・`--max-head-age-days`・`--allow-uncensored-auto`

**前提**

- [この PC を判定するほかのコマンドと同じ](#この-pc-を判定するコマンドの前提)

**副作用**

- なし(読むだけ。通信しない)。

**終了コード**

| コード | 意味 |
|---|---|
| 0 | 成功(一覧が期限切れのときも。一覧の欄・database.status.expired を見る。新しくするには `genmodelfit update --from-release`) |
| 1 | 失敗: 壊れた `--db` のファイルなど(`--json` なら誤りは "error" に入る) |
| 2 | 使い方の誤り(知らないオプション・値) |

**使い方の例(スクリプト・アプリ向け)**

```sh
genmodelfit system --json
genmodelfit system --comfyui-version 0.40.0 --json
```

JSON の出力(`--json`): [schema.ja.md「System / PC の情報」](schema.ja.md#system--pc-の情報)・[schema.ja.md「Data status / 一覧の新しさ」](schema.ja.md#data-status--一覧の新しさ)

## `genmodelfit recommend`

おすすめ順に出す

**使い方**

```text
genmodelfit recommend [-h] [--lang {en,ja}] [--json] [--vram GB] [--ram GB] [--db PATH] [--no-cache]
                      [--modality TYPE[,TYPE]] [--content {all,sfw,uncensored}] [--uncensored]
                      [--width PX] [--height PX] [--frames N] [--length SEC] [--precision PREC]
                      [--limit N] [--limits] [--min-fit {perfect,good,marginal,too_tight}]
                      [--license LIST] [--fit {perfect,good,marginal,runnable,all}] [--any-license]
                      [--task USE[,USE]] [--all-workflows] [--license-allowlist LIST|FILE]
                      [--comfyui-version VER] [--max-head-age-days DAYS] [--allow-uncensored-auto]
                      [--auto-only] [--include-tools]
```

この PC に合う生成モデルを、おすすめ順に出す。

- `-m` も `--task` も無ければ、種類(image, video, music, sfx, tts, 3d)ごとに上位 `-n` 件(既定 3 件)。
- `-m` か `--task` があれば 1 つの表(既定 10 件)。
- 同じモデルは 1 行にまとめ、一番合う精度とワークフローを選ぶ(`--all-workflows` で全部)。
- 並べ方: 動くもの → Perfect/Good → Marginal(速い動かし方が先)→ 公式のテンプレート → 人気(同じ種類・同じ出どころの中の順位)→ Score → ID(`--json` は自動でおすすめに出せるもの(gate: auto)を段階より先にする)。
- Score 0〜100 はメモリの余裕(使用率 70% までは 100。速さではない)。
- Score の決まりは [scoring.ja.md](scoring.ja.md)。
- 無検閲のモデルと補助ツールは、指定しなければ出さない。
- 出力は表。
- アプリは `--json` を付ける。

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `--limit`, `-n` | `N` | 種類ごとに 3(`-m`・`--task` があれば 10) | 出す行の数(1 以上) |
| `--min-fit` | `{perfect,good,marginal,too_tight}` | `marginal` | この段階以上だけ出す(recommend の既定は marginal、fit / search は全部) |
| `--fit` | `{perfect,good,marginal,runnable,all}` |  | 出す段階: perfect / good / marginal(その段階だけ)、runnable(動くもの全部。fit の既定)、all(too_tight・判定できないものも)。`--min-fit` とは同時に使えない |
| `--auto-only` |  |  | 自動でおすすめに出せるものだけ |
| `--include-tools` |  |  | 深度・ポーズ・切り抜き・拡大などの補助ツールも含める(既定は「つくる」モデルだけ) |

- [共通のオプション](#共通のオプション)も使えます: `--json`・`--vram`・`--ram`・`--db`・`--no-cache`・`--modality`・`--content`・`--uncensored`・`--width`・`--height`・`--frames`・`--length`・`--precision`・`--limits`・`--license`・`--any-license`・`--task`・`--all-workflows`・`--license-allowlist`・`--comfyui-version`・`--max-head-age-days`・`--allow-uncensored-auto`

**前提**

- [この PC を判定するほかのコマンドと同じ](#この-pc-を判定するコマンドの前提)

**副作用**

- なし(読むだけ。通信しない)。

**終了コード**

| コード | 意味 |
|---|---|
| 0 | 成功 |
| 1 | 失敗: 壊れた `--db` のファイルなど(`--json` なら誤りは "error" に入る) |
| 2 | 使い方の誤り(知らないオプション・値) |

**使い方の例(スクリプト・アプリ向け)**

```sh
genmodelfit recommend --json
genmodelfit recommend -m image --task edit -n 5 --min-fit good --json
genmodelfit recommend -m video,music --auto-only --comfyui-version 0.40.0 --json
genmodelfit recommend --license apache-2.0,mit --vram 8 --ram 32 --json
```

JSON の出力(`--json`): [schema.ja.md「recommend / fit / search」](schema.ja.md#recommend--fit--search)

1 項目に 1 モデル(同じ重みの系統 + 大きさ)。`--all-workflows` ならワークフローごとに 1 項目。

**例**

```sh
genmodelfit recommend                            # 種類ごとのおすすめ(各 3 件)
genmodelfit recommend -m image --task edit -n 5  # 画像の編集の上位 5 件
genmodelfit recommend --uncensored               # 無検閲のモデルも含める(既定は sfw)
genmodelfit recommend --vram 12 --ram 32         # 別の PC として判定する
genmodelfit recommend -m video --json            # アプリ向けの JSON
```

## `genmodelfit fit`

動くモデルの判定を表で出す(`--all` で全部)

**使い方**

```text
genmodelfit fit [-h] [--lang {en,ja}] [--json] [--vram GB] [--ram GB] [--db PATH] [--no-cache]
                [--modality TYPE[,TYPE]] [--content {all,sfw,uncensored}] [--uncensored]
                [--width PX] [--height PX] [--frames N] [--length SEC] [--precision PREC]
                [--limit N] [--limits] [--min-fit {perfect,good,marginal,too_tight}]
                [--license LIST] [--fit {perfect,good,marginal,runnable,all}] [--any-license]
                [--task USE[,USE]] [--all-workflows] [--license-allowlist LIST|FILE]
                [--comfyui-version VER] [--max-head-age-days DAYS] [--allow-uncensored-auto]
                [--sort {type,score,vram,disk,name}] [--all] [--perfect] [--include-tools]
```

- この PC でモデルを判定し、1 つの表にする(recommend のように上位だけに絞らない)。
- 既定は動くもの(`--fit runnable`: perfect・good・marginal)だけで、補助ツールとライセンスの分からないファイルを含むモデルは出さない。
- `--all` で too_tight・判定できないもの・補助ツール・ライセンス不明も出す。
- 無検閲のモデルは `--uncensored` で出す(`--all` だけでは出ない)。
- 並べ方: 種類 → 段階 → 公式のテンプレート → 人気 → Score(`--sort` で変える)。

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `--limit`, `-n` | `N` | 全部 | 出す行の数(1 以上) |
| `--min-fit` | `{perfect,good,marginal,too_tight}` |  | この段階以上だけ出す(recommend の既定は marginal、fit / search は全部) |
| `--fit` | `{perfect,good,marginal,runnable,all}` | runnable(`--all` なら all) | 出す段階: perfect / good / marginal(その段階だけ)、runnable(動くもの全部。fit の既定)、all(too_tight・判定できないものも)。`--min-fit` とは同時に使えない |
| `--sort` | `{type,score,vram,disk,name}` | `type` | 並べ方: type(既定。種類 → 段階 → 公式 → 人気 → Score)、score(種類をまたいで段階 → Score)、vram / disk(少ない順)、name |
| `--all` |  |  | 全部出す(動かないもの・判定できないもの・補助ツール・ライセンス不明も。無検閲は `--uncensored`) |
| `--perfect`, `-p` |  |  | perfect だけ(`--fit perfect` と同じ) |
| `--include-tools` |  |  | 深度・ポーズ・切り抜き・拡大などの補助ツールも含める(既定は「つくる」モデルだけ) |

- [共通のオプション](#共通のオプション)も使えます: `--json`・`--vram`・`--ram`・`--db`・`--no-cache`・`--modality`・`--content`・`--uncensored`・`--width`・`--height`・`--frames`・`--length`・`--precision`・`--limits`・`--license`・`--any-license`・`--task`・`--all-workflows`・`--license-allowlist`・`--comfyui-version`・`--max-head-age-days`・`--allow-uncensored-auto`

**前提**

- [この PC を判定するほかのコマンドと同じ](#この-pc-を判定するコマンドの前提)

**副作用**

- なし(読むだけ。通信しない)。

**終了コード**

| コード | 意味 |
|---|---|
| 0 | 成功 |
| 1 | 失敗: 壊れた `--db` のファイルなど(`--json` なら誤りは "error" に入る) |
| 2 | 使い方の誤り(知らないオプション・値) |

**使い方の例(スクリプト・アプリ向け)**

```sh
genmodelfit fit --json
genmodelfit fit -m video --task i2v --min-fit good -n 20
genmodelfit fit --uncensored --all-workflows --vram 12 --ram 32 --json
```

JSON の出力(`--json`): [schema.ja.md「recommend / fit / search」](schema.ja.md#recommend--fit--search)

1 項目に 1 モデル(同じ重みの系統 + 大きさ)。`--all-workflows` ならワークフローごとに 1 項目。

**例**

```sh
genmodelfit fit                                         # この PC で動くモデルの全部
genmodelfit fit --sort score --fit good                 # good だけを種類をまたいだ順位で
genmodelfit fit --all                                   # 動かないもの・補助ツールなども全部
genmodelfit fit -m video --task i2v --vram 12 --ram 32  # 別の PC で画像から動画
genmodelfit fit --uncensored --json                     # 無検閲も含めて JSON で(既定は sfw)
```

## `genmodelfit search`

ID・名前の一部で探して、この PC での判定を出す

**使い方**

```text
genmodelfit search [-h] [--lang {en,ja}] [--json] [--vram GB] [--ram GB] [--db PATH] [--no-cache]
                   [--modality TYPE[,TYPE]] [--content {all,sfw,uncensored}] [--uncensored]
                   [--width PX] [--height PX] [--frames N] [--length SEC] [--precision PREC]
                   [--limit N] [--limits] [--min-fit {perfect,good,marginal,too_tight}]
                   [--license LIST] [--fit {perfect,good,marginal,runnable,all}] [--any-license]
                   [--task USE[,USE]] [--all-workflows] [--license-allowlist LIST|FILE]
                   [--comfyui-version VER] [--max-head-age-days DAYS] [--allow-uncensored-auto]
                   [--sort {type,score,vram,disk,name}] [--all] [--perfect] query
```

- ID・名前の一部で探し(大文字小文字は区別しない)、この PC での判定を出す。
- `--content` が効く(既定 sfw)。
- 合うものが無ければ空の一覧で、終了コードは 0。

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `--limit`, `-n` | `N` | 全部 | 出す行の数(1 以上) |
| `--min-fit` | `{perfect,good,marginal,too_tight}` |  | この段階以上だけ出す(recommend の既定は marginal、fit / search は全部) |
| `--fit` | `{perfect,good,marginal,runnable,all}` | すべての段階 | 出す段階: perfect / good / marginal(その段階だけ)、runnable(動くもの全部。fit の既定)、all(too_tight・判定できないものも)。`--min-fit` とは同時に使えない |
| `--sort` | `{type,score,vram,disk,name}` | `type` | 並べ方: type(既定。種類 → 段階 → 公式 → 人気 → Score)、score(種類をまたいで段階 → Score)、vram / disk(少ない順)、name |
| `--all` |  |  | 全部出す(動かないもの・判定できないもの・補助ツール・ライセンス不明も。無検閲は `--uncensored`) |
| `--perfect`, `-p` |  |  | perfect だけ(`--fit perfect` と同じ) |
| `query` |  |  | 探す語(ID・名前の一部。大文字小文字は区別しない) |

- [共通のオプション](#共通のオプション)も使えます: `--json`・`--vram`・`--ram`・`--db`・`--no-cache`・`--modality`・`--content`・`--uncensored`・`--width`・`--height`・`--frames`・`--length`・`--precision`・`--limits`・`--license`・`--any-license`・`--task`・`--all-workflows`・`--license-allowlist`・`--comfyui-version`・`--max-head-age-days`・`--allow-uncensored-auto`

**前提**

- [この PC を判定するほかのコマンドと同じ](#この-pc-を判定するコマンドの前提)

**副作用**

- なし(読むだけ。通信しない)。

**終了コード**

| コード | 意味 |
|---|---|
| 0 | 成功(合うものが無いときも。models は空、表では「(該当なし)」) |
| 1 | 失敗: 探す語が空、壊れた `--db` のファイルなど(`--json` なら誤りは "error" に入る) |
| 2 | 使い方の誤り(知らないオプション・値) |

**使い方の例(スクリプト・アプリ向け)**

```sh
genmodelfit search flux --json
genmodelfit search wan -m video --min-fit good
genmodelfit search sdxl --uncensored --json
```

JSON の出力(`--json`): [schema.ja.md「recommend / fit / search」](schema.ja.md#recommend--fit--search)

1 項目に 1 モデル(同じ重みの系統 + 大きさ)。`--all-workflows` ならワークフローごとに 1 項目。

**例**

```sh
genmodelfit search flux                         # 名前に flux を含むモデル
genmodelfit search wan -m video --min-fit good  # good 以上の動画だけ
```

## `genmodelfit types`

大きな種類と、その中の用途(`--task` の名前)と数

**使い方**

```text
genmodelfit types [-h] [--lang {en,ja}] [--json] [--db PATH] [--no-cache]
                  [--content {all,sfw,uncensored}] [--uncensored]
```

大きな種類(image, video, music, sfx, tts, 3d)と、その中の用途と数を出す。用途の名前は `--task` に使う。

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `--content` | `{all,sfw,uncensored}` | `sfw` | 数えるモデルの内容の印: sfw(既定)/ uncensored(無検閲だけ)/ all(両方。`--uncensored` と同じ)。略さずに書く |

- [共通のオプション](#共通のオプション)も使えます: `--json`・`--db`・`--no-cache`・`--uncensored`

**前提**

- 用意するものは無い。
- PC は判定しない。
- 一覧は同梱のものを使う(`genmodelfit update` で新しい一覧を取ってあればそちら。`--no-cache` で同梱)。

**副作用**

- なし(読むだけ。通信しない)。

**終了コード**

| コード | 意味 |
|---|---|
| 0 | 成功 |
| 1 | 失敗: 壊れた `--db` のファイルなど(`--json` なら誤りは "error" に入る) |
| 2 | 使い方の誤り(知らないオプション・値) |

**使い方の例(スクリプト・アプリ向け)**

```sh
genmodelfit types
genmodelfit types --json
```

JSON の出力(`--json`): [schema.ja.md「Uses / 用途」](schema.ja.md#uses--用途)

`--vram`・`--ram` も受け付けますが、このコマンドでは何もしません(前からのスクリプトのため)。

## `genmodelfit info`

1 つのモデルの詳細

**使い方**

```text
genmodelfit info [-h] [--lang {en,ja}] [--json] [--vram GB] [--ram GB] [--db PATH] [--no-cache]
                 [--content {all,sfw,uncensored}] [--uncensored] [--width PX] [--height PX]
                 [--frames N] [--length SEC] [--precision PREC] [--license-allowlist LIST|FILE]
                 [--comfyui-version VER] [--max-head-age-days DAYS] [--allow-uncensored-auto] model
```

- 1 つのモデルの詳細(ファイル・ライセンス・見積もり・この PC での限界・自動のおすすめの条件・同じモデルのほかのワークフロー)を出す。
- ID がそのまま合えば必ずそのモデル(無検閲も)。
- ID がそのまま合わなければ、ID・名前の一部を `--content` の中で探す。
- 2 件以上合えば失敗にして候補を並べる。

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `model` |  |  | モデルの名前(表の Model)・ワークフローの ID・そのどちらかの一部 |

- [共通のオプション](#共通のオプション)も使えます: `--json`・`--vram`・`--ram`・`--db`・`--no-cache`・`--content`・`--uncensored`・`--width`・`--height`・`--frames`・`--length`・`--precision`・`--license-allowlist`・`--comfyui-version`・`--max-head-age-days`・`--allow-uncensored-auto`

**前提**

- [この PC を判定するほかのコマンドと同じ](#この-pc-を判定するコマンドの前提)

**副作用**

- なし(読むだけ。通信しない)。

**終了コード**

| コード | 意味 |
|---|---|
| 0 | 成功 |
| 1 | 失敗: モデルが無い、2 件以上合う(候補を並べる)、壊れた `--db` のファイルなど(`--json` なら誤りは "error" に入る) |
| 2 | 使い方の誤り(知らないオプション・値) |

**使い方の例(スクリプト・アプリ向け)**

```sh
genmodelfit info image_z_image_turbo_int8 --json
genmodelfit info sdxlturbo --vram 8 --ram 32
```

JSON の出力(`--json`): [schema.ja.md「info」](schema.ja.md#info)

失敗のときは { error, candidates? }。

**例**

```sh
genmodelfit info "Z-Image-Turbo"                  # モデルの名前で
genmodelfit info image_z_image_turbo_int8 --json  # ワークフローの ID で、JSON
```

`--modality`・`--limit`・`--limits` も受け付けますが、このコマンドでは何もしません(前からのスクリプトのため)。

## `genmodelfit update`

新しい一覧を取る(利用者は `--from-release`。オプションなしは上流から作り直す)

**使い方**

```text
genmodelfit update [-h] [--lang {en,ja}] [--json] [--db PATH] [--no-cache]
                   [--license-allowlist LIST|FILE] [--comfyui-version VER]
                   [--max-head-age-days DAYS] [--allow-uncensored-auto] [--offline-sources DIR]
                   [--skip-hf] [--skip-head] [--heads-only] [--fields-only] [--inspect-only]
                   [--offline] [--output PATH] [--from-release] [--force] [--allow-partial]
                   [--rate-limit-budget SEC]
```

- 新しい一覧を取る。
- 利用者は `genmodelfit update --from-release` を実行する。
- `--from-release` は、このプロジェクトが配る署名つきの最新の一覧を取り込み(落とすのは db.json と db.json.sig だけ)、署名を確かめる。
- 取り込んだ後は、ほかのコマンドも同梱の一覧の代わりにこれを使う(`--no-cache` で同梱に戻る)。
- オプションなしの update は、一覧を上流(ComfyUI のテンプレート・Hugging Face Hub・すべてのダウンロードの URL への HEAD)から作り直す。
- 作り直しは保守する人向けで、時間がかかる(問い合わせが多く、Hugging Face の上限で待つ。`--rate-limit-budget` を見る)。
- 保守する人は、HEAD の確認だけ(`--heads-only`)、アプリ向けの項目と手で足したモデルだけ(`--fields-only`)、ファイルの見出しだけ(`--inspect-only`)をやり直すこともできる。
- 方針のオプション(`--comfyui-version`・`--max-head-age-days` など)が変えるのは、最後に出す自動のおすすめの数だけ。
- `--db` / `--no-cache` は、`--heads-only`・`--fields-only`・`--inspect-only` が元にする一覧を選ぶ。

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `--db` | `PATH` | update で取った一覧、無ければ同梱の一覧 | 元にする一覧のファイル(db.json)(`--heads-only`・`--fields-only`・`--inspect-only` のときだけ使う) |
| `--no-cache` |  |  | update で取った一覧ではなく、同梱の一覧を元にする(`--heads-only`・`--fields-only`・`--inspect-only` のときだけ使う) |
| `--offline-sources` | `DIR` |  | (試験用)上流の代わりに使うファイル |
| `--skip-hf` |  |  | Hugging Face を見ない |
| `--skip-head` |  |  | URL の HEAD 確認を飛ばす |
| `--heads-only` |  |  | 今の一覧はそのままで、URL の HEAD と ComfyUI の最新の版の確認だけをやり直す |
| `--fields-only` |  |  | 今の一覧はそのままで、アプリ向けの項目(言語・動かし方・説明)と手のモデル(data/manual.json)だけを当て直す。通信するのは、言語を読むカードと手のモデルのファイルだけ(`--offline` で通信せず、保存済みのカードの言語を使う) |
| `--inspect-only` |  |  | 今の一覧はそのままで、本体のファイルの見出し(精度)・テンプレートのサンプラーの組と読み込みノードの精度・リポジトリの精度(Hub API・config.json)だけを読み直し、401 だったリポジトリを確かめ直す(Range 要求。重み本体は落とさない。読んだ結果はキャッシュに残る) |
| `--offline` |  |  | `--inspect-only` / `--fields-only` で通信せず、キャッシュにある見出し・リポジトリの精度だけを使う |
| `--output` | `PATH` | キャッシュの db.json | 一覧の書き出し先(既定はキャッシュの db.json) |
| `--from-release` |  |  | 配布された署名つきの一覧を取り込む(署名が合わなければ何も変えない)。`--output` があればそこへ書く |
| `--force` |  |  | (`--from-release`)前に取り込んだ一覧より古くても取り込む |
| `--allow-partial` |  |  | 一部(テンプレート・Hugging Face)を取得できなくても書き出す(sources.partial が true になる) |
| `--rate-limit-budget` | `SEC` | `900` | Hugging Face の上限(429)で待つ時間の合計の上限(秒。既定 900)。超えたら止める |

- [共通のオプション](#共通のオプション)も使えます: `--json`・`--license-allowlist`・`--comfyui-version`・`--max-head-age-days`・`--allow-uncensored-auto`

**前提**

- 通信できること。

**副作用**

- 通信: GET / HEAD だけ。
- update は ComfyUI のワークフローのテンプレートとリリース(GitHub)、Hugging Face Hub の API、モデルのダウンロードの URL(HEAD だけ。重みは落とさない)を読む。
- `--from-release` は、このプロジェクトの GitHub のリリースから db.json と db.json.sig だけを落とす。
- この PC のことは何も送らない(User-Agent: `genmodelfit/<版>`。`~/.netrc` の資格情報は送らない)。
- 一覧をキャッシュ(か `--output`)に書く。
- 失敗したらキャッシュは変えない。

**終了コード**

| コード | 意味 |
|---|---|
| 0 | 成功 |
| 1 | 失敗(通信・署名が合わない・前より古い一覧など。`--json` なら誤りは "error" に入る) |
| 2 | 使い方の誤り |

`--vram`・`--ram` も受け付けますが、このコマンドでは何もしません(前からのスクリプトのため)。

## `genmodelfit download`

モデルのファイルを落とす(同意・sha256・続きから・ComfyUI のフォルダ)

**使い方**

```text
genmodelfit download [-h] [--lang {en,ja}] [--json] [--vram GB] [--ram GB] [--db PATH] [--no-cache]
                     [--license-allowlist LIST|FILE] [--comfyui-version VER]
                     [--max-head-age-days DAYS] [--allow-uncensored-auto] [--precision PREC]
                     [--budget GB] [--with-optional] [--list] [--output-dir DIR | --comfyui-dir DIR]
                     [--yes] [--uncensored] [--any-license] [--content {all,sfw,uncensored}] model
```

モデルのファイルをこの PC 向けに落とす(1 つのワークフローの全部のファイル。sha256 で確かめる)。

- MODEL はモデルの名前(表の Model)・ワークフローの ID・そのどちらかの一部(info と同じ)。
- モデルの名前なら、ワークフローは recommend と同じ選び方で決める: この PC で逃がさずに載る(good 以上で GPU)中で一番精度の高いもの、それが無ければ段階が一番良いもの(同じなら逃がさない載せ方、次に精度の高いもの)。
- どの決まりで選んだかは計画に出す。
- `--precision` を付けると、その精度のワークフローを落とす。
- `--budget` を付けると、落とす量が上限に収まる中で一番良いワークフローを選び、収まるものが無ければ断る。
- ファイルは `<ComfyUI>/models/<folder>/<file>` に置く(folder はテンプレートで決まる: diffusion_models・text_encoders・vae・loras・checkpoints など)。
- 置き場所は次の順で最初に決まったもの: `--comfyui-dir`(ComfyUI の外は `--output-dir`。優先の順は同じ)、環境変数 GENMODELFIT_COMFYUI_DIR、`genmodelfit config` で保存した comfyui-dir、自動で見つけた ComfyUI(今いるフォルダ、127.0.0.1 の 8188・8000 で起動中の ComfyUI、ComfyUI デスクトップ版と comfy-cli の設定、C:/ComfyUI などのよくある場所)。
- 置き場所をどれで決めたかは、計画に必ず出す。
- 置き場所が見つからない・2 つ以上見つかったときは、端末なら選ぶかパスを入れ、`--yes`・`--json`・端末でないときは落とさない(終了コード 1)。
- `--list` は起動中の ComfyUI に尋ねないので、本当に落とすときは見つかる場所でも「置き場所: 未定」と出ることがある(正しい計画を見るには `--comfyui-dir` を付ける)。
- ComfyUI のフォルダの extra_model_paths.yaml に is_default の節があれば、そこに置く。
- ComfyUI のどのフォルダか分からないファイルは other/ に置く。
- ComfyUI は other/ を読まないので、使う前に正しいフォルダ(diffusion_models など)へ移す。
- 落とす前に、ファイル・それぞれのライセンスと URL(記載が無ければ確かめる場所)・合計の大きさ・ディスクの空きを出し、[y/N/c] で尋ねる(c で置き場所を変える。`--yes` で尋ねない)。
- 自動で見つけた場所に初めて落とすときは、次からもそこにするかを尋ねる([Y/n]。`genmodelfit config` で見る・消す)。
- 保存できるのは ComfyUI のフォルダだけで、`--output-dir` の場所は毎回指定する。
- 無検閲のモデルとライセンスの分からないファイルには同意が要る: 端末ならそれを書いて y/N で尋ね、`--yes`・`--json`・端末でないときは `--uncensored`・`--any-license` を指定する(前からの書き方: `--content all`、`--include-unknown-license`。同意の指定は略さずに書く)。
- 足りないのが同意だけなら、計画の最後に同意を足したコマンドを 1 つ出す(genmodelfit が勝手に足して落とすことはしない)。
- 断るもの(どのオプションでも変わらない): gated のファイル(ログインしない)、ディスクの空きが足りない(落とす量 + 1 GB が要る)、置き場所の外に出る名前、Hugging Face / GitHub 以外のホスト。
- 落としたファイルを使うには: ComfyUI を開いていたらブラウザで読み込み直し、ComfyUI のテンプレートの一覧から、info の 1 行目の名前のテンプレートを開く([ ] の中の ID はテンプレートのファイル名)。
- ID が hf: で始まるモデルには ComfyUI のテンプレートが無いので、使い方は Hugging Face のページ(`https://huggingface.co/<owner>/<repo>`)を見る。

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `model` |  |  | モデルの名前(表の Model)・ワークフローの ID・その一部 |
| `--precision` | `PREC` | recommend と同じ選び方(上を見る) | 同じモデルのワークフローのうち、この精度のものを落とす(例: fp8 / bf16 / int8 / gguf-q8_0。あるものは表の Prec・info で分かり、無ければ誤りにして並べる)。recommend などの `--precision`(見積もりの換算)とは別 |
| `--budget` | `GB` |  | 落とす量の上限(GB。既にあるファイルは数えない)。収まる中で一番良いワークフローを選び、収まるものが無ければ断る |
| `--with-optional` |  |  | テンプレートが既定で使わない部品(無効・素通しのノードなどにしかないもの)も落とす(#81) |
| `--list` |  |  | 落とすファイルと合計を出すだけ(落とさない・通信しない) |
| `--output-dir` | `DIR` | なし(`--comfyui-dir` の決め方で決める) | ComfyUI の外の置き場所。ファイルは `<DIR>/<folder>/<file>` に置く(folder は diffusion_models・vae など。ComfyUI の models と同じ並びなので、ComfyUI の models のフォルダそのものを渡してもよい) |
| `--comfyui-dir` | `DIR` | GENMODELFIT_COMFYUI_DIR → config の comfyui-dir → 自動で見つけたもの | ComfyUI のフォルダ。ファイルは `<DIR>/models/<folder>/<file>` に置く(既定は環境変数 GENMODELFIT_COMFYUI_DIR、`genmodelfit config` の comfyui-dir、自動で見つけたもの) |
| `--yes`, `-y` |  |  | 同意の確認を省く(`--json` で落とすときは必須) |
| `--uncensored` |  |  | 無検閲のモデルも落とす(前からの書き方 `--content all` と同じ) |
| `--any-license`, `--include-unknown-license` |  |  | ライセンスの分からないファイルを含むモデルも落とす(条件は自分で確かめる。前からの書き方 `--include-unknown-license` も同じ) |
| `--content` | `{all,sfw,uncensored}` | `sfw` | 名前で探すモデルの内容の印: sfw(既定)/ uncensored(無検閲だけ)/ all(両方)。uncensored と all は無検閲のモデルを落とす同意にもなる(`--uncensored` は all と同じ。`--uncensored` と一緒に使えるのは all だけ) |

- 同時には使えない: `--output-dir`, `--comfyui-dir`
- 同意(端末では y/N で尋ねる。`--yes`・`--json`・端末でないときに指定する。略さずに書く): `--uncensored`・`--any-license`・`--content`
- [共通のオプション](#共通のオプション)も使えます: `--json`・`--vram`・`--ram`・`--db`・`--no-cache`・`--license-allowlist`・`--comfyui-version`・`--max-head-age-days`・`--allow-uncensored-auto`

**前提**

- 落とすには huggingface.co(か github.com)と通信できること。
- `--list` は通信しない。

**副作用**

- 通信: 一覧の URL の GET だけ(https。転送先もすべて Hugging Face / GitHub のホストに限る)。
- この PC のことは何も送らない(User-Agent: `genmodelfit/<版>`。`~/.netrc` の資格情報もトークンも使わない)。
- 書くのは置き場所のフォルダの中だけ: 落としている間は `<file>.part`(Ctrl+C や失敗では残し、次の実行で HTTP Range で続きから落とす)、sha256 が合えば `<file>` に名前を変える。
- 落としたファイルの sha256 が合わなければ消す。
- sha256 の合う既にあるファイルは飛ばし、ほかの既にあるファイルは上書きしない。
- 置き場所の指定(オプション・環境変数・設定)が無いときは、この PC で起動中の ComfyUI にフォルダの場所を尋ねる(GET http://127.0.0.1:8188 と :8000 の /internal/folder_paths。時間切れ 2 秒)。
- 「次からも」に Y と答えると、設定ファイルに comfyui-dir を書く(`genmodelfit config path`)。
- `--list`: 通信せず(起動中の ComfyUI にも尋ねない)、何も書かない。

**終了コード**

| コード | 意味 |
|---|---|
| 0 | 成功(`--list` も。計画が止まっていても: "blocked" を見る) |
| 1 | 失敗: モデルが無い・2 件以上合う、断った(上を見る)、同意が無い、通信、sha256 が合わない(`--json` なら誤りは "error" に入る) |
| 2 | 使い方の誤り |
| 130 | 中断(Ctrl+C)。もう一度実行すれば続きから落とす |

**使い方の例(スクリプト・アプリ向け)**

```sh
genmodelfit download "Z-Image-Turbo" --list --json
genmodelfit download "Z-Image-Turbo" --comfyui-dir C:/ComfyUI --yes --json
genmodelfit download image_z_image_turbo_int8 --output-dir D:/models --budget 15 --yes
```

JSON の出力(`--json`): [schema.ja.md「download」](schema.ja.md#download)

**例**

```sh
genmodelfit download "Z-Image-Turbo" --list                    # 1. まず落とすファイル・ライセンス・大きさを見る(落とさない)
genmodelfit download "Z-Image-Turbo" --comfyui-dir C:/ComfyUI  # 2. ComfyUI のフォルダへ落とす(同意を尋ねる)
genmodelfit download MODEL --uncensored --any-license --yes    # 尋ねずに落とす(無検閲・ライセンス不明も。条件は自分で確かめる)
```

## `genmodelfit measure`

測ると役に立つモデルを選んで、ComfyUI で実測する(無ければ起動する。何も送らない)

**使い方**

```text
genmodelfit measure [-h] [--lang {en,ja}] [--json] [--db PATH] [--no-cache] [--url URL]
                    [--comfyui-dir DIR] [--no-start] [--start-timeout SEC] [--yes]
                    [--delete-downloaded] [--free] [--resolution WxH] [--frames N] [--length SEC]
                    [--limit N] [--timeout SEC] [model]
```

- ComfyUI でモデルを実測し、結果をこの PC に保存する(ComfyUI が無ければ起動する。何も送らない)。
- MODEL を省くと、測ると役に立つモデルを出す: この PC の GPU に載り(GPU で good 以上)、この PC でテンプレートの設定のまま、まだ測っていないもの(同じ GPU のほかの人の実測があっても外さない。2 つの出どころの実測がそろうと、段階を実測で決められるため)。
- ライセンスの分からないものは出さない。
- 並びは、もう入っているもの → 時間が短いもの → 落とす量が少ないもの。
- 番号で選ぶ(q でやめる)。
- 起動中の ComfyUI に無いノードが要るものは出さない。
- その実測があるモデルには、予測式の確かめに役立つ「設定を変えた測定」を出す(#116): 画像の解像度 512・768・1024・1536 px(文章から画像)、動画のコマ数 17・33・49・81(大きさはテンプレートのまま。文章・画像から動画)、音楽・効果音の長さ 10・30・60・120 秒(文章から音声)。
- その設定で GPU に載り(good 以上)、この PC でその設定をまだ測っていないものだけで、1 つのモデルに 2 件まで。
- 時間の目安が約 300 秒を超えるものは出さない。
- 1 回は生成 1 回(数分)で、もう一度 measure を実行すると次のものを選べる。
- この PC でもう測ったモデル・設定でも、測ったときと今で ComfyUI の版が違えば、ほかのものの後に出す(行に「ComfyUI 0.19 → 0.20 で測り直し」と出す。起動していない ComfyUI の版はフォルダから読む)。
- 同じ版なら出さない。
- MODEL はモデルの名前・ワークフローの ID・その一部で、download と同じ選び方で決める(ComfyUI のテンプレートのあるワークフローだけ)。
- `--resolution`・`--frames`・`--length` で bench と同じく設定を変える。
- 測る前に 1 画面で、モデルと精度、設定(テンプレートのまま、または変えた設定)、どの ComfyUI か・どう見つけたか、ファイルが入っているか、時間の目安、何も送らないことを見せ、[Y/n] で尋ねる(`--yes` で尋ねない)。
- 時間は目安: 同じモデルの実測の中央値、無ければ種類ごとのおおよその値。
- 設定を変えたときは画素数・コマ数・長さの比で伸び縮みさせる。
- テンプレートが頼んだ設定を受け付けないとき・ComfyUI に要るノードが無いときは、落とす前・生成の前に止める。
- 入っていないモデルは、download と同じ確かめ・同意の決まりで先に落とす。
- 落としたものは、終わると(失敗・Ctrl+C でも)一覧と合計の量を見せて消すかを [y/N] で尋ねる(この実行で新しく落としたファイルだけ。前から入っていたものは消さない。`--yes`・端末でないときは残す。`--delete-downloaded` で尋ねずに消す)。
- genmodelfit が起動した ComfyUI は、測る前に空ける(/free。モデルを外し、前の結果を消す)。
- 自分の ComfyUI は `--free` のときだけ。
- 終わると結果と保存した場所を出し、最後に `genmodelfit share`(送るコマンド)を 1 行だけ案内する(送るかどうかは尋ねない)。
- 測れなかったときは、失敗の記録を保存する(share が送る)。

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `model` |  |  | 測るモデルの名前(表の Model)・ワークフローの ID・その一部(省くと、測ると役に立つモデルを出して選ぶ) |
| `--url` | `URL` | この PC で起動中の ComfyUI(127.0.0.1 の 8188・8000)を探す | 測りに使う ComfyUI の URL(例 http://127.0.0.1:8188) |
| `--comfyui-dir` | `DIR` | download と同じ決め方 | ComfyUI のフォルダ(モデルが入っているかを見る・入っていないとき落とす場所) |
| `--no-start` |  |  | 起動中の ComfyUI が無くても、見つけた ComfyUI を起動しない(環境変数 GENMODELFIT_COMFYUI_START=0 と同じ) |
| `--start-timeout` | `SEC` | `300` | 起動した ComfyUI の準備を待つ時間の上限(秒。既定 300) |
| `--yes`, `-y` |  |  | 測る前に尋ねない(入っていないモデルは download `--yes` と同じ決まりで落とす) |
| `--delete-downloaded` |  |  | 測るために落としたファイル(この実行で新しく落としたものだけ)を、終わったら尋ねずに消す |
| `--free` |  |  | 測る前に ComfyUI の /free でモデルと前の結果を外す(起動中の自分の ComfyUI 向け。genmodelfit が起動した ComfyUI ではいつも外す) |
| `--resolution` | `WxH` |  | 幅と高さ(例: 1536x1536)を変えて測る(bench と同じ。省くとテンプレートのまま) |
| `--frames` | `N` |  | 動画のコマ数を変えて測る(bench と同じ。省くとテンプレートのまま) |
| `--length` | `SEC` |  | 音声の長さ(秒)を変えて測る(bench と同じ。省くとテンプレートのまま) |
| `--limit`, `-n` | `N` |  | 引数なしで出す提案の数(既定 10) |
| `--timeout` | `SEC` | `1800` | 待つ時間の上限(0 より大きい秒数。既定 1800) |

- [共通のオプション](#共通のオプション)も使えます: `--json`・`--db`・`--no-cache`

**前提**

- この PC の GPU と、そこで動く ComfyUI: `--url` か、この PC で起動中のもの(127.0.0.1 の 8188 か 8000)。
- 起動していなくても ComfyUI のフォルダが見つかれば(download と同じ見つけ方)、裏で起動して使い、測り終えたら止める(ポータブル版は python_embeded、手で入れたもの・comfy-cli は .venv・venv、デスクトップ版は .venv とアプリの ComfyUI で、127.0.0.1 の空いているポートに、ブラウザを開かずに)。
- [Y/n] の質問がその同意を兼ねる(`--no-start` か GENMODELFIT_COMFYUI_START=0 で止める。`--start-timeout` は既定 300 秒)。
- 見つからなければ、起動のしかたを出して 1 で終わる。
- GPU が空いていること: ほかのプログラムが GPU のメモリを 1.5 GiB(か 20%)より多く使っていれば測らない(nvidia-smi の無い AMD・Intel・Apple の GPU では、できるところは ComfyUI の /system_stats で確かめ、確かめられなかったことは結果のメモに書く)。
- 測るのは、同時に 1 つの measure か share だけ。
- 起動した ComfyUI は、失敗・Ctrl+C・端末を閉じたときも止める。

**副作用**

- 通信: ComfyUI が起動しているか・フォルダの場所を 127.0.0.1 の 8188・8000 に尋ねる(`--url` が無いとき)、測る ComfyUI(ノードの確かめと仕事を 1 つ)、その ComfyUI に無ければ GitHub の公式のテンプレートと入力の画像、落とすときはモデルのファイル。
- この PC のことはほかへ送らない。
- 結果を `<cache>/measurements/`(この PC の判定に使う)に、送るための下書き(か失敗の記録)を `<cache>/submissions/`(`genmodelfit share` が送るもの)に書く。
- 起動した ComfyUI の出力は `<cache>/comfyui-<日付>.log`。
- 測るあいだは `<cache>/measure.lock`。
- この実行で落としたモデルのファイルを消すのは、y と答えたか `--delete-downloaded` のときだけ(置き場所の中だけ)。

**終了コード**

| コード | 意味 |
|---|---|
| 0 | 成功(選ばなかった・出す提案が無い・「測りますか?」や落とす質問に n と答えた・送れないだけの結果のときも) |
| 1 | 失敗: ComfyUI が見つからない、GPU が無い、GPU が使われている、ほかの measure が測っている、テンプレートが無いモデル、ノードが足りない、落とさなかった、実行の失敗(メモリ不足・ほかの誤り・実行中の Ctrl+C)、端末でなく `--yes` も無い |
| 2 | 使い方の誤り |
| 130 | 質問の途中の Ctrl+C |

**使い方の例(スクリプト・アプリ向け)**

```sh
genmodelfit measure --json
genmodelfit measure image_z_image_turbo_int8 --yes --json
genmodelfit measure "Z-Image-Turbo" --url http://127.0.0.1:8188
genmodelfit measure image_sdxl_simple --resolution 1536x1536 --yes --json
```

JSON の出力(`--json`): [schema.ja.md「measure」](schema.ja.md#measure)

**例**

```sh
genmodelfit measure                              # 測ると役に立つモデルを選んで測る(何も送らない。送るのは genmodelfit share)
genmodelfit measure "Z-Image-Turbo"              # モデルを名前で決めて測る
genmodelfit measure --url http://127.0.0.1:8000  # この ComfyUI で測る(探さない)
```

## `genmodelfit share`

入っているモデルを数分測り、送っていない実測といっしょに GitHub の PR で送る(`--issue` は Issue のフォームで送る)

**使い方**

```text
genmodelfit share [-h] [--lang {en,ja}] [--json] [--yes] [--download] [--delete-downloaded]
                  [--any-license] [--no-measure] [--budget SEC] [--dry-run] [--issue] [--print-url]
                  [--logout] [--all] [--mark-sent] [--url URL] [--comfyui-dir DIR] [--no-start]
                  [--start-timeout SEC] [--timeout SEC] [--db PATH] [--no-cache] [file]
```

- 数分測って、実測を GitHub の PR で送る。
- FILE を省くと、measure と同じく ComfyUI を見つけ(`--url`、無ければこの PC で起動中のもの、無ければ見つけたものを起動し、終わったら止める)、measure の提案のうち、ファイルが入っていて、この PC でまだ測っていないもの(この PC に保存したものは、送ったかどうかによらず測ったと数える。ほかの人の実測は数えない。ComfyUI の版が違うときに測ったものは、measure と同じくほかのものの後に出す)で、その ComfyUI にノードがそろうものを、時間の目安の合計が `--budget`(300 秒)に収まるまで選び、送っていない実測と合計の時間(ComfyUI の起動 約 45 秒・測る時間・落とす時間を 100 Mbps の例で)を添えて計画を見せる。
- 1 回だけ [Y/n] で尋ね、1 つずつ測る(失敗しても残りは続け、Ctrl+C ならそこまでのものを送るかに進む)。
- 測る前には毎回 ComfyUI を空ける。
- 入っていないモデルを落とすのは、`--download` か、1 回の [y/N] の質問に y のときだけ(入っていて測れるものが無ければ最初に、ほかは測り終えてから尋ねる)。
- download と同じ確かめと同意の決まりで、`--yes` だけでは落とさない。
- そのあと送るファイルのすべて(中身のすべてと個人の情報の点検)を見せて、送り方を尋ねる: 1) PR(GitHub にログイン) 2) Issue(ログインなし。ブラウザでフォームを開く。1 つのファイルに 1 つ) q) 送らない。
- `--yes`・`--json`・端末でないときは PR、`--issue` `--yes` は Issue(端末でなければ URL を出す)。
- 測る前は、もうあるトークン(GITHUB_TOKEN か GH_TOKEN、無ければ保存したログイン)を確かめるだけで、使えなければすぐ止める。
- ブラウザでのデバイス認証(権限 public_repo。次のために保存し、`--logout` で消す)は、PR を選んだときだけ。
- デバイス認証を待つあいだはキーが使える: r ページを開き直す、n 新しいコード、q 同じファイルを Issue で送る。
- コードの期限が切れたら、もう一度ログインするかを尋ねる。
- `--yes` で端末でなく、トークンが無ければ、測る前に止める。
- PR は、リポジトリのフォークで、本家とフォークの main の共通の祖先からブランチを作り、`data/contrib/<GPU>/` にファイルを置いて開く(題は「measurements: `<GPU>` (`<件数>`)」)。
- その GPU の share の PR が開いていれば、そこに足す。
- もうあるファイルは飛ばす。
- PR を作れなければ、端末なら同じファイルを Issue で送るかを尋ねる。
- 結果は送る前に保存し、測り直さない: あとで `genmodelfit share --no-measure`(か `--issue`)で送れる。
- 送ったものはこの PC で印を付ける(`--all` で含める)。
- 測るために落としたモデルのファイルは、最後に(失敗・Ctrl+C でも)一覧と合計の量を見せて消すかを [y/N] で尋ねる(この実行で新しく落としたファイルと残った .part だけ。前から入っていたものは消さない。`--yes`・端末でないときは残す。`--delete-downloaded` で尋ねずに消す)。
- 測れなかったものは、1 回に 1 つの失敗の記録のファイルにまとめる(名前と数だけ。エラーの本文は入れない)。
- ComfyUI が無ければ送っていない実測だけを送り、それも無ければ尋ねずに ComfyUI の起動の仕方を出して 1 で終える。
- FILE はそのファイルだけを送る。
- `--yes` の無い share `--issue` は前からの送り方で、送っていない実測を選んで Issue のフォームを開く。

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `file` |  |  | 送る提出の JSON(省くと、測ってから送っていない実測をすべて送る) |
| `--yes`, `-y` |  |  | 測る・送る前に尋ねない(入っていないモデルを落とすことには同意しない) |
| `--download` |  |  | 時間が残れば、入っていない提案も落として測る(download と同じ決まり。尋ねない) |
| `--delete-downloaded` |  |  | 測るために落としたファイル(この実行で新しく落としたものだけ)を、終わったら尋ねずに消す |
| `--any-license` |  |  | `--download`・端末でないとき、ライセンスの分からないモデルも落とす(download と同じ。略せない) |
| `--no-measure` |  |  | 測らずに、送っていない実測だけを送る |
| `--budget` | `SEC` | `300` | 測る時間の合計の上限(時間の目安の合計。秒) |
| `--dry-run` |  |  | 計画と送る中身を見せるだけ(測らない・ログインしない・GitHub に通信しない。ComfyUI が起動しているかは 127.0.0.1 に尋ねる) |
| `--issue` |  |  | ログインせず、GitHub の Issue のフォームで送る(`--yes` なら測ってから。無ければ前からの送り方) |
| `--print-url` |  |  | `--issue` で、ブラウザを開かず、フォームの URL を 1 行だけ出す(スクリプト向け) |
| `--logout` |  |  | 保存した GitHub のログイン(トークン)を消す |
| `--all` |  |  | 送った印のある実測も含める |
| `--mark-sent` |  |  | `--issue` `--json` でも送った印を付ける(アプリがフォームを開くとき。既定の `--json` は見るだけ) |
| `--url` | `URL` | この PC で起動中の ComfyUI(127.0.0.1 の 8188・8000)を探す | 測りに使う ComfyUI の URL(measure と同じ) |
| `--comfyui-dir` | `DIR` | download と同じ決め方 | ComfyUI のフォルダ(モデルが入っているかを見る・落とす場所。measure と同じ) |
| `--no-start` |  |  | 起動中の ComfyUI が無くても、見つけた ComfyUI を起動しない(環境変数 GENMODELFIT_COMFYUI_START=0 と同じ) |
| `--start-timeout` | `SEC` | `300` | 起動した ComfyUI の準備を待つ時間の上限(秒。既定 300) |
| `--timeout` | `SEC` | `1800` | 1 件を測るのを待つ時間の上限(秒。既定 1800) |
| `--db` | `PATH` | 選ぶのは update で取った一覧(無ければ同梱)、検査は同梱の一覧 | この一覧のファイル(db.json)を使う(測るものを選ぶ・送る前の検査) |

- [共通のオプション](#共通のオプション)も使えます: `--json`・`--no-cache`

**前提**

- GitHub のアカウント。
- 測るには GPU と ComfyUI(measure と同じ)。

**副作用**

- 通信: 測る・落とすあいだは measure と同じ。
- GitHub(github.com と api.github.com): 測る前は、もうあるトークンを確かめるだけ(GET /user)。
- ログイン・フォーク・ブランチ・ファイル・PR は、送ることに同意したあとだけ。
- `--dry-run` は ComfyUI が起動しているかを 127.0.0.1 に尋ねるだけ、`--issue` は GitHub に通信しない。
- 実測と失敗の記録を measure と同じく書き、送った印を `<cache>/submissions_shared.json` に、ログインのトークンを `<config>/github_token`(本人だけが読める)に書く。
- この実行で落としたモデルのファイルを消すのは、y と答えたか `--delete-downloaded` のときだけ(置き場所の中だけ)。

**終了コード**

| コード | 意味 |
|---|---|
| 0 | 成功(n と答えた・測るものも送るものも無い・送れないだけの結果のときも) |
| 1 | 失敗: ログインできない、ComfyUI が無く送るものも無い、測れなかったものがある、送れないファイルがある(個人の情報の点検の NG・検査の誤り)、GitHub が受け付けない(結果は保存したまま)、`--json`・端末でないのに `--yes` が無い |
| 2 | 使い方の誤り |
| 130 | Ctrl+C(質問の途中。測る途中なら、先にそこまでのものを送るかに進む) |

**使い方の例(スクリプト・アプリ向け)**

```sh
genmodelfit share --dry-run --json
genmodelfit share --no-measure --yes --json
genmodelfit share --issue --yes
genmodelfit share --issue <cache>/submissions/<GPU>/<日付>-<sha8>.json --print-url
```

`GITHUB_TOKEN=<トークン>` `genmodelfit share --yes --json`

JSON の出力(`--json`): [schema.ja.md「share」](schema.ja.md#share)

`--issue`: { listed, submissions? | file, ok, refused, checklist, report, share, opened, copied, marked_shared }

**例**

```sh
genmodelfit share               # 数分測って、PR で送る
genmodelfit share --dry-run     # 計画と送る中身を見るだけ
genmodelfit share --no-measure  # 送っていない実測だけを送る
genmodelfit share --issue       # ログインせず Issue のフォームで送る
```

## `genmodelfit config`

設定(download の ComfyUI のフォルダ・表示の言語)を見る・変える

**使い方**

```text
genmodelfit config [-h] [--lang {en,ja}] [--json] {show,set,unset,path} ...
```

genmodelfit のコマンドラインの設定を見る・変える(Python の API は読まない)。

キー:

```text
comfyui-dir  download が使う ComfyUI のフォルダ(--comfyui-dir・--output-dir と
             GENMODELFIT_COMFYUI_DIR の次)。set はそのフォルダが ComfyUI に見えることを確かめる
             (main.py・comfy・nodes.py・comfy_extras・comfy_api のうち 4 つ、または ComfyUI
             デスクトップ版のフォルダ: models・input・user・output・custom_nodes)。ComfyUI の外の
             フォルダ(download の --output-dir)は保存できないので、毎回 --output-dir を付ける。
lang         表示の言語。en か ja(--lang と GENMODELFIT_LANG が優先)。
```

操作を書かなければ、それぞれの設定と、どこから来たか(指定・環境変数・設定ファイル・既定)を出す。

- [共通のオプション](#共通のオプション)も使えます: `--json`

**前提**

- なし。

**副作用**

- 通信しない。
- set・unset は設定ファイル(genmodelfit の利用者の設定のフォルダの config.json。GENMODELFIT_CONFIG_DIR でフォルダを変えられる)を書く。
- show・path は何も書かない。
- 壊れたファイルは読まず(注意を出す)、set・unset でも上書きしない。

**終了コード**

| コード | 意味 |
|---|---|
| 0 | 成功 |
| 1 | 失敗: 知らないキー、正しくない値、壊れた・書けないファイル |
| 2 | 使い方の誤り |

**使い方の例(スクリプト・アプリ向け)**

```sh
genmodelfit config --json
genmodelfit config set comfyui-dir D:/ComfyUI
genmodelfit config unset comfyui-dir
genmodelfit config path
```

JSON の出力(`--json`): [schema.ja.md「config」](schema.ja.md#config)

**サブコマンド**: `genmodelfit config show`, `genmodelfit config set`, `genmodelfit config unset`, `genmodelfit config path`

### `genmodelfit config show`

今の設定と出どころ(サブコマンドを省いたときはこれ)

**使い方**

```text
genmodelfit config show [-h] [--lang {en,ja}] [--json]
```

このコマンドだけのオプションはありません。

- [共通のオプション](#共通のオプション)も使えます: `--json`

### `genmodelfit config set`

値を保存する

**使い方**

```text
genmodelfit config set [-h] [--lang {en,ja}] [--json] key value
```

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `key` |  |  | comfyui-dir か lang |
| `value` |  |  | comfyui-dir なら ComfyUI のフォルダのパス、lang なら en か ja |

- [共通のオプション](#共通のオプション)も使えます: `--json`

### `genmodelfit config unset`

値を消す

**使い方**

```text
genmodelfit config unset [-h] [--lang {en,ja}] [--json] key
```

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `key` |  |  | comfyui-dir か lang |

- [共通のオプション](#共通のオプション)も使えます: `--json`

### `genmodelfit config path`

設定ファイルの場所

**使い方**

```text
genmodelfit config path [-h] [--lang {en,ja}] [--json]
```

このコマンドだけのオプションはありません。

- [共通のオプション](#共通のオプション)も使えます: `--json`

## `genmodelfit gate`

自動でおすすめに出せるかの集計

**使い方**

```text
genmodelfit gate [-h] [--lang {en,ja}] [--json] [--db PATH] [--no-cache]
                 [--license-allowlist LIST|FILE] [--comfyui-version VER] [--max-head-age-days DAYS]
                 [--allow-uncensored-auto] [--modality {image,video,music,sfx,tts,3d}]
                 [--only-auto | --only-held]
```

一覧のうち、自動でおすすめに出せる(Verified。JSON では "auto")モデルの数と、ほかが保留になる理由を数える。条件は info(1 つのモデル)や recommend `--json` の "auto"・held_reasons と同じ。

- 出力の読み方: 1 行目は条件に使った ComfyUI の版、2 行目はモデルの数と、出せる数・保留の数。
- 表は条件ごとに 1 行で、「落ちた数」はその条件に通らないモデルの数、「最初に落ちた数」はそれが最初に落ちた条件であるモデルの数(先に直すところ)。
- `--only-auto` / `--only-held` はモデルの ID も並べる(保留は最初の理由つき)。
- 条件と許可リストは [gate.ja.md](gate.ja.md)

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `--modality`, `-m` | `{image,video,music,sfx,tts,3d}` |  | この種類だけ数える(1 つだけ) |
| `--only-auto` |  |  | 自動で出せるものの ID も並べる |
| `--only-held` |  |  | 保留のものの ID と理由も並べる |

- 同時には使えない: `--only-auto`, `--only-held`
- [共通のオプション](#共通のオプション)も使えます: `--json`・`--db`・`--no-cache`・`--license-allowlist`・`--comfyui-version`・`--max-head-age-days`・`--allow-uncensored-auto`

**前提**

- 用意するものは無い。
- PC は判定しない。
- 一覧は同梱のものを使う(`genmodelfit update` で新しい一覧を取ってあればそちら。`--no-cache` で同梱)。

**副作用**

- なし(読むだけ。通信しない)。

**終了コード**

| コード | 意味 |
|---|---|
| 0 | 成功 |
| 1 | 失敗: 壊れた `--db` のファイル、空の `--license-allowlist`(「,」や空のファイル)など(`--json` なら誤りは "error" に入る) |
| 2 | 使い方の誤り(知らないオプション・値) |

**使い方の例(スクリプト・アプリ向け)**

```sh
genmodelfit gate
genmodelfit gate -m video --only-held
genmodelfit gate --comfyui-version 0.40.0 --json
```

JSON の出力(`--json`): [schema.ja.md「gate」](schema.ja.md#gate)

`--vram`・`--ram` も受け付けますが、このコマンドでは何もしません(前からのスクリプトのため)。

## `genmodelfit diff`

2 つの一覧(db.json)の違い(増減・ライセンス・自動のおすすめ・HEAD の失敗)

**使い方**

```text
genmodelfit diff [-h] [--lang {en,ja}] [--json] [--all] [--comfyui-version VER] [--limit N] [old]
                 [new]
```

- 2 つの一覧(db.json)を比べ、利用者に関係のある違いを出す。
- 出すのは、増えた・消えたモデルと、変わったモデル(名前 (種類) — ID)ごとの次の違い。
- 変わったモデルの違い: 増えた・消えた・変わったファイル(ライセンスと出どころつき)、ライセンス(寛容な側に ! を付ける)、新しく無検閲になったもの、動かし方・用途、自動のおすすめ、指示・読み上げの言語、大きさ、精度、確認の状態、ほかに変わった項目の名前(「その他の変化」)。
- 変わったモデルは 3 つの節に分け、節ごとに `--limit` で切る: ファイル・ライセンス・無検閲・動かし方と用途が先、次に自動のおすすめの変化(ID は必ず出す)、最後にライセンスの出どころ・言語・大きさ・確認の印など。
- 節の中では大事な変化を先に出すので、大事な変化は `--limit` で隠れない。
- 大事さの低い同じ変化が多くのモデルにあれば(全部に確認の印が付いたなど)、件数つきの 1 行にまとめる。
- 変わった項目はどこかに必ず出る(`--all` では数える)。
- JSON をそのまま比べるので、知らない鍵の変化も数える(merge-check と同じ)。

- 引数なしなら、同梱の一覧と `update` で取ってきた一覧(キャッシュ)を比べる。
- 引数が 1 つなら同梱の一覧とそのファイル、2 つならその 2 つを比べる。
- `--all` は保守用の内容(毎週の PR の中身)を足す: 新しいものに限らずすべての HEAD の失敗、401 のリポジトリ、`sources` の変わった数、人気の数・日付・HEAD の結果だけが変わったモデル、まとめた行の ID。
- `--all` のときは、`-n` を付けなければ行の上限は無い(`--all` でなければ節ごとに既定 `-n` 20)。

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `old` |  |  | 前の一覧。省くと同梱の一覧と取ってきた一覧(update のキャッシュ)を比べる。1 つだけなら同梱の一覧とそのファイルを比べる |
| `new` |  |  | 新しい一覧 |
| `--all` |  |  | 保守用の内容も出す(前からの HEAD の失敗・401 のリポジトリ・sources の数・人気の数などだけの変化) |
| `--comfyui-version` | `VER` |  | 自動のおすすめを比べるときの ComfyUI の版(既定は新しい一覧の記録) |
| `--limit`, `-n` | `N` | 20(`--all` なら全部) | 1 つの節に出す行の上限(既定 20。`--all` では全部) |

- [共通のオプション](#共通のオプション)も使えます: `--json`

**前提**

- 引数なし: キャッシュに取ってきた一覧があること(先に `genmodelfit update --from-release`。無ければ短い案内を出して終了コード 0)。
- 引数があるときは db.json のファイルを 1 つか 2 つ。
- 新しい一覧(generated_at)が前の一覧より古ければ注意を出す。

**副作用**

- なし(読むだけ。通信しない)。

**終了コード**

| コード | 意味 |
|---|---|
| 0 | 成功(違いがあるときも、取ってきた一覧がまだ無いときも) |
| 1 | 失敗(ファイルが読めない・形が違う。`--json` なら誤りは "error" に入る) |
| 2 | 使い方の誤り |

**使い方の例(スクリプト・アプリ向け)**

```sh
genmodelfit diff                          # 同梱の一覧 ↔ 取ってきた一覧
genmodelfit diff new.json                 # 同梱の一覧 ↔ new.json
genmodelfit diff old.json new.json --json
genmodelfit diff old.json new.json --all  # 保守用の内容
```

自動のおすすめは、両方の一覧を 1 つの条件で比べる: 新しい一覧が記録した ComfyUI の版(か `--comfyui-version`)で、HEAD の期限は無し(HEAD の失敗は数える)。

JSON の出力(`--json`): [schema.ja.md「diff」](schema.ja.md#diff)

取ってきた一覧が無いときは、同じ鍵が空で "note" が付く。

## `genmodelfit review`

確認待ち(新しい項目・ライセンスの変化など)の一覧・確かめる・外す(保守する人向け)

**使い方**

```text
genmodelfit review [-h] [--lang {en,ja}] {list,approve,reject} ...
```

- 確認待ち(review.status = pending)の項目を出す・根拠を付けて確かめる・一覧から外す(保守する人向け)。
- pending は update が前の一覧と比べて付ける(new / license_permissive / need_decreased / uncensored_new / files_changed)。
- 確かめるまで自動のおすすめに出さない(gate の reviewed)。

```text
list     [--json] [--all] [--db PATH]          確認待ちと理由
approve  <id> --evidence URL [--evidence URL…] [--note TEXT] [--by NAME] --db PATH
         根拠が無ければ断る。一覧のファイルをその場で書き換える
reject   <id> --note TEXT --db PATH [--rejected FILE] [--hide-name]
         一覧から外し、review_rejected.txt に記録する(update でも入れない)
```

**副作用**

- 通信しない。
- list は読むだけ。
- approve / reject は `--db` のファイルを書き換える(reject は外した記録のファイルにも足す)。

**終了コード**

| コード | 意味 |
|---|---|
| 0 | 成功 |
| 1 | 失敗(ID が無い・根拠が無い・確認待ちでない・`--db` が読めない。`--json` なら誤りは "error" に入る) |
| 2 | 使い方の誤り |

**使い方の例(スクリプト・アプリ向け)**

```sh
genmodelfit review list --json --db src/genmodelfit/data/db.json
genmodelfit review approve hf:org/model --db src/genmodelfit/data/db.json \
    --evidence https://huggingface.co/org/model/blob/main/LICENSE
genmodelfit review reject hf:org/model --db src/genmodelfit/data/db.json --note "商用には使えない"
```

詳しくは [schema.ja.md「review」](schema.ja.md#review)、[release.ja.md「毎週のスキル」](release.ja.md#毎週のスキルgenmodelfit-weekly42)

**サブコマンド**: `genmodelfit review list`, `genmodelfit review approve`, `genmodelfit review reject`

### `genmodelfit review list`

確認待ちの項目と理由

**使い方**

```text
genmodelfit review list [-h] [--lang {en,ja}] [--json] [--db PATH] [--no-cache] [--all]
```

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `--db` | `PATH` |  | 一覧のファイル(既定はキャッシュ、無ければ同梱) |
| `--all` |  |  | 確認済みの項目も出す |

- [共通のオプション](#共通のオプション)も使えます: `--json`・`--no-cache`

### `genmodelfit review approve`

根拠(一次資料の URL)を付けて確認済みにする(一覧のファイルを書き換える)

**使い方**

```text
genmodelfit review approve [-h] [--lang {en,ja}] [--evidence URL] [--note NOTE] [--by BY] --db PATH
                           [--json] id
```

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `id` |  |  | モデルの ID |
| `--evidence` | `URL` |  | 根拠の URL(https。1 つ以上。繰り返して足す) |
| `--note` | `NOTE` |  | 確かめた内容のメモ |
| `--by` | `BY` | `review` | 確かめた人・仕組み(decided_by。既定 review) |
| `--db` | `PATH` |  | 書き換える一覧(例 src/genmodelfit/data/db.json) |

- [共通のオプション](#共通のオプション)も使えます: `--json`

### `genmodelfit review reject`

一覧から外し、update でも入れないように記録する

**使い方**

```text
genmodelfit review reject [-h] [--lang {en,ja}] --note NOTE --db PATH [--rejected PATH]
                          [--hide-name] [--json] id
```

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `id` |  |  | モデルの ID |
| `--note` | `NOTE` |  | 外す理由 |
| `--db` | `PATH` |  | 書き換える一覧(例 src/genmodelfit/data/db.json) |
| `--rejected` | `PATH` |  | 外した記録のファイル(既定は `--db` と同じフォルダの review_rejected.txt) |
| `--hide-name` |  |  | 記録に ID を残さず sha256 だけ残す(安全上の理由で外すとき。既存の除外と同じく名前を残さない) |

- [共通のオプション](#共通のオプション)も使えます: `--json`

## `genmodelfit bench`

保守する人向け: 動いている ComfyUI で実測する(ふつうは measure)

**使い方**

```text
genmodelfit bench [-h] [--lang {en,ja}] [--json] [--db PATH] [--no-cache] --url URL
                  [--workflow PATH] [--width PX] [--height PX] [--frames N] [--length SEC]
                  [--steps N] [--output PATH] [--save-failed] [--save-nonstandard]
                  [--resolution WxH] [--set NAME=VALUE] [--free] [--timeout SEC] [--force]
                  [--source SOURCE] [--note NOTE] [--submission PATH] [--share] template
```

- 動いている ComfyUI で 1 つのモデルを実測し、結果(VRAM・メモリの最大・時間)を保存する。
- TEMPLATE は ComfyUI のテンプレートのワークフローの ID そのもの(例 image_z_image_turbo_int8。info の 1 行目の [ ] の中)。
- モデルの名前や ID の一部は受けない。
- hf: のモデルにはテンプレートが無い(代わりに `--workflow` で API 形式のワークフローを渡す)。
- 保存した結果はこの PC の判定に使う。
- bench は保守する人向け。
- `genmodelfit measure` は、テンプレートの ID を知らなくても同じことをする(モデルを提案し、ComfyUI を探す)。
- `--share` は提出の用意をし、ブラウザで GitHub の実測のフォームを開く: GitHub にログインし(アカウントが要る)、「Create」を押す(`genmodelfit share --issue` と同じ)。

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `template` |  |  | ComfyUI のテンプレートのワークフローの ID そのもの(info の 1 行目の [ ] の中) |
| `--url` | `URL` |  | 測りに使う ComfyUI の URL(例 http://127.0.0.1:8188。既定は持たない) |
| `--workflow` | `PATH` |  | API 形式のワークフロー(省略時はテンプレートから作る) |
| `--width` | `PX` | テンプレートの値 | 幅(ピクセル)(省くとテンプレートのまま) |
| `--height` | `PX` | テンプレートの値 | 高さ(ピクセル)(省くとテンプレートのまま) |
| `--frames` | `N` | テンプレートの値 | 動画のコマ数(省くとテンプレートのまま) |
| `--length` | `SEC` | テンプレートの値 | 音声の長さ(秒)(省くとテンプレートのまま) |
| `--steps` | `N` | テンプレートの値 | サンプラーのステップ数(省くとテンプレートのまま) |
| `--output` | `PATH` | キャッシュの measurements/ | 結果の JSON の書き出し先(失敗・標準でない結果もここへ書く) |
| `--save-failed` |  |  | 失敗(OOM など)の結果もキャッシュに保存する(既定は表示だけ) |
| `--save-nonstandard` |  |  | 標準でない条件(`--workflow`・`--set`・一部だけ実行など)の結果もキャッシュに保存する |
| `--resolution` | `WxH` |  | 幅と高さ(例: 768x768)。`--width`/`--height` と同じ |
| `--set` | `NAME=VALUE` |  | 入力を名前で変える(例: octree_resolution=192) |
| `--free` |  |  | 開始前に /free でその ComfyUI のモデルを解放する(自分だけが使う ComfyUI のときに) |
| `--timeout` | `SEC` | `1800` | 待つ時間の上限(0 より大きい秒数。既定 1800) |
| `--force` |  |  | GPU が使われている・GPU の空きを確かめられないときも測る |
| `--source` | `SOURCE` |  | 誰がどこで測ったか(結果の source に入る) |
| `--note` | `NOTE` |  | 結果の note に足す文 |
| `--submission` | `PATH` |  | 提出用の JSON(contrib_schema 3)も書く。フォルダなら `<フォルダ>/<GPU>/<日付>-<sha8>.json`(フォークの data/contrib を渡すとそのまま PR にできる)。何も送らない([CONTRIBUTING.ja.md](../CONTRIBUTING.ja.md)) |
| `--share` |  |  | 提出用の JSON を書き(`--submission` が無ければキャッシュの submissions に)、中身と点検を見せて、GitHub の実測のフォームを開く(`genmodelfit share --issue` と同じ。端末でなければ URL を出す)。送るには GitHub のアカウントが要る。genmodelfit は何も送らない([CONTRIBUTING.ja.md](../CONTRIBUTING.ja.md)) |

- [共通のオプション](#共通のオプション)も使えます: `--json`・`--db`・`--no-cache`

**前提**

- `--url` で ComfyUI が動いていて、TEMPLATE のモデルのファイルがすべてその models のフォルダにあること(先に `genmodelfit download` で落とす)。
- GPU が空いていること: ほかのプログラムが使っている、または空きを確かめられない(nvidia-smi)ときは、`--force` が無ければ測らない。
- 自分だけが使う ComfyUI で測る。
- `--free` を付けると、先にその ComfyUI のモデルを解放する。

**副作用**

- 通信: `--url` の ComfyUI だけ(仕事を 1 つ送り、結果を読む)。
- その ComfyUI にテンプレートが無ければ、GitHub から公式のテンプレートのファイルを読む。
- この PC のことはほかへ送らない。
- `--share` はブラウザでフォームを開く(か URL を出す)だけ。
- 結果の JSON を `<cache>/measurements/`(か `--output`)に書く。
- 失敗した結果と標準でない条件の結果は、`--save-failed`・`--save-nonstandard`・`--output` が無ければ表示だけ。
- `--submission`・`--share` は提出用の JSON も書く。
- 省いた設定(`--width`・`--height`・`--frames`・`--length`・`--steps`)はテンプレートのまま。

**終了コード**

| コード | 意味 |
|---|---|
| 0 | 成功 |
| 1 | 失敗: ComfyUI につながらない、GPU が使われている(`--force` なし)、テンプレートやファイルが無い、実行の失敗(メモリ不足など)、時間切れ、Ctrl+C(送った自分の仕事を先に取り消す) |
| 2 | 使い方の誤り |

**使い方の例(スクリプト・アプリ向け)**

```sh
genmodelfit bench image_z_image_turbo_int8 --url http://127.0.0.1:8188
genmodelfit bench image_z_image_turbo_int8 --url http://127.0.0.1:8188 --share
genmodelfit bench video_wan2_2_5B_ti2v --url http://127.0.0.1:8188 --resolution 640x640 --json
```

JSON の出力(`--json`): [schema.ja.md「bench」](schema.ja.md#bench)

`--vram`・`--ram` も受け付けますが、このコマンドでは何もしません(前からのスクリプトのため)。

## `genmodelfit contrib`

保守する人向け: 実測の提出を作る・検査する・まとめる(送るのは measure と share)

**使い方**

```text
genmodelfit contrib [-h] [--lang {en,ja}] {make,show,share,validate,intake,aggregate} ...
```

実測の提出用 JSON(contrib_schema 3)を作る・送る前に見る・検査する・まとめる(保守する人向け。実測を送るのは `genmodelfit measure` と `genmodelfit share`)。genmodelfit は何も送らない(共有は、利用者が GitHub のフォームで「Create」を押すときか、自分で Pull Request を出すときだけ。[CONTRIBUTING.ja.md](../CONTRIBUTING.ja.md))。

```text
make      --from <bench の結果>  → data/contrib/<GPU>/<日付>-<sha8>.json
show      <file>                 送る中身と個人の情報の点検(同意のため)
share     [file]                 genmodelfit share --issue と同じ(前からのスクリプトのため)
intake    <event.json> --out DIR GitHub の実測の Issue を検査し、PR に入れるファイルを作る
                                 (measurement-intake.yml が使う)
validate  <files|dirs>...        形・値の範囲・個人の情報・重複・ファイル名を検査
aggregate [dir] [--output F] [--report] [--threshold 25] [--fail-on-breach]
          同じモデル・設定・GPU ごとに中央値と MAD で外れ値を除いてまとめ、校正の誤差を点検
aggregate [dir] --check
          同梱のまとめにまだ入っていない提出を数える(まとめは版を出すときに作る)
```

**副作用**

- 通信しない。
- make は提出のファイルを書く。
- aggregate は `--output` のときだけ書く。
- intake は `--out` に書く。
- share は `genmodelfit share --issue` と同じ(端末ならブラウザを開く。`--json`・`--print-url`・端末でないときは URL を出すだけ。送った印を書く)。

**終了コード**

| コード | 意味 |
|---|---|
| 0 | 成功(validate: すべてのファイルが通った。show: 直すところが無い) |
| 1 | 失敗(読めないファイル・知らない形など) |
| 1 | validate: 機械的な検査に通らないファイルがある、または `--strict` で注意がある(マージする人向けの確認では失敗にしない) |
| 1 | show: 直すところがある |
| 1 | share: 個人の情報の点検に NG がある、または検査の誤りがある(送れない) |
| 1 | aggregate: フォルダが無い、または `--output` で結果が空(`--allow-empty` が無いとき) |
| 1 | aggregate `--check`: 同梱のまとめに入っていない提出がある、またはまとめ直すと違う |
| 2 | 使い方の誤り |
| 3 | aggregate `--report` `--fail-on-breach`: 誤差が閾値を超えた |

**使い方の例(スクリプト・アプリ向け)**

```sh
genmodelfit bench image_sdxl_simple --url http://127.0.0.1:8188 --submission data/contrib/
genmodelfit contrib show data/contrib/<GPU>/<日付>-<sha8>.json
genmodelfit contrib share data/contrib/<GPU>/<日付>-<sha8>.json --json
genmodelfit contrib validate data/contrib --json
genmodelfit contrib aggregate --report --json --fail-on-breach
```

詳しくは [schema.ja.md「contrib」](schema.ja.md#contrib)

**サブコマンド**: `genmodelfit contrib make`, `genmodelfit contrib show`, `genmodelfit contrib share`, `genmodelfit contrib validate`, `genmodelfit contrib intake`, `genmodelfit contrib aggregate`

### `genmodelfit contrib make`

bench が保存した結果から提出用の JSON を作る

**使い方**

```text
genmodelfit contrib make [-h] [--lang {en,ja}] [--json] [--db PATH] --from FILE [--output PATH]
                         [--no-pc-info]
```

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `--db` | `PATH` |  | モデル ID を確かめる一覧(既定は同梱のもの。キャッシュは使わない) |
| `--from` | `FILE` |  | bench の結果の JSON |
| `--output` | `PATH` | `data/contrib` | 書き出す場所(フォルダなら `<GPU>/<日付>-<sha8>.json`。既定 data/contrib) |
| `--no-pc-info` |  |  | この PC の OS の種類・メモリ・GPU の世代を入れない(ComfyUI が別の PC だったとき) |

- [共通のオプション](#共通のオプション)も使えます: `--json`

### `genmodelfit contrib show`

送る中身と個人の情報の点検を見せる

**使い方**

```text
genmodelfit contrib show [-h] [--lang {en,ja}] [--json] [--db PATH] file
```

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `--db` | `PATH` |  | モデル ID を確かめる一覧(既定は同梱のもの。キャッシュは使わない) |
| `file` |  |  | 提出用の JSON |

- [共通のオプション](#共通のオプション)も使えます: `--json`

### `genmodelfit contrib share`

`genmodelfit share --issue` と同じ(前からのスクリプトのため)

**使い方**

```text
genmodelfit contrib share [-h] [--lang {en,ja}] [--json] [--db PATH] [--print-url] [--all]
                          [--mark-sent] [file]
```

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `--db` | `PATH` |  | モデル ID を確かめる一覧(既定は同梱のもの。キャッシュは使わない) |
| `file` |  |  | 送る提出の JSON(省くと、送っていない実測を出して選ぶ) |
| `--print-url` |  |  | ブラウザを開かず、フォームの URL を 1 行だけ出す(スクリプト向け) |
| `--all` |  |  | 送った印のある実測も一覧に出す |
| `--mark-sent` |  |  | `--json` でも送った印を付ける |

- [共通のオプション](#共通のオプション)も使えます: `--json`

### `genmodelfit contrib validate`

形・値・個人の情報・重複を検査する(CI でも使う)

**使い方**

```text
genmodelfit contrib validate [-h] [--lang {en,ja}] [--json] [--db PATH] [--strict]
                             [--no-filename-check] [--markdown] FILE|DIR [FILE|DIR ...]
```

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `--db` | `PATH` |  | モデル ID を確かめる一覧(既定は同梱のもの。キャッシュは使わない) |
| `FILE\|DIR` |  |  | 提出用の JSON(フォルダなら下の \*.json すべて) |
| `--strict` |  |  | 注意(warnings)も失敗にする |
| `--no-filename-check` |  |  | ファイル名(`<GPU>/<日付>-<sha8>.json`)を確かめない |
| `--markdown` |  |  | Markdown の表で出す(CI のまとめ用) |

- [共通のオプション](#共通のオプション)も使えます: `--json`

### `genmodelfit contrib intake`

GitHub の実測の Issue を検査し、PR に入れるファイルを作る(measurement-intake.yml が使う)

**使い方**

```text
genmodelfit contrib intake [-h] [--lang {en,ja}] [--json] [--db PATH] [--existing DIR] --out DIR
                           EVENT_JSON
```

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `--db` | `PATH` |  | モデル ID を確かめる一覧(既定は同梱のもの。キャッシュは使わない) |
| `EVENT_JSON` |  |  | issues のイベントのファイル |
| `--existing` | `DIR` | `data/contrib` | 同じ内容の提出を探すフォルダ(既定 data/contrib) |
| `--out` | `DIR` |  | 結果のファイルを書くフォルダ |

- [共通のオプション](#共通のオプション)も使えます: `--json`

### `genmodelfit contrib aggregate`

同じモデル・設定・GPU ごとにまとめ、精度を点検する

**使い方**

```text
genmodelfit contrib aggregate [-h] [--lang {en,ja}] [--json] [--db PATH] [--output FILE] [--report]
                              [--threshold PCT] [--fail-on-breach] [--allow-empty]
                              [--allow-file-sources] [--check] [dir]
```

**オプションと引数**

| オプション / 引数 | 値 | 既定 | 説明 |
|---|---|---|---|
| `--db` | `PATH` |  | モデル ID を確かめる一覧(既定は同梱のもの。キャッシュは使わない) |
| `dir` |  | `data/contrib` | 提出のフォルダ(既定 data/contrib) |
| `--output` | `FILE` |  | まとめを書き出す(同梱するなら src/genmodelfit/data/measurements_community.json) |
| `--report` |  |  | 校正をやり直し、種類ごとの誤差の表と閾値の判定を出す |
| `--threshold` | `PCT` | `25.0` | 誤差の閾値(%、既定 25) |
| `--fail-on-breach` |  |  | `--report` で閾値を超えたら終了コード 3 にする |
| `--allow-empty` |  |  | まとめが空でも `--output` に書き出す(既定は、空のまとめで上書きしない) |
| `--allow-file-sources` |  |  | git の履歴が無くても(出どころを提出のファイルごとに数えても)`--output` に書き出す。試験・手元の確かめだけ。版を出すときは使わない |
| `--check` |  |  | 書き出さず、同梱のまとめにまだ入っていない提出を数える(あれば終了コード 1) |

- [共通のオプション](#共通のオプション)も使えます: `--json`

## このページについて

コマンドの節は、プログラムの引数の解釈から `tools/cli_reference.py` が作っているので、プログラムといつも一致します。
手で直さず、`src/genmodelfit/cli.py` の説明の文を直して `python tools/cli_reference.py` を実行してください
(このページが古いと `tests/test_commands_doc.py` が落ちます)。

環境変数を読むのは `src/genmodelfit/` の `i18n.py`(`GENMODELFIT_LANG`)・`cli.py`(`GENMODELFIT_COMFYUI_DIR`)・
`comfyfind.py`(`GENMODELFIT_COMFYUI_SEARCH`)・`comfystart.py`(`GENMODELFIT_COMFYUI_START`)・`config.py`(`GENMODELFIT_CONFIG_DIR`)・`db.py`(`GENMODELFIT_CACHE_DIR`)・
`signing.py`(`GENMODELFIT_SIGNING_KEY`)・`github.py`(`GITHUB_TOKEN`・`GH_TOKEN`・`GENMODELFIT_GH_CLIENT_ID`)です。
