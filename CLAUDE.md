# CLAUDE.md — genmodelfit

画像・動画・音楽・効果音・読み上げ・3D の生成モデルが、その PC で動くかを判定する(llmfit の生成モデル版)。
**MPL-2.0** で公開(コードも同梱のデータも。2026-10-20 にユーザが決めた。Issue #18)。使う側の代表は AI生成キット
(laxshmate/local_model_ai_gen_kit。ソース非公開で販売するアプリ。改変せずに import して同梱する)。
新しい `.py` には MPL の表示(Exhibit A の 3 行)を先頭に入れる。外からの修正は MPL-2.0 で受け入れる(CLA なし)。
データの出典(ComfyUI workflow_templates の MIT の表示・HF の扱い・llmfit)は THIRD_PARTY_NOTICES に書く

## 決まり(2026-10-20)

- **依存は MIT / Apache-2.0 / BSD のものだけ**。GPL・AGPL・独自の制限付きライセンスの部品は使わない
  (使う側のアプリがソース非公開で配布するため)。足すときは pyproject.toml にライセンスを書く
  - 例外: `certifi`(MPL-2.0、requests の依存)。改変せずに使う。MPL-2.0 はファイル単位のコピーレフトで、
    改変しなければ、義務はライセンス表示を残すことと、実行形式で配るときにソースの入手先を知らせること(§3.2)だけ。
    アプリのソース非公開には影響しない。
    これ以外の MPL・LGPL などを足すときは先に相談する
  - tests/test_licenses.py が、入っている実行時の依存(dev などの extra は除く)のライセンスを許可リストと照らす
    (MIT-0・ISC・PSF-2.0 も MIT と同じ扱いで許す。cffi が MIT-0)
- **ComfyUI(GPL-3.0)のコードは import しない**。動いている ComfyUI には HTTP だけで話す(bench)
- データ源は ComfyUI 公式テンプレート(MIT)の index.json とテンプレートの JSON、Hugging Face Hub API。
  willitrunai.com などのサイトのデータは取り込まない(式の考え方だけ参考にする)
- 持つのはモデルの**情報**だけ。モデル本体は持たない・配らない
- 無検閲のモデルも一覧から外さず、`content: uncensored` の印と根拠(content_evidence)を付ける。
  **未成年の性的表現を目的とするものは除外**する
  年齢制限があるので、`recommend` / `fit` / `gmf.recommend()` の既定は `sfw`。無検閲は `--uncensored`(= `--content all`)か
  `--content uncensored`(Python は `content="all"|"uncensored"`)を指定したときだけ出す(2026-10-20)。`info <id>` は ID を指定するので出す
- 見積もりには必ず `basis`(measured / estimated)を付ける。実測があれば実測を使う
- 精度の目標に届かないとき、係数を無理に合わせてごまかさない。docs/accuracy.md に正直に書く
- 版の正本は `src/genmodelfit/_version.py`(pyproject は hatch が読む)。変えたら CHANGELOG.md も書く
- 一覧の新しさ(HEAD の 30 日の期限)は `status.py`(`gmf.data_status()`、`system` の Data の欄)。gate の
  `evaluate_offline` で判定する(期限の式を別に持たない)
- HTTP は必ず `fetch.make_session()`(User-Agent を付け、~/.netrc の資格情報を送らない。プロキシの環境変数は使う)。
  読むだけのコマンドと Python の関数はネットに触らない(README「Privacy」、tests/test_privacy.py・test_embedding.py)
- `download`(#31、`downloader.py`)は一覧の URL だけを https で落とし、転送先は `HOST_POLICY` のホストだけ。
  同意(confirm / yes)・sha256・`.part` の続き・置き場所の外に書かない・gated は落とさない・無検閲 / ライセンス不明は
  はっきりした同意があるときだけ(端末なら名指しした y/N の質問(--yes / --json を除く)、それ以外(--yes・--json・端末でない)は
  --uncensored / --any-license。
  ライブラリは content / include_unknown_license を渡したときだけ)、
  の約束は tests/test_download.py が検査する(約束を変えたら README「Download」と docs/schema.md「download」も直す)
  テンプレートの既定で読み込まれない部品(無効のノード・出力に届かないノードだけにあるもの。#81、`optional.py`)は既定で落とさず、
  `--with-optional`(`with_optional=True`)のときだけ落とす。スイッチで切ってある枝の部品は ComfyUI の起動前の確かめが見るので落とす
  (どちらも VRAM・メモリには数えない。docs/scoring.md「既定で切ってある部品」)
- REST の serve は(未定)(作るならポートは ~/.claude/PORTS.md で選んで登録する)
- 実装は Claude、レビューはユーザ。Issue ごとに 1 本の PR。依存する PR は前の PR のブランチから作り、PR の説明に書く
- **全試験は手元で通してから push する**(2026-10-22 オーナー決定、2026-10-28 に回数を見直し)。
  - push の前に毎回: `tools/ci_local.py --quick`(Windows の Python 3.10 と 3.13・ruff・配布物。約 15 分)
  - マージの前に 1 回(積んだ PR の最後のブランチで): `tools/ci_local.py --linux`(ci.yml と同じ検査を Windows と WSL で
    順に流す。約 1 時間。メモリを食わないよう 1 つずつ)GitHub の ci.yml は確認用で、このリポジトリの
  ブランチの PR では動かない(main へのマージ後・手動・フォークからの PR だけ)。PR の説明に手元の結果を書く。
  ci.yml の検査を変えたら ci_local.py も合わせる(tests/test_release.py が対応を見る)
- **文書は英語版と日本語版の組**(2026-10-23。README / README.ja、CONTRIBUTING / CONTRIBUTING.ja、docs の X.md / X.ja.md。
  目次は docs/README.md)。片方を直したらもう片方も同じ内容に直す。1 つのファイルに 2 つの言語を混ぜない。
  コードのコメントは `docs/<ページ>.md「節の名前」` で引き、節の名前は日本語版の見出しの文字にする。
  README.md は PyPI にも出るので、中のリンクは GitHub の絶対 URL にする。tests/test_docs.py が組・リンク・見出し・引いた節の名前を検査する

## コマンド

```powershell
uv venv --python 3.12; uv pip install -e ".[dev]"
.venv\Scripts\python -m pytest -q
.venv\Scripts\ruff check . ; .venv\Scripts\ruff format --check .
.venv\Scripts\python tools\ci_local.py --linux    # push の前に必ず: ci.yml と同じ検査(3.10〜3.13・ruff・配布物)を Windows と WSL で
.venv\Scripts\genmodelfit system | recommend --json | fit | search <語> | types | info <id> | update | download <id> | gate | diff | review list | bench <template> | contrib {make|show|validate|aggregate}
.venv\Scripts\python -m genmodelfit.calibrate      # 実測から係数を合わせ直す(calibration.json と精度の表)
.venv\Scripts\genmodelfit update --heads-only --no-cache --output src\genmodelfit\data\db.json   # 同梱の一覧の HEAD だけやり直す
.venv\Scripts\genmodelfit diff [<old.json>] [<new.json>] [--all]  # 一覧の違い(引数なし = 同梱 ↔ 取ってきた一覧。--all は毎週の PR と同じ中身)
.venv\Scripts\python -m genmodelfit.weekly --dry-run --out <dir>   # 毎週の作り直し・確かめ・PR の本文を手元で(#19)
.venv\Scripts\python -m genmodelfit.gpuwatch [--save-fixtures tests\fixtures\gpu_official]   # GPU の表の見張り(通信する。#103)
.venv\Scripts\python tools\gpu_inventory.py      # docs/gpu-inventory.md / .ja.md を表から作り直す
```

毎週の一覧の更新は、オーナーの PC の起動スクリプト(タスク スケジューラ `genmodelfit-weekly`、月曜 9:00 JST。
docs/release.md「毎週のスキル」)が作り直し・試験・PR を行う。安全な週(変わったファイルが db.json だけ・merge-check が safe・status の変化なし・手元の
ruff と pytest が通った)だけ自動でマージし、ほかはオーナーが見る。weekly-data.yml は手動の予備。配るのはマージした
main の db.json だけ(data.yml)。試験は同梱の一覧の件数・日付を決め打ちしない(毎週変わる。一覧から数える)。

ネットワークに触るテストは `GENMODELFIT_NETWORK_TESTS=1` のときだけ動く(既定では飛ばす)。
ライブラリの関数は stdout / stderr に何も書かない(組み込み側の出力に混ざるため)。表示は cli.py に寄せ、
途中経過は `logging.getLogger("genmodelfit")` か、呼び出し側が渡す progress / log に出す(tests/test_embedding.py が検査)。

## 形

- HF のモデルは全期間のダウンロード数 10,000 以上(trendingScore・likes30d で拾ったものは 1,000 以上)、merge のタグ・
  試験用の名前は入れない(llmfit の scrape_hf_models.py にならう。2026-10-20)。利用者のテンプレート(index の username が
  ComfyUI でない)は `community`。公式の仲間があれば表の代表にしない
- 段階は動かし方で使うメモリの使用率(60 / 85 / 98%。2026-10-20 に利用者が決めた。`estimate.FIT_BANDS`)。
  上限は 載せ替え・逃がすは good まで(llmfit と同じ)、CPU は実時間で動く設計の読み上げ(`estimate.CPU_REALTIME_TTS`。作者の資料の文つき)・1 回の計算の道具が good まで・生成が marginal まで
  (llmfit は CPU を good まで。拡散は計算で決まり CPU でとても遅いので分けた)。逃がすのは段階が上がるときだけ
  (llmfit の「98% を超えるときだけ」にすると VRAM を増やして段階が下がる)。GPU の会社による上限は無く、実測は段階を上げるだけ。
  確認済みの GPU の種類(その種類の GPU で 2 機種以上で動いた記録。作者・ほかの利用者を分けない)は表の ✓ と
  JSON の `platform_verified`(gate には入れない)。#108、docs/scoring.md「段階の上限」
- 表・JSON では同じモデル(本体の系統 + 大きさ。精度・使い方だけ違う項目)を 1 行にまとめる(`groups.py`。
  鍵の決め方を変えたら docs/schema.md「Models」と tests/test_groups.py の名前の表を直す)。用途の表は `tasks.py`
- データの形は `src/genmodelfit/models.py` が正本。変えたら `docs/schema.md` と `SCHEMA_VERSION` も直す
- 一覧のモデル ID: 公式テンプレート由来はテンプレート名そのまま(例 `image_z_image_turbo_int8`)、
  Hugging Face 由来は `hf:<owner>/<repo>`
- 同梱のデータ: `src/genmodelfit/data/db.json`(一覧)、`measurements.json`(実測)、`measurements_community.json`(ほかの利用者の実測のまとめ)、`contrib_nonstandard.json`(山を飛ばしたなど、まとめに使わない提出の sha256 と理由。#131)、
  `calibration.json`(係数)、`review_rejected.txt`(外した項目)、`overrides.json`・`manual.json`(手の表・手で足すモデル)
- アプリ向けの項目(`languages` / `runtime` / `description`。#36、docs/sources.md「言語・動かし方・説明」)は任意で、
  `schema_version` は上げない(古い版は知らないキーを捨てるので読める。tests/test_appinfo.py が確かめる)。
  手の表は `src/genmodelfit/data/overrides.json`、手で足すモデル(`source: manual`)は `src/genmodelfit/data/manual.json`、決める処理は `appinfo.py`。
  言語は当て推量で `ja` を付けない(HF のカードか手の表に根拠があるものだけ。多言語の部品の表だけでは付けない。
  カードの言語が上限)。当てるたびに材料(保存した `card_languages`・表・手の表)から作り直し、前の値は引き継がない。手のモデルのライセンスは HF のカードの原文で確かめて書く
- **実測や、実測のあるモデルのファイル情報を変えたら `python -m genmodelfit.calibrate` で合わせ直し、
  出た表で docs/accuracy.ja.md(出力をそのまま貼る)と docs/accuracy.md(同じ数で英語に訳す)を直す**(tests/test_estimate.py が calibration.json と合わせ直した結果の一致を検査する)
- キャッシュ: platformdirs の user_cache_dir(`GENMODELFIT_CACHE_DIR` で変えられる)

## 判定・Score の決まり(2026-10-27、#95。docs/scoring.md が正本)

- **指標(項目・重み・式・並びの鍵)を足す・変えるときは、先に docs/scoring の仕様を直し、項目ごとに 4 つを確かめる**:
  説明(1 文で書ける)・根拠(一次資料・実測・llmfit の実装と経緯)・フェア(作者の PC・作者が測ったかどうか・作者のアプリの都合・
  実行した日で、ほかの利用者の判定・点・順位が変わらない)・試験(期待値を決めて自動の試験にできる)。満たさないものは印・注意・絞り込みに回す。
  llmfit を手本にし、違えるところは docs/scoring に理由を書く。独立した検証(別のモデル)を通してから実装する
- 判定の設定は、指定が無ければ全モデル種類ごとの既定(実測の設定は、実測からずらす量の計算だけに使う)
- 実測で段階を決めるのは、同じ GPU(PCI の型番 ID(両方にあるとき)、無ければそろえた名前が同じ(汎用の名前は一致としない)、
  かつ VRAM の比 0.95 以上。`estimate.same_gpu`)・同じ設定で、別々の出どころ 2 つ以上がそろったときだけ
  (作者の実測は何行あっても 1 つ、ほかの利用者は「同じ PR」か「同じ人」でつながるまとまりごとに 1 つ(PR 1 本は多くても 1 つ、同じ人も 1 つ。人は Issue なら作者の GitHub アカウント、手の PR なら PR の元の GitHub アカウント(マージコミットの 1 行目)。実測の PR はマージコミットでマージする。#113。`contrib.submission_sources`)。`estimate.count_sources`)。
  段階に使う量は表の VRAM と同じ(実測のピーク + 文脈の分と、重み + 固定分の大きい方)。1 つだけなら値をずらすだけ
- Score は収まり(`memory_fit`。llmfit の fit_score と同じ曲線)だけ。人気は Score に混ぜず並びの鍵(`popularity.py`)。
  並び・Score・人気の式は docs/scoring にある
- 実測の提出(`data/contrib`)の受け付けの決まり(足すだけ・置き場所・個人の情報・`files_fp`・GPU 名)は docs/schema.md「contrib」。
  GPU 名は表でなく形で見る(断るのは汎用の名前・名前の代わりの値だけ。形に合わない名前・公称に合わない VRAM は確認。#101)。
  同梱のまとめ(`measurements_community.json`)は版を出す手順で作る(docs/release.md)。大きさの入力が無いテンプレートは手の表
  `estimate.NO_SIZE_INPUT`(ほかの利用者の提出では決めない)

## ハードウェアの検出(2026-10-20、#21。docs/hardware.md が正本)

- NVIDIA の GPU があれば、nvidia-smi で確かめたもの(backend `cuda` の最大の 1 枚)を使う。PyTorch の CUDA 版のビルドが
  どれも対応しない古い世代・どれにもドライバが足りない GPU は backend `none`(CPU として判定し、理由を notes に書く)。
  世代・ドライバが分からないとき、表より新しい世代は断らない(#101)。VRAM を返さない NVIDIA は、名前が `NVIDIA GB10` と
  完全一致の Linux aarch64(DGX Spark)だけ統合メモリ(MemTotal。段階は同じ基準)で使う。Jetson・GH200 は範囲外。表(`gputables.TORCH_CUDA_BUILDS`・`NAME_CC_RULES`)を変えたら docs/hardware.md の出典も直す
- 判定に使える NVIDIA の GPU が無いときだけ、AMD(ROCm: RDNA 2 以降、Windows 11・Linux)・Intel(XPU: Arc A/B・Core Ultra の Arc Graphics)・Apple(MPS: macOS 14 以降)を一次資料の対応表(`hardware.classify_adapter`、docs/hardware.md「NVIDIA 以外の GPU」)で判定に使う。段階は NVIDIA と同じ基準(0.11.0 から上限なし。#108)、NVIDIA の実測は較正だけに使う。2 CU の内蔵 GPU(610M・820M)は CPU より速い見込みが無いので CPU、TheRock で Release Ready でない gfx は対応の根拠が無いので CPU(docs/hardware.md「断る理由」)。共有メモリの GPU は逃がさず、メインメモリに GPU の分(取り分けを超える分)も足す。OS の版・世代が分からないものは CPU。表を変えたら docs/hardware.md の出典も直す。
  見つけた GPU は `system --all-gpus` で調べる。WSL2 の AMD・Intel は、/dev/dxg・相互運用・powershell.exe で Windows 側を読み、
  rocminfo / clinfo -l で準備を確かめ、公式の WSL の対応表にあるときだけ使う(#101。docs/hardware.md「WSL2」。
  rocminfo の UUID の行は読まない)。検出は環境ごとに docs/hardware.md「すべての環境」の表に従う
- GPU の名前・型番 ID・PyTorch の CUDA 版の表は `gputables.py`(1 行 = 出典と確かめた日の付いた `Rule`。全部は docs/gpu-inventory.md)。
  毎週の更新の「GPU 表の見張り」(gpuwatch。表は自動で書き換えない)が GPU を知らせたら、一次資料で確かめて行を出典と日付つきで足し、
  `python -m genmodelfit.gpuwatch --save-fixtures tests/fixtures/gpu_official` と `python tools/gpu_inventory.py` で作り直す(#103)
- 精度の直しは ComfyUI のコード(should_use_fp16・supported_inference_dtypes・VAE の working_dtypes)で確かなところだけ。
  系統の表は `families.py`(行番号は ComfyUI のコミット付き)。表・判定の規則を変えたら同梱の一覧の `comfy_family` も作り直す
  (`families.assign`。tests/test_hardware.py が一致を検査)。VRAM の量で変わる動き(fp32・fp16 に広げる)は
  見積もりに入れず注意だけにする(入れると VRAM を増やして段階が下がる)。ComfyUI の版を上げたら名前の一覧と分岐を読み直す
- 段階は VRAM の総量で決め、今の空きが足りなければ `vram_free_short` と理由で知らせるだけ
- PC 名・ユーザ名・GPU の UUID・PCI の差し場所や個体の ID を読まない・出さない
- 試験は本物の nvidia-smi・PowerShell・lspci・レジストリを呼ばない(tests/conftest.py の `_no_real_hardware`)。
  出力はフィクスチャ(tests/fixtures/hardware)で差し替え、検出は失敗しても例外を出さない

## 共用マシン

- GPU は他の作業と共用。bench の前に nvidia-smi で空きを確かめ、他のプロセスが使っていたら測らない
- 実測に使うモデルは 1 つずつ試して消し、ディスクの空きを 20GB 以上残す
