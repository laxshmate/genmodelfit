# モデルを落とす(`genmodelfit download`)

[English](download.md) · **日本語**

[README](../README.ja.md)の「2. 手順」の 5(ダウンロードする)の詳しい決まりです。
CLI を使う人と、Python の API を使うアプリの開発者向けです。
JSON の形は [schema.ja.md](schema.ja.md)「download」にあります。
実装は `src/genmodelfit/downloader.py` で、ここに書いた約束は `tests/test_download.py` が検査します。

## コマンドライン

```
genmodelfit download "Z-Image-Turbo" --list                 # what would be downloaded (no network, writes nothing)
genmodelfit download "Z-Image-Turbo"                        # into the ComfyUI found on this PC (asks first)
genmodelfit download "Z-Image-Turbo" --comfyui-dir C:\ComfyUI
genmodelfit config set comfyui-dir D:\ComfyUI_windows_portable\ComfyUI   # use this ComfyUI from now on
genmodelfit download image_z_image_turbo_int8 --output-dir D:\models --budget 15 --yes
genmodelfit download MODEL --uncensored --any-license --yes   # without the question (scripts)
```

- **落とすもの**: 1 つのワークフローの全部のファイル。
  - モデル名(表の Model)なら、`recommend` と同じく、この PC で逃がさずに載る(good 以上で GPU)中で一番精度の高いワークフローを選ぶ。それが無ければ段階が一番良いもの(同じなら逃がさない載せ方、次に精度の高いもの)。どの決まりで選んだかは計画に出す。
  - ID を指定すればそのまま使う。
  - `--precision int8` で精度を決められる。
  - `--budget 15`(GB)で落とす量の上限を決められる(既にあるファイルは数えない)。収まる中で一番良いワークフローを選び、収まるものが無ければ断る。
