# JSON の形

[English](schema.md) · **日本語**

このページは、genmodelfit が `--json` で出す JSON、Python API が返す値、一覧ファイル `db.json` の形を定める。
この出力を読むアプリの開発者と、形を変えるコントリビューター向け。

**目次**

- [Common envelope / 共通の外枠](#common-envelope--共通の外枠)
- [Side effects / 通信と書き込み](#side-effects--通信と書き込み)
- [System / PC の情報](#system--pc-の情報)
- [Data status / 一覧の新しさ](#data-status--一覧の新しさ)
- [recommend / fit / search](#recommend--fit--search)
  - [Models / モデルのまとめ方](#models--モデルのまとめ方) · [Uses / 用途](#uses--用途) · [Fit / models の 1 件](#fit--models-の-1-件) · [Score / 点数](#score--点数)
- [Estimate / 見積もり](#estimate--見積もり)
- [Gate object / ゲートの結果](#gate-object--ゲートの結果)
- [gate](#gate)
- [diff](#diff)
- [review](#review)
- [info](#info)
  - [ModelEntry](#modelentry) · [Measurement / 実測](#measurement--実測)
- [bench](#bench)
- [measure](#measure)
- [share](#share)
- [contrib](#contrib)
- [download](#download)
- [config](#config)
- [Python API (stable) / 公開の範囲](#python-api-stable--公開の範囲)
- [Database file (`db.json`) / データベースファイル](#database-file-dbjson--データベースファイル)
- [update](#update)

`schema_version`: **1**。正本は `src/genmodelfit/models.py`。このページはそれと一致していなければならない。
形を変えたら `SCHEMA_VERSION` とこのページを直す。

**単位**: `*_gb` の値はすべて **GiB**(2^30 バイト)。ファイルの大きさは bytes / 2^30、VRAM は nvidia-smi の MiB / 1024、RAM は psutil の bytes / 2^30。
実測も GiB とみなす(`measurements.json` の `unit`)。
CLI の表示の「GB」も GiB。

## Common envelope / 共通の外枠

`--json` を付けたコマンドは、どれも 1 つのオブジェクトを出す。

| キー | 型 | 意味 |
|---|---|---|
| `genmodelfit` | str | ツールの版 |
| `command` | str | `system` / `recommend` / `fit` / `search` / `types` / `info` / `update` / `bench` / `measure` / `share` / `gate` / `download` / `diff` / `review` / `contrib` / `config` |
| `system` | System | 判定した PC(`--vram` / `--ram` で置き換えたあと)。`types` / `gate` / `update` / `bench` / `share` / `diff` / `review` / `contrib` / `config` には無い |
| `database` | object | `origin`(`bundled` / `cache` / `path`)、`generated_at`、`models`、`measurements`(件数)。`system` では `status`(DataStatus。下)も入る。`bench` / `share` / `diff` / `contrib` / `config` / `review approve` / `review reject` / `update --from-release` には無い |
| `error` | str | 失敗したときだけ(終了コード 1) |
| `candidates` | object[] | `info` が複数のモデルに当たったときだけ(info を参照) |

## Side effects / 通信と書き込み

各コマンドの `--help` の SIDE EFFECTS と、[README](../README.ja.md) の「送るもの・送らないもの」と同じ内容。

| コマンド | 通信 | 書き込み |
|---|---|---|
| `system`、`recommend`、`fit`、`search`、`types`、`info`、`gate`、`diff`(と Python API) | なし | なし |
| `update` | GitHub(ComfyUI のテンプレート、リリース)、Hugging Face Hub API、モデルの URL への GET / HEAD(HEAD。主な重みのファイルでは、ファイルの見出し部分だけの範囲指定 GET も行う。重みそのものは取らない) | キャッシュの `db.json`(または `--output`)、キャッシュの `file_headers.json` |
| `update --fields-only` | `languages` を決めるカードの `language` を読むための Hugging Face Hub API への GET(主なファイルのリポジトリと、その `license_from`)。手で足したモデルでは、HEAD、ファイルの見出しの範囲指定 GET、上流のカードの API も行う。`--offline` では何も送らない | キャッシュの `db.json`(または `--output`)、キャッシュの `file_headers.json` |
| `update --inspect-only` | Hugging Face の主な重みのファイルの見出しの範囲指定 GET、ComfyUI のテンプレート JSON の GET。`--offline` では何も送らない | キャッシュの `db.json`(または `--output`)、キャッシュの `file_headers.json` |
| `update --from-release` | このプロジェクトの `data-latest` リリースから `db.json` と `db.json.sig` を GET | キャッシュの `db.json`(または `--output`)、`release_state.json` |
| `measure` | `--url` が無ければ `GET http://127.0.0.1:8188/internal/folder_paths` とポート 8000 の同じもの(2 秒で打ち切り、転送は追わない、プロキシは使わない)。そのあとはその ComfyUI に `bench` と同じ。入っていないモデルを落とすことに同意したときは `download` と同じ。提案の一覧だけで ComfyUI が動いていなければ通信しない。(0.13.0)起動中のものが無く、入っている ComfyUI が見つかれば、127.0.0.1 の空いているポートで別のプロセスとして起動し、準備ができるまで `GET /system_stats`、そのあとノードを確かめるため `GET /object_info` とテンプレート。終わったらそのプロセスを止める | `bench` と同じ: 結果を `<cache>/measurements/` に、送るための下書きを `<cache>/submissions/<GPU>/<date>-<sha8>.json` に。落としたときはモデルのファイル。測れなかったときは失敗の記録を `<cache>/submissions/` に(0.13.0)。起動した ComfyUI の出力は `<cache>/comfyui-<date>.log`。測るあいだは `<cache>/measure.lock` |
| `share` | (0.13.0)測る・落とすあいだは `measure` と同じ。GitHub(https で github.com と api.github.com だけ): 測る前に、もうあるトークン(`GITHUB_TOKEN`・`GH_TOKEN`・保存したもの)を確かめる `GET /user`。デバイス認証は PR を選んだときだけ。送ることに同意したあとで、フォーク・ブランチ・ファイル(Contents API)・PR。`--dry-run`(ComfyUI が起動しているかを 127.0.0.1 に尋ねるだけ)と `--issue` は GitHub と通信しない(`--issue` は端末ではブラウザで GitHub のフォームを開く。中身が送られるのは「Create」を押したときだけ) | `measure` と同じ。`<cache>/submissions_shared.json`(送った印)。`<config>/github_token`(ログインのトークンと期限。本人だけが読める。`--logout` で消す)。`--issue` は中身をクリップボードに写すことがある |
| `bench` | ComfyUI の `--url` だけ(ComfyUI に無ければ GitHub の公式テンプレートのファイルも)。`--share` はほかに通信しない(下の `share`) | キャッシュの実測 JSON(または `--output`)。`--submission` を付けると提出用の JSON も。`--share` で `--submission` が無ければ `<cache>/submissions/<GPU>/<date>-<sha8>.json` |
| `contrib` | なし(genmodelfit は提出を送らない)。`share` は `genmodelfit share --issue` と同じ | `make`: 提出用の JSON。`aggregate --output`: community のファイル。`intake`: `--out` のファイル。`share`: 送った印。`show` / `validate` / `aggregate --check`: なし |
| `review` | なし | `list`: なし。`approve` / `reject`: `--db` のファイルをその場で書き換える。`reject` は `review_rejected.txt` にも追記する(`--rejected` が無ければ `--db` の隣) |
| `python -m genmodelfit.weekly merge-check` | なし | なし |
| `python -m genmodelfit.review publish SRC DST` | なし | DST(確認待ちを除いた一覧。data.yml が署名する) |
| `python -m genmodelfit.review apply DECISIONS --db PATH` | なし | `--db` のファイル。reject は `review_rejected.txt` にも追記する |
| `download` | 一覧のモデルの URL への GET(https。リダイレクトは Hugging Face / GitHub のホストへだけ)。置き場所の指定(`--comfyui-dir`・`--output-dir`・環境変数・設定)が無いときは、`GET http://127.0.0.1:8188/internal/folder_paths` とポート 8000 の同じもの(この PC で起動中の ComfyUI。時間切れ 2 秒、転送は追わない、プロキシは使わない)も。`--list` では通信しない | 保存先のファイル(ダウンロード中は `<file>.part`)。「次からも」に Y と答えたときは設定ファイルの `comfyui-dir`。`--list` では書かない |
| `config` | なし | `set`・`unset`: 設定ファイル(`config path`)。`show`・`path`: なし |

テレメトリは無い。PC や利用者の情報は何も送らない。HTTP は `fetch.make_session()` を使う。
User-Agent は `genmodelfit/<version> (+https://github.com/laxshmate/genmodelfit)`。
プロキシと CA の環境変数は守る。`~/.netrc` の資格情報は付けない(tests/test_privacy.py)。

## System / PC の情報

| キー | 型 | 意味 |
|---|---|---|
| `os`、`cpu` | str | |
| `cpu_cores` | int | 論理スレッド数 |
| `ram_total_gb`、`ram_available_gb` | float | GiB。0 = 不明(0.11.0。メモリが読めないとき。見積もりはメインメモリで断らない) |
| `gpus` | Gpu[] | `nvidia-smi` で確かめた NVIDIA の GPU。使える NVIDIA の GPU が無いときは、[hardware.md](hardware.ja.md) の対応表が受け付ける(0.7.0、#22) AMD / Intel / Apple の GPU も入る |
| `best_gpu` | Gpu \| null | 判定に使う GPU。`backend` が `cuda` の GPU のうち VRAM が最大のもの。無ければ `rocm` / `xpu` / `mps` の GPU(専用 VRAM のものが先、次に最大のもの)。null は CPU として判定する |
| `disk_free_gb` | float \| null | ホームディレクトリ(または `--disk-path`)の空き容量 |
| `overrides` | str[] | 置き換えたときの `vram` / `ram` |
| `swap_total_gb` | float \| null | 今のページファイル(Windows)/ スワップ(Linux。zram は除く)の大きさ |
| `swap_max_gb` | float \| null | どこまで増やせるか。固定ページファイルの最大値、無ければ 0、Linux ではスワップの大きさ。null は不明またはシステム管理 |
| `swap_auto` | bool \| null | true はシステム管理のページファイル(増える)/ macOS。false は固定サイズまたは無し。null は不明 |
| `commit_limit_gb`、`commit_used_gb` | float \| null | Windows のコミット上限(RAM + ページファイル)と今のコミット(`GlobalMemoryStatusEx`) |
| `other_gpus` | Adapter[] | どのベンダーのものでも、ディスプレイアダプタ。AMD / Intel / Apple のものは分類する(`usable`、`reason`)。使える NVIDIA の GPU が無いときは、使えるものを `gpus` にも入れる。調べなかったときは空 |
| `other_gpus_checked` | bool | `other_gpus` を調べたか。既定では、`nvidia-smi` が NVIDIA の GPU を見つけず `--vram` も無いときだけ。`system --all-gpus` では常に調べる |
| `notes` | str[] | 検出で分かったこと・分からなかったこと(表示の言語 `en` / `ja`): GPU を CPU として判定する理由、古いドライバ、NVIDIA 以外の GPU、ページファイル、WSL / コンテナの制限 |
| `platform_verified` | bool | (0.11.0、#108。CLI の JSON の外枠だけ。`SystemInfo.to_dict` には無い)判定に使う GPU の種類(会社 × 専用 / 共有のメモリ)で、同梱の実測に 2 機種以上で動いた記録があるか。表の PC の行の「✓ 確認済みの種類」と同じ。GPU が無い・`--vram` で仮定したときは false。段階・`auto`(gate)には使わない([scoring.ja.md](scoring.ja.md)「確認済みの GPU の種類」)。ライブラリは `gmf.platform_verification(system)` |
| `platform_verified_by` | object \| null | 確認済みのときの根拠 `{"kind": "nvidia/dedicated", "models": ["NVIDIA GeForce RTX 3070", "NVIDIA GeForce RTX 5080"]}`(種類・動いた機種の名前)。確認済みでなければ null |

`--ram` を付けると、swap / commit の値は null になる(別の PC を仮定するため)。
検出は失敗しない。分からない値は null にして、理由を `notes` に書く。
ホスト名、ユーザー名、GPU の UUID、PCI のバス ID、デバイスインスタンス ID は、出力に読み込まない。
詳しい内容と出典は [hardware.md](hardware.ja.md)。

Gpu:

| キー | 型 | 意味 |
|---|---|---|
| `name`、`vendor` | str | `vendor` は `nvidia` / `amd` / `intel` / `apple` |
| `vram_total_gb` | float | nvidia-smi が報告しないときは 0(そのとき `backend` は `none`)。`memory` が `shared` / `unified` のときは、GPU が使うとみなすメモリの量([hardware.md](hardware.ja.md)「共有メモリ」) |
| `vram_free_gb`、`vram_used_gb` | float \| null | 今の空き / ほかのアプリ(画面、ブラウザなど)が使っている量。置き換えたとき、または報告が無いときは null |
| `driver` | str \| null | nvidia-smi が出すドライバの版 |
| `backend` | str | 判定に使う: `cuda` / `rocm`(AMD)/ `xpu`(Intel)/ `mps`(Apple silicon)。`none` は使わない(どの PyTorch CUDA ビルドも対応しない世代、すべてに対してドライバが古い、または VRAM が不明。理由は `notes`)。`rocm` / `xpu` / `mps` は 0.7.0(#22)で追加した推定。段階は NVIDIA と同じ基準(0.11.0 から上限なし。#108) |
| `compute_capability` | str \| null | 例: `"8.6"` |
| `compute_capability_source` | str \| null | `nvidia-smi` / `name`(nvidia-smi が古すぎるとき、GPU の名前から推測) |
| `arch` | str \| null | `kepler` / `maxwell` / `pascal` / `volta` / `turing` / `ampere` / `ada` / `hopper` / `blackwell`(`fermi` / `unknown`) |
| `torch_cuda` | str[] | この GPU とこのドライバで動く PyTorch の CUDA ビルド(`cu132` … `cu126`)。新しい順。参考情報 |
| `compute_dtype` | str \| null | この GPU で ComfyUI が既定で計算する精度: `fp16` / `bf16` / `fp32`(fp32 では作業メモリが 2 倍になる)。`backend` が `none` のとき、または世代が不明のときは null |
| `bf16`、`fp8` | bool \| null | ComfyUI から見た bf16 / fp8 計算の対応(null は上と同じ)。bf16 が無いと、ComfyUI が fp16 では計算しない系統のモデルは fp32 で計算する([hardware.md](hardware.ja.md)) |
| `notes` | str[] | この GPU の理由と注意 |
| `memory` | str | (0.7.0)`dedicated`(専用の VRAM)/ `shared`(システム RAM を使う内蔵 GPU または APU。0.11.0 から DGX Spark の GB10 も: 総量は MemTotal)/ `unified`(Apple silicon)。shared と unified の GPU は逃がさない(`GPU+RAM` にしない) |
| `gfx_target` | str \| null | (0.7.0)AMD の LLVM ターゲット。例: `gfx1100` |
| `carveout_gb` | float \| null | (0.7.0)`memory: shared` のとき、BIOS が確保する VRAM(`ram_total_gb` には含まれない)。RAM の確認では、これを超える GPU 使用分だけ数える。それ以外は null |
| `pci_id` | str \| null | (0.9.0)AMD / Intel の GPU の PCI 型番 ID `vendor:device`(`1002:744c`)。Windows の WMI `PNPDeviceID`、Linux の `lspci -nn` から取る。NVIDIA(nvidia-smi の名前はどこでも一致する)と Apple では null。同じ GPU でのほかの利用者の実測と照らすのに使う(`estimate.same_gpu`) |

Adapter(`other_gpus`): `name`、`vendor`(`nvidia` / `amd` / `intel` / `apple` / `unknown`)、
`vram_gb`(Windows: レジストリの `HardwareInformation.qwMemorySize`、無ければ 4 GiB 未満の WMI `AdapterRAM`。Linux の AMD: sysfs。Linux の Intel Arc: 名前から小さいほうのモデルの VRAM。不明なら null)、
`vram_source`(`registry` / `wmi` / `sysfs` / `name` / null)、
`source`(`wmi` / `lspci` / `system_profiler`。0.11.0 で `sysfs`(lspci が無い Linux)と `wsl`(WSL2 から読んだ Windows 側の部品)を、0.12.0 で `app`(`gmf.system_from` に渡したもの。`vram_source` も `app`。#96)を足した)。

0.7.0(#22)で追加:
- `integrated`(bool | null)
- `usable`(bool | null。NVIDIA は `nvidia-smi` が決めるので null)
- `reason`(使う・使わない理由の文。表示の言語 `en` / `ja`)
- `shared_gb`(Linux の amdgpu の GTT。sysfs の `mem_info_gtt_total`)
- `needs`(string | null。GPU を使えるようにするのに要るもの。今は、既知の Windows 10 のビルドだけが理由のときの `"Windows 11"` だけ。表の見出しは `needs Windows 11 for generation`)

0.9.0 で追加: `pci_id`(str | null。Windows の WMI `PNPDeviceID` または Linux の `lspci -nn` から取った PCI 型番 ID `vendor:device`。どのベンダーでも。バスアドレスは取らない)。

0.11.0 で追加(#101。[hardware.ja.md](hardware.ja.md)「すべての環境」):
- `driver`(str | null。Windows の WMI の DriverVersion。Intel の WSL2 の条件に使う)
- `gfx_target`(str | null。名前より確かな所から読んだ AMD の世代: Linux の amdkfd の `gfx_target_version`、WSL2 の rocminfo の `Name: gfx…`)
- `wsl_runtime`(bool | null。WSL2 から読んだ部品で、WSL の中の準備(AMD は rocminfo、Intel は `clinfo -l`)ができているか)
- `config_error`(int | null。WMI の ConfigManagerErrorCode が 0 以外のとき。22・28・43 の AMD・Intel の部品は判定に使わない)

## Data status / 一覧の新しさ

使っているモデルの一覧が、自動のおすすめに使えるほど新しいか。
別の `status` コマンドは無い。次の場所に出る。
- `genmodelfit system --json` の `database.status`
- `update --json`(と `update --from-release --json`)の `status`
- Python の `gmf.data_status(db=None, *, now=None, comfyui_version=None)`(凍結した dataclass の `DataStatus`。`.to_dict()` で同じキーが得られる)

`system` / `update` のテキスト出力では Data の行として出る。通信はしない。

| キー | 型 | 意味 |
|---|---|---|
| `source` | str | `bundled`(パッケージに同梱)/ `cache`(ローカルの `genmodelfit update` が書いた)/ `release`(`update --from-release` で取り込んだ。`generated_at` が `release_state.json` に記録したものと同じキャッシュ)/ `path`(`--db`、`db=`) |
| `generated_at` | str \| null | 一覧を作った日時(一覧に書かれたまま) |
| `heads_checked_oldest`、`heads_checked_newest` | str \| null | 全ファイルの `head_checked_at` の最古 / 最新(UTC、`Z`) |
| `max_head_age_days` | int \| null | ゲートの上限(`--max-head-age-days`、既定 30。null は上限なし) |
| `expired` | bool | **HEAD の確認が古いことだけが理由で、自動のおすすめから落ちたエントリが 1 つでもあれば true**(`auto_count < auto_count_if_fresh`)。アプリは genmodelfit の「新着モデル」を出すのをやめ、`update --from-release`(または `update --heads-only`)で更新する |
| `expires_at` | str \| null | 自動のエントリが最初に期限切れになる時刻。最も古い HEAD の確認 + `max_head_age_days`。ちょうどこの時刻まではまだ有効(ゲートは、確認からの経過が上限より**大きい**ときに確認を捨てる) |
| `models` | int | 一覧のエントリ数(ワークフローごとに 1 つ) |
| `auto_count` | int | 今、自動でおすすめに出せるエントリ数(ゲートの既定の方針: 内容が `sfw` のみ。ComfyUI の版は `--comfyui-version` / `comfyui_version`、無ければ update が記録したもの) |
| `auto_count_if_fresh` | int | HEAD の古さの上限を外した場合の同じ数 |
| `checked_at` | str | 判定に使った時刻(UTC) |

どちらの数も、ゲートの `url_reachable` の確認そのものが決める(`gate.evaluate_offline`。方針どおりに 1 回、`max_head_age_days=None` で 1 回流す)。
だから `expired` とゲートが食い違うことはない。
使っている一覧の日付は `genmodelfit system --json` の `database.status`(`heads_checked_oldest`、`expires_at`)にある。
0.9.1 の同梱の一覧では、最も古い HEAD の確認は 2026-10-21T06:20:06Z。
更新しなければ 2026-11-20T06:20:06Z を過ぎると期限が切れ、`auto_count` が 0 になる。

## recommend / fit / search

この 3 つのコマンドは同じ形を出す(Issue #17。llmfit の `recommend --json` を手本にした)。

| キー | 型 | 意味 |
|---|---|---|
| `filters` | object | 実際に使った値: `modality`(str[] \| null)、`content`、`min_fit`、`license`(str[] \| null)、`include_unknown_license`(bool)、`fit`(str \| null。`--fit`)、`task`(str[] \| null。与えた名前)、`all_workflows`、`limit`。`recommend` は `per_type`、`auto_only`、`include_tools` を足す。`fit` は `sort`、`all`、`include_tools` を足す。`search` は `query`、`sort`、`all` を足す |
| `matched` | int | すべての絞り込みを通った行数。`limit` / `per_type` の前(モデル単位。`--all-workflows` ではワークフロー単位) |
| `hidden` | object | ほかのエントリを出さなかった理由。`{reason: count}`。件数のある理由だけ(下) |
| `groups` | object[] | 種類ごと。順は image、video、music、sfx、tts、3d(`-m` を付けたときはその種類だけ): `{modality, matched, shown}` |
| `models` | Fit[] | 良いものが先(順は下) |

**1 モデルにつき 1 件**(下の「Models」): 精度やワークフロー(文章から画像、編集、LoRA など)だけが違うエントリは 1 件にまとめる。
ほかのものは `workflows` / `variants` に並べる。
`--all-workflows`(Python では `all_workflows=True`)なら、従来どおり一覧のエントリごとに 1 件になる。

`fit`(0.1.1、Issue #28。Python は `gmf.fit_list()`):
- 既定では、動くモデルだけを出す(`--fit runnable` = perfect / good / marginal)。補助ツール、ライセンス不明のもの、無検閲のモデルは出さない。
- `--all`(`show_all=True`)は `--fit all` に、補助ツールとライセンス不明のものを足したもの。無検閲は `--uncensored` も要る。
- `--fit perfect|good|marginal` はその段階だけを出す。ほかの動く段階は `hidden.other_fit` に数える。
- `--fit` と `--min-fit` は同時に使えない(`--perfect` = `--fit perfect`)。

`fit` と `search` は、`--sort`(`sort=`)で並べた**1 つの表**を出す:

| sort | 順 |
|---|---|
| `type`(既定) | 種類(image、video、music、sfx、tts、3d)→ 段階(perfect → good → marginal → unknown → too_tight)→ 同じ段階の中で公式が community より先 → `popularity` の降順 → `score` の降順 → `id` |
| `score` | 動く(perfect / good / marginal)→ unknown → too_tight、その中で段階(逃がさずに動く perfect / good → marginal)→ `score` の降順 → `popularity` の降順 → `id`。種類をまたいだ 1 つの順位 |
| `vram` / `disk` | 動く → unknown → too_tight、その中で `memory_required_gb` / `disk_size_gb` の昇順(null は最後) |
| `name` | 動く → unknown → too_tight、その中でモデル名 |

段階は常に点数より先に見る(段階は「動くか」の答えそのものなので。決まりは [scoring.ja.md](scoring.ja.md)「並び」)。
自動でおすすめに出せるモデルを、`fit` / `search` で上へ動かすことはしない(条件は `info` で見られる)。
JSON の `models` も同じ順。`fit` の `filters` は `sort`、`all`、`include_tools` を足し、`search` は `query`、`sort`、`all` を足す。

表の列(全コマンド共通): `Type | Status | Score | Censored | Model | Use | Prec | VRAM | Mem% | Mode | Disk | License`。

端末が狭いときは、次の順で詰める。
- まず Model の列を 24 文字まで縮める。
- そのあと Disk、Mem%、License、Prec、Use の順に落とす。
- Type、Status、Score、Censored、Model は落とさない。

**Verified** の列(0.5.0 までは Auto。JSON の `auto` で、`yes` か最初に失敗したゲートの確認)は表から外した(#84)。
ゲートの確認は `info` で、数は `gate` で見られる。JSON は変わらない(`auto`、`held_by`、`gate`)。

**Censored**(0.6.0。それまでの名前の頭の `[U] ` に代わる)は、`Y`(`content: sfw`)か `N`(`content: uncensored`)。空欄にはならない。
- 無検閲の行が出うるときだけ出す(`--content all|uncensored`。Python で内容の絞り込み無しに作った表は、無検閲の行があれば出す)。
- 既定の `sfw` では出さない。
- 出すときの凡例は `Censored: N = safety filter removed / adult model`。
- JSON は変わらない(`content`)。

`search` は既定で全部の段階を出す(`--fit` で絞る)。

**不明と非該当(`?` と `-`)**(0.2.0、#30)。表・`info`・凡例では、`?` は値が**不明**、`-` は**当てはまらない**ことを表す:

| 列 / 行 | `?`(不明) | `-`(当てはまらない) |
|---|---|---|
| Status | 大きさが不明で、何も判定していない(`fit_level: "unknown"`) | — |
| Prec / `info` の精度 | 精度が不明(`precision: null`) | — |
| VRAM | 大きさが不明 | CPU で動く(VRAM を使わない) |
| Mem% | 大きさが不明 | GPU が要るのに無い(割る相手がない) |
| Mode | 大きさが不明(`run_mode: "unknown"`) | 動かない(`run_mode: "none"`) |
| Disk / License | 大きさ / ライセンスが不明(`null`) | — |
| Use / `info` の tasks | 分かっている task が無い | — |
| `info` のファイルのフォルダ | — | ファイルに ComfyUI のフォルダが無い(Hugging Face のリポジトリ) |

JSON では次のように分ける。
- 不明な**値**は `null`(`precision`、`license`、`memory_required_gb`、`disk_size_gb`、大きさが不明のときの `estimate` の VRAM / RAM の値)。
- 不明または当てはまらない**状態**は、決まった列挙値(`fit_level: "unknown"`、`run_mode: "unknown"` と `"none"`)。ラベルの `?` / `-` は `fit_label` / `run_mode_label` に入る。

`memory_available_gb` は、大きさが不明でも、この PC の VRAM(CPU なら RAM)になる。その量は分かっているため。

**0.2.0 の破壊的変更**(JSON。大きさが不明のモデルだけ):
- `run_mode` の `"none"` → `"unknown"`
- `run_mode_label` の `-` → `?`
- `estimate.vram_gb` / `vram_offload_gb` / `ram_gb` / `vram_sequential_gb` の `0.0` → `null`
- `memory_available_gb` の `null` → この PC の量

移行は CHANGELOG.md「Breaking (JSON)」を見る。`schema_version` は 1 のまま。

`recommend`:
- `-m` も `--task` も無いときは、**種類ごとに 1 つの節**(image、video、music、sfx、tts、3d)を出し、それぞれ上位 `-n` 件(既定 3。`filters.per_type`、`limit` は null)。
- 動くものが無い種類は、テキストでは 1 行を出し、`groups` では `matched: 0` になる。
- `-m` か `--task` を付けると 1 つの一覧になり、`-n` の既定は 10。
- JSON の `models` は、種類の順、その中で下の順に並ぶ。
- そのほかの既定: `min_fit` は `marginal`、補助ツールは隠す、大きさが不明のモデルは出さない。

`fit` は既定で動く段階を、`search` は全部の段階を、件数の上限なしで出す(`--fit` / `--min-fit` で絞る)。
大きさが不明のモデル(`fit_level: "unknown"`、`?`)は、同じ種類の marginal の後ろに来る(`--fit all`)。

`search <query>`:
- id か名前の一部に合うものを探す(大文字小文字を区別しない)。
- 合わないエントリは `hidden` に数えない。
- 1 件も合わないときは `models: []` を出して終了コード 0(テキストでは、英語なら `(no match)`、日本語なら `(no match / 該当なし)` を出す)。
- 空の query は終了コード 1。

`content` の既定は **`sfw`**。無検閲のモデルは `--uncensored`(= `--content all`)か `--content uncensored`(無検閲だけ)のときだけ出す。`--uncensored` と `--content sfw` を同時に指定すると使い方の誤り。

**ライセンス不明は既定で隠す**(0.1.1、Issue #28)。
- どれかのファイルのライセンス(ファイルを持たないエントリはエントリのライセンス)が不明(`null`)のエントリは、`recommend` / `fit` / `search` と `gmf.recommend()` / `gmf.search()` で出さない。`hidden.license_unknown` に数える。
- `--any-license`(前からの書き方は `--include-unknown-license`。Python は `include_unknown_license=True`)で出す。
- `info <ID そのまま>` は常にエントリを出す。

`hidden` の理由。各エントリは、当てはまる最初の理由で 1 回だけ数える。順は次のとおり:
- `modality`(別の `-m`)
- `uncensored` / `sfw`(`--content` による)
- `tools`(`--include-tools` / `--all` の無い recommend / fit)
- `task`(`--task` 以外の用途)
- `license`(`--license`)
- `license_unknown`(どれかのファイルのライセンスが不明。`--include-unknown-license` が無いとき)
- `size_unknown`(recommend、または `too_tight` 以外の `--min-fit`)
- `too_tight`
- `below_min_fit`(`--min-fit` より下の perfect / good / marginal)
- `other_fit`(`--fit` で選ばなかった、動く段階)
- `held`(`--auto-only`)
- `same_model`(同じモデルの別の行にまとめた。テキストの見出しは「N more workflows merged into their models」)

**`matched` + `hidden` の合計 = 見たエントリ数**(一覧の全エントリ。`search` では id か名前が合ったエントリ)。`tests/test_cli_json.py` で試験している。

`--license a,b`: すべてのファイルのライセンス(`ModelFile.license`。ファイルを持たないエントリはエントリの `license`)が、この一覧に入っていなければならない(大文字小文字を区別しない)。不明なライセンス(`null`)は決して合わない。

### Order of `recommend` / recommend の並べ方

`gmf.recommend()` と `recommend --json`([scoring.ja.md](scoring.ja.md)「並び」):
1. 動く → 判定できない(`unknown`)→ 動かない(`too_tight`)。0.10 までは `too_tight` が `unknown` より先だった(0.11.0 で表とそろえた)
2. `auto: true`
3. **逃がさずに動くもの**(`GPU` / `GPU~` の perfect か good。1 つの階層。0.1.1: 精度を上げて `good` の bf16 の行になっても、`perfect` の int8 の行があった位置より下に動かさない)
4. marginal。marginal の中だけ動かし方の速い順(`GPU` → `GPU~` → `GPU+RAM` → `CPU`。0.11.0)
5. **同じ階層の中で**公式が community のテンプレート(`community`)より先
6. `popularity`(降順。0.11.0)
7. `score`(降順)
8. `id`

0.1.0 までは、community のテンプレートが全段階の公式の行より後ろに来ていた。

`recommend` の**表**(文字の出力)は、この順から 2(`auto: true`)を除いた順で並べる(#84。だから日付や許可リストで表の並びは変わらない)。`-n` はその順で切るので、
`-n` を付けると表と `--json` で出るモデルが違うことがある。JSON の並びと中身は変わらない。

### Models / モデルのまとめ方

llmfit と同じ考え方: **大きさ**が違えば別のモデル、**精度**が違うだけなら同じモデル(自動で選ぶ)、**ワークフロー**はそのモデルの用途。
`group_key` は主な重みから作る(`src/genmodelfit/groups.py`):

1. 主なファイルは、役割が `main` のもの(推定器の規則で決める: ComfyUI のフォルダ → `role` → HF のフォルダ → 名前)。
   - テキストエンコーダ(umt5 など)と VAE は、キーにしない。
   - 主なファイルが複数あるときは、最大のものと、その 95% 以上のもの(Wan 2.2 の high / low noise のような対)。
   - 1 つも無いときは、テキストエンコーダ / VAE / clip_vision 以外で最大のファイル。ファイルが無ければ、エントリ自身。
   - 超解像、アップスケーラ、refiner のファイル(名前に `sr`、`upscal…`、`refiner`)は、ほかに主なファイルがあれば主な重みとしない(Hunyuan Video 1.5 は、720p の t2v と i2v のどちらのワークフローにも 1080p の SR ファイルを載せている)。
2. 各ファイル名から次を落とす(`model.safetensors` のような汎用の名前は、代わりにリポジトリ名を使う)。
   - 拡張子、シャードの番号、GGUF の量子化タグ(`Q4_K_M`、`Q8_0` など)
   - 精度・梱包の語(`fp16`、`bf16`、`fp8`、`e4m3fn`、`scaled`、`mixed`、`int8`、`int4`、`nf4`、`nvfp4`、`w4a8`、`convrot`、`kj`、`comfy`、`transformer`、`only`、`pruned`、`aio`、`high`、`low`、`noise` など)
   - 版・大きさ・派生の語は残す(`2.1`、`1.5`、`14b`、`1.3b`、`turbo`、`dev`、`distilled`、`base`、`edit`)。
   - `wan2_1` は `wan 2.1`、`v1.5` は `1.5`、`ace_step` は `acestep` と読む。
   - 大きさの語が残らなければ、`params_b` を足す(`4b`)。
3. 語を並べ替えてつなぐ。キーは `<modality>:<content>:<words>`。だから、種類や内容の印が違えば同じモデルにならない。
   例: `z_image_turbo_bf16` と `z_image_turbo_int8_convrot` → `image:sfw:image turbo z`。
   `wan2.1_t2v_1.3B_fp16` → `… 1.3b 2.1 t2v wan`、`wan2.1_t2v_14B_fp8_scaled` → `… 14b 2.1 t2v wan`(別のモデル)。
   試験の表は `tests/test_groups.py`。

エントリごとのキーを作ったあと、一覧全体を見る(`groups.build_index`):

- 名前の違う主なファイルを 2 つ持つエントリ(FLUX.2 klein 4B の文章から画像は `klein-base-4b` と `klein-4b` の両方を読み込む)は、そのどちらかのモデルが単独で存在すれば、そちらに入る。選ぶのは `base` の無いほう、次に名前順。無ければ、名前を `+` でつないだまま(Ideogram 4 の無条件の対)。
- 主なファイルの sha256 の**集合全体**が等しいエントリは、同じモデルとする(名前やリポジトリが違う同じファイル。例: Stable Audio Open 1.0 のテンプレートと HF)。主なファイルの一部だけが同じでは足りない。ファイル名と URL / ハッシュが食い違う上流の記録は、先に直す(`update.UPSTREAM_FILE_FIXES`。匿名の HEAD で確かめる)。
- `params_b` をキーに足すのは、大きさの語が無い汎用の HF のファイル名(`model.safetensors`)だけ。テンプレートの `params_b` は題名から来るので、言語モデルの大きさのことがある(ACE-Step 1.5 の「4B LM」)。

精度(`ModelEntry.precision`)は、キーに使った主なファイルから読む(`groups.model_precision_source`。`update` も同じ関数を使う)。
分かった最初の出どころが勝ち、`ModelEntry.precision_source` に残る(#30、0.2.0):

1. `header`: 読めたときの、ファイルの `header_precision`。見出しでは分からない詰め込み形式(`w4a8`、`nvfp4`、`mxfp4`、`fp4`、`nf4`、`int4`)と、名前が量子化を示す GGUF のファイルは、名前が勝つ(`filename`)。
2. `filename`: ファイル名。例: 10Eros の W4A8 のファイルの `w4a8`。
3. `hf_api` / `config`: 見出しも名前も分からないときの `ModelFile.repo_precision`。置いてある Hugging Face のリポジトリから読む(`repo_precision.py`)。
   - Hub API の `safetensors.parameters`: 1 つの dtype がパラメータの 90% 以上を持ち、**かつ**、要素数 × 1 要素のバイト数が、そのリポジトリにあるエントリの主な safetensors ファイルの合計(シャードを足したもの)の 5% 以内であるとき。主なものの横にほかの重みがあるリポジトリは、それを表さない。主なファイルが safetensors のときだけ。ゲート付きのリポジトリでも読める。
   - そうでなければ、主なファイルのフォルダにある `config.json` の `torch_dtype` / `dtype`(API が一覧に出し、リポジトリがゲート付きでないときだけ)。
4. `workflow`: `ModelEntry.workflow_precision`。公式テンプレートの主なローダのノード(`workflow.loader_precision`)から推す。
   - 主なファイルを読み込むノード(ファイルの一覧が無いテンプレートでは、`workflow.MAIN_LOADERS` の最初のもの: `UNETLoader`、`CheckpointLoaderSimple`、`WanVideoModelLoader`、`SeedVR2LoadDiTModel`、`DownloadAndLoadGIMMVFIModel` など)。
   - 読み込み時に fp8 へ変える指定(`weight_dtype` / `quantization` = `fp8_*`)なら `fp8`。
   - そうでなければ、読み込むファイル名の精度の語。
   - それも無ければ、明示した精度のウィジェット(`fp32`、`bf16`、`fp16`、`fp16_fast`)。
   - `default` / `auto` / `disabled` は何も言わない。LoRA、CLIP、VAE のローダは使わない。

見出しから読んだ精度を、あとの出どころで置き換えることはない。null は不明(`precision_source` も null)。

**表示名**(`model_name`。表の Model の列。`groups.display_name`):
- 候補は、各メンバーの名前の `:` の前、`:` の後、その両方をつないだもの。精度・梱包の語(`Int8`、`FP16`、`F16`、`ConvRot`、`KJ`、`AIO` など)と用途の言い回し(`Text to Video`、`Depth Estimation`、`Image Upscale` など)は除く。
- キーの語をいちばん多く含む候補が勝つ(公式テンプレートが community のテンプレートや HF のリポジトリより先、次に短いほう)。
- 含んでいるキーの語の最後のところで切り、キーにある大きさ・版・派生の語が足りなければ足す(`ACE Step v1 3.5B`、`Hunyuan Video 1.5 720p T2V`、`Image Upscale: SeedVR2 7B Int8` → `SeedVR2 7B`)。
- こうして足した版は、数字の後ろに来るなら `v` を付ける(`Kokoro-82M v1.0`)。`Wan2.1` は `Wan 2.1` と書く。
- キーの語が種類以外は同じモデルは、1 つの名前に種類を付けて共有する(`SAM 3.1 (3D)` / `(image)` / `(video)`。公式テンプレートがある種類が 1 つだけなら、その種類には付けない)。
- 数字の無い名前は、版だけが違う兄弟があれば、1 桁の版を付ける(`MoGe 3` の隣の `MoGe 2`)。
- ファイルの無いテンプレートは、モデル名の語をすべてキーに含む同じ種類のモデルが 1 つあれば、それに入る(`Frame Interpolation: FILM` → `FILM`)。無ければ、`<name> (files unknown)` という名前の独立したモデルになる。
- それでも名前がぶつかるものにはキーの語を足し(`fl2va` → `first/last frame`)、それでもだめなら `#2` を付ける。
- 小さな表 `groups.NAME_OVERRIDES`(エントリ id → 名前。題名にモデル名が無い community のテンプレートといくつかの HF のリポジトリ)が結果を上書きする。`tests/test_groups.py` が検査する。
- 名前に id は出ない。
- `genmodelfit info <モデル名>`(大文字小文字を区別しない)は、そのモデルのこの PC に合う最良のワークフローと、ほかのワークフローを出す。

モデルのうちどのエントリを出すか(`recommend.variant_key`)。絞り込みを通ったエントリの中で、次の順に決める:

0. 公式が community のテンプレートより先(`community`: テンプレートの `username` が ComfyUI でない)。公式のあるモデルを、community のワークフローが代表することはない。
1. 用途: `gen` → `edit` → `i2v` → `lipsync` → `motion` → `voice` → `upscale` → `tool`(`--task` があれば、合う用途の中で)。重みを共有する補助ツールやアップスケールのワークフローが、モデルを代表することはない。
2. **逃がさずに GPU で動くエントリ**(`fit_level` が `perfect` か `good` で `run_mode` が `gpu`。つまり Mode が `GPU` か `GPU~`。`recommend.runs_clean`)を先にする。**その中では精度の高いほうが先**、次に良い段階(llmfit と同じ。入る中でいちばん高画質。0.1.1、Issue #28)。
3. 逃がさずに動くエントリが無いときは、良い段階、次に逃がさないほう(`GPU` / `GPU~` が `GPU+RAM` より先、その次が CPU。同じ段階なら速い)、次に精度の高いほう。
4. 上の順。

精度の順: bf16 / fp16 / fp32 > fp8 > int8 > GGUF Q8 > Q6 > Q5 > Q4 / 4 ビット > それ以下。精度が不明なものは、フル精度として数える。

例:
- 24 GB では、Z-Image Turbo は int8(`perfect`)でなく bf16(`good`、`GPU~`)を出す。
- 8 GB では、bf16(逃がす、`marginal`)でなく int8(載せ替え、`marginal`)を出す。
- `--task edit` なら、代表は編集のワークフロー。

**行の値は、`score`(と `score_components`)も含めてすべて出すエントリのもの**(0.11.0。表の Mem% と合う。0.10 までは、同じ用途・同じ階層の仲間の一番高い点数だった)。`popularity` はモデルの値(仲間で同じ)。

VRAM を足しても、行が「逃がさずに動く」階層から出ることはない。その階層の中で、出す精度は上がるだけ(例外: 同じ GPU の実測が決めた段階。「Score」を参照)。行の `score` は、より重い精度・動かし方に替わると下がることがある(0.11.0)。
ただし、より高い精度で動くようになると、行の `fit_level` は `perfect` から `good` になりうる(同梱の一覧では LTX 2.3 10Eros だけ: 48 GB で nvfp4 の `perfect` → 64 GB で bf16 の `good`)。
`tests/test_groups.py` で試験している。

同梱の一覧(329 エントリ、2026-10-21)では、これで 210 モデルになる。そのうち 55 モデルは複数のエントリを持つ。

### Uses / 用途

大きな 6 つの種類を最上位とする。用途は、エントリの `tasks` から決める、種類の中の細かい分け。
`--task` と `genmodelfit types` で使う。ラベルは `types` が出すもの(`label`、`label_ja`):

| 用途 | `label`(英語のラベル) | `label_ja` | task コード |
|---|---|---|---|
| `gen`(別名 `generate`) | generate from text (3D: from an image) | 文章から作る(3D は画像から) | t2i, t2v, t2a, tts, t23d, i23d |
| `edit` | edit / transform / control | 編集・変換・形の指定 | i2i, inpaint, outpaint, relight, control, v2v, a2a |
| `i2v` | image to video | 画像から動画 | i2v, flf2v, ref2v |
| `lipsync`(別名 `lip`) | lip sync / audio to video | 口の動き・音から動画 | lipsync, a2v |
| `motion` | motion control | 動きの指定 | motion |
| `voice` | voice conversion | 声の変換 | vc |
| `upscale` | upscale / restore | 高解像度化 | upscale |
| `tool` | utility tool (depth, pose, mask, …) | 補助ツール(深度・ポーズ・切り抜きなど) | tool, estimate, matting, interp, segment, separate |

`--task` には、用途の名前、別名、task コードを、コンマ区切りで渡す(`--task edit`、`--task i2v,lipsync`、`--task t2i`)。
エントリの task のどれかが集合に入っていれば合う。
JSON の `use` は、合った用途。合うものが無ければ、エントリが持つものの中で tool、upscale、lipsync、motion、voice、edit、gen、i2v のうち最初のもの。
表の Use の列(#84)は、先頭に行自身の `use`(`--task` で合った用途。無ければ代表の用途)、そのあとに同じモデルのほかのワークフロー(`workflows`。絞り込みで隠したものも)の `use` を、重複なく上の表の順に並べたもの(例 `gen,edit`。`--task edit` なら `edit,gen`)。
`--all-workflows` では行の `use` だけ。列(9 文字)に収まらないときは、収まる最後の用途の後ろで切って `,…` を付ける(`gen,…`、`lipsync,…`)ので、行自身の用途はいつも見える。ワークフローの一覧は `info` で見られる。
0.10.0 までは、行の `use` に `+N`(ほかのワークフローの数)を付けていた。

補助ツールは、`--include-tools` か、`--task` で tool 系の用途を指定しないかぎり、`recommend` では隠す。
補助ツールとは、次のもの:
- `upscale` と `tool`
- Image / Video Tools のテンプレートのうち、題名が深度・法線・線画・ポーズ・ジオメトリのマップか、セグメンテーションのもの
- 音の分離すべて
- MoGe などのジオメトリ推定(3D Model の下にあっても)

`genmodelfit types --json` が出すもの:
- `filters`(`content`)
- `types`(`[{modality, label, label_ja, models, workflows, uses: {use: workflows}}]`。`models` は上のモデルの数、`workflows` は一覧のエントリ数。2 つの用途を持つエントリは両方に数える)
- `uses`(`[{use, label, label_ja, tasks}]`)
- `aliases`

### Fit / models の 1 件

Python の `Fit.to_dict()` は、まさにこの dict を返す(`gmf.recommend()` / `gmf.fit_list()` / `gmf.search()` は CLI の `--json` と同じ一覧を返す)。

| キー | 型 | 意味 |
|---|---|---|
| `id` | str | テンプレート名(`image_z_image_turbo_int8`)または `hf:<owner>/<repo>` |
| `name`、`modality`、`tasks` | | modality は `image` `video` `music` `sfx` `tts` `3d` のどれか。`tasks` はリスト(`t2i`、`i2v` など) |
| `content` | str | `sfw` または `uncensored`([README](../README.ja.md) を参照) |
| `source` | str | `comfyui-template` / `huggingface` / `manual` |
| `runtime` | str \| null | モデルの動かし方: `comfyui` / `piper` / `irodori-tts` など(`ModelEntry.runtime`、0.5.0)。null は不明 |
| `languages` | object | `{"prompt": [...], "speech": [...]}`。ISO 639-1(`ModelEntry.languages`、0.5.0)。キーが無ければ不明、`[]` は無し(画像から 3D のモデルはプロンプトを取らない。歌声の無い音楽のモデルに speech は無い) |
| `description` | object | `{"ja": "...", "en": "..."}`。手書きで、一部のモデルだけ(`ModelEntry.description`、0.5.0)。`{}` は無し |
| `fit_level` | str | `perfect` / `good` / `marginal` / `too_tight`(= `estimate.fit`)。大きさが不明で何も判定していないときは `unknown`(0.1.1。0.1.0 まではこれらは `too_tight` だった) |
| `fit_label` | str | `Perfect` / `Good` / `Marginal` / `Too tight` / `?` |
| `run_mode` | str | `gpu` / `offload` / `cpu` / `none`(動かない)/ `unknown`(大きさが不明で判定していない。0.2.0。0.1.1 まではこれらは `none` だった)(= `estimate.run_mode`) |
| `run_mode_label` | str | `GPU`(全部 GPU)/ `GPU~`(部品を順に載せ替え)/ `GPU+RAM`(逃がす)/ `CPU` / `-`(動かない)/ `?`(大きさが不明) |
| `level_cap` | str \| null | 動かし方が段階に上限をかけている(0.1.1): `sequential`(`GPU~`。最大 `good`)、`offload`(`GPU+RAM`。最大 `good`。0.10 まで `marginal`)、`cpu`(生成は最大 `marginal`、CPU で実時間に動く設計の読み上げ・1 回の計算の道具は最大 `good`。0.11.0、#108)。全部 GPU に載る、または動かないときは null。だから「Good、54 %、GPU~」は矛盾ではない |
| `score` | float | 0–100。メモリの余裕(下の「Score」) |
| `score_components` | object | `{"memory_fit": …}`(0.11.0。`score` と同じ値。0.10 までの `fit`・`headroom`・`readiness`・`basis`・`popularity` は無くした) |
| `popularity` | float | (0.11.0)人気の順位 0–100。同じ種類・同じ出どころ(テンプレートの使用数 / Hugging Face の全期間のダウンロード数)の中の百分位で、モデルの値。並びの鍵にだけ使い、`score` には入れない([scoring.ja.md](scoring.ja.md)「人気」) |
| `memory_kind` | str | `vram`(GPU で判定)または `ram`(CPU) |
| `memory_required_gb` | float \| null | 段階を判定した量。全部載せ・載せ替え・逃がすなら VRAM(表の VRAM の列)、CPU なら RAM。GPU が要るのに無いときは、最小の VRAM。大きさが不明なら null |
| `memory_available_gb` | float \| null | この PC の VRAM(CPU なら RAM)。GPU が要るのに無いときは `0.0`。大きさが不明でも入る(0.2.0。以前は null) |
| `utilization_pct` | float \| null | その動かし方で一番きつい資源の使用率 r × 100(表の Mem%。`score` の入力)。ふつうは `memory_required_gb / memory_available_gb × 100`。`GPU+RAM` とメモリが足りない行は、要るメモリ ÷ メインメモリのほうが大きければそちら(0.11.0) |
| `gpu_memory_required_gb` | float \| null | (0.7.1、#58)この PC の GPU で動かすのに要る VRAM。`run_mode` が `cpu`(`memory_required_gb` が RAM になる)のときも入る。すべての GPU の動かし方と `too_tight` では `memory_required_gb` と同じ値。`cpu` では `too_tight` が使うのと同じ値で、読み込み方の中で最も小さい要る量(GPU+RAM で逃がす量 `estimate.vram_offload_gb`。共有 / 統合メモリの GPU でも同じで、`cpu` と `too_tight` で意味が変わらない)。つまり「GPU があるなら少なくともこれだけ要る」。判定に GPU を使わない(CPU だけの PC、`gpu_usable=False`、使えない GPU)とき、または大きさが不明なときは null。追加のキーで、ほかのキーは変わらない |
| `ram_required_gb` | float \| null | 要るシステム RAM(= `estimate.ram_gb`。大きさが不明なら null) |
| `disk_size_gb` | float \| null | 数えたダウンロードの大きさ(部品ごとに 1 つの版。= `estimate.disk_gb`) |
| `total_size_gb` | float \| null | 載っているすべてのファイルの大きさ |
| `license` | str \| null | ファイルのうち最も厳しいライセンス(`apache-2.0`、`mit`、`other` など。null は不明)。同じ階層の中では義務の多いほう(`apache-2.0` が `bsd-3-clause` より先、その次が `mit`。`hf.OBLIGATION_RANK`) |
| `licenses` | (str \| null)[] | ファイルの重複しないライセンス(null は最後。どれかのファイルが不明) |
| `auto` | bool | 自動でおすすめに出せる(= `gate.auto`) |
| `held_reasons` | str[] | 失敗したゲートの確認(= `gate.held_by`) |
| `community` | bool | community のテンプレート(索引の `username` が ComfyUI でない。[sources.md](sources.ja.md)) |
| `estimate_confidence` | str | `measured` / `estimated`(= `estimate.basis`。細かい段階は `estimate.basis_detail`) |
| `notes` | str[] | 理由(表示の言語 `en` / `ja`。= `estimate.reasons`) |
| `files_count` | int | 載っているダウンロードのファイル数 |
| `use` | str | 出した用途(上の Uses) |
| `precision` | str \| null | 選んだエントリの精度(`ModelEntry.precision`: `bf16`、`fp8`、`int8`、`gguf-q8_0` など。null は不明で `?` と表示し、順位はフル精度と同じ扱い)。出どころは `precision_source`(次の行) |
| `precision_source` | str \| null | `header` / `filename` / `hf_api` / `config` / `workflow`(`ModelEntry.precision_source`。「Models」を参照)。null は不明(0.2.0) |
| `group_key` | str | このエントリが属するモデル(上の Models) |
| `model_name` | str | モデルの表示名(一覧の中で一意。`info` で使える) |
| `variants` | object[] | 精度ごとに、絞り込みを通った最良のエントリ: `{precision, workflow_id, memory_required_gb, fit_level, auto, disk_size_gb}`。精度の良いものが先(このエントリは必ず含む) |
| `workflows` | object[] | 同じモデルの**ほかのすべての**エントリ。絞り込みで外れたものも含む: `{id, name, tasks, use, precision, precision_source, fit_level, memory_required_gb, disk_size_gb, auto, matched}`(`matched`: 今の絞り込みを通った)。良いものが先 |
| `estimate` | Estimate | 見積もりの全体(下。`--limits` を付けると `limits` も) |
| `gate` | Gate | ゲートの結果の全体(下) |

### Score / 点数

決まりの全体は [scoring.ja.md](scoring.ja.md)「Score」(0.11.0 で変えた)。

```
r = その動かし方で一番きつい資源の使用率(utilization_pct ÷ 100)
memory_fit = 100                                     (r ≤ 0.70)
           = 100 × exp(−0.5 × ((r − 0.70) / 0.20)²)    (r > 0.70)
動く行(perfect / good / marginal)は 37.5 が下限。too_tight と unknown は 0
score = round(memory_fit, 1)
```

| 動かし方 | r |
|---|---|
| `GPU` | 全部載せの VRAM ÷ VRAM |
| `GPU~` | 順に載せ替えたときの最大の VRAM ÷ VRAM |
| `GPU+RAM` | max(逃がしたときの VRAM ÷ VRAM、要るメモリ ÷ メインメモリ) |
| `CPU` | 要るメモリ ÷ メインメモリ |
| 段階を実測で決めた行(`fit_from: measurement`) | `estimate.measured_level_vram_gb` ÷ VRAM |
| メモリが足りない行 | max(上の値、要るメモリ ÷ メインメモリ) |

- 定数 0.70・0.20 は llmfit の `fit_score` と同じ。例: r = 0.80 → 88.2、0.90 → 60.7、0.98 → 37.5。
- 段階・準備(gate)・実測の有無・人気は入れない(人気は並びの鍵。`popularity`)。
- 0.10 までの式(`0.45·fit + 0.15·headroom + 0.20·readiness + 0.10·basis + 0.10·popularity`)と内訳の鍵は無くした。

**約束**: VRAM やメモリを増やしても、段階は下がらない。メモリを増やしても `score` は下がらない。
VRAM を増やすと、代表がより重い精度・動かし方に替わって `score` が下がることはある(例: Z-Image Turbo int8 は 14 GB で GPU~ の 100、16 GB で全部載せの 82.3。0.11.0。llmfit も同じ)。
`tests/test_invariants.py` と `tests/test_scoring.py` で試験している。

**例外: 同じ GPU の実測が決めた段階**(0.9.1、0.11.0)。
- 実測で段階を決めるのは、この PC と同じ GPU(`estimate.same_gpu`。VRAM の比が 0.95 以上)だけ。作者の実測もほかの利用者の実測も同じ。
- だから VRAM がそれを超えて増える(`--vram` で量を変える)と、実測が段階を決めなくなり、段階が下がりうる(例: RTX 5080 の PC で `--vram 17`)。
- これは意図した動き(違う量の GPU の判定だから)。

## Estimate / 見積もり

| キー | 型 | 意味 |
|---|---|---|
| `vram_gb` | float | すべて GPU に載せたときの VRAM のピーク(全部載せ) |
| `vram_sequential_gb` | float \| null | ComfyUI が段階の間で部品を載せ替える(テキストエンコーダ → モデル → VAE)ときの VRAM のピーク(順に載せ替え)。ワークフローが複数の拡散モデルをサンプラー 1 つずつ順に動かす場合(Wan 2.2 14B の high_noise → low_noise。`ModelEntry.sampler_stages`)、モデルの段階では最大のもの(とその LoRA)だけを数える |
| `vram_resident_gb` | float \| null | 全部を載せたまま仕上げまで動かすときに ComfyUI が空けようとする VRAM: 固定分 + すべての重み + 一番重い段の作業用メモリ(VAE は**タイルにしない**値。#102、[accuracy.ja.md「タイル」](accuracy.ja.md#タイル))。使う量(それは `vram_gb`)ではない。VRAM の 98% を超えると全部載せは選ばず、部品を順に載せ替える(`GPU~`、段階は good まで)。段ごとの作業用メモリを当てられないモデルは `vram_gb` と同じ。`vram_gb` 以上。段階を実測で決めたとき(`fit_from: measurement`)は使わない。大きさが分からないときは `null` |
| `vram_offload_gb` | float | 重みの一部を RAM に残したとき(lowvram / dynamic VRAM)の VRAM のピーク(逃がす)。順に動かす複数の拡散モデルは、上と同じく 1 つずつ数える |
| `ram_gb` | float | 選んだ動かし方で要るシステム RAM。GPU(全部載せ / 載せ替え)= 3 + 最大の段階の重み。逃がす / CPU = 3 + すべての重み。足りないが RAM + ページファイル(固定のページファイル / スワップ。RAM の 1.5 倍が上限。システム管理または不明なら RAM の 1.5 倍)に収まるなら `marginal`(ページファイル。遅い)。それ以上は `too_tight` |
| `disk_gb` | float \| null | 数えたファイルの大きさ(部品ごとに 1 つの版。[accuracy.md](accuracy.ja.md) を参照) |
| `basis` | str | **`measured`**(実際に動かした値)か **`estimated`**(式)。必ず入る。互換のため 2 値のまま。細かい段階は `basis_detail` |
| `basis_detail` | str | (#34)値がどこまで実測に基づくか。詳しくは下 |
| `fit_from` | str | (0.7.0、#26)`estimate`(段階が見積もりから来る)か `measurement`。詳しくは下 |
| `measured_peak_vram_gb` | float \| null | (0.7.0)`fit_from` が `measurement` のときの、そろった実測の VRAM のピークの一番大きいもの(文脈の分を足す前) |
| `measured_level_vram_gb` | float \| null | (0.11.0)`fit_from` が `measurement` のときの、段階に使った量: max(ピーク + 文脈の分, 重み + 固定分)。表の VRAM の列(`m` / `c` を付ける)・Mem%・`memory_required_gb` はこの値 |
| `sample_count` | int \| null | (2026-10-22、任意。llmfit と同じ)値の元になった別々の出どころの数(0.11.0 で件数から変えた)。保守者 / この PC の実測は何行あっても合わせて 1。ほかの利用者の実測はまとめの `n_sources`(無い古い行は 1)。段階を実測で決めたときは、ID(`source_ids`)の和集合で数えた、そろった出どころの合計。実測を使わなかったとき(`basis_detail` が `estimated`)は null。`info` は `n=3` のように出す。表の VRAM の列は、実測なら誰の実測でも `m` を付ける(0.11.0 で `c` をやめた。#108) |
| `measured_on` | str \| null | `basis` が `measured` のときの、実測した GPU(GPU 無しで判定したときは null) |
| `fit` | str | 下の 4 つの段階、または `unknown`(大きさが不明で判定していない) |
| `run_mode` | str | `gpu` / `offload` / `cpu` / `none`(動かない)/ `unknown`(大きさが不明) |
| `reasons` | str[] | 人が読める理由(表示の言語 `en` / `ja`) |
| `settings` | object \| null | 実際に判定した設定(未指定の項目は、実測の有無によらず種類ごとの既定で埋める。0.11.0。それまでは実測があれば実測の設定) |
| `limits` | Limits \| null | この PC の GPU で、逃がさずに `good` に届く最大の設定(0.11.0。GPU の無い PC では null)。`info` が入れる。`fit` / `recommend` は `--limits` を付けたとき |
| `vram_free_short` | bool | GPU で動くと判定したが、今の空き VRAM(`nvidia-smi` の `memory.free`)が選んだ動かし方に要る量を下回る: ほかのアプリを閉じること。段階は全体の VRAM のまま。空き VRAM が不明(`--vram`)のときは常に false |
| `optional_parts` | object[] | (0.12.0、#81)テンプレートの既定の値で読み込まれない部品(上の量には入れていない)。1 つずつ `name`、`size_gb`(null は不明)、`optional`(`inactive` / `switch_off`。`ModelFile.optional`)、`kind`(`replace` = 使うと既定の部品の代わりに読み込む / `add` = 足すだけ)、`replaces`(代わりに読み込まなくなる既定の部品の名前)。無ければ `[]`。[scoring.ja.md](scoring.ja.md)「既定で切ってある部品」 |
| `vram_gb_all_options` | float \| null | (0.12.0、#81)スイッチで切ってある部品(`switch_off`)を全部入れたときの、判定に使う VRAM(上限。`gpu_memory_required_gb` と同じ選び方)。`switch_off` が無い・GPU が無い・大きさ不明なら null |
| `fit_all_options` | str \| null | (0.12.0、#81)そのときの段階。`switch_off` が無ければ null。`fit` と違うときだけ `reasons` に 1 文 |
| `unknown_size` | bool | 重みの大きさが不明で、何も判定していない。`fit` は `unknown`(`?` と表示。「Too tight」にはしない)、`run_mode` は `unknown`(0.2.0。以前は `none`)、VRAM / RAM の値は JSON で `null`(`Estimate.to_dict`。Python の属性は 0.0 のまま)、`limits` は null、最初の理由は「大きさが分からないため判定できません」。`recommend` はこれを落とし、`fit` / `search` は最後に並べる |

`basis_detail`(#34)。実測に近い順に並べる(`models.BASIS_DETAILS`):
1. `measured`: 同じ設定の実測。保守者 / この PC の bench の実測と、ほかの利用者の提出を `contrib aggregate` でまとめたもの(値には **1 件**の提出から使う)を分けない。出どころの数は `sample_count`
2. `calibrated`: 別の設定の実測でずらしたもの(同じ設定でも、下の上限・下限で切り詰めたもの)
3. `estimated`: 式だけ

0.11.0(#108)で `measured_community` を `measured` にまとめた。アプリは `measured_community` を見ていたら `measured` に直す(どの出どころかは `info --json` の `measurements` の `origin` に残る。印には使わない)。

2026-10-22 から(llmfit と同じ)、community の実測は**両方向**に使う(要る量を下げることも上げることもできる)。
物理的な上限だけで縛る:
- 全部載せの値は、重み + 固定の上乗せ(作業メモリ 0)より下がらず、実測した GPU の VRAM × 1.02 + 0.25 GiB より上がらない。
- GPU の上限に張り付いた値は下限。
- 別の設定に広げるときは、実測 ÷ 式の比を [0.6, 1.4] に切る(`estimate.COMMUNITY_CALIBRATION_RANGE`。llmfit は速度に [0.05, 3.0] を使う)。

ほかの利用者の実測は、同じ GPU でだけ使う。
- `estimate.same_gpu`: 両方に PCI 型番 ID があればそれ、無ければ正規化した名前。
- 0.9.1 から、VRAM の比が 0.95 以上でもある(0.10 までは 0.97)。
- 別の GPU の PC は、それが無いものとして判定する。

そのほかの決まり:
- 同じ設定では、そのような行が保守者 / この PC の行より先に来る。
- 保守者の `torch_reserved` の行は下限のまま(上げるだけ)。
- 作業用メモリだけの行(`vram_metric: torch_reserved` かつ `offload: dynamic`。#100、`estimate.working_only`)は、ここでは使わない。動的 VRAM は重みを必要なときだけ読み込み・追い出すので、重みを含まない。段ごとの山を測れるようになるまでは [accuracy.ja.md](accuracy.ja.md) の照合にだけ使う(ずらしにも段階にも使わない)。
- 上限を当てたときは、値は実測そのものではない。`basis` は `estimated` で、`basis_detail` は `calibrated`。
- `basis` が `measured` で、実測が GPU の上限に張り付いていなければ、`vram_gb` は実測の値と等しい。
- [accuracy.md](accuracy.ja.md)「ほかの利用者の実測」を参照。

`fit_from`(0.7.0、#26。条件は 0.11.0 で変えた): `estimate`(段階が見積もりから来る)か `measurement`。
`measurement` になる条件と段階(`estimate.measured_level`。詳しくは [scoring.ja.md](scoring.ja.md)「実測で段階を決める条件」):
- この PC と**同じ GPU**(`estimate.same_gpu`: PCI 型番 ID が同じ、無ければ正規化した名前が同じ。かつ VRAM の比が 0.95 以上。汎用の名前は決して合わない)の、同じ設定・同じベンダーの標準の実測。保守者の実測もほかの利用者の実測も同じ規則(0.10 までは、保守者の実測は同じベンダーで VRAM が測った GPU 以上ならどの GPU にも効いた)。
- **別々の出どころが 2 つ以上**: 保守者 / この PC の実測は何行あっても合わせて 1 つ(保守者だけでは決めない)、ほかの利用者はまとめの出どころの ID(`source_ids`)の和集合(ID の無い古い行は `n_sources` の一番大きいもの)。足して数える(`estimate.count_sources`)。ピーク + 文脈の分が重み + 固定分より小さい実測は、段階を決めない(重みを逃がして動いた記録)。
- **そろっている**: OOM の記録が無い(`outcome: oom` の行・`n_oom`・`runs_evidence: false` が無い)、まとめの行の中もそろっている(`consistent`)、行どうしの幅が中央値の 15% 以内。
- 段階に使う量 = max(そろった一番大きいピーク + 文脈の分, 重み + 固定分)(`measured_level_vram_gb`)。`torch_reserved` のピークには `estimate.TORCH_RESERVED_CONTEXT_GB`(0.5 GiB)を足す。この量がこの PC の VRAM に収まらなければ決めない。
- 段階は少なくとも Good。Perfect にするのは、その量がこの VRAM の 60% 以内で、GPU の上限に張り付いた実測が無いときだけ。
- 実測で決めた段階が見積もりの段階より良いときだけ使う(実測は段階を上げるだけで、下げない。GPU の会社によらない)。
- 実測のベンダーは、記録があればその `gpu_backend`、無ければ GPU の名前から推す。別のベンダーの GPU の実測は、較正にだけ使う(`basis: estimated`)。

実測から来る場合、動かし方は `gpu` で、理由に実測で決めたことが書かれる。
0.11.0 より前は、AMD / Intel / Apple の GPU と DGX Spark は、段階が実測から来る場合を除き Good までだった(#108 で上限をやめ、確認済みの種類の印 `platform_verified` にした)。

設定が与えられないときは、実測の有無によらず種類ごとの既定を使う(image 1024²、video 832×480 × 81 フレーム、music 30 秒、sfx / tts 10 秒。大きさの入力の無いテンプレートは大きさなし)。0.10 までは、実測のあるモデルは実測の設定で判定していた(0.11.0 で変えた。[scoring.ja.md](scoring.ja.md)「判定に使う設定」)。

実測を `basis: "measured"` として使うのは、width / height / frames / seconds / batch / precision が合い(精度は大文字小文字を区別せず比べる。精度の無い実測は、モデル自身の精度を意味する)、GPU があるときだけ。
ほかの設定では、実測でずらすだけで `estimated` のまま。
`outcome` が `ok` でないもの、`standard: false` のものは無視する。
だから既定の設定では、実測の設定が既定と違うモデル(作者の画像・動画・音楽の実測)は `calibrated` になる。実測の設定で判定するには `--width` などを付ける。
実測と係数の使い方は [accuracy.md](accuracy.ja.md) を参照。

Limits / 限界(値はすべて、逃がさずに GPU で `perfect` か `good` になる最大のもの。GPU の無い PC では `limits` が null。ほかの設定は変えない。`null` は、試した最小の値でも `good` に届かない):

| キー | 型 | 意味 |
|---|---|---|
| `level` | str | 常に `good` |
| `max_side_px` | int \| null | image / video: 縦横比を保った長辺の px(image は 256–2048、video は 256–1280 を、32 刻みで探す) |
| `max_frames` | int \| null | video: 判定した解像度でのフレーム数(4n+1。最大 241) |
| `max_seconds` | int \| null | music / sfx / tts: 秒数(5 刻み。最大 600) |
| `at_cap` | str[] | 探した範囲の端に届いたキー(本当の上限はもっと上) |

段階は、llmfit と同じく**動かし方が使う置き場所(メモリのプール)の使用率**(要る量 / 使える量。GPU なら VRAM、CPU なら RAM)で決める(llmfit の `docs/how-it-works.md`「Fit analysis」。2026-10-20 に採用):

| 使用率 | 段階 |
|---|---|
| ≤ 60% | `perfect` |
| ≤ 85% | `good` |
| ≤ 98% | `marginal` |
| > 98% | `too_tight` |

動かし方ごとの上限(0.11.0、#108。[scoring.ja.md](scoring.ja.md)「段階の上限」):
- 全部 GPU(`GPU`)は上限なし。
- 部品を順に載せ替える(`GPU~`)と RAM に逃がす(`GPU+RAM`)は最大 `good`(逃がすのは 0.10 まで `marginal`)。
- CPU は、画像・動画・音楽・効果音・3D の生成と、CPU で実時間に動くと作者が書いていない読み上げが最大 `marginal`、作者が CPU で実時間に動くと書いている読み上げ(`estimate.CPU_REALTIME_TTS`)と 1 回の計算の道具(用途がすべて `tool` / `estimate` / `matting` / `interp` / `segment` / `separate`)が最大 `good`。CPU の使用率はメインメモリ。
- GPU の会社による上限は無い(0.10 まで、NVIDIA 以外と DGX Spark は `good` まで)。

最も良い段階になる動かし方を使う(同じなら 全部載せ → 載せ替え → 逃がす)。だから逃がすのは、全部載せ・載せ替えより段階が上がるときだけ(全部載せ・載せ替えが `good` 以上なら逃がさない)。逃がす動かし方の使用率は、逃がしたときの VRAM の使用率と、要るメモリ ÷ メインメモリのきついほう。
実測で段階を決めた行は、使用率が 98% を超えても `good` になる(例外)。`perfect` は余裕があるという意味で、速さの保証ではない。
RAM の確認はそのあとに掛ける(足りないが上のページファイルの上限に収まるなら最大 `marginal`、それ以上は `too_tight`)。
ComfyUI が fp32 で計算する GPU(`compute_dtype: "fp32"`: Maxwell、一部の Pascal、GTX 16xx など)では、判定の前に作業メモリと VAE の重みを 2 倍にする([hardware.md](hardware.ja.md) の「精度」)。
GPU の判定が、GPU が無いものとした判定(CPU)より悪いときは、CPU の判定を使う(同じなら GPU)。だから VRAM を足しても段階は下がらない(例外: 同じ GPU の実測が決めた段階。VRAM の比が 0.95 以上でだけ効く。「Score」を参照)。
大きさが不明のモデル(`unknown_size: true`)は、段階の代わりに `unknown` になる。定数は `estimate.FIT_BANDS`。

**1 つのワークフローに拡散モデルが複数ある場合**(0.1.1、Issue #28):
- ComfyUI はサンプラーを順に動かす。次のサンプラーがモデルを読み込むとき、`load_models_gpu` が `free_memory` を呼び、読み込み済みで今使わないモデルを必要な分だけ降ろす(ComfyUI のコミット `426f5eea52a7ece1dd599fff4cb6cd1f352ddd24`: `comfy/samplers.py:1242` → `comfy/sampler_helpers.py:201` → `comfy/model_management.py:1001`。降ろすのは `:905` / `:919`。dynamic VRAM のモデルは必要に応じて読み込む。`:914`)。
- だから載せ替え(`GPU~`)と逃がす量は、サンプラー 1 つ分の組を 1 つずつ数える: そのモデル + MODEL 入力のファイル(LoRA)+ すべてで共有するノイズ除去の段階のファイル(ControlNet、モデルのパッチ)。
- 全部載せの量とシステム RAM は、全部を数える(降ろしたモデルは RAM に残る)。
- 組は `ModelEntry.sampler_stages`(`update` がテンプレートのグラフから読む)から来る。それが null(未確認)のときは、「主なファイルの名前が `*high_noise*` と `*low_noise*`」という規則から決める(high_noise / low_noise という名前の LoRA はその組に入り、ほかは共有)。`estimate.expert_sets`。
- 公式の Wan 2.2 14B I2V テンプレート自身が、RTX 4090D 24 GB、640×640、81 フレームで VRAM 84% と注記している。

## Gate object / ゲートの結果

毎回計算する(`db.json` には保存しない)。詳細と既定のライセンスの許可リストは [gate.md](gate.ja.md)。

| キー | 型 | 意味 |
|---|---|---|
| `auto` | bool | すべての確認が通ったときだけ true |
| `held_by` | str[] | 失敗した確認の id。下の順(`held_by[0]` を、`gate --only-held` で `held:<id>` として出す) |
| `comfyui_version` | str \| null | 比べた ComfyUI の版(`--comfyui-version`、無ければ `sources.comfyui.latest_release`) |
| `checks` | Check[] | 10 個の確認。順は `license`、`gated`、`official_workflow`、`custom_nodes`、`comfyui_version`、`url_reachable`、`sha256`、`content`、`size_known`、`reviewed`(0.6.0、#42: `review.status` が `ok` でなければ通らない。常に最後なので、古い確認の `held_by[0]` は変わらない)。`runtime` が `comfyui` でも null でもないモデル(0.5.0)では、ComfyUI の 3 つの確認を、決して通らない `app_runtime` の確認 1 つに置き換える(8 個の確認) |

Check: `id`(安定した、機械が読む名前)、`passed`(bool)、`detail`(人向けの文。表示の言語 `en` / `ja`。文言は変わりうる)。

## gate

`genmodelfit gate --json` が出すもの:
- `policy`(`license_allowlist`、`comfyui_version`、`allow_uncensored`、`max_head_age_days`)
- `filters`(`modality`、`only_auto`、`only_held`)
- `summary`(`models`、`auto`、`held`、`failing`、`first_failing`)
  - `failing` = 確認ごとに、それに失敗したモデルの数。
  - `first_failing` = 確認ごとに、最初に失敗した確認がそれであるモデルの数。
  - どちらも確認の id ごとにキーがある(`app_runtime` は 0.5.0 から、`reviewed` は 0.6.0 から)。
- `models`(`{"id", …Gate}`。全部、または `--only-auto` / `--only-held` で auto / held だけ)

## diff

`genmodelfit diff [OLD] [NEW] --json`(Python: `gmf.diff_db(old_db, new_db)`。`genmodelfit` / `command` を除いた同じ dict。#19)。
2 つの `db.json` を比べる。通信も書き込みもしない。週ごとの Pull Request に使う(`python -m genmodelfit.weekly`、[release.md](release.ja.md))。`system` / `database` は無い。

比べる相手:
- 引数なし: 同梱の一覧と、キャッシュにある取ってきた一覧(`update` / `update --from-release`)を比べる。
- 引数 1 つ: 同梱の一覧と、そのファイルを比べる(#59)。
- CLI は生の JSON を比べる(この版が知らないキーも数える。`merge-check` と同じ)。

取ってきた一覧がまだ無いとき:
- 出力は、いつものキーを空の値で持つ。`old` / `new` / `policy` / `classification` は null、`summary` はいつものキーで 0(`head_unchecked` は null)、リストは空。
- それに `note`(`genmodelfit update --from-release` を実行するよう書く)が付く。
- 終了コードは 0。

| キー | 意味 |
|---|---|
| `old`、`new` | `generated_at`、`models`(件数)、`license_policy`、`comfyui_latest_release`、`partial`、`comfyui_checked_at` |
| `policy` | **両方**の一覧に適用したゲートの方針(GatePolicy.to_dict): `comfyui_version` は `--comfyui-version`、無ければ新しい一覧に記録された版(それも無ければ古い一覧の版)。`max_head_age_days` は null(HEAD の古さは無視。HEAD のステータスコードは数える) |
| `summary` | `models_old`、`models_new`、`added`、`removed`、`license_changed`、`license_permissive_side`、`auto_old`、`auto_new`、`auto_gained`、`auto_lost`、`size_changed`、`precision_changed`、`newly_uncensored`、`head_failures`、`head_failures_new`、`head_unchecked`(新しい一覧の `sources.head_check.unchecked`)、`unauthorized_added`、`unauthorized_removed` |
| `added`、`removed` | `{id, name, modality, content, source, license}`(`license` はラベル: `mit`、`other(<name>)`、不明は `不明`) |
| `license_changes` | `(license, license_name)` が変わったエントリ: `{id, name, old, new, old_tier, new_tier, allowed_old, allowed_new, permissive_side}`。階層は `hf.license_tier`(1 緩い … 6 不明)。`permissive_side` = 階層が下がった(不明 → 何でも、を含む)、またはライセンスが新しく許可リストを通る |
| `auto_changes` | auto の結果が違うエントリ(追加・削除されたエントリも。無いほうの `old` / `new` は null): `{id, name, old, new, held_by, held_by_old}` |
| `size_changes` | 合計の大きさが 10 % 以上**かつ** 100 MiB 以上変わった(または 判明 / 不明 になった)もの: `{id, old_bytes, new_bytes, ratio}` |
| `precision_changes` | `{id, old, new, old_source, new_source}` |
| `newly_uncensored` | `content` が `uncensored` になった、または 追加された `uncensored` のエントリ: `added` と同じキー + `evidence`(`content_evidence`)、`added`(bool) |
| `head_failures` | 新しい一覧の URL のうち HEAD の答えが 200 / 302 / 307 以外のもの: `{url, status, ids, new}`。`new` = その URL が古い一覧では失敗していなかった(または無かった) |
| `unauthorized` | `{added, removed}`: `sources.huggingface.unauthorized`(401)に出てきた / 消えたリポジトリ id |
| `partial_counts` | 新しい一覧の `sources.partial_counts`(無ければ null) |
| `classification` | 0.6.0(#42): `{safe, judgement_needed, safe_changes}`。下を参照 |
| `old_label`、`new_label` | #59: 表示用の、何を比べたか: `同梱の一覧(genmodelfit <version>)`、`取ってきた一覧(<cache path>)`、または与えたままのファイルのパス。`gmf.diff_db` では、渡さなければ null |
| `field_changes` | #59: 変化のある共通のエントリすべて(安全なものも含む)の `{id: [field, …]}`。項目は `diff.entry_changes`(分類が使うのと同じ関数)の名前: 最上位のキー(`languages`、`license`、`review` など)、`files`(ファイルの追加・削除・並べ替え)、`files.<key>`(対になったファイルのキー)、`template.<key>`。ファイルの対応づけは、同じ名前のファイルが混ざらないよう段階的に行う: 同じ(name, url, sha256, directory, repo_id)→ 同じ(directory, name, sha256)→ 同じ(directory, name, url)→ 残りを(directory, name)の順で。名前や directory が変わったら、削除 + 追加のファイルとして扱う。片側に無く もう片側が null のキーは、変化としない |
| `model_changes` | #59: 安全の一覧の外の変化、または 自動のおすすめの変化がある共通のエントリ: `{id, name, modality, items, other}`。`items`(変化ごとに 1 つ)は下の形。`other`: 残りの変化した項目名(日本語では「その他の変化:」、英語では "other changes:" として表示)。安全の一覧の外で変化した項目は、すべて `items` か `other` に入る |
| `top_changes` | #59: `generated_at` / `sources` / `models` 以外の、一覧の最上位のキーの変化(生の JSON を比べるとき、つまり `genmodelfit diff` と `merge-check` のときだけ。`raw` 無しの `gmf.diff_db` は [] を返す) |
| `warnings` | #59、CLI だけ: 比較についての注意: 新しい一覧が古い一覧より古い(`generated_at`)(どの引数の形でも。引数なしのときは `genmodelfit update --from-release` の実行も促す) |
| `stats` | `sources` の変化した値を、ドット区切りのキー(`counts.modality.image`、`head_check.status.302` など)に平らにしたもの: `{key, old, new}`。リストは長さで比べる。`checked_at` / `url` / `api` / `generator` / `failures` / `errors` は飛ばす |

`model_changes` の `items` の形:
- `{kind: "value", field, old, new}`: `name` / `modality` / `tasks` / `runtime` / `comfy_family` / `content` / `precision` の変化
- `{kind: "license", old, new}`(ラベル)
- `{kind: "languages", key, old, new}`(`key` は `prompt` / `speech` など。無ければ null。無いことと空のリストの違いは項目にしない)
- `{kind: "file_added" | "file_removed", name, directory, size_bytes, license, role, source}`(`source` = HF のリポジトリ、無ければ URL のホストとパス)
- `{kind: "files_order"}`
- `{kind: "file_changed", name, field, old, new}`(`field` は対になったファイルの `url` / `sha256` / `repo_id` / `directory` / `gated` / `role`)
- `{kind: "file_license", name, old, new}`
- `{kind: "license_source", name, field, old, new}`(ライセンスを確かめた場所: エントリの `license_url`、またはファイルの `license_url` / `license_from` / `license_path` / `repo_license`。`name` はファイル。エントリなら null)
- `{kind: "size", old, new}`(合計バイト数。どんな変化でも)
- `{kind: "review", old, new, reasons, fields}`(`old` / `new` は status。確認の印が無ければ null。新しい理由。変化した review のキー)
- `{kind: "auto", old, new, held_by}`(`auto_changes` と同じ)

**classification**(0.6.0、#42)は、変化を分けて、週ごとの実行が次のどちらかを判断できるようにする。
- 持ち主なしでマージしてよい週(`safe: true`。実行側は `python -m genmodelfit.weekly merge-check` で計算し直す)。
- 持ち主のために根拠付きの Pull Request が要る週。

これは**許可リスト**。共通のエントリのすべての項目とファイルのすべての項目を比べ、下に安全と書いた変化だけが安全。ほかはすべて、この版が知らない項目も含めて、判断が要る。

| キー | 意味 |
|---|---|
| `safe` | `judgement_needed` が空なら true |
| `judgement_needed` | `[{id, name, reasons}]`。id 順(`generated_at` / `sources` / `models` 以外の最上位のキーが変わったときの `id` は `(database)`。merge-check だけ)。`reasons` は次の順(下) |
| `safe_changes` | 安全な向きで見た変化の数: `removed`、`license_stricter`、`size_changed`、`precision_changed`(diff と同じ。この中のエントリが判断が要ることもある)、`head_failures_new`、`unauthorized_added`、`unauthorized_removed`、`stats`(変化した `sources` の値)、`auto_gained_head_only`、`auto_lost`、`pending_carried`(両方の一覧で同じ pending の `review`) |

`judgement_needed` の `reasons`。この順:
- `new`: 追加されたエントリ
- `license_permissive`: エントリまたはファイルのライセンスの階層が下がった、または新しく許可リストに入った、または ゲートの license の確認が新しく通る
- `need_decreased`: ファイルが小さくなった、または見積もりの要る量が下がった(`review.review_reasons`)
- `uncensored_new`
- `files_changed`:
  - ファイルの追加・削除・並べ替え。
  - ファイルの `name`、`url`、`sha256`、`repo_id`、`directory` の変化。
  - `size_bytes` の変化(どちら向きでも。判明 / 不明 になった場合も)。小さくなったファイルには `need_decreased` も付く。
  - 大きくなる場合も数える(0.9.0 から): アプリは載っている URL をそのまま落とすので、同じ URL で大きなファイルは別の中身になる。
- `use_changed`: `tasks` / `modality`
- `auto_gained`: 新しく自動でおすすめに出せる。ただし、古い一覧が `url_reachable` **だけ**で止めていて、**かつ** どのファイルの name / url / sha256 も変わっていないときは除く。
- `review_approved`: `review` が ok でない から ok になった
- `review_pending`: ok から ok でない になった(`status` は ちょうど `ok` でなければならない。だから `"OK"` は ok でない。review 自身の理由が続く)
- そのあと、ほかの変化した項目ごとに `other_change:<field>`: `other_change:name`、`other_change:languages`、`other_change:description`、`other_change:license_url`、`other_change:precision`、`other_change:template.<key>`、`other_change:files.<key>`、同じ階層のライセンスの名前変更なら `other_change:license`、ほかの review の変化なら `other_change:review`、など

安全(許可リスト。これだけでは `judgement_needed` に入らない):
- エントリの `downloads`、`downloads_all_time`、`likes`、`updated`、`discovered_from`(見つけた場所。検索の順で変わる)
- テンプレートの `usage`
- ファイルの `head_status`、`head_checked_at`
- 厳しい階層へ動くライセンス(エントリでもファイルでも)
- 削除されたエントリ
- 自動のおすすめから外れたモデル
- 同じファイルのまま、HEAD が回復しただけで自動のおすすめに戻ったもの
- `sources` の統計
- そのまま引き継いだ pending の `review`

ほかはすべて判断が要る。

```json
"classification": {
  "safe": false,
  "judgement_needed": [
    {"id": "hf:org/new-model", "name": "New Model", "reasons": ["new"]},
    {"id": "image_example", "name": "Example", "reasons": ["license_permissive", "auto_gained"]}
  ],
  "safe_changes": {"removed": 1, "license_stricter": 0, "size_changed": 2, "precision_changed": 0,
    "head_failures_new": 3, "unauthorized_added": 0, "unauthorized_removed": 1, "stats": 12,
    "auto_gained_head_only": 1, "auto_lost": 2, "pending_carried": 0}
}
```

`python -m genmodelfit.weekly --out DIR` が書くもの:
- `DIR/db.json`
- `DIR/diff.json`(上の dict)
- `DIR/pr_body.md`
- `DIR/review_queue.json`(0.6.0: `{genmodelfit, generated_at, pending: [review list items], classification}`)
- `DIR/result.json`: `genmodelfit`、`ok`、`problems`(str[]。失敗の理由)、`base`、`offline`、`run_url`、`partial`、`status`(新しい一覧の DataStatus)、`status_old`(基準の一覧の)、`warnings`(str[])、`summary`(上と同じ)、`classification`(`{safe, judgement_needed: 件数}`)、`review_pending`(review list の項目)、`files`(`db`、`diff`、`pr_body`、`review_queue`)。`--apply` のときは `applied_to` も。

Pull Request の本文には「分類」の節と「確認待ち」の表がある(名前は、上流のすべての文と同じくエスケープする)。
終了コードは変わらない: 判断が要る週も `ok`(終了コード 0)。

`python -m genmodelfit.weekly merge-check --base <main の db.json> --head <新しい db.json> [--json]` は、2 つのファイルから classification を計算し直す(生の JSON なので、知らないキーも数える):
`{genmodelfit, command: "merge-check", base, head, safe, judgement_needed (件数), reasons ([{id, name, reasons}]), safe_changes}`。
終了コード: 0 = 安全、3 = 安全でない、1 = ファイルを読めない(`--json` では `error`)、2 = 使い方の誤り。通信も書き込みもしない。

## review

確認の印(0.6.0、#42)。新しい、またはより危ういエントリは一覧に入るが、**誰かが一次資料と照らして確かめるまで、自動のおすすめにはしない**。
`ModelEntry.review` は任意のオブジェクト(`schema_version` は 1 のまま。0.5.0 までの読み手はこのキーを捨てる):

| キー | 型 | 意味 |
|---|---|---|
| `status` | str | `ok` または `pending`(ほかの値は ok でないとして扱う) |
| `reasons` | str[] | 印を付けた理由: `new`、`license_permissive`、`need_decreased`、`uncensored_new`、`files_changed`(承認後も残す: 何を確かめたか) |
| `decided_by` | str \| null | 承認した人(`--by`。同梱の一覧は `baseline-2026-10-21` から始まる) |
| `decided_at` | str \| null | 承認の `YYYY-MM-DD`(UTC) |
| `evidence` | str[] | 一次資料(ライセンス本文、モデルカードなど)の https の URL |
| `note` | str \| null | 自由な文 |

**`review` が無い(null)のは ok の意味**(0.6.0 より前に作った一覧)。
**アプリは `entry.review is None or entry.review.status == "ok"` で絞る**(Python: `entry.reviewed`)。印の無い古い一覧は確認済みとして扱う。
ゲートはすでに pending のモデルを止める(`reviewed`。`info` では落ちた条件として出る)。

`update` が印を付ける方法(`review.apply_reviews`。引き継ぎ元の一覧 = キャッシュまたは同梱の一覧、週ごとの実行では基準の一覧と比べる):

- 前の一覧に無い id → `new` 付きの `pending`(無検閲なら `uncensored_new` も)。前の一覧が全く無い(`carry_over=False`)ときは、すべてのエントリが `new`。
- 共通のエントリは、次のどれかなら `pending`。すでに pending だったエントリには、理由を足す。
  - ライセンスが緩い方向に動いた(`license_permissive`): `hf.license_tier` が下がった、セットのライセンスが新しく許可リストに入った、またはゲートの `license` の確認が新しく通る。
  - 要る量が下がった(`need_decreased`): 基準の PC `review.REFERENCE_PCS` = (VRAM 0 GB、RAM 16 GB)、(6, 16)、(8, 16)、(12, 32)、(16, 32)、(24, 64) のどれかで、段階が上がった、大きさが判明した、または全部載せの VRAM か RAM が 1 % より大きく下がった。見積もりは、要る量に関わる項目が変わったときだけ。
  - 新しく `uncensored` になった(`uncensored_new`)。
  - ファイルが変わった(`files_changed`):
    - ファイルの追加・削除。
    - ファイルの `url`(`#fragment` だけは無視。クエリ文字列は数える)、`repo_id`、`size_bytes` の変化(どちら向きでも。判明 / 不明 になった場合も。0.9.0 から大きくなる場合も数える: 同じ URL が別の中身を返すため)。
    - 値のあった `sha256` が違う、または空になった。空だった `sha256` に値が入る(最初の確認)のは数えない。
    - ファイルの対応づけは `diff` と同じ。
- それ以外は、前の印を引き継ぐ(前の一覧に印が無ければ、メモ付きの `ok`)。

`update --heads-only` / `--fields-only` / `--inspect-only` は、実行前の一覧に対して同じ規則を適用する。
`sources.review`: `{ok, pending, marked: {reason: count}, rejected_skipped}`。

`data-latest` に公開する署名つきの一覧(data.yml)は、pending のエントリを含めない(`python -m genmodelfit.review publish`。`sources.review.withheld_pending` が数える)。genmodelfit 0.5.x は知らない `review` のキーを捨てて、それらを自動でおすすめにしてしまうため。
main にある同梱の `db.json` は、それらを残す(0.6.0 は `reviewed` の確認で止める)。

却下したエントリは入れない: `src/genmodelfit/data/review_rejected.txt`(同梱)。1 エントリ 1 行で `<id または sha256:<id の hex>><TAB><YYYY-MM-DD><TAB><理由>`、`#` はコメント。`update` は合う id を落とす(`rejected_skipped`)。`--hide-name` ではハッシュだけを保存する(安全のための除外と同じく、名前は残さない)。

コマンド(書き込みには `--db` が要る。チェックアウトでは `--db src/genmodelfit/data/db.json`):

```text
genmodelfit review list [--json] [--all] [--db PATH]
genmodelfit review approve <id> --evidence <https URL> [--evidence …] [--note TEXT] [--by NAME] --db PATH [--json]
genmodelfit review reject <id> --note TEXT --db PATH [--rejected FILE] [--hide-name] [--json]
```

`approve` は、次のときに断る(終了コード 1)。
- ホスト付きの `https` の evidence の URL が 1 つも無い(`https://` だけは断る)。
- 知らない id。
- エントリが pending でない。

承認では `reasons` を残し、`status: ok`、`decided_by`、`decided_at`(今日、UTC)、`evidence`、`note` を設定する。
`reject` は `--note` が要る。エントリを削除し、`sources.counts` / `sources.review` を更新し、行を追記する。

`--json`(`system` / `database` は無い):
- `approve` は `{genmodelfit, command: "review", action: "approve", id, db, review: {status, reasons, decided_by, decided_at, evidence, note}}` を出す。
- `reject` は `{genmodelfit, command: "review", action: "reject", id, key (id または `sha256:<hex>`), date, note, rejected_file, db}` を出す。
- 断ったときは `{genmodelfit, command: "review", error}` を出して終了コード 1。

Python: `gmf.review_list(db=None, include_ok=False)`、`gmf.review_approve(db_path, id, evidence, note=, by=)`、`gmf.review_reject(db_path, id, note, rejected_path=, hide_name=)`。
`review.ReviewError`(`ValueError`)を送出し、何も出力しない。

`python -m genmodelfit.review apply DECISIONS.json --db PATH [--max-approve 10] [--rejected FILE] [--json]` は、週ごとの決定を適用する(実行側のスクリプトが呼ぶ。Claude は決定を書くだけ)。
入力: `{"decisions": [{"id", "action": "approve"|"reject"|"hold", "evidence": [ホスト付きの https URL], "note": str, "safety": bool}]}`。
検証は厳しく、全部か無しか(終了コード 2、何も書かない)。次のものを断る:
- どの階層でも、知らないキー。
- 知らない action。
- pending でない id。
- 同じ id が 2 回。
- evidence の無い `approve`、または `safety` 付きの `approve`。
- note の無い `reject`。
- 500 文字を超える note、または 制御・書式文字(改行、NUL、ESC、双方向の上書き、幅ゼロの文字)を含む note。
- bool でない `safety`。
- `--max-approve` を超える承認。

承認には `decided_by: "claude-weekly"` が付く。`safety: true` の reject は id のハッシュだけを保存する(`--hide-name`)。
出力(`--json`): `{applied, db, rejected_file, counts: {approve, reject, hold}, pending_left, results: [{id, action, result, safety, evidence, note}]}`。すべての文字列は `weekly.md` が エスケープする(Pull Request の本文に置いても安全。safety の reject は ハッシュの先頭だけを出し、note は出さない)。
終了コード: 0 = 適用した、2 = 決定が無効、1 = `--db` を読めない。

`review list --json` / `review_queue.json` の項目には、次も入る。
- `card_url`: Hugging Face のカード。`hf:` エントリならそのリポジトリ、そうでなければ主なファイルのライセンスの出どころか置いてあるリポジトリ、それも無ければ `homepage`。
- `files`: `[{name, repo, path, url, license, license_name, license_from, license_url, role}]`(`repo` / `path` は Hugging Face の URL から取る)。

JSON の文字列はデータ(エスケープしない)。

id の重複したモデルは、どこでも断る(0.6.0、#42)。
- `parse_db` / `load` / `Database.save` は `DatabaseError` を送出する。
- `merge-check` は、id が重複したファイルに対して終了コード 1 を返す(そうしないと `get()` が 2 つのエントリの片方を黙って選ぶ)。

`review list --json`:

```json
{
  "genmodelfit": "0.12.0",
  "command": "review",
  "database": {"origin": "path", "generated_at": "2026-10-28T03:12:00Z", "models": 331, "measurements": 21},
  "pending": 1,
  "models": [
    {"id": "hf:org/new-model", "name": "New Model", "modality": "image", "content": "sfw",
     "source": "huggingface", "license": "apache-2.0", "license_url": "https://huggingface.co/org/new-model",
     "homepage": "https://huggingface.co/org/new-model", "status": "pending", "reasons": ["new"],
     "decided_by": null, "decided_at": null, "evidence": [], "note": null,
     "card_url": "https://huggingface.co/org/new-model", "files": []}
  ]
}
```

## info

出力のキー: `model`(ModelEntry)、`estimate`、`gate`、`measurements`(Measurement[])、`group_key`、`same_model`(同じモデルのほかのエントリ。それぞれ `workflows` の項目と同じ形で、この PC で判定したもの。良いものが先)、`model_name`、`community`(bool。Fit と同じ)。

`info <query>` は、モデルの表示名(大文字小文字を区別しない完全一致)も受け付ける。そのときは、そのモデルのこの PC に合う最良のワークフローを出す。

`info <query>` がモデルを選ぶ方法:

- id がそのまま合えば、内容によらず(無検閲も)常に出す。
- そうでなければ、query を id か名前の一部として探し、**`--content` で絞る(既定 `sfw`)**。ちょうど 1 件なら、そのモデル。
- 2 件以上は、選ばずに終了コード 1。テキストでは候補を stderr に並べる。`--json` は `error` と `candidates`(`[{"id", "name", "content"}]`、全件)を付けた外枠を出す。
- 1 件も合わない(または `sfw` で無検閲だけが合った)ときは、`error` を付けて終了コード 1(メッセージは `--uncensored` を勧める)。

Python の `gmf.info(model_id)` は、id の完全一致だけで探し、無ければ `None` を返す。

### ModelEntry

`id`、`name`、`modality`、`tasks`、`source`、`files`(ModelFile[])、`template`(Template \| null)、`params_b`、`precision`、`license`、`license_url`、`base_model`、`content`、`content_evidence`(str[]: 無検閲とした理由)、`discovered_from`(str[]: 見つけた場所)、`updated`、`homepage`、`downloads` / `likes`(int \| null: Hugging Face の数。`source: huggingface` に入る。`downloads` は直近 30 日)、`downloads_all_time`(int \| null: 通算のダウンロード数。入れる下限に使う)、`total_size_bytes`、`license_name`(`other` のライセンスの名前。最も厳しいファイルから)、`license_evidence`(str[]: ライセンスが不明な理由。項目は `license_unverified:` で始まる)、`files_complete`、`size_floor_bytes`(下)、`workflow_precision`(str \| null: テンプレートの主なローダのノードから推した精度。上の「Models」を参照。任意、0.2.0)、`precision_source`(str \| null: `header` / `filename` / `hf_api` / `config` / `workflow`。`precision` の出どころ。null は不明または古いデータ。任意、0.2.0)。

**一覧だけで画面を作るアプリのために**(0.5.0、#36。すべて任意で、`schema_version` は 1 のまま。古い読み手は知らないキーを捨てる。「Database file」を参照):

- `languages`(`{"prompt": str[], "speech": str[]}`。ISO 639-1 のコード): `prompt` = 文章のプロンプトが通じる言語、`speech` = 出力で話す・歌う言語(TTS の声、歌詞)。
  - キーが無ければ不明。`[]` はそのモデルに無いこと(画像から 3D はプロンプトの文を取らない。歌声の無い音のモデルに speech は無い)。
  - 適用のたびに入力から作り直す(前の一覧から引き継がない。[sources.md](sources.ja.md)「言語・動かし方・説明」)。
  - 手の表 `src/genmodelfit/data/overrides.json`(またはエントリ自身の、`src/genmodelfit/data/manual.json` にある `languages`)が、それが決めるキーでは勝つ。
  - そうでなければ、Hugging Face のカードの `language`(`card_languages` として保存)が上限になる。`speech` はカードの一覧。`prompt` はカードの一覧で、すべてのテキストエンコーダが英語だけ(CLIP / T5)のとき、および music では `en` に絞る(music のカードの `language` は歌詞の言語を並べていることが多いので、music のエントリはカードから最大 `["en"]` を得て、`speech` は無し。0.6.0、#41)。
  - カードが無ければ、既知のテキストエンコーダから `["en"]` になる(多言語のエンコーダだけで `ja` を足すことはない)。
  - 何も決まらなければ、キーを出さない(推測しない)。必要な言語が入っているかを調べること。
- `card_languages`(str[] \| null): `update` / `update --fields-only` が保存した、Hugging Face のカードの `language`(`languages` の材料。だからオフラインでの適用し直しでも同じ結果になる)。`[]` はカードを読んだが言語が無い、null は読んでいない。
- `runtime`(str \| null): 公式テンプレート、およびファイルが ComfyUI の系統に合う Hugging Face の image / video / music / sfx / 3D のエントリでは `comfyui`。手で足した読み上げのモデルでは `piper`、`irodori-tts`。null は未決定。`comfyui` 以外(と null 以外)の runtime では、推定器が `.onnx` などの ComfyUI 以外の重みのファイルも数える。
- `description`(`{"ja": str, "en": str}`): 手書きの短い説明。あるモデルだけ。
- `source: "manual"`(id は `manual:<slug>`): `src/genmodelfit/data/manual.json` に手で足したモデル(`template` は null)。ファイルごとのライセンスは、足した人が上流のカードから読んだもの(`license_evidence` が `manual:` で始まる)。`update` はそれを `license_from` のカードと比べ、食い違えば厳しいほうを取る(警告つき)。
- 手で足した部品(0.6.0、#40): `src/genmodelfit/data/manual.json` の `components` が、Hugging Face のコレクションが拾わないファイルを、別の出どころのエントリに足す(最初の例: `hf:stabilityai/stable-audio-3-small-sfx` の T5Gemma テキストエンコーダ。Gemma 利用規約)。ライセンスは手で書かない。ファイルごとの出どころの表(`license_sources.py`。行が無ければ不明)から来る。`license_evidence` は `manual_component: <このモデルがそのファイルを使う理由>` で終わる。`update` は(どのモードでも)毎回これを適用する。

**ライセンスの約束**: `license` は**すべての**ファイル(LoRA、アップスケーラ、clip_vision を含む)のうち最も厳しいライセンス。
`null` は不明で、「許可しない」として扱わなければならない。
ライセンスは、上流で確かめられたものより緩くはしない([sources.md](sources.ja.md)「ライセンス」)。

**大きさの約束**(`files_complete` / `size_floor_bytes`):

| `files_complete` | 意味 | 推定器がすること |
|---|---|---|
| `true` | 載っているファイルが全部そろっている | `total_size_bytes` を使う |
| `false` | 一覧が不完全だと分かっている(主なモデルのファイルが無い、ファイルがテンプレートの索引の `size` の 50% 未満、または GGUF の主なファイルにテキストエンコーダ / VAE が無い) | そのエントリを小さいものとして扱わない: `max(total_size_bytes, size_floor_bytes)`(`ModelEntry.estimate_size_bytes`)を使う。`size_floor_bytes` が null なら、大きさは不明(判定できない)として扱う |
| `null` | 確認していない(古いデータ) | `true` として扱う |

`size_floor_bytes` は、`files_complete` が false のときのテンプレートの索引の `size`(索引に大きさが無い Hugging Face のエントリでは null)。

`sampler_stages`(str[][] \| null、任意。0.1.1): 公式テンプレートで、各サンプラー(KSampler、KSamplerAdvanced、SamplerCustom、CFGGuider など)が MODEL 入力から読み込むモデルのファイル(拡散モデル、LoRA、モデルのパッチ)。異なる組ごとに 1 つのリストで、グラフの順(`workflow.sampler_stages`。エントリの主なファイルを含む組だけ残す)。主なファイルの組が 2 つ以上異なるときだけ設定する(`[]` は 1 つの組、`null` は未確認。HF のエントリは null)。推定器が上のように使う。

`review`(object \| null、任意。0.6.0、#42): 確認の印。「review」を参照。null は ok(古い一覧)。

`comfy_family` / `comfy_vae`(str \| null、任意。#21 で追加。無い古いファイルも読める): ComfyUI のモデルの系統と VAE の種類(`families.FAMILIES` / `families.VAES` のキー)。`update` が主なファイル / VAE のファイル名から設定する。bf16 の無い GPU で ComfyUI が fp32 で計算するかを決めるのと([hardware.md](hardware.ja.md)「系統の表」)、#100 からは段ごとの作業用メモリの式を選ぶのに使う(`working.py`。[accuracy.ja.md](accuracy.ja.md)「式」)。#100 で VAE の種類 `wan22`(Wan 2.2)・`qwen21`(Qwen Image 2.1)・`flux2`(Flux2 の VAE)・`oobleck`(ACE-Step 1.5・Stable Audio Open)・`yue2`・`pixel`(ピクセル空間)を `wan` / `kl` / null から分けた。`schema_version` は上げない(古い一覧は前の鍵のままで、推定器は系統の VAE に読み替える)。null は不明(見積もりは変えない。bf16 の無い GPU では、理由にそう書く)。null のときは、推定器がファイル名からもう一度導く。

ModelFile: `name`、`url`、`sha256`、`size_bytes`、`directory`(ComfyUI のフォルダ)、`repo_id`、`license`、`license_name`、`gated`(bool \| null。null は不明)、`role`(`main` / `text_encoder` / `vae` / `clip_vision` / `lora` / `other`)、`license_from`(ライセンスを読んだリポジトリ: 再梱包のファイルでは上流のリポジトリ、既知の部品では表の上流。リポジトリ id で、パスではない)、`repo_license`(置いてあるリポジトリ自身のラベル。上流のものと違うとき)、`license_url`、`license_path`(str[]: 確かめた導出の経路。置いてあるリポジトリ → base_model … → 決めたリポジトリ)、`license_evidence`(str[]: `license_unverified: …`、`component:<key>`、`family:<key>`、`upstream_gated: <repo>`。部品の上流が利用条件への同意を求める)、`head_status`(int \| null: リダイレクトを辿らない匿名の HEAD のステータス。`update` が記録する。null は未確認)、`head_checked_at`(その HEAD の UTC ISO-8601)、`header_precision`(str \| null: ファイル自身の見出しから読んだ精度。safetensors: 最もバイト数の多い dtype、`F32`→`fp32`、`F16`→`fp16`、`BF16`→`bf16`、`F8_*`→`fp8`、`I8`→`int8`、4 ビットを詰めた `U8` → null。GGUF: `general.file_type`、無ければ最も多いテンソルの型を `gguf-q8_0` などで)、`header_params`(int \| null: ファイルの全テンソルの要素数の合計)。
この 2 つは、Hugging Face の主な重みのファイルだけについて `update` / `update --inspect-only` が読む(`headers.py`。最初の 8 バイトと JSON の見出し、GGUF では最初の 1〜数 MiB の範囲指定 GET。重みは決して取らない)。null は未読。
主なファイルとは、`main` の役割のものと、`groups.main_files` が主なファイルとして扱うもの(どのファイルにも `main` の役割が無いときの、エンコーダ / VAE 以外で最大のファイル。例: BiRefNet。0.2.0)。
`repo_precision` / `repo_precision_source`(str \| null、任意、0.2.0): 置いてあるリポジトリから読んだ精度(`hf_api` か `config`。「Models」を参照)。見出しも名前も何も教えない最大の主なファイルにだけ設定する。
`optional`(str \| null、任意、0.12.0、#81): 公式テンプレートの既定の値で読み込まれない部品。`inactive`(無効・素通しのノードや、どの出力のノードにも届かないノードにしかない。VRAM・メモリ・`disk_gb`・`download` に数えない)か `switch_off`(値の決まったスイッチの選ばれない枝にしかない。VRAM・メモリに数えず、`disk_gb`・`download` には数える)。null は既定で読み込む(または分からない。HF・手のモデル)。知らない値は null と同じ(数える)。`optional_replaces`(str[] \| null、任意): `switch_off` の部品のスイッチを切り替えると読み込まなくなる既定の部品のファイル名(空・null は足すだけ)。決め方は [sources.ja.md](sources.ja.md)「任意の部品」。古い読み手はこの 2 つを捨て、今までどおり全部を数える。
推定器は、ファイルがどの部品かを、まず `directory`、次に `role`、次に Hugging Face のフォルダ / 既知のファイル名で決め、部品ごとに 1 つの版を数える([accuracy.md](accuracy.ja.md)「ファイルの数え方」)。

Template: `name`、`title`、`category`、`min_comfyui_version`、`open_source`、`requires_custom_nodes`、`usage`、`date`、`size_bytes`、`username`(作った人。`ComfyUI` 以外は community のテンプレート)。

### Measurement / 実測

`model_id`、`gpu_name`、`gpu_vram_gb`、`peak_vram_gb`、`peak_ram_gb`、`seconds`(実時間)、`settings`(`width`、`height`、`frames`、`seconds` = 音声の長さ、`steps`、`precision`、`batch`)、`driver`、`comfyui_version`、`offload`、`source`、`date`、`note`、`outcome`、`standard`、`vram_metric`。
さらに(#34)`origin`、`n_submissions`、`consistent`。
さらに(2026-10-22、任意)`n_used`、`n_oom`、`gpu_backend`、`gpu_pci_id`。

| キー | 型 | 意味 |
|---|---|---|
| `gpu_vram_gb` | float | VRAM の合計。bench は `/system_stats` の `vram_total` を GiB で小数 2 桁に丸めて保存する(例: `7.99`)。古い同梱の行は公称の大きさ(`16`) |
| `offload` | str \| null | ComfyUI がモデルをどう置いたか: `none`(全部 GPU)/ `dynamic`(dynamic VRAM)/ `partial`(一部だけ読み込み)/ `lowvram`(`--lowvram` / `--novram`)/ `oom`(メモリ不足) |
| `outcome` | str | `ok`(終わった)/ `oom`(メモリ不足)/ `error`(ほかの理由で失敗)。既定は `"ok"` |
| `vram_metric` | str \| null | `peak_vram_gb` の取り方: `torch_reserved`(bench: ComfyUI のプロセスの torch の確保のピーク。CUDA コンテキストは除く)、または null(記録なし。同梱の RTX 5080 の行)。較正は null の行だけを使う(`calibrate.CALIBRATION_METRIC`)。#100: `torch_reserved` で `offload` が `dynamic` の行は重みを含まない(作業用メモリだけ。`estimate.working_only`)。見積もりのずらしにも段階にも使わず、照合では作業用メモリの見積もりと比べる。それ以外の `torch_reserved` の行は重みを含む(全体の下限) |
| `origin` | str \| null | (#34)`measurements_community.json` の行(ほかの利用者の提出を `contrib aggregate` でまとめたもの)は `community`。保守者の同梱の行と、この PC の bench の結果は null |
| `n_submissions` | int \| null | community の行: グループの提出数(外れ値と OOM を含む)。ファイルには `n_outliers`、`spread_mad_gb`、`spread_rel_pct`、`peak_vram_min_gb` / `peak_vram_max_gb`、`outlier_submissions`、`submissions`(各ファイルの sha8)も残るが、読み手は無視する |
| `n_used` | int \| null | community の行: 中央値の元になった `ok` の提出数(外れ値を除く)。0.10 までは `Estimate.sample_count` だった(0.11.0 からは `n_sources`) |
| `n_oom` | int \| null | community の行: 同じグループでメモリ不足になった提出数。`ok` と `oom` のあるグループは `ok` の中央値を使い、この数を残す(理由に出す。こうした行は決して Perfect にならない) |
| `consistent` | bool \| null | community の行: 使った提出が 2 件以上で、その幅が中央値の 15% 以内で、グループに OOM が無い。係数の較正(`calibrate`)に入るのは consistent の行だけ。見積もりは、1 件の提出からの `ok` の行すべてを使う(2026-10-22) |
| `gpu_backend` | str \| null | (0.9.0)bench が見た、実測した GPU の PyTorch のバックエンド: `cuda`(NVIDIA)/ `rocm`(AMD。ComfyUI は `type: cuda` と報告するので、`pytorch_version` の `+rocm` か GPU の名前で見分ける)/ `xpu`(Intel)/ `mps`(Apple)。null は記録なし(同梱の行はすべて NVIDIA)。#26 と、ベンダーをまたぐ較正での実測のベンダーを決める(`estimate.measurement_vendor`)。null のときだけ GPU の名前を使う(Apple の MPS は名前を `mps` と報告する)。`contrib aggregate` は community の行にこれを残す |
| `files_fp` | str \| null | (#90)測ったときの一覧のモデルのファイルの指紋(`estimate.files_fingerprint`。下の「提出の流れの決まり」)。今の一覧の指紋と違えば、その実測は使わない。null は記録が無い(作者の measurements.json・古い行。今までどおり使う) |
| `settings_override` | bool \| null | (0.12.0、#116。任意)bench・measure がテンプレートの設定を変えて測ったとき(`--width`・`--height`・`--resolution`・`--frames`・`--length`・`--steps`)だけ `true`。null・無しはテンプレートのまま(キーの無い古い行も)。`measure` が、この PC でこの GPU のテンプレートの設定のまま測ったかを見分けるのに使う |
| `gpu_known` | bool \| null | (#90、#101)community の行: false は GPU 名が汎用の名前・名前の代わりの値(記録として残し、判定には使わない) |
| `n_sources` | int \| null | (#90)community の行: 値に使った別々の出どころ(提出を送った人。#113)の数 |
| `source_ids` | str[] \| null | (#95)community の行: 値に使った出どころの短い ID(送った人の鍵の sha256 の先頭 12 桁か `file:<内容の sha256 の先頭 12 桁>`)。段階を実測で決めるとき、行をまたいだ ID の和集合で出どころを数える(`estimate.count_sources`)。null は記録の無い古い行 |
| `n_error` | int \| null | (#90)community の行: 同じ組で error で終わった提出の数(値には使わない) |
| `runs_evidence` | bool \| null | (#90)community の行: false は OOM の出どころが ok の出どころ以上(「動く」根拠にしない。使い方は段階の決まり PR #95) |
| `gpu_pci_id` | str \| null | (0.9.0)実測した GPU の PCI 型番 ID `vendor:device`(`1002:744c`。型番で、バスアドレスやシリアルではない)。bench は、ComfyUI がこの PC で動いているとき AMD / Intel について書く(`hardware.local_pci_id`: Windows の WMI `PNPDeviceID` の VEN/DEV、Linux の `lspci -nn`)。community の行は、あればこれでグループにする。`estimate.same_gpu` は、両側に ID があれば ID を、無ければ正規化した名前(`estimate.gpu_key`。`AMD Radeon(TM) Graphics` のような汎用の名前は決して合わない)を比べる。0.9.1 からは VRAM の比も 0.95 以上でなければならない(`estimate.same_vram`、`MEASURED_VRAM_TOLERANCE`。0.11.0 で 0.97 から 0.95。共有 / 統合メモリには適用しない) |
| `standard` | bool | 公式テンプレートをそのまま使い(width / height / frames / length / steps / seed だけ変えた)、到達できるノードがすべて実際に動き(部分的なキャッシュが無い)、間にほかのジョブが動いていないとき `true`。既定は `true` |

`outcome` と `standard` は、既定値のある任意の項目として後から足した。無い古いファイルは `ok` / `true` として読む(`schema_version` は 1 のまま)。
見積もりが使うのは、`outcome == "ok"` かつ `standard != false` の実測だけ。
OOM の行もデータとして歓迎するが、`outcome: "oom"` を保たなければならない。

## bench

`genmodelfit bench <template> --json` は、外枠(`system` / `database` は除く)に次を足して出す:

| キー | 型 | 意味 |
|---|---|---|
| `measurement` | Measurement | 結果(保存したときは `saved_to` にも保存される) |
| `saved_to` | str \| null | JSON を書いた場所(`--output`、既定は `<cache>/measurements/<model>_<gpu>_<UTC>.json`)。保存しなかったときは null |
| `not_saved` | str | 保存しなかったときだけ: その理由(失敗した結果と標準でない結果は、`--save-failed` / `--save-nonstandard` / `--output` が無いとキャッシュしない) |
| `workflow_source` | str | `server-template` / `github-template` / `file:api` / `file:ui` |
| `warnings` | str[] | 適用できなかった設定、アップロードした入力など(表示の言語 `en` / `ja`) |
| `failed` | str | ComfyUI で実行に失敗したときだけ(OOM またはほかの実行エラー。終了コード 1)。`measurement.outcome` は `oom` / `error` |
| `timing` | object \| null | (0.13.0、#132)時間の内訳。contrib の提出の `measurement.timing` と同じキー(contrib を参照。分からない値は null) |
| `submission` | object | `--submission` か `--share` を付けたときだけ: `{saved_to, path_in_repo}`(提出用の JSON。contrib を参照)。`--share` では `share` も入る(`share --json` の `file`・`ok`・`refused`・`checklist`・`report`・`share`・`opened`・`copied`・`marked_shared`。ブラウザは開かない) |

bench が Measurement を埋める方法(測り方):

- `peak_vram_gb`: `/system_stats` の `devices[0].torch_vram_total` を 0.2 秒ごとに読んだ最大値(ComfyUI のプロセス。CUDA コンテキストは除く)。
  - 実行前の基準からの GPU 全体の差は `note` に入れる。NVIDIA は `nvidia-smi`。
  - ほかの GPU(0.9.0、`gpustat.py`)は、`amd-smi` → `rocm-smi`(AMD、Linux)、`xpu-smi`(Intel、Linux)、`typeperf` 経由の Windows のパフォーマンスカウンタ(AMD / Intel、Windows)、`ioreg`(Apple)。
  - 同じ道具で、実行前の使用中の確認もする(平均使用率が 30% 以上なら、`--force` が無いと断る)。
  - どの道具も読めないときは、bench は止まらない。ComfyUI のキューが空であること、ComfyUI 以外のアプリがデバイスメモリの 50% 以下しか使っていないこと(Apple の統合メモリでは確認しない)を確かめてから測り、警告と `note` の文を残す。`standard` は変えない([accuracy.md](accuracy.ja.md))。
  - リモートの ComfyUI は、やはり `--force` が要る。
- `peak_ram_gb`: ポートで待ち受けているプロセス(とその子)の常駐メモリの最大値。コマンドラインに `main.py` か `comfy` を含むときだけ。リモートまたは特定できなければ null。
- `seconds`: 投入から完了までの実時間。モデルの読み込みを含む。`/free` は `--free` のときだけ最初に送る。無ければ、実行前の torch の確保量を `note` に書き、警告を出す。
- `offload`: ComfyUI のログから読む: `dynamic` / `partial` / `lowvram` / `none` / `oom`、不明なら null。
- `--set` の値: 数だけを `note` に書く。ほかの値は、入力の名前だけを残す。
- 保存するファイルは `{"measurements": [Measurement]}` で、パス、ユーザー名、プロンプトは含まない。

## measure

`genmodelfit measure [MODEL] --json`(#113)は、外枠(`system`・`database` を含む)に `action` などを足して出す:

- MODEL を省くと `action: "suggest"`: `comfyui` `{url, found_by, start?}`(`found_by` は `--url` なら `option`、127.0.0.1 の
  8188・8000 で見つけた ComfyUI なら `running`、見つからなければ null。`start`(0.13.0)は、起動中のものが無く、入って
  いるものを起動するときの `{kind, comfyui_dir}`。`kind` は `portable`・`venv`・`desktop`)と `suggestions`
  [{model_id, model_name, name, modality, precision, fit_level, installed, download_bytes, total_bytes, seconds,
  seconds_basis, settings, args}]。ここの `installed` は状態の文字列だけ(`yes` / `no` / `unknown`。下の「入っているか」)。
  MODEL を付けたときはオブジェクト。`settings`(0.12.0、#116)はテンプレートのままなら null、設定を変えた測定なら
  変えた設定(`{width, height}`・`{frames}`・`{seconds}`)。`args` は同じものを `measure`・`bench` の引数にしたもの
  (`["--resolution", "1536x1536"]`。テンプレートのままなら空)。測らない。
- MODEL を付けると(`--yes` が要る)`action: "measure"`: `model_id`・`settings`(0.12.0。`--resolution`・`--frames`・
  `--length` で指定したか一覧で選んだ設定。テンプレートのままなら null)・`comfyui`・`installed`
  `{state, missing_bytes, total_bytes, missing_files}`・`time_hint` `{seconds, basis}`、続けて `bench` の鍵
  (`measurement`・`workflow_source`・`warnings`・`failed?`・`saved_to`・`not_saved`)、`submission`
  `{saved_to, path_in_repo}`(提出を書かなかったときは null)、`hint`(`genmodelfit share` を案内する 1 行。無ければ null)。
  失敗したときは `time_hint` までの鍵と `error`。

決まり:

- **提案**: ComfyUI の公式テンプレートのモデル(`source: comfyui-template`)で、この PC の判定が `perfect` か `good` で
  動かし方が `gpu`、ライセンスが分かり(`share` の `--any-license` でほかも出す)、この PC でこの GPU のテンプレートの
  設定のまま、まだ測っていないもの(0.13.0: 数えるのはこの PC の結果だけ。`measure.measured_by_this_pc`: キャッシュの
  bench の結果と保存した提出。同梱の作者・ほかの利用者のまとめは数えず、時間の目安と見積もりにだけ使う。
  `measure.has_measurement_here`:
  `estimate.same_gpu` で同じ GPU、`files_match` で同じファイル、標準の条件、結果が `ok` か `oom`、`settings_override`
  が無い(0.12.0。キーの無い行は数える)。作業用メモリだけの実測も数える。同じ GPU で測り直しても材料が増えないため)。同じモデル(`groups` の鍵)は 1 件: 入っているワークフロー、無ければ `recommend` が
  選ぶもの。並びは 入っているもの → `seconds` → `download_bytes` → 名前。既定 10 件(`--limit`)。
- **設定を変えた測定**(0.12.0、#116。`measure.variants`): 予測式の段ごとの作業用メモリを確かめるため、同じ一覧・
  同じ並びに混ぜる。文章から画像(`t2i`。`estimate.NO_SIZE_INPUT` に無いもの)は 512・768・1024・1536 px の正方形
  (`VARIANT_PIXELS`)、文章・画像から動画(`t2v`・`i2v`)は 17・33・49・81 コマ(`VARIANT_FRAMES`。大きさはテンプレート
  のまま)、文章から音楽・効果音(`t2a`)は 10・30・60・120 秒(`VARIANT_SECONDS`)。#102 の台本にならった。その設定の
  見積もりが `gpu` で `perfect` か `good` で、この PC でこの GPU のその設定をまだ測っていないもの(`has_measurement_here` の
  `settings`: 指定した項目が同じなら同じ設定とみなし、ほかの項目は比べない。`settings_override` は問わないので、
  1024 px のようなテンプレートの値は重ねて出さない)だけを出す。出すのは、この PC でこの GPU のそのワークフローをテンプレートの
  設定のまま測ったものだけ(先にそれを測ってもらう。設定を変えた実測ではテンプレートのままの行は消えない)。
  テンプレートにその入力が無い項目も出さない: テンプレートのままの実測(`measure.template_settings`。どの GPU でも)に
  幅・コマ数・長さが記録されていなければ、その項目の測定は出さない(コマ数を `batch_size` で決める AnimateDiff など)。
  動画は、その実測の幅・高さで判定する(`measure.judged_settings`。無ければ種類ごとの既定 832x480)。同じモデル・同じ
  設定は 1 件(選び方は上と同じ)で、並べたあと 1 つのモデルに `measure.VARIANTS_PER_MODEL`(2)件まで。1 つ測ると
  次が出る。
- **時間の上限**: 時間の目安が `measure.MAX_SUGGEST_SECONDS`(300 秒)を超える行は出さない(`measure` は 1 回 1〜5 分)。
  動画の設定を変えた測定で、時間の目安が種類ごとのおおよその値(`basis: typical`)のときは
  `measure.TYPICAL_VIDEO_FRAMES_MAX`(33)コマまで。
- **書けない設定**: 設定を付けて測るとき(`--resolution`・`--frames`・`--length` か一覧で選んだもの)、measure は bench を
  `strict_settings` で動かす: `apply_settings` のあとのワークフローに頼んだ値が無ければ(`workflow.effective_settings`。
  入力が無い・ほかのノードから来る)、生成の前に誤りで止め、何も測らない。測ったあとの設定がそれでも違えば `warnings` で知らせる。
- **入っているか**(`installed.state`): ワークフローのファイルがすべて、一覧の大きさで models のフォルダのどれか
  (`--comfyui-dir`、無ければ `download` と同じ決め方で ComfyUI に尋ねずに決めたフォルダ、と起動中の ComfyUI が
  答えたフォルダ)にあれば `yes`、無ければ `no`(`missing_bytes` は `download` が落とす量)、フォルダが見つからない・一覧にファイルが無ければ
  `unknown`。sha256 は計らない(download が確かめる)。
- **時間**(`seconds`。10 秒単位に切り上げ): 同じワークフローのどの GPU でもよい `ok` の実測の `seconds` の中央値
  (`basis: measured`)、無ければ種類ごとのおおよその値(`basis: typical`。`measure.TYPICAL_SECONDS`: image 60・
  video 300・music 90・sfx 60・tts 60・3d 120)。目安だけで、落とす時間は入れない。設定を変えたときは、それぞれの
  実測の時間を、変えた項目の比(画素数・コマ数・長さ。`measure.work_ratio`。値の無い項目は種類ごとの既定)で
  伸び縮みさせてから中央値を取り、おおよその値は種類ごとの既定の設定から伸び縮みさせる。ステップ数は変えないので
  比に入れない。読み込みの時間も同じ比で動くので、小さい設定では短めに出る。
- **MODEL** は `download` と同じ探し方(`downloader.resolve_candidates`)で、テンプレートのあるワークフローだけに
  絞る。ID がそのまま合えばそれ、名前なら `recommend` が選ぶワークフロー。
- 送るための下書きは `<cache>/submissions/<GPU>/<date>-<sha8>.json` に書く(標準の条件で結果が `ok` か `oom` の
  ときだけ)。`share` がこれを一覧に出す。結果は `bench` と同じく `<cache>/measurements/` に書く。
- **ComfyUI の起動**(0.13.0、#124。`comfystart.py`): 起動中の ComfyUI が無ければ(`--url` も無ければ)、`download` と
  同じ見つけ方(`--comfyui-dir`・`GENMODELFIT_COMFYUI_DIR`・`genmodelfit config`・よくある場所)で見つけたものを別の
  プロセスで起動する: ポータブル版は `<root>/python_embeded/python.exe -s <root>/ComfyUI/main.py`、手で入れたもの・
  comfy-cli は `.venv`・`venv` の python(venv が無ければ起動しない。ほかの python を当て推量で使わない)、ComfyUI
  デスクトップ版は `<basePath>/.venv` とアプリの `resources/ComfyUI/main.py` とデスクトップ版のフォルダの引数(両方が
  見つかったときだけ。アプリ自体は起動しない)。いつも `--listen 127.0.0.1 --port <空いているポート> --disable-auto-launch`。
  環境から `GITHUB_TOKEN`・`GH_TOKEN` を外す。`GET /system_stats` で準備を待ち(`--start-timeout`、既定 300 秒)、
  終わったら(失敗・Ctrl+C でも)そのプロセスの木だけを止める(Windows は `taskkill /T /F`、POSIX はプロセスグループ)。
  「測りますか?」の質問が同意を兼ねる。`--no-start` か `GENMODELFIT_COMFYUI_START=0` で止める。JSON は、起動の前は
  `comfyui.start`、起動したあとは `comfyui.started` `{kind, comfyui_dir, url, log}`。
- **残さない・2 つ同時にしない**(0.13.0): Windows では起動した ComfyUI を `KILL_ON_JOB_CLOSE` の Job Object に
  入れるので、端末を閉じても genmodelfit が殺されても止まる。Linux では `PR_SET_PDEATHSIG` も付ける。POSIX では
  端末を閉じた(SIGHUP)ときも Ctrl+C と同じく止める。制限: macOS で genmodelfit が SIGKILL で殺されると止められない。
  測るのは同時に 1 つの `measure`・`share` だけ(`<cache>/measure.lock` にプロセスの番号。そのプロセスが無ければ
  取り直す)。127.0.0.1 の 8188・8000 で ComfyUI が答えるのにフォルダを読めないときは、2 台目を起動しない(`--url` で
  使う)。起動中のサーバーが答えたフォルダは、あってシステムのフォルダでないときだけ、落とす場所に使う。
- **足りないノード**(0.13.0): ComfyUI に届いたら `/object_info` を 1 回読み、テンプレートごとに確かめる
  (`workflow.missing_nodes`。`bench` と同じ確かめ。`bench.NodeCheck`)。一覧からは、その ComfyUI に無いノードを使う
  提案を外す。名前で指定したものは、落とす・測る前に止め、「次にすること:」で ComfyUI の更新(かカスタムノード)を言う。
- 終了コード: 0 成功・選ばなかった・出す提案が無い・「測りますか?」に n と答えた・落とすのを断った・送れないだけの
  結果 / 1 ComfyUI が無い・GPU が無い・GPU が使われている・ほかの `measure`・`share` が測っている・テンプレートが
  無い・ノードが足りない・`--json` で入っていない・端末でなく `--yes` も無い・ComfyUI を起動できない・実行の失敗
  (実行中の Ctrl+C も) / 2 使い方の誤り / 130 質問で Ctrl+C。テキストの出力の失敗は、最後に
  「次にすること:」の行で何をすればよいかを出す。

## share

`genmodelfit share`(0.13.0、#124)は測って GitHub の PR で送る。`share --issue` は Issue のフォームの前からの送り方
(#113)。`contrib share` は `share --issue` と同じで、`command: "contrib"`・`action: "share"`。

### 測って送る(`share`・`share --no-measure`・`share FILE`)

`--json` には `--yes` が要る(`--dry-run`・`--logout` を除く)。外枠に `system`・`database` は無く、次を足す:

| キー | 型 | 意味 |
|---|---|---|
| `action` | str | `send`(`--logout` は `logout` で、`removed` を付ける) |
| `no_measure` | bool | `--no-measure` か FILE |
| `comfyui` | object | `measure` と同じ(`url`・`found_by`・`start?`・`started?`) |
| `budget_seconds` | float | `--budget`(既定 300。`measure.SHARE_BUDGET_SECONDS`) |
| `plan` | Suggestion[] | 測る、入っている提案(`measure` の `suggestions` の項目) |
| `download_plan` | Suggestion[] | 入っていない提案のうち、残りの時間に収まるもの |
| `skipped` | object[] | `{model_id, name, missing_nodes}`: ComfyUI に無いノードが要るので外したもの |
| `pending` | object[] | 測る前の、送っていない実測(`share --issue --json` の `submissions` の項目) |
| `measured` | object[] | `{model_id, settings, outcome, peak_vram_gb, seconds, submission}` |
| `failed` | object[] | `{model_id, settings, error}`(ComfyUI を起動できなかったときは `model_id` が null) |
| `interrupted` / `downloaded` | bool | Ctrl+C で止めた / `download_plan` を落とした |
| `download_hint` | object | 尋ねられない(`--yes`・`--json`・端末でない)ので落とさなかったときの `{count, bytes}` |
| `reason` / `reasons` / `next_step` | str / object[] / str | 送るものが無いときだけ(0.14.0、#146): 1 つの文、理由ごとの `{code, text}`、次にできることの 1 行。`code` は `no_measure`・`measured_here`(入っていて載るモデルは、この PC でもう測ってある)・`installed_unknown`(models の場所が分からない)・`missing_nodes`(ComfyUI を更新する)・`budget`・`download_declined` / `not_downloaded`(`share --download`)・`failed`・`unsendable`・`interrupted`・`failures_sent`(同じ失敗の記録はもう送ってある)・`no_unsent`(いつも最後。送ったファイルがあれば `share --no-measure --all` を書く) |
| `files` | object[] | 点検したファイルごとの `{file, path_in_repo, ok, refused}` |
| `prs` | object[] | GPU ごとの `{url, reused, branch, fork, uploaded, skipped, on_main}` |
| `marked_shared` | int | 送った印を付けた数 |
| `sent` | bool | PR を作ったか、足した |
| `github` | object | `{login, source}`(`source` は `env:GITHUB_TOKEN`・`env:GH_TOKEN`・`cache`・`device`)。トークンは入れない |
| `method` / `issues` | str / object[] | Issue で送ったときの `issue` と、ファイルごとの `share --issue --json` のオブジェクト |
| `hint` | str | PR で送れなかったとき: 保存した結果をあとで送る方法(`share --no-measure`) |
| `dry_run` | bool | `--dry-run` のときだけ |
| `error` | str | 失敗したとき |

決まり:

- **ComfyUI** は `measure`(上)と同じく見つけ・起動し・確かめる。無ければ(送っていない実測も無ければ)計画と ComfyUI の
  起動のしかたを出し、尋ねず・ログインせずに 1 で終える。送っていない実測があれば、それを送るかに進む。
- **計画**: `measure.suggestions` のうち `installed.state == "yes"` のものを、その順に、`seconds` の合計が上限に収まる
  あいだ取る(`measure.share_plan`。収まらない行は飛ばして次を見る)。ComfyUI に届けば、その ComfyUI に無いノードを
  使う提案を外し(`skipped`)、次の提案で埋める(`download_plan` も)。起動する ComfyUI なら起動したあとで行い、新しい
  計画を見せる。
- **順**: 合計の時間を付けた計画 → 1 回の `[Y/n]`(測ることと ComfyUI の起動を兼ねる。`--yes` で省く)→ もうある
  トークンを確かめる → ComfyUI の起動 → 1 つずつ測る(`_measure_run`。`measure` と同じ。失敗しても残りは続け、Ctrl+C
  なら送るかに進む)→ ファイルのすべてと個人の情報の点検を見せる → 送り方を選ぶ(`1) PR 2) Issue q) 送らない`。
  既定は 1。`--yes`・`--json`・端末でないときは PR、`--issue --yes` は Issue)→ 要ればログイン → PR か、ファイルごとに
  Issue のフォーム(`_share`。`share --issue` と同じ。端末でなければ URL を出す)。
- **合計の時間**(計画に出す): 起動するなら ComfyUI の起動(`comfystart.TYPICAL_START_SECONDS`、45 秒)、時間の目安の
  合計、落とすことになっていれば、それを測る時間と 100 Mbps で落とす時間(`cli.DOWNLOAD_EXAMPLE_MBPS`。例で、
  「回線しだい」と添える)。落とすかの質問にも同じものを出す。
- **毎回空ける**: `share` は 1 つの ComfyUI で何件も測るので、毎回測る前に `/free`(`unload_models`・`free_memory`。
  ComfyUI はモデルを外し、ノードの結果のキャッシュを捨てる)を送る(`bench --free` と同じ)。起動中だった ComfyUI では
  計画にそう書く(「測る前に毎回 ComfyUI のメモリを空けます」)。`measure` は自分で起動した ComfyUI では空け、利用者の
  ものは `--free` のときだけ(0.13.0)。それでも前の結果を使ったなど標準でない条件の結果は、保存も送りもせず、理由を
  付けて `failed` に入れる(黙って落とさない)。
- **送れないもの**(0.13.0): 提出を書く前に `share` と同じ点検(個人の情報の点検と `validate` の誤り)をする。送れない
  (点検の範囲を外れる値など。#132 からは、GPU の全体より大きい最大 VRAM は `spilled` を付けて送る)なら、この PC の実測だけを残し、理由を付けて `failed` に入れる。保存してある
  送れない提出は 1 回だけ出して `<名前>.unsendable` に名前を変える(次から出さない。JSON の `retired`)。ほかを送れた
  なら、それで `share` を失敗にしない。
- **GPU と VRAM**: 判定に使う GPU(backend が `cuda` / `rocm` / `xpu` / `mps`。`SystemInfo.best_gpu`)ならどれでも
  測る。止めるのは使える GPU が無いときだけ(0.13.1。0.13.0 は誤って NVIDIA だけで測っていた)。
  `measure.suggestions` はライセンスの分からないモデルを出さない(`--any-license` で出す)。ほかのプログラムが VRAM を
  1.5 GiB か全体の 20% より多く使っていれば測らない(`bench.others_vram_limit`。NVIDIA は ComfyUI を起動する前は
  nvidia-smi、空けたあとは `/system_stats` で見る。AMD(ROCm)は空けたあとの `/system_stats` だけで見る(ComfyUI が
  GPU 全体の空きを出す))。Intel(XPU)・Apple(MPS)の `/system_stats` にはほかのプログラムの分が出ないので確かめず、
  `note` にそう書く(`bench.OTHERS_VRAM_UNCHECKED`)。NVIDIA では、測っているあいだに GPU を使い始めた計算だけの
  プロセス(nvidia-smi の表の種類 C。ブラウザなど C+G・G のアプリは数えない)があれば、標準でない結果にする。
  ほかの会社にはその表が無いので見張らず、`note` にそう書く(`bench.GPU_APPS_UNCHECKED`)。測る前に GPU が空いて
  いるかは bench の会社ごとの確かめ方による([accuracy.ja.md](accuracy.ja.md)「NVIDIA 以外の GPU の bench」)。
- **測り直さない**: この PC に保存した提出(`<cache>/submissions/`。送ったかどうかによらない)を、提案では実測として
  数える(`measure.suggestions(extra=...)`)ので、あとの `share` は測り直さずにそれを送る。結果は送る前に保存する。
  `q`・ログインの失敗・PR の失敗のあとも送っていないまま残り、`share --no-measure`(か `--issue`)で送れる。端末で PR に
  失敗したら、同じファイルを Issue で送るかを尋ねる。
- **落とす**: `--yes`・`--json` だけでは落とさない。`--download` か、1 回の `[y/N]` の質問(モデル・量の合計・時間を見せる)
  に y のときだけ。尋ねるのは、使える入っている提案が無ければログインと ComfyUI の起動の前、入っているものがどれも
  ノードが足りなければ起動のあと、ほかは測り終えたあと。落とすのは `download` で、その確かめと同意の決まりのまま
  (`--any-license` は渡す。提案に無検閲のものは出ない)。尋ねないときは `download_hint` で `--download` を案内する。
- **ログイン**: 測る前は、もうあるトークン(`GITHUB_TOKEN`・`GH_TOKEN`、無ければ保存したもの)を確かめるだけ(使えなければ
  止める。無いときは、`--yes`・`--json`・端末でないなら `--issue` を除いて測る前に止める)。ログインするのは PR を
  選んだとき: `GITHUB_TOKEN`・`GH_TOKEN`、保存したトークン(`<config>/github_token` の
  `{access_token, expires_at}`。期限切れか 401 なら消す)、デバイス認証(client id は `github.CLIENT_ID`。
  `GENMODELFIT_GH_CLIENT_ID` で替え、空にすると使わない。scope は `public_repo`。端末で `--json` の無いときだけ。
  `slow_down` で間隔を 5 秒増やし、`expired_token`・`access_denied` で止める)。refresh_token は保存しない。送る前にもう一度確かめ、切れて
  いれば端末ならログインし直し、そうでなければ、測った結果は保存してあり `share --no-measure` で送れることを言って 1 で終える。
- **PR**: `laxshmate/genmodelfit` をフォークし(無ければ作り、出来るまで待つ)、本家の `main` とフォークの `main` の
  共通の祖先(`github.fork_base`。compare の API の `merge_base_commit`。比べられなければ本家の先頭。`merge-upstream` は
  `workflow` の scope が要るので、フォークの `main` は変えない)からブランチ `genmodelfit-share/<GPU の slug>/<パスの sha8>` を作り、ファイルを `data/contrib/<GPU>/<date>-<sha8>.json`(contrib の
  `suggested_path`)に Contents API で置いて、題が `measurements: <GPU> (<件数>)` の PR を開く(本文はファイルの表・
  genmodelfit の版・`<!-- genmodelfit:share -->`)。GPU ごとに 1 本。同じアカウントの開いている PR でブランチが
  `genmodelfit-share/<GPU の slug>/` で始まるものがあれば、そこに足す。本家の `main` にもうあるファイルは先に確かめて
  置かない(送った印を付ける)。ブランチにもうあるファイルは飛ばす(422)。PR が途中で失敗したときは、もう置いたもの
  (ほかの GPU の PR・開いている PR やブランチに置いたファイル)はそのままにし、残りのファイルだけを Issue で送るかを尋ねる。
  ブランチに本家の `main` に無いファイルがあれば、前の失敗のやり直しでも PR を開く。送った印を付けるのは、PR
  (新しいか、足したもの)のファイルと、本家の `main` にもうあるファイルだけ。
- **誤り**: 書き込みの 403・404 は、トークンの scope(`public_repo`)が足りない(作ったばかりのフォークは、先に git の
  データを待つ)。`Retry-After`・`X-RateLimit-Remaining: 0`・「rate limit」の付いた 403・429 は、60 秒以下なら 1 回
  待ち、ほかは待つ時間を言って 1 で終える。開いている PR はページを追って読み、読めなければ 2 本目を開かずに止める。
- `--yes` の無い `share --json`(上のフラグも無いとき)と `share FILE --json` は、`--issue` の出力のまま(使っていた
  アプリのため)。FILE と `--no-measure`・`--download` は使い方の誤り(2)。
- 終了コード: 0 送った・送るものが無い・n と答えた / 1 ログインできない・ComfyUI が無く送るものも無い・測れなかった
  ものがある・送れないファイルがある・GitHub が受け付けない・`--json` か端末でないのに `--yes` が無い / 2 使い方の誤り /
  130 質問で Ctrl+C、または測る途中の Ctrl+C(そこまでのものを送るかに進んだあと)。送れないだけの結果では 1 に
  しない。

### Issue のフォーム(`share --issue`)

`genmodelfit share --issue [FILE] --json`:

- FILE を省くと: `listed: true`・`folder`・`submissions` [{file, sha256, shared, date, model_id, gpu, outcome,
  peak_vram_gb, seconds}](送っていないものを新しい順。`--all` なら送った印のあるものも)。何も開かない。
- FILE を付けると: `listed: false`・`file`・`ok`・`refused`(送れない理由)・`checklist`・`report`・`share`
  (`{path_in_repo, title, issue_url, issue_url_length, url_limit, form_url}`。送れないときは null)・
  `opened`・`copied`(`--json` では false)・`marked_shared`。

決まり:

- 点検は `contrib show` と同じ(全項目・個人の情報のチェックリスト・`validate` の結果。ファイル名は見ない)。
  チェックリストに NG があるか `validate` の誤りがあれば送れない(終了コード 1)。注意と確認では断らない。
- `issue_url`: `https://github.com/laxshmate/genmodelfit/issues/new?template=measurement.yml&title=<題>&submission=<中身>`
  (Issue のフォーム `.github/ISSUE_TEMPLATE/measurement.yml`。`submission` はその JSON の欄の id、`contrib.ISSUE_FIELD`)。
  中身はファイルのまま(改行は LF)を URL エンコードしたもの。題は `measurement: <model_id> on <GPU 名>`。
- URL が 8000 字(`contrib.SHARE_URL_LIMIT`)を超えるときは `issue_url` を null にし、`form_url`(題だけを入れた同じ
  フォーム)を開いて、中身を OS の道具でクリップボードに写す(Windows は `clip`、macOS は `pbcopy`、Linux は
  `wl-copy` / `xclip` / `xsel`。標準入力で渡し、シェルを通さない)。道具が無ければ、テキストの出力で中身を 2 つの
  `---` の行の間に出す(ファイルの場所はその中に入れない)。
- ブラウザを開くのは端末(標準入力と標準出力がどちらも端末)で、`--json`・`--print-url` が無いときだけ。尋ねない。
  `--print-url`(`--issue` のときだけ)は URL だけを出す。
- 送った印(`<cache>/submissions_shared.json`。中身の sha256 → 日付)を付けるのは、ブラウザでフォームを開いたときと、
  `--print-url` で URL を出したときだけ。`--json` は見るだけで、`--mark-sent` があるときだけ印を付ける(フォームを自分で
  開くアプリ向け)。端末でないときのテキストの出力・ブラウザを開けなかったときも付けない。
- FILE の無い `--print-url` は、送っていない実測が 1 つだけならそれを使う。無い・2 つ以上なら終了コード 1 で FILE を
  求める(ファイルは標準エラーに並べる)。
- 通信しない。中身が送られるのは、利用者がフォームで「Create」を押したときだけ。

## contrib

利用者の実測の提出(#34)。
genmodelfit は、これを**勝手には決して送らない**。利用者が `share` で同意し、`share` が利用者のアカウントから Pull Request を開く(#124)か、`share --issue` の開いた実測の Issue のフォームで「Create」を押す(ボットが Issue を Pull Request にする。`contrib intake`、#113)か、自分のフォークの `data/contrib/` にファイルを足して、自分で Pull Request を出す([CONTRIBUTING.md](../CONTRIBUTING.ja.md))。
コードは `src/genmodelfit/contrib.py`。

動的 VRAM の bench の提出(`vram_metric: torch_reserved`・`offload: dynamic`)は作業用メモリだけを持つ(#100)。まとめて残すが、段ごとの山を測れるようになるまでは照合だけに使う(見積もりのずらしにも段階にも使わない。「Measurement」と [accuracy.ja.md](accuracy.ja.md))。

### Submission file (`contrib_schema` 1・2・3) / 提出ファイル

`genmodelfit share`・`measure`、`genmodelfit bench … --submission PATH` または `genmodelfit contrib make --from <bench の結果>` が書く。
リポジトリでは `data/contrib/<gpu-slug>/<date>-<sha8>.json` に置く。
- `gpu-slug` は、GPU の名前の `estimate.gpu_key`(小文字。`NVIDIA`、`GeForce`、`(R)`、`(TM)`、`™`、`®` は除く。ほかの記号は `-` に。例: `rtx-3070`)。
- `sha8` は、正規化した JSON(キーを並べ替え、空白なし、UTF-8)の sha256 の最初の 16 進 8 桁。だから名前はインデントによらず、同一のファイルは衝突する。

```json
{
 "contrib_schema": 2,
 "kind": "genmodelfit-measurement",
 "tool": {"name": "genmodelfit", "version": "0.12.0"},
 "date": "2026-10-20",
 "system": {
  "os_family": "windows",
  "ram_total_gb": 32,
  "gpu": {"name": "NVIDIA GeForce RTX 3070", "vram_gb": 8.0, "arch": "ampere", "compute_capability": "8.6", "driver": "566.03"}
 },
 "comfyui_version": "0.40.0",
 "measurement": {
  "model_id": "image_sdxl_simple",
  "settings": {"width": 832, "height": 832, "steps": 20},
  "outcome": "ok", "standard": true, "offload": "dynamic",
  "peak_vram_gb": 6.9, "peak_ram_gb": 7.4, "seconds": 41.2,
  "vram_metric": "torch_reserved",
  "note": "…"
 }
}
```

| キー | 型 | 意味 |
|---|---|---|
| `date` | str | UTC の日付だけ(`YYYY-MM-DD`)。時刻は無し |
| `system.os_family` | str \| null | `windows` / `linux` / `macos`。版やビルドは無し。ComfyUI が別の PC で動いたときは null |
| `system.ram_total_gb` | int \| null | RAM を GiB で整数に丸めたもの。不明、または `--ram` で置き換えたときは null |
| `system.gpu` | object | 下に詳しく書く |
| `measurement` | object | 意味のある Measurement の項目(settings に `batch: 1` は入れない)。`note` は文(`。`、改行、`;`)に分け、この PC のホスト名 / ユーザー名 / ホームフォルダを含む文と、プライバシーのパターン(下)に合う文は、文ごと捨てる。捨てた文の数を末尾に足す(`contrib.scrub_text`) |

`system.gpu` の中身:
- `name`
- `vram_gb`(測ったとおり)
- `arch` / `compute_capability`(NVIDIA だけ。名前が合えばこの PC の nvidia-smi から、そうでなければ GPU の名前の表から)
- `driver`
- (0.9.0、任意)`vendor`(`nvidia` / `amd` / `intel` / `apple`)と `backend`(`cuda` / `rocm` / `xpu` / `mps`)。bench がバックエンドを記録したとき(`Measurement.gpu_backend`)だけ書く。両方あるか、両方無いか。
- (0.9.0、任意)`pci_id`(PCI 型番 ID `1002:744c`、小文字)。そのベンダー ID は `vendor` と合わなければならない。`hardware.PCI_DEVICE_NAMES` にある ID は、`name` と同じ GPU を指さなければならない(AMD は gfx、Intel は世代と公称 VRAM で)。一覧に無い ID は `unknown_gpu` の通知。

その他の決まり:
- これらが無い古いファイルも有効のまま。
- `contrib_schema` は 1・2・3 を受け付ける(#90・#132)。2 は 1 に `measurement.files_fp`(任意。16 進の小文字 64 桁)を足した形で、0.11.0 から書く。1 に `files_fp` があれば誤り。3(0.13.0 から書く)は下に書く。
- (0.12.0、#116、任意)`measurement.settings_override`: テンプレートの設定を変えて測ったときだけ `true` を書く(true / false 以外は誤り)。0.9.0 の GPU の任意の項目と同じく、`contrib_schema` は上げない。`contrib aggregate` は、この印の付いた提出を別の行にまとめ、行にも印を付ける。
- 名前には `(R)`、`(TM)`、`™`、`®` を含んでよい。`vendor` と別のベンダーの名前はエラー。`apple` はチップ名(`Apple M2 Pro`)か `mps` だけ。
- 256 GiB を超える `vram_gb`、`ram_total_gb` × 1.05 + 1 GiB を超える `peak_ram_gb`、NaN / 無限大 / 負の数はエラー(ファイルの NaN は読むときに断る)。

genmodelfit が書かないもの: ホスト名、ユーザー名、パス、GPU の UUID、PCI のバス ID、シリアル番号、IP / MAC アドレス、実測の自由記述の `source`、CPU 名、正確な時刻。

**限界(正直に)**: 確認はパターンで、理解ではない。
**検出しないもの**:
- 平文の個人名や、ほかの身元が分かる言葉(例: `山田太郎のPCで測った`、`on Taro's desktop`、`github some-user`)
- 住所(`東京都港区…`)
- base64 らしい続きと URL エンコード以外の符号化
- 区切りのない、言葉で書いたパス

だから `contrib show` は、利用者に note を読むよう求め、人が Pull Request を 1 件ずつ確かめる。

自由記述の項目は小さく保つ:
- `gpu.name`: 英数字、空白、`.`、`-`、`(R)`、`(TM)`、`™`、`®`、`(16GB)` のような大きさだけ(64 文字)
- `comfyui_version`: 厳密に `x.y.z`
- `precision`: 既知の精度のどれか
- `model_id`: `[A-Za-z0-9_.:/+-]` で最大 200 文字
- ほかの文字列: 100 文字
- `note`: 500 文字(#90 で 2000 から下げた。bench の note は長いと切り詰める)
- どこでも、制御文字・改行・見えない文字は不可
- 知らないキー、配列(失敗の記録の `failures` と `missing_nodes` は除く)、深さ 8 を超える入れ子は断る
- 64 KB を超えるファイルとシンボリックリンクは読まない

### contrib_schema 3 で足した項目(#132)

0.13.0 から `genmodelfit share`(と `measure`)は `contrib_schema` 3 を書く。実測のほかに、見積もりを良くするのに役立つものも送る。
どれも数・決まった語・公開の名前だけで、文(エラーの本文など)・生成した画像や音などの中身・テンプレートのプロンプト以外の入力・ホスト名・ユーザー名・GPU の UUID・PCI の位置・パス・IP アドレスは入れない(個人の情報の点検はすべての新しい項目に効く)。

| キー | 型 | 意味 |
|---|---|---|
| `db` | object | 測ったときの一覧: `generated_at`(日付だけ。`YYYY-MM-DD`)と `sha256`(同梱かキャッシュの `db.json` の sha256 の先頭 16 進 12 桁。分からなければ無し)。任意 |
| `measurement.estimate` | object | 測ったモデル・設定に対する、その時点の genmodelfit の見積もり。その実測と、同じ PC のほかの実測を**使わずに**作る(使うのは同梱の実測だけ: 作者の実測とほかの利用者のまとめ。作者の実測はこの PC のものかもしれないので、同じ GPU・同じモデルの実測も除く。`measurements: bundled-excluding-this-gpu` がその印): `fit_level`(`perfect` / `good` / `marginal` / `too_tight`)、`run_mode`(`gpu` / `sequential` / `offload` / `cpu` / `none`)、`vram_gb`(段階を決めた VRAM)、`vram_resident_gb`、`work_gb`(作業用メモリの式の値。いちばん重い段)、`ram_required_gb`、`seconds`(`measure` の時間の目安)、`basis`(`measured` / `estimated`)。どの値も null がありうる。ComfyUI が別の PC のとき・大きさが分からないときは書かない |
| `measurement.timing` | object | 時間の内訳(秒。分からなければ null): `queue_s`(送ってから始まるまで)、`load_s`(始まりからサンプラの最初のステップまで。読み込みと文の符号化)、`sampling_s`(サンプラの合計)、`sec_per_step`、`decode_s`(サンプラの最後のステップから終わりまで。VAE の復元と保存)、`save_s`(今はいつも null。HTTP では分けられない)、`total_s`(`seconds` と同じ)、`cold_load`(true = genmodelfit が起動した ComfyUI で初めての読み込み、false = 同じ ComfyUI で `/free` のあとの 2 回目、null = 分からない) |
| `measurement.spilled` | true | 最後まで動いた(`outcome: ok`)のに、ピークの VRAM が GPU の総量 × 1.02 + 0.25 GB を超えた(Windows で共有メモリに逃げた)ときだけ。true のときだけ書く |
| `measurement.spilled_gb` | number | ピークの VRAM − GPU の総量(GiB)。`spilled` といっしょ |

時間の内訳の取り方(HTTP だけ。websocket は使わない): `/history` の `status.messages` の `execution_start`・`execution_success`(か `_error`)の時刻と、ComfyUI のログ(`/internal/logs/raw`)の進み具合の行(tqdm。`20/20 [00:05<00:00, 3.80it/s]`)。2 つの時計を比べる値(`queue_s`・`load_s`・`decode_s`)は、ComfyUI が同じ PC のときだけ書く。使う進み具合の行は、前に説明の無い行(`Fetching 3 files: …` などは使わない)で、送ったワークフローのサンプラの `steps` と同じステップ数のものだけ。合う行が無い・実行の時間より長いときは `sampling_s`・`sec_per_step` は null。時間の内訳・見積もりが検査に通らないときは、それを外して実測を送る。

決まり: 1・2 のファイルの `db`・`estimate`・`timing`・`spilled`・`spilled_gb` は誤り。`spilled` は true だけで、`outcome: ok` のときだけ。ピークは総量の線を超え、GPU の総量 + メインメモリ(× 1.05 + 1 GiB。メインメモリが分からなければ GPU の総量)まで。`spilled_gb` はピーク − 総量と 0.1 GiB 以内で合うこと。spilled の提出は受け付けるが、まとめ・見積もり・段階・`calibrate` には**使わない**(「この GPU では共有メモリに逃げる(遅い)」の材料。使い方は別の Issue で決める)。

### 失敗の記録(`kind: genmodelfit-failures`、#132)

1 回の `share`・1 つの GPU で 1 ファイル(`measure` で測れなかったときも 1 ファイル保存し、送っていないものとして次の `share` がいっしょに送る)。実測と同じフォルダの `data/contrib/<gpu-slug>/<date>-<sha8>-failures.json`。
`contrib_schema` 3 だけ。上のキーは `contrib_schema`・`kind`・`tool`・`date`・`system`(実測と同じ PC の概要)・`comfyui_version`・`db`(任意)・`failures`(1〜50 件)。

```json
{
 "contrib_schema": 3,
 "kind": "genmodelfit-failures",
 "tool": {"name": "genmodelfit", "version": "0.13.0"},
 "date": "2026-10-29",
 "system": {"os_family": "windows", "ram_total_gb": 32, "gpu": {"name": "NVIDIA GeForce RTX 3070", "vram_gb": 8.0, "arch": "ampere", "compute_capability": "8.6", "driver": "566.03", "vendor": "nvidia", "backend": "cuda"}},
 "comfyui_version": "0.40.0",
 "db": {"generated_at": "2026-10-21", "sha256": "0123456789ab"},
 "failures": [
  {"model_id": "image_moge", "outcome": "missing_nodes", "seconds": null, "missing_nodes": ["LoadMoGeModel"], "before_run": true},
  {"model_id": "video_x", "outcome": "timeout", "seconds": 1800.4, "timeout_s": 1800.0},
  {"model_id": "image_y", "outcome": "error", "seconds": 3.0, "error_type": "ValueError", "node_type": "KSampler"}
 ]
}
```

| キー | 意味 |
|---|---|
| `model_id`・`outcome` | 必須。`outcome` は `missing_nodes` / `timeout` / `error` / `oom`。中断(利用者が止めた)と、ほかのプログラムのせいの失敗は**送らない** |
| `settings` | 任意。実測と同じキー・範囲 |
| `seconds` | 経過時間。測る前に分かったものは null |
| `timeout_s` | 時間切れの上限。`timeout` だけ |
| `missing_nodes` | ComfyUI に無かったノードの名前(組み込みかカスタムノードの公開の名前): 文字(各国の文字も)・数字・空白と `_ . + ( ) \| ' : , [ ] -`、100 字まで。`/`・`\` は入れない。`missing_nodes` だけ |
| `other_nodes` | 足りないノードのうち、名前の形に合わない・個人の情報らしい名前の数(名前は送らない)。`missing_nodes` が空でよいのは、これが 1 以上のときだけ |
| `before_run` | ノードの確かめで測る前に分かったとき true(実行していないので数値は無い) |
| `error_type` | `error`・`oom` だけ: 例外の型名(`torch.OutOfMemoryError`)か ComfyUI の `node_errors` の type(`value_not_in_list`)。`[A-Za-z_][A-Za-z0-9_.]{0,63}`。本文は送らない(パスが入りうる) |
| `node_type` | `error`・`oom` だけ: 失敗したノードの種類。genmodelfit が送ったワークフローにあるものだけ |
| `peak_vram_gb`・`files_fp` | 任意。実測と同じ |

記録にするもの: この ComfyUI に無いノードが要るテンプレート(`share`・`measure` の測る前のノードの確かめ、または bench がそれで止めたとき)、自分の仕事が実行中になってからの時間切れ(キューで待っただけ・ほかの仕事が動いた・PC がスリープしたときは入れない)、ComfyUI がワークフローを受け付けなかったとき(`value_not_in_list` = この PC にモデルのファイルが無い、は除く)、実行中のエラー、実測として送れなかった OOM。`measure` の記録も送っていないファイルとして保存し、次の `share` が送る。この PC に保存した記録と同じもの(同じモデル・設定・失敗・エラーの種類・ComfyUI の版。足りないノードはモデルとノードの名前だけで比べる)は、もう書かない。検査に通らない記録はその記録だけ外し、64 KB に収まるまで後ろの記録から外す。受け取る側は、`failures` の中のすべての文字にすべての検出(電話番号・ドメイン・符号化も)を当てる。名前の形に合う普通の言葉はノードの名前と見分けられないので、PR は人が確かめる。失敗の記録は見積もりの式・まとめ・`calibrate` には使わない。何を勧めるか・要る ComfyUI の版を決める材料にする。

### 提出の流れの決まり(#90)

どれも 1 文で説明できる決まりにした。分析で作った約 60 種の提出(tests/test_contrib_robust.py)で確かめている。

- **足すだけ**: 提出の PR は `data/contrib` に新しいファイルを足すだけで、既にあるファイルを消す・書き換える・名前を変える PR は CI(contrib.yml)が失敗にする(外すのはメンテナの PR)。
- **置き場所**: 置けるのは `data/contrib/<GPU>/<日付>-<sha8>.json` と失敗の記録の `<日付>-<sha8>-failures.json`(#132。小文字の `.json`)だけで、ほかの形・場所のファイルは CI でも `contrib validate <フォルダ>` でも誤り `layout` になる。
- **空の提出**: 提出のファイルが 1 件も無い PR・フォルダは失敗にする(`contrib validate` は終了コード 1)。
- **読めない形**: UTF-8 の BOM は消さずに理由を出して断り、同じオブジェクトの中に同じキーが 2 回ある JSON も断る(後の値が黙って勝つため)。
- **個人の情報**: base64 らしい続き(`=` の詰め物があれば 16 字から、無ければ 20 字から)・32 桁以上の 16 進(ちょうど 40 桁の git のコミットと 64 桁の sha256 は除く)・ドメイン名・電話番号・点で区切った MAC・崩したメールアドレス(`[at]`・アット)・「製品番号」などを誤りにする。モデル ID などの決まった形の項目には、base64・16 進・ドメイン・電話番号は当てない(形と一覧で縛っている)。公開の置き場のドメイン(`contrib.PUBLIC_DOMAINS`)は、ホスト名がその名前で終わるときだけ外す(`nvidia.com.taro.jp` は当てる)。
- **測ったファイル**: bench は測った時点の一覧のモデルのファイルの指紋 `files_fp`(ファイルの sha256 を小文字にして並べ替え、改行でつないだ文字列の sha256。sha256 の無いファイルが 1 つでもあれば書かない)を書き、一覧のファイルが変わった(指紋が違う)実測は使わない。
- **GPU の名前**(0.11.0、#101 で名前の表をやめた): 判定に使わない(`gpu_known: false` の記録として残すだけ。確認 `unknown_gpu`)のは、名前の代わりの値(空・`n/a`・`none`・`null`・`unknown`・`-`)と汎用の名前(`NVIDIA Graphics Device` など)だけ。NVIDIA の名前は形(`hardware.nvidia_name_well_formed`。[hardware.ja.md](hardware.ja.md)「GPU 名の形」)で見て、合わない名前は誤りでなく確認 `gpu_name_form`(メンテナが確かめる)、形に合うが表に無い名前は確認 `unknown_gpu`(判定には使う)。作った名前は同じ文字列を返す PC にしか当たらない(`estimate.same_gpu`)。名前の文字には括弧の世代名(`(Pascal)` など)も使える。
- **GPU の量**: 名前で VRAM が決まる NVIDIA の GPU(`estimate.NVIDIA_NOMINAL_VRAM`。デスクトップの GeForce RTX 20〜50 系だけ。ほかの系列(ノート用・業務用・データセンター)は量を見ない)で、`vram_gb` がどの版の量の 0.95〜1.03 倍にも入らなければ確認 `gpu_vram_nominal`(0.11.0 で誤りから下げた。改造品があるため。別の VRAM の PC に混ざらないことは VRAM の比 0.95 が守る。Linux の CUDA は 6 GB の版を 5.80 GiB と報告するので、下を広くした)。
- **error の提出**: `outcome: error` の提出は行の値に使わず、数(`n_error`)だけを残す(error だけの組は行にしない)。
- **ステップ数**: 最大 VRAM はステップ数で変わらないので、まとめる鍵にも判定の照合にもステップ数を入れない(行の `settings.steps` はいちばん多い値で、表示用)。
- **Issue**(#113): 実測の Issue は `contrib intake` が同じ検査をして 1 つの PR になる(ブランチ `measurement/issue-<N>`。GitHub Actions が作り、Issue を書いた人を PR に、`Submitted-by: github:<アカウント>` としてコミットに書く)。そのアカウントの提出として数える。`GITHUB_TOKEN` で作った PR では contrib.yml は動かない(intake が同じ検査を済ませている)。
- **出どころ**(#113): 同じ Pull Request(マージのコミット)か同じ人でつながった提出は 1 つの出どころ(`contrib.submission_origins`・`submission_sources`。決まりは [scoring.ja.md](scoring.ja.md)): 人は GitHub のアカウント(アカウントごとに 1 回。別のアカウント・組織・名前を変えたアカウントは別の人になるので、メンテナが確かめる): `Submitted-by-id` の数の ID(ID の無い前の版のボットのコミットは `Submitted-by` のアカウント)はマージのコミットで入れた受け取るボット自身のブランチのときだけ信じ、ほかはマージのコミットの 1 行目(`Merge pull request #N from <アカウント>/<ブランチ>`)の Pull Request の元のアカウント(コミットのメールアドレスは使わない)。そういうマージでないもの(main に直接・squash・rebase)だけ、書いた人のメールアドレスで数える(弱い)。値はまとまりのいちばん小さい鍵の sha256 で、アドレスは出さない。それを 1 つの出どころとして、その中央値を 1 つの値にし、外れ値も出どころの値で見る(`n_sources`。git の履歴が無い・浅い clone では提出ごとに数え、出力にそう書く)。
- **OOM の多い組**: OOM の出どころが ok の出どころ以上の行は `runs_evidence: false`(「動く」根拠にしない。使い方は PR #95)。
- **版**: 検査する genmodelfit より新しい版で作った提出(`tool_newer`)、テンプレートが求めるより古い ComfyUI(`comfyui_old`)、一覧が知る最新より 6 つ以上新しい ComfyUI の版(`comfyui_newer`)は確認だけにする。
- **大きさの入力の無いテンプレート**で `batch` が 1 でない提出は確認 `batch_sizeless`(0.10.1 の bench の記録の誤り)。

### `contrib make --from FILE [--output PATH] [--no-pc-info] [--json]`

bench の結果 FILE の実測ごとに 1 つの提出を作る。
`--output` はフォルダ(既定 `data/contrib`。ファイルはその下の `<gpu-slug>/<date>-<sha8>.json` に入る)。実測が 1 つのときは `.json` ファイルでもよい。
`--json`: 外枠 + `action: "make"`、`submissions` [{saved_to, path_in_repo, submission}] (`submission` はファイルの中身)。
bench の結果を読めないときは終了コード 1。

### `contrib show FILE`

ファイルの全項目と、プライバシーのチェックリストを出す(`known_fields`、`paths`、`host_user`(この PC 自身のホスト名 / ユーザー名も)、`device_ids`、`network`、`date_only`、そして利用者が自分で印を付ける `note_reviewed`)。
`--json`: 外枠 + `action: "show"`、`file`、`ok`、`submission`、`checklist` [{id, label, ok (null = 利用者が確かめる), detail}]、`report`(下)。
直すべきものがあれば終了コード 1。

### `contrib share [FILE]`

`genmodelfit share --issue` と同じ(「share」を見る。前からのスクリプトのため)。JSON は `command: "contrib"`・`action: "share"`。

### `contrib intake EVENT_JSON --out DIR [--existing DIR]`

`.github/workflows/measurement-intake.yml`(#113)が、`measurement` の印の Issue で使う。issues のイベントのファイルから
Issue の本文・番号・書いた人を読み、`{` で始まる最初の囲み(フォームの JSON の欄。`json` のコードの囲み)をデータとして取り出して
`contrib.intake_issue` を通す: `validate` の検査(ファイル名は見ない)、`share` の個人の情報の点検(この機械自身のホスト名・ユーザ名の
確かめは除く。GitHub では実行機のもので、送った人のものではないため)、64 KiB まで、`--existing` に同じ内容の提出が無いこと。
同じアカウントがその GPU・モデルで `--existing` に既に送った数も数える(`contrib.submitter_count`。git の履歴が要る)。`result.json`(`IntakeResult`: `ok`・`reasons`・`notices`・`path_in_repo`・
`sha256`・`title` など)と `comment.md`(英語と日本語。理由の文は `contrib.md_cell` で無害にする)を書き、受け付けたときは
`submission.json`(本文の文字そのままでなく `contrib.dumps` で書き直した中身)・`path.txt`・`title.txt`・`commit_msg.txt`・
`pr_body.md`(Issue の番号と書いた人・その数・メンテナへの確認・`Closes #N`)・`comment_updated.md`(PR ができた
あとの編集のとき)も書き、断ったときは `comment_pr_closed.md`(閉じる PR に書く)も書く。`commit_msg.txt` の最後は
`Submitted-by: github:<アカウント>` と `Submitted-by-id: <数の ID>`(Issue を書いた人の `user.id`。数字のときだけ)で、`submission_sources` が 1 つのアカウントを 1 つの出どころとして数えるのに使う。本文の文字は出力しない
(`accepted` / `refused (N reasons)` だけ)。受け付けても断っても終了コード 0。イベントを読めないときだけ 1。通信しない。

### `contrib validate FILE|DIR… [--json] [--strict] [--no-filename-check] [--markdown]`

ファイルごとに、レポート `{path, ok, errors: [{code, message}], warnings: [{code, message}], notices: [{code, message}], sha256, suggested_path, model_id, gpu_name}` を出す。
`--json`: 外枠 + `action: "validate"`、`ok`、`files`、`valid`、`invalid`、`notices`(通知のあるファイル)、`warnings`(件数)、`reports`。
`--markdown`: CI のジョブの要約用の表。通知のあるファイルがあれば、その上に保守者向けの 1 行を付ける。

llmfit の `validate_community_benchmarks.py` と同じく、**機械的な確認だけが失敗にする**(2026-10-22)。エラーは次のとおり。

| エラーコード | いつ |
|---|---|
| `read` | 読めない、JSON でない、64 KB を超える、入れ子が深すぎる、またはシンボリックリンク(シンボリックリンクのフォルダは辿らない) |
| `string` | 制御文字・改行・見えない文字・双方向の文字を含む文字列、長すぎる文字列やキー、配列、深さ 8 を超える入れ子 |
| `schema` | `contrib_schema` が 1・2・3 でない、古い版に 3 の項目がある、`kind` / `tool` が違う、キーが足りない、オブジェクトでない |
| `unknown_field` | 決まりに無いキー(`settings` の中を含め、どこでも) |
| `privacy` | 値かキー(NFKC 正規化し、幅ゼロの文字を除き、`¥` を `\` と読んだあと)に、次のどれかが含まれる(詳細は下) |
| `date` | `YYYY-MM-DD` でない、または未来 |
| `value` | 範囲外(詳細は下) |
| `settings` | width / height は 64–8192、frames は 1–2000、seconds は 0.1–3600、steps は 1–1000、batch は 1–64(seconds 以外は整数) |
| `gpu_inconsistent` | GPU の名前から分かる compute capability や世代がファイルのものと違う。`vendor` と `backend` が対にならない(または片方だけ)。NVIDIA 以外の `vendor` に `compute_capability` / `arch` がある。`vendor` と別のベンダー(AMD / Intel / Apple)の GPU の名前。`pci_id` のベンダー ID が `vendor` と違う(`vendor` が無ければ `10de`)、または `hardware.PCI_DEVICE_NAMES` にある GPU が名前と違う。`apple` で、チップ名か `mps` 以外の名前 |
| `filename` | 名前が `<date>-<sha8>.json`(失敗の記録は `<date>-<sha8>-failures.json`)でない、date や sha8 が中身と合わない、フォルダが GPU の slug でない |
| `value`(厳密な形) | `gpu.name` / `comfyui_version` / `precision` / `model_id` / `tool.version` が、上の形の外 |
| `duplicate` | 同じ実行の前のファイルと中身のハッシュが同じ |
| `layout` | (#90)フォルダの検査で、`<GPU>/<日付>-<sha8>.json`(小文字の `.json`)でない場所・形のファイル |

`privacy` が断るもの:
- ドライブのパス(`C:\`、`C:/`)
- バックスラッシュすべて
- 語の間の 2 つ以上のパス区切り(`a/b/c`、`Users\name\`)
- `/home/` のような根
- `//server`
- `~/`
- `%VAR%`
- `$HOME`
- URL エンコードした区切り(`%2F` `%5C` `%3A`)
- `@` の後に英字(メールアドレス、@ハンドル)
- UUID / GPU の UUID
- PCI アドレス(`0000:01:00.0`、`01:00.0`)
- IPv4 / IPv6 / MAC アドレス
- URL(`scheme://`、`www.`)
- ホスト名のような名前(`DESKTOP-XXXX`、`*-PC`、`*.local`、「host name」「PC 名」などの語)
- 時刻(`14:05`、`2pm`、`14時05分`、ISO の日時)
- シリアル番号(`SN:`)

害のない文字列も断ることがある(例: `12:30`、4 つの部分の版 `1.2.3.4`)。

`value` が断る範囲外:
- NaN / 無限大 / 負の数
- `vram_gb` ≤ 0 または > 256
- `peak_vram_gb` が GPU の VRAM × 1.02 + 0.25 より大きい、または `ok` で 0
- `peak_ram_gb` が RAM × 1.05 + 1 より大きい
- `seconds` ≤ 0、> 86400、または `ok` で無い
- 形の崩れた `pci_id`
- 知らない `outcome` / `offload` / `vram_metric` / `os_family` / `arch` / `vendor` / `backend`
- 形の崩れた `driver` / `comfyui_version` / `compute_capability`

通知(ファイルは有効で、集計される。マージする保守者のために CI の要約に出る。マージがレビュー):
- `unknown_model`: 同梱の一覧に無い。
- `unknown_gpu`: GPU の名前の表に無い(例: AMD / Intel / Apple・新しい NVIDIA。判定には使う)か、汎用の名前・名前の代わりの値(判定には使わない)。
- `gpu_name_form`: NVIDIA の GPU 名が名前の形に合わない(0.11.0。メンテナが確かめる。個人の名前などが入っていないか)。
- `gpu_vram_nominal`: VRAM が名前の公称の量に合わない(0.11.0。改造品でなければ誤り)。
- `batch_sizeless`: 大きさの入力の無いテンプレート(`estimate.NO_SIZE_INPUT`)なのに `batch` が 1 でない(0.10.1 の bench が MoGe の処理の単位を枚数と記録した。判定は batch 1 なので同じ設定の実測にならない。#90)。
- `deviation`: ピーク VRAM が、その設定での式の全部載せの見積もりから 40% より離れている。
- `below_weights`: `offload: none` なのにピーク VRAM が 重み + 固定の上乗せより小さい。見積もりは、これより小さいすべての提出に対して、その下限を使う(dynamic VRAM や部分的な読み込みでは、小さなピークは普通)。
- `fit_change`: この提出だけで、提出された GPU の PC の段階が変わる。
  - 「提出された GPU の PC」= 名前・PCI 型番 ID・ベンダーが同じで、VRAM が提出の `vram_gb` と同じ。同じ名前の、ほかの公称 VRAM が分かっていればそれも含める(Intel Arc: `hardware._INTEL_XPU`。NVIDIA: `estimate.NVIDIA_VRAM_VARIANTS`、0.9.1: RTX 2060 / 3050 / 3060 / 3080 / 4060 Ti / 5060 Ti)。
  - これに RAM 16 / 32 / 64 GB を掛け、既定の設定と提出の設定で調べる。
  - 段階が上がる場合を含む。
  - ほかの GPU には、0.9.0 から影響しない。
  - 0.8.0 までは これらは「要レビュー」で、sha8 ごとの承認(`--allow-review`、`data/contrib/REVIEWED`)が要った。どちらも廃止した。

警告(受け付けるが報告する。`--strict` では失敗にする): `nonstandard`(記録として残すが、集計はしない)、`metric_missing`、`driver_missing`、`comfyui_version_missing`、`ram_not_rounded`、`settings_missing` / `settings_modality`、`offload_outcome`、`date_old`。`settings_missing` は、画像・動画のモデルで幅・高さが無く、モデルが大きさの入力の無いテンプレートの手の表(`estimate.NO_SIZE_INPUT`)に無いときに出す(#90。新しいテンプレートなら、メンテナが bench の記録を確かめて表に足す)。

`--markdown` は、すべてのセルをエスケープする(`| \` < > [ ] & @`、改行、GFM の自動リンク `://` と `www.`)。だからファイル名や値が、CI の要約に Markdown、HTML、リンク、ワークフローコマンドを差し込めない。
CI は genmodelfit の stderr も、同じエスケープを通して要約に足す。

終了コード: 0 = すべてのファイルが通った(警告と通知は許す)/ 1 = ファイルが失敗した、`--strict` で警告がある、または提出が 1 件も無い(#90)/ 2 = 使い方の誤り。

### `contrib aggregate [DIR] [--output FILE] [--report] [--threshold PCT] [--fail-on-breach] [--json]`

`--output` は、出どころをファイルごとに数える(`file:…`。git の履歴が無い)ときは書き出さない(終了コード 1)。`--allow-file-sources` で書き出せるが、試験・手元の確かめだけで、版を出すときは使わない(#95)。`--report`・`--check` は表示だけなので git の履歴が無くても動く。

`DIR`(既定 `data/contrib`。フォルダが無ければエラー)を読み、`validate` を通ったファイル(通知は許す)を残し、標準の提出を次でグループにする。
- モデル id
- GPU(PCI 型番 ID があればそれ、無ければ `estimate.gpu_key` で正規化した名前: 大文字小文字、空白・記号、`(R)` `(TM)` `™` `®`、`NVIDIA` / `GeForce`)
- GPU の VRAM を 1 GB に丸めたもの(RTX 4060 Ti の 8 GB と 16 GB は別のグループ)
- `vram_metric`
- 設定(精度は正規化: `BF16` = `bfloat16` = `bf16`。ステップ数は入れない。#90)
- 測ったときの一覧のファイルの指紋 `files_fp`(#90)

集計の方法:
- `error` の提出は値に使わず `n_error` に数える(#90)。
- `ok` の提出は出どころ(`contrib.submission_sources`: そのファイルを足したコミット。git の履歴が無い・浅い clone では提出ごと)ごとに中央値をとり、その出どころの値のうち |x − 中央値| / (1.4826 × MAD) > 3.5 のものは外れ値(出どころが 3 つ以上のときだけ。MAD は中央値の 2% を下限にする)。
- 残りの出どころの値の中央値が値になる。`n_used`(使った提出の数)・`n_sources`(使った出どころの数)・`n_oom`・`n_error`・`runs_evidence`(OOM の出どころが ok の出どころより少ないか)・`gpu_known`・`files_fp` を付ける(OOM の実行だけのグループは、`outcome: oom` の 1 行になり、決して使われない)。
- グループが `consistent`(較正に入る)になるのは、使った出どころが 2 つ以上で、その幅が中央値の 15% 以内で、グループに OOM が無いとき。
- `--json` と出力の 1 行目の下に、出どころの数え方(`sources_from`: `git` / `files`)を出す。同梱のまとめは、履歴のある clone で版を出すときに作る(docs/release.ja.md「版を出す手順」)。
- 出力の行は、`origin: "community"` の Measurement の記録(Measurement を参照)で、順は固定。だから同じ入力は常に同じファイルになる。

`--output` は `{unit, source: "community", contrib_schema, date (最新の提出の日付), method, submissions, measurements}` を書く(空の結果は `--allow-empty` が無いと書かない。同梱のファイルを誤って空にしないため)。
- `submissions`(#90)は、まとめに渡した提出(検査を通ったもの)ごとの `{sha256, source}` を sha256 の順に並べたもの。`sha256` は内容の hash(`contrib.canonical`。ファイル名の sha8 の元)、`source` はまとめたときの出どころ(足したコミット、git の履歴が無ければ `file:<sha256>`)。
- `contrib aggregate --check` と試験は、git の履歴を読まずに、この記録した出どころでまとめ直して比べる(浅い clone・git の外でも同じ結果)。最初の形(sha256 の文字列だけ)も読む(出どころは提出ごと)が、今の形で作り直すまで `--check` は「まとめ直すと違う」(`matches: false`)と出す(書き出す形が違うため)。
- 古い版は知らないキーとして読み飛ばす(読むのは `measurements` だけ)。

同梱の写しは `src/genmodelfit/data/measurements_community.json` で、ほかの実測と一緒に読み込まれる。
- 作り直すのは版を出すとき(docs/release.ja.md「版を出す手順」)。提出の PR は data/contrib だけを変えるので、マージしてもまだ入らない。
- 試験は、`submissions` の提出がすべて data/contrib にあり、それだけをまとめ直すと同梱のファイルと同じになることを確かめる。まだ入っていない提出が data/contrib にあってもよい。

`--report` は、同梱の保守者の行 + 新しい community の行(ローカルのキャッシュは決して使わない)で較正(`calibrate.calibrate`)をやり直し、`report` を返す: `{threshold_pct, n_fit, coefficients, summary, by_modality, loo, check, breaches, breach, fit_changes}`。
- `by_modality` は `{<modality>: {loo_n, loo_mean_abs_pct, loo_max_abs_pct, evaluable, check_n, check_mean_abs_pct, check_max_abs_pct}}`。
- `breaches` は [{kind (`loo` / `community_check`), model_id, gpu_name, origin, error_pct}]。
- `loo` / `check` は、`calibrate.calibrate` の行ごとの結果。
- しきい値(既定 25%)に対して数えるのは、評価できる modality の LOO の誤差と、community の行の check の誤差。保守者自身の check の行(RTX 3070)は表示するが数えない。
- `fit_changes` [{model_id, gpu_name, vram_gb, ram_gb, before, after}] は、community の行が、実測した GPU の PC(名前、PCI 型番 ID、ベンダー、実測した GPU の VRAM と、その名前のほかの公称 VRAM。`fit_change` の通知と同じ)× RAM 16 / 32 / 64 GB で起こす段階の変化の一覧。ほかの GPU には、0.9.0 から影響しない(テキストのレポートの「Fit-level changes caused by community data」の節)。
- テキスト出力は Markdown(#19 の週ごとの Pull Request 用)。

`--json`: 外枠 + `action: "aggregate"`、`dir`、`files`、`valid`、`invalid`、`notices`、`submissions_used`、`groups`、`consistent`、`written_to`、`community`(上の文書)、`--report` のときは `report`。
終了コード: 0 / 1 エラー(フォルダが無い。`--allow-empty` 無しの `--output` で結果が空)/ 2 使い方の誤り / **3** `--report --fail-on-breach` で上限を超えた。

### `contrib aggregate [DIR] --check [--json]`

何も書かず、同梱のまとめと `DIR`(既定 `data/contrib`。無ければ誤りで終了コード 1)を比べる(#90)。`--output`・`--report` とは一緒に使えない。git のコミットでなくファイルごとに数えた出どころ(`file:…`)の数 `file_sources` も出す(同梱のまとめは 0 でなければならない。#95)。
- まだまとめに入っていない、検査を通った提出(マージした後の提出)のパスを出す。
- まとめの `submissions` にあるのに `DIR` に無い(または検査を通らない)提出と、それだけをまとめ直したときに同梱のファイルと同じになるかも出す。
- 同梱のまとめのうち、測ったときの一覧のファイル(`files_fp`)が今の一覧と違い、判定に使われない行(`stale`)も出す(知らせるだけ。毎週の一覧の更新で上流のファイルが変わると起きる)。

`--json`: 外枠 + `action: "aggregate_check"`、`dir`、`listed`(まとめの提出の数)、`valid`、`invalid`(検査を通らないファイル)、`pending`(まだ入っていない提出のパス)、`missing`(まとめにあるのに無い hash)、`stale`([model_id, gpu_name])、`file_sources`(#95)、`matches`、`ok`(missing が無く matches)。
終了コード: 0 = まだ入っていない提出が無く ok / 1 = まだ入っていない提出がある、ファイルごとの出どころがある、または ok でない / 2 使い方の誤り。

## download

`genmodelfit download <model> --json` は、外枠(`system` と `database` あり)に次のキーを足して出す。

- `--list --json` は計画だけを出す(`listed: true`、`result: null`。通信しない、書かない。`plan.ok` が false でも終了コード 0)。
- `--json` でダウンロードするには `--yes` が要る。
- 失敗したとき(終了コード 1、Ctrl+C の後は 130)は、`error` が入り、`result` は null、`plan` は計画(作れていれば)。
- モデルが見つからない、または複数のモデルに合うときは、`plan` 無しで `error`(と `candidates`)を出す。
- 置き場所が決まらない(ComfyUI のフォルダが見つからない・2 つ以上見つかり、尋ねられない)ときは、`plan` は null で、`destination_candidates` に見つけたものを入れる([download.ja.md](download.ja.md)「置き場所の決め方」)。
- Python: `gmf.plan_download(...)` は `DownloadPlan` を返し(`.to_dict()` が `plan`)、`gmf.download(...)` は `DownloadResult` を返す(`.to_dict()` が `result`)。

| キー | 型 | 意味 |
|---|---|---|
| `listed` | bool | `--list` なら true(何も落とさない) |
| `plan` | DownloadPlan | 落とす(または落とすことになる)もの |
| `result` | DownloadResult \| null | 起きたこと。`--list` のとき、失敗のときは null |
| `destination_from` | str \| null | (0.11.0、#111)置き場所の決め方: `comfyui_dir_option`、`output_dir_option`、`env`(`GENMODELFIT_COMFYUI_DIR`)、`config`(`genmodelfit config`)、自動で見つけたもの: `cwd`、`running`(起動中の ComfyUI)、`desktop`(ComfyUI デスクトップ版の設定)、`comfy_cli`、`common`(よくある場所)、または `typed`(端末で入れたパス)。null = 未定(`--list`、または上の誤り) |
| `comfyui_dir` | str \| null | (0.11.0)ComfyUI のフォルダ。`--output-dir` と未定のときは null |
| `extra_model_paths` | object \| null | (0.11.0)使った `is_default` の節: `file`、`section`、`root`(置き場所)、`folders`(節にあるフォルダのキーの str[]。`download_model_base` のときは null)。使わなければ null |
| `destination_candidates` | object[] | (0.11.0)自動で見つけた ComfyUI のフォルダ: `comfyui_dir`、`from`(`destination_from` と同じ)、`detail`(例 `127.0.0.1:8188`、設定ファイル)。置き場所を指定したときは空 |

DownloadPlan:

| キー | 型 | 意味 |
|---|---|---|
| `model_id`、`name` | str | 選んだワークフロー(一覧のエントリ) |
| `model_name` | str | 表に出るモデル名 |
| `modality`、`content`、`precision` | str / str / str \| null | ModelEntry と同じ |
| `fit_level`、`run_mode_label`、`auto` | str / str / bool | この PC での判定(`recommend` と同じ) |
| `chosen` | str | このワークフローを選んだ理由(表示の言語 `en` / `ja`) |
| `layout` | str | `comfyui`(`--comfyui-dir`。`root` は `<dir>/models`)/ `output`(`--output-dir` または Python の既定。同じ配置。CLI は `is_default` の節と、起動中の ComfyUI が答えた models のフォルダでも `output`) |
| `root` | str | ファイルを置くフォルダの絶対パス(`<root>/<directory>/<name>`)。置き場所が未定(CLI の `--list`)なら "" |
| `files` | PlannedFile[] | ワークフローのファイルごとに 1 つ |
| `total_bytes` | int | ファイルの大きさの合計 |
| `download_bytes` | int | これから落とす量(すでにあるファイルと `.part` のバイト数を引いたもの) |
| `present_bytes` | int | すでにあり、sha256 が正しいファイル |
| `disk_free_bytes` | int \| null | `root`(または最も近い既存の親)の空き容量 |
| `disk_needed_bytes` | int | `download_bytes` + `margin_bytes`(落とすものが無ければ 0) |
| `margin_bytes` | int | 1 GiB |
| `budget_bytes` | int \| null | `--budget` をバイトで |
| `blocked` | str[] | 落とさない理由(表示の言語 `en` / `ja`)。空なら落とせる |
| `blocked_codes` | str[] | `blocked` のそれぞれの種類。同じ順(任意。#75 から): `uncensored`・`unknown_license`(この 2 つは利用者の同意で外せる: Python は `content="all"`・`include_unknown_license=True`、CLI は `--uncensored`・`--any-license` か端末の y/N の質問。ライブラリの `blocked` の文は Python の引数を書き、CLI は自分の指定を書く)、`gated`・`no_files`・`unavailable`(このワークフローは genmodelfit では落とせない)、`conflict`・`budget`・`disk`(同意の指定では外れない。ファイルを移す・`--output-dir`、`--budget` を上げる、ディスクを空ける) |
| `warnings` | str[] | 知っておくべきこと(許可リストに無いライセンス、この PC では動かない、配置のヒント) |
| `alternatives` | object[] | 検討したほかのワークフロー: `id`、`precision`、`use`、`fit_level`、`memory_required_gb`、`size_bytes`、`auto`、`ok`(任意。#75 から。gated・取得できない・同じ名前の別のファイルが無ければ true。無検閲・ライセンス不明・空き・上限は数えない) |
| `license_url` | str \| null | モデルのライセンスのページ |
| `ok` | bool | `blocked` が空 |
| `skipped_optional` | str[] | (0.12.0、#81)落とさない任意の部品(`ModelFile.optional` が `inactive`)のファイル名。`with_optional=True`(CLI は `--with-optional`)なら空 |

PlannedFile:

| キー | 型 | 意味 |
|---|---|---|
| `name` | str | 一覧にあるファイル名(ComfyUI のサブフォルダ `/` を含むことがある) |
| `relpath` | str | `root` の下のパス(`<directory>/<name>`。ComfyUI のフォルダが分からなければ `other/<name>`。ComfyUI は `other` を読まないので、使う前に移す)。断ったときは "" |
| `dest` | str | 保存先の絶対パス。断ったとき、置き場所が未定のときは "" |
| `url`、`sha256`、`size_bytes`、`role` | | ModelFile から |
| `directory` | str \| null | ComfyUI のフォルダ。ModelFile から。一覧に無ければ決めたもの(`directory_basis` を参照)。null は分からない(`other/`) |
| `directory_basis` | object \| null | 一覧に置き場所の無いファイル(出どころによらない)の `directory` を決めた根拠(任意。#75 から): 本体のファイルのヘッダのパラメータ数が、公式の ComfyUI テンプレートのファイルと 0.001% の幅で一致し、一致したファイルの置き場所が 1 つだけ(`unet` は `diffusion_models`、`clip` は `text_encoders` と同じとみなす。例 SDXL の全部入り = `sd_xl_base_1.0.safetensors` → `checkpoints`)。キー: `kind`(`header_params`)、`template`(テンプレートの ID)、`file`(そのファイル)、`params`、`reference_params`、`needs`(GGUF を `diffusion_models`・`text_encoders` に置くときの `ComfyUI-GGUF`)。null は一覧の値か、分からない |
| `license`、`license_name` | str \| null | 確認に使ったライセンス(ゲートの保守的な読み。null は不明) |
| `license_url` | str \| null | ライセンスの本文がある場所 |
| `check_url` | str \| null | `license_url` が null のとき、条件を確かめるページ(任意。#75 から): `hf:` の ID なら `https://huggingface.co/<owner>/<repo>`、無ければエントリの `homepage`(https のものだけ)、それも無ければファイルの Hugging Face のリポジトリ(公式テンプレートでは Comfy-Org などの置き場所で、作者とは限らない。CLI はそう書く)。`license_url` があれば null |
| `license_note` | str \| null | 一覧の値とライセンスが違う理由、または確かめられなかった理由 |
| `license_allowed` | bool | ライセンスの許可リストにある(ゲートの既定、または `--license-allowlist`) |
| `gated` | bool \| null | アクセスの申請が要る(部品の上流が求める場合も) |
| `status` | str | `download` / `resume`(`.part` がある)/ `present`(すでにあり、sha256 が合う)/ `conflict`(別のファイルが その名前を使っている。決して上書きしない)/ `unavailable`(`reason` を参照) |
| `part_bytes` | int | 既存の `.part` の大きさ |
| `remaining_bytes` | int | このファイルでこれから落とすバイト数 |
| `reason` | str \| null | `conflict` / `unavailable` の理由(表示の言語 `en` / `ja`) |
| `verified` | bool | `present` について: 計画のときに sha256 を測った(`verify_existing=False` では false で、大きさだけ)。`download` は、飛ばす前に、既存のファイルを必ずもう一度測る |

DownloadResult:

| キー | 型 | 意味 |
|---|---|---|
| `model_id`、`root` | str | 計画と同じ |
| `downloaded` | str[] | 落としたファイルの `relpath`(sha256 を確認済み) |
| `resumed` | str[] | そのうち、HTTP Range で `.part` から続けたもの |
| `restarted` | str[] | そのうち、サーバーが Range を無視したため最初からやり直したもの |
| `skipped` | str[] | すでにあったもの。この実行で sha256 を測って合った |
| `bytes_downloaded` | int | この実行で受け取ったバイト数 |
| `seconds` | float | 実時間 |
| `files` | object[] | すべてのファイルの `relpath`、`dest`、`sha256` |

## config

`genmodelfit config [show|set|unset|path] --json`(0.11.0、#111。[download.ja.md](download.ja.md)「genmodelfit config」)。
外枠に `system`・`database` は無い。

| キー | 型 | 意味 |
|---|---|---|
| `action` | str | `show`(操作を書かないときも)/ `set` / `unset` / `path` |
| `path` | str | 設定ファイル(まだ無いこともある) |
| `settings` | object | `show`: `comfyui-dir` と `lang`、それぞれ `{ value, from }`。`from`: `option`(`--lang`)、`env`(`GENMODELFIT_COMFYUI_DIR`・`GENMODELFIT_LANG`)、`file`、`default`(`comfyui-dir` は値が null で download が探す。`lang` は `en`) |
| `warnings` | str[] | `show`: ファイルや値を使わなかった理由(壊れた JSON、型の違い) |
| `key`、`value` | str | `set`: 保存したもの(`comfyui-dir` は絶対パス) |
| `removed` | bool | `unset`: 設定されていなかったときは false |

誤り(終了コード 1: 知らないキー、正しくない値、壊れた・書けないファイル)は `{ genmodelfit, command, error }`。

## Python API (stable) / 公開の範囲

**安定した API は `genmodelfit.__all__` に入っている名前だけ**(新しいものは tests/test_judge_model.py で固定する)。
サブモジュールの関数(`genmodelfit.recommend.judge`、`genmodelfit.hardware.classify_gpu` など)は内部のもので、どのリリースでも変わりうる。

ひととおりの使い方(0.13.2 で README から移した):

```python
import genmodelfit as gmf

for f in gmf.recommend(modality="image", task="edit", limit=5):
    row = f.to_dict()  # `recommend --json` の 1 件と同じ dict
    # memory_required_gb: 選んだ動かし方で要る量(表の VRAM。CPU ならメモリ)。
    # f.estimate.vram_gb は全部を GPU に載せたときの量で、GPU より大きいことがある(GPU~・GPU+RAM の行)
    print(f.model.id, f.model.precision, f.score, f.auto, f.estimate.fit, row["memory_required_gb"])

st = gmf.data_status()  # 一覧は Verified(auto)を出せるほど新しいか
if st.expired:
    print("run: genmodelfit update --from-release", st.expires_at)

# 一覧を自分で読むときは、確認待ちのものを除く。
# 形や型の正しくない一覧では、load_db が RuntimeError を出す(RuntimeError で受ける。派生の
# genmodelfit.db.DatabaseError は gmf.__all__ に無い)
ok = [e for e in gmf.load_db().models if e.reviewed]

# アプリが調べた GPU で 1 モデルを判定する(世代と精度は genmodelfit が決める)
fit = gmf.judge_model("image_sdxl_simple", gpu_name="NVIDIA GeForce RTX 3060", vram_gb=12, driver="560.94")
print(fit.to_dict()["fit_level"])

# AMD・Intel の GPU も検出と同じ対応表で判定する。使えない理由は other_gpus[].reason
pc = gmf.system_from(
    [{"name": "AMD Radeon RX 7800 XT", "vendor": "amd", "vram_gb": 16}],
    windows=True,
    windows_build=22631,
    cpu="AMD Ryzen 7 7700X",
)
gmf.gpu_family("Intel(R) Arc(TM) B580 Graphics", "intel")  # 名前の表から {"backend": "xpu", ...}
db = gmf.load_db(use_cache=False, user_measurements=False)  # 同梱の一覧と同梱の実測だけ

# 署名つきの一覧を、自分のセッション(アプリの CA バンドルなど)で取り込む(通信するのはこれと gmf.download だけ)
db = gmf.update_from_release(session=my_session, dest=data_dir / "db.json", state_dir=data_dir)
```

アプリが自分で検出した GPU で 1 つのモデルを判定する(0.6.0、#27)。
世代(compute capability)と ComfyUI の計算精度は、GPU の名前とドライバから genmodelfit が決める。nvidia-smi も PowerShell も実行しない:

```python
import genmodelfit as gmf

fit = gmf.judge_model(
    "hf:stabilityai/stable-audio-3-small-sfx",  # id、またはモデル名 / id の一部(`info` / `download` と同じ)
    gpu_name="NVIDIA GeForce RTX 3060",
    vram_gb=12.0,
    driver="560.94",
    ram_gb=32.0,  # None = この PC のメモリ(gmf.system() と同じ)
    gpu_usable=None,  # None: ドライバ / 世代から genmodelfit が決める。True: アプリが確かめた
    db=gmf.load_db(),  # 任意。アプリは同梱の一覧を渡してもよい
)
row = fit.to_dict()  # `recommend --json` の models の 1 件と同じ dict(上の「Fit」)
row["fit_level"], row["run_mode"], row["memory_required_gb"], row["estimate_confidence"], row["licenses"]
```

| 関数 | 返すもの | 備考 |
|---|---|---|
| `gmf.system_from(gpus=(), *, ram_gb=None, gpu_usable=None, windows=None, windows_build=None, cpu=None)` | `SystemInfo` | `gpus`: dict `{"name", "vram_gb", "driver"?, "compute_capability"?, "vendor"? (既定 nvidia), "pci_id"?}` または `Gpu`。判定するのは、使える NVIDIA の GPU のうち最大のもの。(0.12.0、#96)`vendor` が `amd` / `intel` / `apple` の GPU は、検出と同じ対応表(`hardware.ja.md`「NVIDIA 以外の GPU」)で判定し、使える NVIDIA が無いときだけ判定に使う(これらは `vram_gb` を省ける: Intel の単体 GPU は名前の公称、内蔵 GPU は `ram_gb` の共有の分。`1002:744c` のような `pci_id` で汎用の名前の世代を引く)。部品ごとに `other_gpus` に入り、`usable`・`reason`(使う根拠か使えない理由)・`integrated`・`needs` が分かる(上の Adapter。`source: app`)。0.12.0 より前は NVIDIA 以外を常に CPU として扱った。`windows_build`: Windows のビルド番号(22000 以上が Windows 11。AMD・Intel は Windows 11 が要る。既定はこの PC が Windows ならその番号、ほかは分からない(分からなければ AMD・Intel は使わない)。OS が Windows でないときは `ValueError`)。`cpu`: CPU の名前(名前が `AMD Radeon(TM) Graphics` だけの AMD の内蔵 GPU の世代を決める。`SystemInfo.cpu` にも入る。既定はこの PC のもの)。対応表の OS は `windows=True` が Windows、`False` が Linux(Mac では macOS)、`None` がこの PC の OS。`ram_gb` は 合計 = 空き とし、この PC のページファイルは無視する(`--ram` と同じ)。`gpu_usable`: `None` = 今のどの PyTorch CUDA ビルドも対応しない GPU、またはドライバが古すぎる GPU は CPU として扱う(理由は `notes`、[hardware.md](hardware.ja.md))。`True` = 呼び出し側が確かめた(世代と精度はここで決める)。`False` = CPU(どの会社の GPU も。`None` / `True` が効くのは NVIDIA だけ)。`windows`(既定: この PC): Windows のドライバの表と、Windows の ComfyUI の精度(Pascal は fp16)。OS、CPU、ディスクの空きはこの PC のもの。不正な GPU の dict は `ValueError` |
| `gmf.judge_model(model, *, gpu_name=None, vram_gb=None, driver=None, compute_capability=None, ram_gb=None, cpu_only=False, gpu_usable=None, windows=None, gpu_vendor=None, windows_build=None, cpu=None, settings=None, content="sfw", system_info=None, db=None, comfyui_version=None)` | `Fit` | (0.12.0、#96)`gpu_vendor`(`amd` / `intel` / `apple`。既定 nvidia)・`windows_build`・`cpu` は `system_from` に渡す(NVIDIA 以外は `vram_gb=0` で VRAM が分からないことを示す)。 `model`: id の完全一致は内容によらず使う。そうでなければ `download` の名前の探索(`content` で絞る。1 つのモデルのワークフローが複数あれば、`recommend` の行の代表。ほかは `fit.workflows`)。見つからない / 複数のモデルに合う → `gmf.ModelNotFound`(`.candidates`)。PC は、`system_info`、無ければ `gpu_name` + `vram_gb`(両方必要。そうでなければ `ValueError`)、無ければ `cpu_only=True`(GPU 無し)、無ければこの PC(`gmf.system()`)。`system_info` を GPU の引数と一緒に渡すと `ValueError` |
| `gmf.gpu_family(name, vendor, cpu="")` | `dict \| None` | (0.12.0、#96)AMD・Intel の GPU 名を genmodelfit の名前の表(対応表の判定と同じ表)で引く。入れる PyTorch の種類を決めるのに使える: `{"vendor": "amd"\|"intel", "backend": "rocm"\|"xpu", "arch": "gfx1101"(AMD の gfx。Intel は null), "family": "RDNA 3" / "Arc B-Series (Battlemage)"(表に無い gfx は "?"), "integrated": bool, "vram_floor_gb": float \| null(Intel の単体 GPU の名前から推す公称の VRAM。量の違う版があれば少ないほう)}`。名前が表に無い(Intel の UHD / Iris、表より新しい GPU など)ときは `None`。`cpu` は名前が `AMD Radeon(TM) Graphics` だけの AMD の内蔵 GPU を決める。表にあることは、その OS で動くことではない(RDNA 1 も表にある)。動くかは `system_from` が決める。ほかの会社は `ValueError`。通信しない |
| `gmf.load_db(path=None, *, use_cache=True, user_measurements=True, source=None)` | `Database` | `path`: 一覧のファイル。無ければキャッシュの一覧(`use_cache`)か同梱の一覧。(0.12.0、#96)`user_measurements=False` は同梱の実測(作者の `measurements.json` とほかの利用者の実測のまとめ)だけを使い、キャッシュにあるこの PC の `bench` の実測を使わない(どの PC でも同じ判定になる)。`source`: `path` の出どころ(`Database.origin`。`gmf.data_status()` の `source`)。`"path"`(既定)か、アプリが `update_from_release` で取り込んだファイルなら `"release"`。ほかの値や、`path` 無しの `source` は `ValueError`。通信しない |
| `Database.generated_time` / `DataStatus.generated_time` | `datetime \| None` | (0.12.0、#96)`generated_at` を UTC の aware な `datetime` にしたもの(配布の取り込みと同じ読み方。末尾の `Z` も、小数の秒の桁数によらず読む)。無い・形が違う・タイムゾーンが無いときは `None`。2 つの一覧の新しさを比べるのに使う。`to_dict()` には入らない |
| `ModelEntry.license_unknown` / `ModelEntry.is_tool` | `bool` | (0.12.0、#96)`recommend` / `fit_list` / `search` の既定の絞り込み(`include_unknown_license=False`・`include_tools=False`)と同じ判定を 1 件ずつ: ライセンスの分からないファイルがある(ファイルが無ければ一式)、補助ツール(前処理・音の分離など)。`reviewed` と同じプロパティで、`to_dict()` には入らない |
| `gmf.self_check()` | `dict` | (0.12.0、#96)同梱物がそろっているか(exe に固めたアプリのスモーク試験など): `{"ok": bool, "version": str, "checks": {"db" \| "measurements" \| "calibration" \| "cryptography" \| "public_keys": {"ok": bool, "detail": str}}}`(`detail` は件数・版、または誤り)。例外を出さず、通信せず、何も表示しない |
| `gmf.parse_safetensors_header(header)` | `HeaderInfo` | (0.12.0、#96)`.safetensors` の見出しの JSON(先頭の 8 バイトのリトルエンディアンの長さの後ろ)→ `HeaderInfo(precision, params, dtypes)`(`precision` はバイト数が最大の dtype で `ModelFile.header_precision` と同じ値、`params` は要素の数、`dtypes` は dtype → バイト数。`to_dict()`)。形のおかしい見出しは `gmf.HeaderError`(`ValueError`)。通信しない |
| `gmf.platform_verification(system, measurements=None)` | `dict` | (0.11.0、#108)`system` の GPU の種類が確認済みか: `{"verified": bool, "kind": "nvidia/dedicated" など \| None, "models": [動いた機種の名前]}`(2 機種以上で確認済み)。`measurements` を省くと同梱の実測(この PC の bench は入れない)。CLI の JSON の `system.platform_verified` と同じ。通信しない |
| `gmf.set_language(lang)` / `gmf.get_language()` | `None` / `str` | genmodelfit が返す文(`estimate.reasons`・`notes`・gate の `detail`・エラーの文など)の表示の言語。`"en"` か `"ja"`。`None` で取り消す。**既定は英語**で、OS の言語は見ない。決め方(上が優先): CLI の `--lang`、環境変数 `GENMODELFIT_LANG`、`set_language()`、`en`。CLI の `--lang` はその呼び出しの間だけ効き(`contextvars.ContextVar`)、ほかのスレッドのライブラリの呼び出しの言語は変えない。文は、それを持つ値を作ったときの言語で決まる(`ja` で作った `Fit` の `notes` は、後で `set_language("en")` にしても日本語のまま)。変わるのは文の言語だけで、キー・ID・コード・点数・判定は変わらない。一覧に保存された自由記述(`review.note`・`license_evidence`・実測の `source` / `note`)は訳さない。ほかの値は `ValueError`(#73) |

Python から配布の一覧を取ってくる(0.7.1、#58)。`__all__` には `gmf.RemoteError`(`DatabaseError`)も入る:

```python
db = gmf.update_from_release(
    "https://github.com/laxshmate/genmodelfit/releases/download/data-latest",  # 既定
    timeout=30,
    dest=data_dir / "db.json",  # 既定: キャッシュの db.json
    force=False,  # True: 前に取り込んだものより古い一覧も受け付ける
    session=my_requests_session,  # 既定 None: genmodelfit 自身のセッション
    state_dir=data_dir,  # 既定 None: キャッシュのフォルダ
)
```

| 引数 | 意味 |
|---|---|
| `session` | `requests.Session`(例: `verify` にアプリの CA バンドルを設定した、またはプロキシ付きのもの)。この呼び出しの GET はすべてこれを使う。決まりは下 |
| `state_dir` | `release_state.json`(前に取り込んだ一覧の `generated_at`)を読み書きする場所。既定: キャッシュのフォルダ。環境変数とモジュールのグローバル変数は変えない |

`session` の決まり:
- `verify` / `proxies` / ほかのヘッダはそのまま使い、セッションは変更しない。
- `~/.netrc` の資格情報は決して送らない。すべてのリクエストに何もしない `auth` を付ける(requests が netrc を読むのは、リクエストにもセッションにも `auth` が無いときだけ)。また、requests はリダイレクトのたびに netrc を読み直すので、リダイレクトは genmodelfit が辿る(1 回ごとに `allow_redirects=False`)。
- 固定の `User-Agent`(`genmodelfit/<version> (+https://github.com/laxshmate/genmodelfit)`)は、リクエストごとに設定する。
- **限界**: セッション自身に `auth` があれば、それは呼び出し側の選択で、最初のホストにだけ使う(別のホストへのリダイレクトには決して使わない。セッションに設定した `Authorization` ヘッダもそこで落とす)。
- セッションの cookie、フック、アダプタには genmodelfit は触れない。ただしセッションは通常どおり動く。セッションに設定した `Authorization` 以外のヘッダは、リダイレクト先にも送られる(requests の既定)。レスポンスの `Set-Cookie` はセッションの cookie jar を更新する。
- 空の `auth`(`None`、`()`、`""`)は無いものとして数える。
- セッションの `verify` と `proxies` はリクエストごとに渡すので、呼び出し側の CA バンドルが `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE` に勝つ(requests#3829)。プロキシの環境変数は、セッションに無い分だけを埋める。
- 呼び出し側のセッションでは、リダイレクトを最大 10 回まで辿る(requests の既定は 30。セッションの `max_redirects` は無視する)。不正なリダイレクトの URL(例: ポート 99999)は `RemoteError`。
- 署名の確認、`MAX_BYTES`、1 回の再試行、エラー(`RemoteError`)は、`session` が無いときと同じ。

`session` が無いときは、これまでどおり `fetch.make_session()` を使う(非公開の `remote._http_get` のフックもまだ動くが、安定した API ではない)。

同等性: `gpu_usable=True, windows=True, ram_gb=1024` にすると、AI生成キットが genmodelfit 0.5.0 まで使っていた内部の呼び出し(`hardware.classify_gpu` + `comfy_precision` → `recommend.judge`、そのあと `recommend.vram_need` = 2 桁に丸める前の `memory_required_gb`)と同じ `Estimate` になる。

**名前の上書き**: `genmodelfit.recommend` は上の関数で、同じ名前のサブモジュールを隠す。だから `import genmodelfit.recommend as r` は関数に結び付く。`__all__` にある関数を使うこと。どうしてもモジュールが要るなら `importlib.import_module("genmodelfit.recommend")`(内部のもの。安定していない)。

## Database file (`db.json`) / データベースファイル

```json
{"schema_version": 1, "generated_at": "2026-10-20T03:17:00Z", "sources": {…}, "models": [ModelEntry, …]}
```

`generated_at` は常に、`Z` で終わる タイムゾーン付きの UTC の ISO-8601 文字列。

次のものは、`schema_version` を変えずに足した(任意の項目。古い読み手は知らないキーを無視する: `from_dict` が捨て、`check_entry` は知っているキーだけを確かめ、そこで `source` は自由な文字列。`tests/test_appinfo.py` が古い読み手の写しで同梱の一覧を読む):
- `downloads`、`likes`、`license_from`、`repo_license`
- (PR #13 のレビュー)`ModelEntry.license_name` / `license_evidence` / `files_complete` / `size_floor_bytes` と `ModelFile.license_url` / `license_path` / `license_evidence`
- (#3)`ModelFile.head_status` / `head_checked_at`
- (#30)`ModelEntry.workflow_precision` / `precision_source` と `ModelFile.repo_precision` / `repo_precision_source`
- (#36、0.5.0)`ModelEntry.languages` / `runtime` / `description` / `card_languages` と `source: "manual"`
- (#81、0.12.0)`ModelFile.optional` / `optional_replaces`

task の `tool` は、既知のタグが無い Image Tools / Video Tools のテンプレートのために足した。

**一覧を読むときに型を確かめる**(0.9.1、#63。`db.check_entry`):
- エントリ、ファイル、テンプレート、review のすべての既知のキーが、決まりの型を持たなければならない(表に `| null` とあるところは null)。最上位の `generated_at` / `sources` も同じ。
- そうでなければ `DatabaseError` で、何も読み込まない。
- null のリスト(`content_evidence`、`discovered_from`、`license_evidence`)と null の `languages` / `description` は、空として読む。
- これに失敗するキャッシュの一覧は飛ばして、同梱の一覧を使う(`update` の前の一覧としても)。
- 知らないキーは、これまでどおり無視する。

`sources`(`genmodelfit update` が書く。[sources.md](sources.ja.md)):

| キー | 意味 |
|---|---|
| `comfyui-templates` | `url`(テンプレートの `index.json`)、`license`(テンプレートのリポジトリの、`MIT`)、`templates_total`、`local`、`skipped`(理由別: `api` / `not_open_source` / `no_size` / `category`)、`loaded`、`failed` |
| `huggingface` | `api`(Hub API の URL のパターン)、`repos`(API 呼び出し数)、`failed`、`rate_limited`(429 の待ち)、`rate_limit_waited_s`、`failures`(最初の 20)、`unauthorized`(Hub API が 401 を返したリポジトリ id。匿名の API は、非公開と存在しないの両方で 401 を返す。ライセンスは不明のまま。0.2.0)、`unauthorized_missing`(そのうち存在しないと確認できたもの、`repo_precision.KNOWN_MISSING`)。`update --inspect-only` の後: `unauthorized_recheck` = `{unauthorized, private_or_missing, missing, readable, failed, checked_at}`(それらのリポジトリをもう一度問い合わせたもの。`readable` のものは次の完全な `update` でライセンスが付く) |
| `huggingface-search` | `queries`、`candidates`、`added`、`skipped`(理由別。`duplicate_weights` を含む)、`excluded`、`failed_queries`、`all_time_missing`(通算のダウンロード数が無い候補) |
| `partial` | bool: 上流のデータの一部を取れなかった(そのとき `update` は、`--allow-partial` が無ければ終了コード 1) |
| `partial_counts` | `templates_failed`、`hf_repos_failed_transient`、`entries_affected`、`hf_search_failed`、`rate_limit_budget_exhausted`、`carried_over` |
| `license_policy` | ライセンスの規則の版(`update.LICENSE_POLICY`、今は 5)。値は、前の一覧が同じ版のときだけ引き継ぐ |
| `civitai` | 使わない理由 |
| `size_check` | `templates`、`equal`、`within_10pct`(索引の `size` とファイルの合計) |
| `counts` | `models`、`modality`、`content`、`source`、`license`(null は不明)、`license_tier`、`files_complete_false` |
| `head_check` | `checked_at`、`urls`(HEAD を送った重複しない HF の URL)、`status`(ステータスコードごとの数)、`unchecked`(答えなし)、`not_sent`(ほかのホストのファイル。ホストごと) |
| `file_headers` | `files`、`urls`(HF の主な重みのファイル)、`from_cache`、`fetched`、`failed`、`skipped_offline`、`errors`(最初の 20)、`checked_at`、`precision_unknown`(精度がまだ null のエントリ) |
| `sampler_stages` | `update --inspect-only` の後だけ: `templates`、`failed`、`multi_stage`(サンプラーの組が 2 つ以上のエントリ)、`workflow_precision`(ローダのノードから精度を推したエントリ)、`optional_parts`(任意の部品のあるエントリ。0.12.0、#81)、`checked_at` |
| `repo_precision` | `files`(見出しも名前も精度を教えない、HF の最大の主なファイル)、`repos`、`from_cache`、`fetched`(リクエスト)、`hf_api`、`config`、`unknown`(それぞれで決まったファイル / どちらでも決まらなかったファイル)、`skipped_offline`、`unauthorized`(API が 401 を返したリポジトリ)、`size_mismatch`(大きさが合わず API の dtype を使わなかったリポジトリ)、`errors`(最初の 20)、`checked_at`、`precision_unknown`(精度がまだ null のエントリ。最終)、`precision_source`(出どころごとのエントリ数。`unknown` = null) |
| `manual` | `models`(`src/genmodelfit/data/manual.json` から手で足したモデルの数)、`ids`(0.5.0) |
| `app_fields` | `languages_prompt`、`languages_speech`(空でないリストを持つエントリ)、`runtime`、`description`、`from_card`(保存した Hugging Face のカードの言語 `card_languages` を持つエントリ)(0.5.0)、`card_languages_carried`(カードの言語を前の一覧から引き継いだエントリ。完全な `update` だけ) |
| `comfyui` | `latest_release`(最新の ComfyUI リリースのタグ。例: `v0.42.1`。読めなければ null)、`url`、`checked_at` |
| `excluded` | 除外の規則で落としたモデルの数(名前は残さない) |
| `review` | `{ok, pending, marked: {reason: count}, rejected_skipped}`。公開する一覧は `withheld_pending` を足す(「review」を参照) |
| `warnings` | str[] |
| `generator` | 一覧を書いた `genmodelfit <version>` |

## update

`genmodelfit update --json` は、外枠に `output`(書いたパス)と `sources`(上)を付けて出す。
失敗したときは `error` を出して終了コード 1。前のキャッシュは触らない。

`update --from-release --json` は、小さなオブジェクトを出す: `genmodelfit`、`command`(`update`)、`models`(**int**: 取り込んだモデルの数)、`generated_at`、`output`、`status`(`source: release` の DataStatus)。

`update --json`(作り直し / `--heads-only` / `--fields-only` / `--inspect-only`)には `status`(書いた一覧の DataStatus)も入る。`source` は、キャッシュなら `cache`、`--output` でほかの場所なら `path`。

`update --heads-only`:
- 今のデータベース(`--db`、キャッシュまたは同梱)を読む。
- HEAD と ComfyUI の版だけをやり直す。
- 書き戻す(`--output`、既定はキャッシュ)。

`update --fields-only`:
- 同じように今のデータベースを読み、アプリ用の項目(`languages` / `runtime` / `description`。`src/genmodelfit/data/overrides.json`、表、Hugging Face のカードから)と、手で足したモデル(`src/genmodelfit/data/manual.json`)だけを作り直す。
- カードをもう一度読む(Hub API)。手で足したモデルだけは、HEAD を送り、ファイルの見出しを読み、ライセンスを上流のカードと比べる。
- `--offline` では何も送らず、保存した `card_languages` を使う。
- ほかのエントリのファイル、ライセンス、精度、HEAD の結果には触れない。ただし、手で足した部品(`src/genmodelfit/data/manual.json` の `components`。上)は、そのエントリに足す(新しいファイルの HEAD だけ。`--offline` では無し)。

`update --inspect-only`:
- 同じように今のデータベースを読み、ファイル自身から読むものだけをやり直す。
  - 主な重みのファイルの見出し(`header_precision` / `header_params`、そのあと `precision`)
  - 公式テンプレートの `sampler_stages` と `workflow_precision`
  - リポジトリの精度(`repo_precision`: Hub API / `config.json`。`<cache>/repo_precision.json` にキャッシュ)
- `sources.huggingface.unauthorized` のリポジトリには、Hub API をもう一度問い合わせる。
- ほかは変えない(review の印を除く。「review」を参照)。
- `<cache>/file_headers.json`(URL と sha256 がキー)にある見出しは、もう一度読まない。リクエストは 1 つずつ、短い間をおいて送り、`Retry-After` を守る。
- `--offline` ではそのキャッシュだけを使い、何も送らない(テンプレートも読み直さない)。

実測は `measurements.json`(`{"unit": "GiB", "unit_note": "…", "measurements": [Measurement, …]}`)にある。次の 3 つを合わせて読み込む。
- 同梱の `measurements.json`
- `measurements_community.json`(community の行。contrib を参照)
- `<cache>/measurements/*.json`

NaN / Infinity と負の値は、読むときに断る(0.9.0)。