- **置き場所**: テンプレートの置き場所(`models/diffusion_models`・`text_encoders`・`vae`・`loras`・`checkpoints` など)に置く。ファイル名の中のサブフォルダは保つ。どの ComfyUI かは下の[置き場所の決め方](#置き場所の決め方)で決める。`--output-dir` は ComfyUI の外のフォルダに同じ並びで置く。
  - ComfyUI のどのフォルダか分からないファイル(Hugging Face のモデルに多い)は `other/` に置く。ComfyUI は `models/other` を読まないので、計画の中でそう知らせる。使う前に(モデルのページの説明に従って `checkpoints`・`diffusion_models`・`loras` などへ)移すこと。
  - 一覧に置き場所の無いファイル(出どころによらない)の置き場所は、同梱の一覧の 1 つの根拠だけで決める: 本体のファイルのヘッダのパラメータ数が、公式の ComfyUI テンプレートのファイルと 0.001% の幅で一致し、一致したファイルの置き場所が 1 つだけのとき(`unet` と `diffusion_models`、`clip` と `text_encoders` は同じとみなす。例 SDXL の全部入りのファイルは `sd_xl_base_1.0.safetensors` と同じ数なので `checkpoints`)。計画に根拠を出す(JSON は `directory_basis`)。名前やタグからの推測はしない。こうして `diffusion_models`・`text_encoders` に置く GGUF のファイルは、読むのにカスタムノード ComfyUI-GGUF が要る(計画にそう出す)。
  - #75 から、Hugging Face の 12 モデルの本体は `other/` ではなく `checkpoints/`・`diffusion_models/` に置く。0.9.2 でそれを `other/` に落とした人は、新しいフォルダへもう一度落とすことになる。先にファイルを自分で移せば落とし直さない(sha256 は計り直す)。
- **先に同意**: 落とす前に、次を見せる。
  - ファイルごとのライセンスと原文の URL。原文の URL が無いファイルは「ライセンスの記載がありません」と書き、確かめる場所(モデルの Hugging Face のページか、一覧にあるページ)を出す。そのときの同意の質問は、そのページで自分で確かめることを求める文になる。
  - 許可リストに無いライセンスの印(利用制限の無い商用利用ができるもの。[gate.ja.md](gate.ja.md)。`--license-allowlist` で変えられる)。
  - 合計の大きさと空き。
  - そのあと `[y/N/c]` で尋ねる(既定は No。`c` で置き場所を変える。[落とす前の確かめ](#落とす前の確かめ))。`--yes` で省ける(`--json` で落とすときは必須)。
  - 無検閲のモデルとライセンスの記載が無いファイル: 端末(標準入力と標準出力が端末で、`--yes`・`--json` が無い)では計画を止めず、計画に出したうえで、同意の質問で名指しする(「このモデルは無検閲(成人向けを含みうる)で、ライセンスの記載が無いファイルを含みます(上の確かめる場所で条件を確かめてください)。…それでも落としますか?」)。1 回の `y` で名指ししたもの全部に同意する。端末でない・`--yes`・`--json` では `--uncensored`・`--any-license`(前からの書き方は `--content all`・`--include-unknown-license`)が要る。`--list` は本当に実行したときと同じ: 端末(`--yes` 無し)なら尋ねることを `同意:` の 1 行と、スクリプト向けのコマンド(`--uncensored`・`--any-license` と `--yes` 付き)を出し、そうでなければ「落とせません」と指定のコマンドを出す。同意の指定は略さずに書く(`--u`・`--an` は使い方の誤りで終了コード 2)。`--uncensored` は `--content sfw|uncensored` と一緒に使えない。Python の API は変えない(`content=`・`include_unknown_license=` を自分で渡す)。
  - 使う前に各ファイルの条件を読むこと。
- **落とさないもの**: 1 つでも当てはまるファイルがあれば、そのワークフローは何も落とさない。一部だけ落とすことはしない。
  - 理由は 1 回ずつ出す。足りないのが `--uncensored`・`--any-license` だけなら(端末でない・`--yes`)、計画の最後に「落とすなら(端末で実行すれば、指定の代わりに y/N で尋ねます):」(`--yes` のときは「--yes を外せば y/N で尋ねます。または下の指定を足す」)として、それを全部足したコマンドを 1 行で出し(ほかの指定はそのまま)、指定ごとに何に同意するかを 1 行ずつ添える。勝手に足して落とすことはしない。gated・genmodelfit では落とせないファイル(自分で落とせるよう URL を出す)・ファイルの無いワークフローでは、このワークフローは genmodelfit では落とせないと書き、同じモデルの別のワークフローにその問題が無ければそれを示す。空き・`--budget`・同じ名前の別のファイルでは、外し方(空ける・`--output-dir`、`--budget`、ファイルを移す)と、足りなければそのあとに要る同意の指定を書く。どちらもコマンドは出さない。コマンドは計画のワークフローの ID と、実行したときの形(`genmodelfit` か `python -m genmodelfit`)で書き、要る引数は ' で囲む。stderr には短い `error:` の 1 行を出す(終了コード 1)。
  - gated(ログインや利用申請が要る)。genmodelfit はログイン情報やトークンを扱わない。モデルのページで申請して自分で落とす。
  - 無検閲のモデル(端末の質問で y、または `--uncensored`・`--content all|uncensored` のときだけ)。
  - ライセンス不明(端末の質問で y、または `--any-license` のときだけ)。
  - 一覧に sha256・大きさの無いもの。
  - Hugging Face・GitHub 以外の取得先。
- **安全**:
  - 書きながら sha256 を計り、合わなければ消して失敗にする。
  - 途中は `<名前>.part` に書く。Ctrl+C や通信の失敗のあとで同じコマンドを実行すると、HTTP Range で続きから落とす。相手が Range に応じなければ最初から(3 回まで)。
  - 既にあるファイルは、飛ばす直前に sha256 を計り直す。同じ名前の別のファイルは上書きしない。
  - 同じファイルを 2 つの download が同時に書かないよう `<名前>.lock` を置く。止まったプロセスの残りは片付ける。
  - 空きが「落とす量 + 1 GB」に足りなければ始めない。
  - 置き場所の外には書かない。次を拒む: `..`・絶対パス・ドライブ名・`\`・Windows の予約名(フォルダ名も)・制御文字・双方向文字・外へ出るシンボリックリンクやジャンクション。
- **通信**:
  - 一覧の URL だけを https で使う(update が匿名の HEAD で確かめたもの。download 自体は HEAD の結果を条件にしない)。
  - 最初の URL は `huggingface.co`・`github.com` だけ。
  - 転送先は次のホストだけ(https・443 だけ): `huggingface.co`・`*.huggingface.co`・`hf.co`・`*.hf.co`(CDN、Xet)・`github.com`・`objects.githubusercontent.com`・`release-assets.githubusercontent.com`。
  - 2 通りのホストに読める URL は、つなぐ前に拒む: `\`・空白・制御文字・ホストの中の `%` や `@`・ASCII でないホスト。ホストは requests / urllib3 と同じ読み方で取る。
  - `~/.netrc` もトークンも使わず、PC の情報も送らない(User-Agent と Range だけ)。
  - HTTP 429・500・502・503・504 では `Retry-After`(無ければ間を空けて)待ってやり直す。進まない失敗が 4 回続いたら止める。
- **途中経過**(stderr): 端末では、全体の行とファイルごとの行(棒・量・速さ・残り時間)をその場で書き直す。sha256 を
  確かめるあいだは「確かめています」と出す。端末でなければ、ファイルの始めと全体の 10% ごとに普通の行を出す。
  `--json` では出さない。

## 置き場所の決め方

次のうち、上から順に最初に決まったものを使う。どれで決めたかを計画に毎回出す(JSON は `destination_from`)。

1. `--comfyui-dir <ComfyUI>` か `--output-dir <フォルダ>`。`--comfyui-dir` は ComfyUI のフォルダに見えること(`models` か `main.py` がある)。`--output-dir` は ComfyUI の外の好きなフォルダで、同じ並びで置く。
2. 環境変数 `GENMODELFIT_COMFYUI_DIR`(フラグと同じく確かめる)。
3. [`genmodelfit config`](#genmodelfit-config) で保存した `comfyui-dir`。そのフォルダが無くなっていれば誤りにし、設定の消し方を出す。
4. 自動で探す。見つけたものは確かめの画面に必ず出す:
   1. 今いるフォルダ。`main.py`・`comfy/`・`nodes.py`・`comfy_extras/`・`comfy_api/` のうち 4 つがあるとき(comfy-cli と同じ見分け方)。`measure`・`share` がこのフォルダの ComfyUI を起動するときは、そのフォルダの python を動かすので、起動の前に場所と python を見せて y/N で確かめる(端末でないとき・`--json` では起動しない。`--comfyui-dir` で指定したものは尋ねない)。
   2. この PC で起動中の ComfyUI: `GET http://127.0.0.1:8188/internal/folder_paths` と、ポート 8000 の同じもの(ComfyUI とデスクトップ版の既定)。時間切れ 2 秒、転送は追わない、プロキシは使わない。ComfyUI はこの口を自分の画面のためだけのもので変わりうると書いているので、答えが無い・形が違うときは黙って次へ進む。ComfyUI のフォルダは `custom_nodes` の親(無ければ `models` の親)。尋ねるのは**落とすとき**と、`measure`・`share` が測りに使う ComfyUI を探すときだけ: `--list` とほかのコマンドは通信しない。
   3. ComfyUI デスクトップ版の設定: 利用者データのフォルダ(Windows `%APPDATA%\ComfyUI`、macOS `~/Library/Application Support/ComfyUI`、Linux `~/.config/ComfyUI`)の `config.json` の `basePath` と、そこの `extra_models_config.yaml`。comfy-cli: `config.ini` の `default_workspace`・`recent_workspace` と既定の場所(Windows・macOS は `~/Documents/comfy/ComfyUI`、Linux は `~/comfy/ComfyUI`)。新しい「Comfy Desktop」は探さない(確かめたソースにフォルダの場所が無い)。
   4. よくある場所(1 段だけ): 固定ドライブ(Windows の `C:\`・`D:\` など)・ドキュメント・デスクトップ・ダウンロード・ホームの直下の `ComfyUI` と `ComfyUI_windows_portable\ComfyUI`。

   ComfyUI に見えるもの(上の 5 つのうち 4 つ、または `models`・`input`・`user`・`output`・`custom_nodes` のそろったデスクトップ版のフォルダ)だけを数える。同じフォルダは 1 つにまとめる。
   環境変数 `GENMODELFIT_COMFYUI_SEARCH=0` なら 4 は全部しない(組み込むアプリ・スクリプト・試験向け。決まらなければ下の「決まらない」になる)。
- **`is_default`**: ComfyUI のフォルダの `extra_model_paths.yaml`(デスクトップ版はその `extra_models_config.yaml` も)に `is_default: true` の節があれば、ComfyUI-Manager と同じくそこに置く: 節に `download_model_base` があれば `<base_path>/<download_model_base>`、無ければフォルダのキーがどれも `<親>/<キーの名前>` のときの共通の親。並びの違う節(A1111 など)・無いフォルダ・genmodelfit の読めない書き方(依存を足さずに、`名前: 値` と `|` の形だけを読む)は使わず、注意を出す。節に無いフォルダは注意を出す(ComfyUI はそこから読まない)。
- **決まらない**(見つからない、2 つ以上見つかった): 端末なら番号で選ぶか、ComfyUI のフォルダのパスを入れる。`--yes`・`--json`・端末でないときは落とさずに誤り(終了コード 1)にし、候補と決め方(`--comfyui-dir` か `genmodelfit config set comfyui-dir`)を出す。`--list` は落とさないので、決まらなくても計画を出す(置き場所は「未定」と候補)。
- **0.11.0 で変わった点**: ComfyUI が読まない genmodelfit のフォルダに黙って落とすことはやめた。ComfyUI の外に置くのは `--output-dir` を指定したときだけ。

## 落とす前の確かめ

端末では、計画の最後に、置き場所と見つけ方・ファイルと大きさとライセンス・合計と空きを出し、そのあと尋ねる:

```
  置き場所:     D:\ComfyUI_windows_portable\ComfyUI\models(ComfyUI の models のフォルダ: <folder>/<file>。見つけ方: 起動中の ComfyUI(127.0.0.1:8188))
  ファイル(3 件。GB = GiB):
    - diffusion_models/z_image_turbo_int8_convrot.safetensors  5.78 GB
  ...
  合計:         11.34 GB。落とす量 11.34 GB(既にある 0.00 GB)
上のファイルを、それぞれのライセンス・利用条件(URL)に従って使うことに同意して落としますか?
[y/N/c](c = 置き場所を変える)
```

- `c` で、見つけた場所の一覧(またはパスを入れる画面)を出す。選ぶと計画を出し直して聞き直す。空で答えれば置き場所はそのまま。
- 自動で見つけた場所(上の 4、または入れたパス)に初めて落とすときは、`y` のあとに「次からもここに落としますか? [Y/n]」と尋ねる。`Y` なら `comfyui-dir` に保存する。
- 無検閲・ライセンスの記載が無いファイルの同意の質問は今までと同じ(同じ質問の文に入る)。
- テンプレートが既定で使わない部品のうち、無効・素通しのノードや出力に届かないノードにしかないもの(`ModelFile.optional` が `inactive`。0.12.0、#81)は落とさず、知らせに名前を出す(計画の `skipped_optional`)。そのノードを有効にして使うなら `--with-optional`(Python は `with_optional=True`)。スイッチで切ってある部品(`switch_off`)は、ComfyUI が動かす前の検証で選ばれない枝のファイルも確かめる(無いと「Value not in list」で止まる)ので落とす。分け方は [sources.ja.md](sources.ja.md)「任意の部品」。

## genmodelfit config

```
genmodelfit config                          # current settings, and where each comes from (option, environment variable, config file, default)
genmodelfit config set comfyui-dir D:\ComfyUI
genmodelfit config set lang ja              # default display language (--lang and GENMODELFIT_LANG win)
genmodelfit config unset comfyui-dir
genmodelfit config path                     # where the config file is
```

- どれも `--json` が使える([schema.ja.md](schema.ja.md)「config」)。
- ファイルは genmodelfit の利用者の設定のフォルダの `config.json`(platformdirs: Windows `%LOCALAPPDATA%\genmodelfit`、macOS `~/Library/Application Support/genmodelfit`、Linux `~/.config/genmodelfit`)。環境変数 `GENMODELFIT_CONFIG_DIR` でフォルダを変えられる。
- 読むのはコマンドラインだけ。Python のライブラリは読まない。
- `set comfyui-dir` は、そのフォルダが ComfyUI に見える(5 つのうち 4 つ、またはデスクトップ版のフォルダ)ときだけ保存する。知らないキーは断る。壊れた `config.json` は読まず(注意を出す。落ちない)、上書きもしない。

## Python

```python
import genmodelfit as gmf

plan = gmf.plan_download("Z-Image-Turbo", comfyui_dir=r"C:\ComfyUI")  # 通信しない・何も書かない
for f in plan.files:  # status: download / resume / present / conflict / unavailable
    print(f.relpath, f.size_bytes, f.license, f.license_url, f.status)
print(plan.blocked, plan.warnings, plan.download_bytes, plan.disk_free_bytes)


def ask(plan) -> bool:  # ライセンス・URL・合計・ディスクの空きを見せ、続けるなら True
    ...


def show(p):  # gmf.DownloadProgress
    print(p.phase, p.name, p.done_bytes, p.total_bytes)


result = gmf.download(plan, confirm=ask, progress=show)
# アプリが先に尋ねてあれば gmf.download("Z-Image-Turbo", yes=True, comfyui_dir=...) でもよい
```

- `comfyui_dir` / `output_dir` を渡さないと、ライブラリは今も ComfyUI が読まない genmodelfit のデータのフォルダを使う。`comfyui_dir` を渡すこと。
- ライブラリは何も表示しない。
- `gmf.download` は `gmf.DownloadRefused`(ブロック・同意なし)と `gmf.DownloadError`(通信・sha256 の不一致・空き)を投げる。
- `KeyboardInterrupt` はそのまま通し、`.part` を次の呼び出しのために残す。
