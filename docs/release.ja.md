# リリースとデータの配り方

[English](release.md) · **日本語**

genmodelfit の版のリリースと、モデル一覧の配り方(パッケージのリリース・署名つきのデータ配布・毎週の一覧の更新・署名の鍵)をまとめたページです。
管理者(メンテナ)向けです。一覧を使うだけのアプリの開発者は「[データの配布](#データの配布)」だけ読めば足ります。

## 目次

- [GitHub の environment(2026-10-20 に設定)](#github-の-environment2026-10-20-に設定)
- [版を出す手順](#版を出す手順)
- [実測の Issue を受け取る(#113)](#実測の-issue-を受け取る113)
- [データの配布](#データの配布)
- [毎週の一覧の更新(#19)](#毎週の一覧の更新19)
  - [PR で確かめること](#pr-で確かめること)
  - [GPU 表の見張り(#103)](#gpu-表の見張り103)
  - [毎週のスキル(genmodelfit-weekly、#42)](#毎週のスキルgenmodelfit-weekly42)
  - [手元で同じ流れを試す](#手元で同じ流れを試す)
  - [メンテナが一度だけ設定すること](#メンテナが一度だけ設定すること)
- [鍵](#鍵)
  - [鍵を替える(定期・または念のため)](#鍵を替える定期または念のため)
  - [鍵が漏れたら](#鍵が漏れたら)
- [使わないものの確認](#使わないものの確認)

3 つの流れがあります。

| 流れ | 何を配る | きっかけ | 配り先 |
|---|---|---|---|
| 版のリリース | パッケージ(sdist と wheel) | `v*` のタグを push(タグは管理者だけが作れる) | PyPI と GitHub Release |
| データの配布 | モデル一覧 `db.json` と署名 `db.json.sig` | main の db.json が変わった push・手動実行 | GitHub Release `data-latest` |
| 毎週の一覧の更新 | 作り直した `db.json` の PR(配らない) | メンテナの PC のスクリプト(週 1 回)。weekly-data.yml は手動の予備 | PR(安全な週だけ自動でマージ。下の「[毎週のスキル](#毎週のスキルgenmodelfit-weekly42)」) |

- データの配布は `v*` のタグではないので、PyPI には出ません。
- main はブランチの保護で PR を必須にし、force push と削除を禁じている。

## GitHub の environment(2026-10-20 に設定)

| environment | 使えるところ | 承認 | シークレット |
|---|---|---|---|
| `pypi` | `v*` のタグだけ | なし(2026-10-21 にメンテナが外した。代わりにルールセットで `v*` のタグを管理者だけにする) | なし(Trusted Publishing) |
| `data-signing` | `main` ブランチだけ | なし | `GENMODELFIT_SIGNING_KEY`(署名の秘密鍵) |

- リポジトリ全体のシークレットには何も置かない。
- 秘密鍵は `data-signing` だけにあるので、main 以外のブランチや PR のワークフローからは読めない。
- data.yml でも、署名する 1 ステップにだけ環境変数で渡す。

設定の確認:

- `gh api repos/laxshmate/genmodelfit/environments`
- `gh secret list -R laxshmate/genmodelfit`(空であること)
- `gh secret list --env data-signing -R laxshmate/genmodelfit`

## 版を出す手順

**公開はメンテナの承認を得てから。**

1. ほかの利用者の実測(data/contrib)を同梱のまとめに入れる(#90)。
   - 提出の PR は data/contrib だけを変えるので、マージしただけでは利用者に届かない。届くのは版のリリースだけ
     (毎週のデータの配布は db.json だけ)。
   - `genmodelfit contrib aggregate --check` で、まだ入っていない提出を数える(あれば終了コード 1)。最初の提出をマージするまでは data/contrib が無いので、`--check` は終了コード 1(フォルダが無い)になる。それで正しい。
   - あれば `genmodelfit contrib aggregate --output src/genmodelfit/data/measurements_community.json` で作り直す
     (使った提出の sha256 が `submissions` に入る。試験はそれだけをまとめ直して一致を確かめる)。
   - まとめは git の履歴のある(浅くない)clone で作る。出どころ(同じ人が送った提出は 1 つ。#113)を git の履歴で数えるため(#90。浅い clone・git の外では提出ごとに数え、そう出力する)。0.11.0 から、出どころをファイルごとに数える(`file:…`)ときは `--output` が書き出さない(終了コード 1)。1 人の何件ものファイルが何人分にも数えられ、段階を決められてしまうため(#95)。`--allow-file-sources` で書き出せるが、試験・手元の確かめだけで、版を出すときは使わない。`--check` は `file_sources` を出す(同梱のまとめは 0 でなければならない。試験が確かめる)。
   - 直した提出を出し直した人がいれば、古いファイルをメンテナの PR で外す(PR の説明に理由を書く。提出の PR は足すだけで、消せない)。この PR では `Contrib` のワークフローの「足すだけ」の検査が失敗するが、それでよい(提出の PR だけを通す検査。メンテナが確かめてマージする)。
   - `--check` が「使われない行」(`files_fp` が今の一覧と違う行)を出したら、上流のファイルが変わったということ。行は判定に使われないので、そのままでもよい。新しいファイルで測り直した提出が来るのを待つ。
   - そろった組(`consistent`)が変わったら `python -m genmodelfit.calibrate` で合わせ直し、
     docs/accuracy.ja.md と docs/accuracy.md を直す。
2. `src/genmodelfit/_version.py` の `__version__` を上げ(pyproject.toml は hatch がここから読む)、変更を
   `CHANGELOG.md` の `## [<版>] - Unreleased` の節に書く。
3. **タグを打つ前に、その節の `Unreleased` を出す日の日付にする**(`## [0.12.0] - 2026-11-02`)。`CHANGELOG.md` の
   末尾のリンクの定義も直す。この節が GitHub Release の本文になり、タグの版の節が無い・日付が無い・中身が空なら
   release.yml は公開の前に止まる(手元では `python tools/changelog_notes.py <版>` で本文か理由が見られる)。
4. PR で main に入れる。マージは普通のマージコミット(squash・rebase はしない)。
   PR では ci.yml が動かないので、PR の説明に `tools/ci_local.py --linux` の結果を書く。
5. マージの後に main で動いた ci.yml が緑なのを確かめる。
   マージで `src/genmodelfit/data/db.json` が変わっていれば、data.yml が一覧も配る(「[データの配布](#データの配布)」)。
6. **タグを push する**。main の先頭に付け、`_version.py` の版と合わせる:

   ```powershell
   git switch main; git pull
   git tag v<版>
   git push origin v<版>
   ```
7. `.github/workflows/release.yml` が次の順に進む。
   - test: ruff と pytest(3.12)
   - build: `uv build` → タグと `_version.py`・wheel / sdist の名前の版が一致するか確認 → `CHANGELOG.md` からタグの版の節を
     取り出す(`tools/changelog_notes.py`。日付が無ければ失敗)→ `twine check --strict`
   - publish: environment `pypi`(承認者なし。`v*` のタグはルールセットで管理者だけが作れる)で PyPI に公開する。
     Trusted Publishing なので API トークンは使わない
   - github-release: 同じ dist を付け、`CHANGELOG.md` の節を本文にした GitHub Release `v<版>` を作る
8. **確かめる**(まっさらな venv で。キャッシュも別の場所にする):

   ```powershell
   uv venv -p 3.12 $env:TEMP\gmf-check
   uv pip install --python $env:TEMP\gmf-check\Scripts\python.exe genmodelfit==<版>
   $env:GENMODELFIT_CACHE_DIR = "$env:TEMP\gmf-check-cache"
   & $env:TEMP\gmf-check\Scripts\genmodelfit --version          # genmodelfit <版>
   & $env:TEMP\gmf-check\Scripts\genmodelfit system             # Data の欄: List: bundled …
   & $env:TEMP\gmf-check\Scripts\genmodelfit update --from-release   # 署名を確かめて取り込む
   & $env:TEMP\gmf-check\Scripts\genmodelfit system             # List: release、HEAD checks の期限
   & $env:TEMP\gmf-check\Scripts\genmodelfit recommend --vram 8 --ram 32 -n 2
   ```
9. AI生成キット(laxshmate/local_model_ai_gen_kit)に公開を知らせる。

最初の公開(v0.1.0、2026-10-20)も同じ手順で、その前に最初の版の積み重ねた PR を順にマージした(どれもマージの前に土台を main に付け替えた)。

手動実行と PyPI について:

- 手動実行(workflow_dispatch)はビルドまでで、公開しない。
  タグを選んで手動実行しても、publish と github-release は `github.event_name == 'push'` の条件で飛ばす。
  タグを打つ前の試し用(`CHANGELOG.md` の日付の確かめはタグで動かしたときだけ)。
- PyPI では、アカウント `laxshmate`(2 段階認証あり)に、`genmodelfit` / `laxshmate/genmodelfit` / `release.yml` /
  environment `pypi` の Trusted Publisher を登録してある。
- 公開した版は消せない(取り下げ=yank だけ)。版の番号は使い回せないので、直すなら次の版を出す。

## 実測の Issue を受け取る(#113)

`genmodelfit share --issue`(と `genmodelfit share` の送り方の 2「Issue」)は、実測を入れた Issue のフォーム
`.github/ISSUE_TEMPLATE/measurement.yml` を開きます。
ワークフロー `.github/workflows/measurement-intake.yml` が、`measurement` の印の開いている Issue を、作られたときと
本文が編集されたときに受け取ります(題だけの編集・印を付けただけでは動かないので、1 つの Issue を 2 回扱いません)。
ジョブは 2 つです:

1. `check`(読むだけのトークン): genmodelfit を入れ、`genmodelfit contrib intake` がイベントのファイルから Issue の本文を
   データとして読み(台本に `${{ }}` で埋め込まない)、`contrib validate` と同じ検査と `share` の個人の情報の点検(実行機
   自身のホスト名・ユーザ名の確かめは除く)をし、同じ中身が既に `data/contrib` にあれば断り、同じアカウントがその GPU・
   モデルで既に送った数を数えます(git の履歴をすべて取る)。結果のファイルは artifact にします
   ([schema.ja.md「contrib」](schema.ja.md#contrib))。
2. `publish`(contents・pull-requests・issues の書き込み。何も入れず、`git` と `gh` だけ):
   - 通って新しいとき: `data/contrib/<GPU>/<日付>-<sha8>.json` をブランチ `measurement/issue-<N>` に push し(コミットに
     `Submitted-by: github:<アカウント>` と、名前を変えても変わらない `Submitted-by-id: <アカウントの数の ID>`)、PR を作り(本文は Issue を書いた人・そのアカウントの前の提出の数・メンテナへの
     確認・`Closes #N`)、Issue にリンクをコメントします。
   - この Issue の開いている PR と同じ中身: 何もしません(push もコメントもしない)。
   - 別の Issue の開いている PR と同じ中身: その PR のリンクをコメントし、何も作りません。
   - PR ができたあとに、通る別の中身に編集された: PR(題・本文・ファイル)を直し、PR と Issue の両方にコメントします。
     前に通らない中身で閉じた PR は、先に開き直します。
   - 断ったとき: 理由(英語と日本語)を Issue にコメントします。同じ理由の組は 1 回だけ(コメントの隠した印で見分け、
     同じ理由を繰り返さない)。Issue に開いている PR があれば、PR にも理由を書き、印 `measurement-invalid`(無ければ
     作る)を付けて **閉じます**。Issue が直ったら、この印のある PR だけを開き直し(印は外す)、メンテナが閉じた PR は
     開き直しません(Issue にそう書きます)。
   - 別の Issue の PR と同じ中身に編集された: その PR を知らせ、この Issue の PR を同じように閉じます。
   - 見るのはボット自身の PR だけ(`isCrossRepository` が偽で、作者が GitHub Actions。`gh` は `app/github-actions` と
     返す)。フォークの同じ名前のブランチの PR は見ません。
   PR をマージすると Issue は閉じます。

マージの前に確かめること: PR の本文の確認(`Contrib` のジョブのまとめと同じ。`GITHUB_TOKEN` で作った PR では contrib.yml は
動きません)と、同じアカウントがその GPU・モデルで既に送った数の行(1 つのアカウントは何件送っても 1 つの出どころだが、別のアカウント・
その人の組織・名前を変えたあとの Pull Request は別の人になる。Pull Request や Issue を書いた人を確かめる)。マージは、ボットの Pull Request も人が出した実測の Pull Request も **「Create a merge commit」だけで、squash・rebase は
しない**(「版を出す手順」と同じ決まり)。出どころはマージのコミットで数えるため(`Merge pull request #N from laxshmate/measurement/issue-M` のときだけ `Submitted-by:` の行を信じる)。

一度だけの設定(リポジトリの Settings):
1. Settings → Actions → General → 「Workflow permissions」→「Allow GitHub Actions to create and approve pull requests」に
   チェック → Save。
2. Issues → Labels → New label → 名前 `measurement` → Create label。
3. Settings → General →「Pull Requests」: 「Allow merge commits」はチェックのまま、「Allow squash merging」と
   「Allow rebase merging」のチェックを外す(出どころは GitHub のマージのコミットで数えるため)。メンテナの毎週の
   スクリプト(リポジトリの外)は PR を `gh pr merge --merge` でマージするので、そのまま動きます。このリポジトリの中に
   `--squash`・`--rebase` を使うものはありません。
   同じページの「Allow merge commits」の下の「Default commit message」は「Default message」にする。出どころのアカウントを
   マージのコミットの 1 行目 `Merge pull request #N from <owner>/<branch>` から読むため(「Pull request title」などに
   すると、その行が変わる)。
4. main を、管理者も含めて PR なしで push・マージできないように守る: Settings → Rules → Rulesets → New ruleset →
   New branch ruleset → 名前 `main` → Enforcement status「Active」→ Bypass list は空のまま → Target branches で
   「Include default branch」→「Require a pull request before merging」と「Block force pushes」にチェック → Create。
   (昔からのブランチの保護なら: Settings → Branches → Add branch protection rule → `main` →「Require a pull request
   before merging」と「Do not allow bypassing the above settings」にチェック → Create。)
ワークフローが使うのは `GITHUB_TOKEN` だけです(シークレットは使わない)。

## データの配布

`.github/workflows/data.yml` は、**main にコミットされた `src/genmodelfit/data/db.json` をそのまま**署名して配る。
上流から作り直さない。作り直して PR にする流れは下の「[毎週の一覧の更新](#毎週の一覧の更新19)」。
きっかけは、main の db.json が変わった push と手動実行。

1. sign:
   - environment `data-signing`(main だけ)で動く。
   - コミットされた一覧を読めることを確かめ、署名し、同梱の公開鍵で確かめる。
   - `contents: read`。秘密鍵はこのジョブの「署名する」ステップだけ。
   - main 以外のブランチを選んだ手動実行は `if: github.ref == 'refs/heads/main'` で動かない。
2. upload: Release `data-latest`(prerelease)の `db.json` と `db.json.sig` を置き換える。ここだけ `contents: write`。
3. verify:
   - 置いた直後に、まっさらな venv で `update --from-release` を流す。venv は 2 つで、main のコードを入れたものと、`genmodelfit==0.5.0`(`review` を知らない、いちばん古い対応の版)を入れたもの。
   - source が release・期限切れでない・100 件超・自動のおすすめが 1 件以上なのを確かめる。

checkout は `persist-credentials: false`。

同梱の一覧の HEAD の確認は 30 日で切れる(切れると自動のおすすめが 0 件になる)。
普段は毎週の PR(下)をマージすれば延びる。毎週の PR が止まっているときは、切れる前に次をする:

1. `genmodelfit update --heads-only --no-cache --output src/genmodelfit/data/db.json` で取り直す。
2. PR にして main に入れる。data.yml が配り直す。

今の一覧の期限は `genmodelfit system --no-cache` の Data の欄(`valid until`)で分かる。

使う側:

```powershell
genmodelfit update --from-release                       # キャッシュに取り込む
genmodelfit update --from-release --output db.json      # 確かめた一覧を指定の場所に書く(キャッシュは触らない)
genmodelfit update --from-release --force               # 前に取り込んだ配布より古くても取り込む(下記)
```

```python
from genmodelfit.remote import update_from_release

db = update_from_release()  # 失敗したら RemoteError(DatabaseError の仲間)
```

取り込みの手順:

1. `db.json` と `db.json.sig` を取る。それぞれ 64 MB まで。読みながら数え、超えたらそこでやめる。
2. 同梱の信じる公開鍵(`src/genmodelfit/data/signing_keys/*.pub`)の**どれか 1 つ**で署名が合うか確かめる。
   - 合わなければ 5 秒待って**1 回だけ**取り直す。GitHub Release の資産は 1 つずつ置き換わるので、data.yml の upload の途中に取ると、新しい `db.json` と古い `db.json.sig` の組になることがあるため。
   - 取り直しても合わなければ失敗。
3. `schema_version` と形を確かめる。`generated_at` は必須で、タイムゾーン付きの ISO 8601(`Z` も可)。
4. 前に取り込んだ配布より古くないか確かめる(巻き戻しの防止)。
   - 比べる相手はキャッシュの `release_state.json`(前に取り込んだ配布の `generated_at`)。
   - キャッシュの `db.json` は手元の `genmodelfit update` も書くので比べない。
   - 日時は UTC に直して比べる(`Z` と `+00:00` は同じ時刻)。同じ日時の取り直しは通す。
5. `release_state.json` を進めてから、書き出し先(既定はキャッシュの `db.json`)を原子的に置き換える。

どれかが失敗したら:

- 書き出し先(無ければ同梱のデータ)はそのまま。
- メッセージを出して終了コード 1(`--json` なら `{"error": ...}` の形)。
- ほかのプログラムがキャッシュを開いていて置き換えられないとき(Windows)も同じ。

`release_state.json` の日時が未来になってしまった(作り直した一覧の日時が戻った等)とき:

- 普段の取り込みは「古い一覧です」で止まる。
- 正しい配布だと確かめたうえで `--force` を付けると取り込み、記録もその日時に直す。
- `release_state.json` が壊れているときは無いものとして扱い、次の取り込みで書き直す。

署名は Ed25519。`db.json.sig` は 64 バイトの署名を base64 にした 1 行。
署名の確認だけ手でやるなら `python -m genmodelfit.signing verify db.json`(同梱の信じる鍵のどれかで確かめる)。

## 毎週の一覧の更新(#19)

llmfit の weekly-model-update.yml にならい、上流から作り直した一覧を毎週 PR にする。**公開するのは人が見て
マージした一覧だけ**(作り直しと公開を分ける)。

```text
weekly-data.yml(手動の予備。#42 から定期の実行はメンテナの PC の起動スクリプト)→ PR automated/weekly-data → メンテナが見てマージ
  → data.yml が main の db.json に署名して data-latest に置く → 使う側は genmodelfit update --from-release
```

`.github/workflows/weekly-data.yml` は 2 つのジョブでできている。

1. build(`contents: read` だけ):
   - キャッシュを戻す(`actions/cache/restore`。`file_headers.json`・`repo_precision.json`)。
     鍵は毎回違う run_id で、restore-keys で一番新しいものを取る。
   - `python -m genmodelfit.weekly --out weekly-out --apply src/genmodelfit/data/db.json --rate-limit-budget 900` を流す(時間の上限 90 分)。
     - `genmodelfit update` と同じ作り直し(テンプレート・HF・HEAD・ファイルの見出しと精度)。
     - 続けて `update --inspect-only` と同じ読み直し(同梱の一覧と同じ 2 段)。
     - 引き継ぎの元は main の一覧。
     - HF の 429 は Retry-After を守って待ち、待ち時間の合計が 900 秒を超えたら止まる(HF_TOKEN は使わない)。
   - 確かめ。どれかに落ちたら終了コード 1 で、PR を作らない。理由は `weekly-out/result.json` の `problems` と実行の要約にある。
     - 形(読み直し)。
     - 自動のおすすめが 0 件でない。
     - **区分ごとに main の半分未満に減っていない。** 区分は全体・HF・公式テンプレート・無検閲・自動のおすすめ。元が 4 件以上の区分だけ見る。
     - **HEAD の期限(`expires_at`)が 7 日より先。** 作り直した直後なのに近ければ HEAD の取り直しが効いていない。`genmodelfit system --json` の側からも二重に確かめる。
     - **新しく無検閲の印が付いた項目が 5 件まで。** 超えたら選び方の規則が緩んだ疑い。手動実行で `allow_many_uncensored` を選んだときだけ、失敗にせず本文の先頭に警告する。
   - キャッシュを保存する(`if: always()`。失敗・時間切れでも残す)。GitHub は 7 日使わないキャッシュを消す。
   - 新しい一覧を入れた状態で `ruff check`・`ruff format --check`・`pytest -q` を流す(CI の代わり。下の注意)。
   - `genmodelfit contrib aggregate --report` の結果を PR の本文に足す。
     - この手動の予備だけ。メンテナの PC の起動スクリプトは流さない。
     - data/contrib がまだ無ければ「提出 0 件」と書く。
     - 報告には、ほかの利用者の実測で変わる段階の表(1 件の提出も含む)が入る。2026-10-22 から両方の向きに使うので、上がる・下がるの両方が出る。
   - `weekly-out/`(db.json・diff.json・pr_body.md・result.json・gpu_watch.json・gate.json・system.json)を artifact に残す(30 日)。
2. pr(`contents: write`・`pull-requests: write` はこのジョブだけ。署名の鍵の environment は使わない):
   - `result.json` が ok の一覧だけを入れる。
   - `peter-evans/create-pull-request`(コミットで固定)で PR を作る・更新する。
   - ブランチ `automated/weekly-data`、ラベル `automated`・`data-update`、本文は `weekly-out/pr_body.md`。

決めたこと:

- **PR の本文の上流の文字はすべて無害にする。**
  - 対象はモデルの名前・ID・ライセンスの名前・根拠・URL・リポジトリ名・失敗の理由。`weekly.md()` で行う。
  - HTML の文字は実体参照に(`<!--`・`<img>`・`</details>` が効かない)。
  - Markdown の記号はバックスラッシュで(リンク・画像・強調・表の区切り)。
  - `@` の後に幅の無い空白を入れる(メンションしない)。
  - 長い本文は行の切れ目で切り、開いたままの `<details>` を閉じる。
  - #34 の実測の集計(利用者の出した値を含む)は `weekly.code_block()` の囲みに入れる。
  - 実行の要約(`$GITHUB_STEP_SUMMARY`)にも同じ本文を出す。
- **自動ではマージしない**(auto-merge も使わない)。ここでは署名も公開もしない。
- **GITHUB_TOKEN で作った PR では CI(ci.yml)が自動で動かない**(GitHub の仕様。ループを防ぐため)。
  - 代わりに同じワークフローの build で、新しい一覧を入れた状態の ruff と pytest を通してから PR を作る(PR の本文にもそう書く)。
  - 同じリポジトリのブランチの PR でも ci.yml は動かない(#68)。
  - 確かめるなら手元で `python tools/ci_local.py --linux` を回すか、マージの後に main で動く ci.yml を見る。
- **一部を取得できなかったとき**(`sources.partial`): 既定は失敗(PR を作らない)。
  - 手動実行で `allow_partial` を選んだときだけ、「一部欠け」の警告を本文の先頭に付けて PR にする。
  - 条件は、欠けたのが HF のリポジトリの一時的な失敗だけで、前の一覧から引き継げたこと(`weekly.partial_problems`)。次の 4 つがすべて要る:
    - 429 の待ち時間の上限に達していない
    - HF の検索が失敗していない
    - 読めないテンプレートが無い
    - 前の一覧と `license_policy` が同じ
  - それ以外の欠け方は、消えた・古いままの項目が「変化」に見えてしまうので PR にしない。
- **試験が落ちたら PR を作らない。** 上流の変化で試験が落ちたときは、直す PR を別に作る。
  例: 2026-10 に HF のリポジトリの名前が変わり、出どころの表の当て直しが合わなくなった。

### PR で確かめること

本文(`genmodelfit diff` と同じ中身)の「注意」と表を見る:

- **⚠ ライセンスが寛容な側に変わった項目。** 段階が下がった、または許可リストに入った。不明 → 何か、も含む。
  自動のおすすめに出る側の変化なので、`license_url` の原文で確かめる。
- **自動のおすすめに入った・外れた項目。** 両方の一覧に同じ方針を当てる: ComfyUI の版は新しい一覧の記録、HEAD の古さは見ない。
  外れた項目に AI生成キットが使うものが無いか。
- **新しく HEAD が失敗した URL**(gated になった・消えた)。前から失敗している URL は畳んである。
- **消えた項目**(上流から消えた・検索の上位から外れた)。
- **新しく無検閲の印が付いた項目**の根拠。未成年の性的表現を目的とするものは除外する([sources.ja.md](sources.ja.md))。
- 実測のあるモデルのファイルが変わっていたら、`python -m genmodelfit.calibrate` で直す PR を別に作る。
- **確認待ち(review)の項目**と、`review approve` で付けた根拠(`evidence`)の URL(#42。下の「[毎週のスキル](#毎週のスキルgenmodelfit-weekly42)」)。
- 全部の違いは artifact の `diff.json`(形は [schema.ja.md](schema.ja.md#diff)「diff」)。
- 「GPU 表の見張り」の候補(下)。マージは止めないので、候補は別の Issue にする。

### GPU 表の見張り(#103)

毎週の更新(`python -m genmodelfit.weekly`)は、一覧の作り直しのあとに GPU の登録の表(`src/genmodelfit/gputables.py`。全部の行は [gpu-inventory.ja.md](gpu-inventory.ja.md))を公式の一覧と照らす(`gpuwatch.py`)。

- 読む出典(`fetch` の HttpFetcher。User-Agent を付け、~/.netrc を送らない):
  - pci.ids(pciutils/pciids の raw)
  - ROCm の GPU hardware specs と、Radeon・Ryzen の Windows・Linux の互換表
  - TheRock の `SUPPORTED_GPUS.md`・`RELEASES.md`(GFX target lookup table)
  - PyTorch の「Getting Started on Intel GPU」(stable)の Hardware Prerequisite の表
  - NVIDIA の cuda-gpus・cuda-legacy-gpus
  - PyTorch の最新の版(GitHub の releases API)、その版の `torch/cuda/__init__.py`(`PYTORCH_RELEASES_CODE_CC`・`DEVICE_REQUIREMENT`。2.14 から build_cuda.sh の arch の一覧はここに写され、PyTorch の試験が一致を見る)と `.ci/docker/manywheel/build.sh`(`PYTORCH_ROCM_ARCH`)
  - download.pytorch.org の torch の索引(PEP 503 の HTML。PEP 691 の JSON は返さない: 2026-10-27 に `Accept: application/vnd.pypi.simple.v1+json` で text/html が返った)
  - CUDA のリリースノートの「Minimum Required Driver Version for CUDA Minor Version Compatibility」の表
  - ComfyUI の最新のタグ(releases API)の `comfy/model_management.py`。**import せず** ast で名前の一覧を読む(GPL-3.0 のコードを動かさない)
- 表は見出しの言葉で読む(列の位置に頼らない)。出典ごとに時間の上限(接続 10 秒・読み込み 60 秒、やり直し 4 回)があり、読めなければ「読めなかった出典」に書いて続ける。見張りが何で失敗しても例外を外に出さない。
- 候補は 2 つの照らし方で出す:
  - **表と照らす**: 公式の製品の gfx・compute capability が表と合うか、表に無い gfx・CUDA の版(wheel が出たもの)・CUDA の主番号が無いか、新しい pci.ids の名前と型番 ID の表で判定が逆になっていないか、PyTorch・ComfyUI の一覧が表と同じか。
  - **前回と比べる**: `tests/fixtures/gpu_official`(表と一緒に直す、前回の公式の一覧)と比べて、新しい型番 ID・名前が変わった ID・新しい NVIDIA の名前・TheRock の状態の変化・互換表と Intel の表の増減・新しい wheel。
- 結果は PR の本文の「GPU 表の見張り」の節(候補の表と読めなかった出典)と `weekly-out/gpu_watch.json`。
- **表は自動で書き換えない。** 候補は人が一次資料で確かめ、別の Issue で `gputables.py` に行(出典と確かめた日)を足し、`python -m genmodelfit.gpuwatch --save-fixtures tests/fixtures/gpu_official` で前回の一覧を取り直し、`python tools/gpu_inventory.py` で一覧を作り直す。
- **データの自動マージは止めない。** 見張りの候補・読めなかった出典は `result.json` の `ok` にも、毎週のスキルの安全な週の判定(変わったファイルが db.json だけ・merge-check・status)にも効かない。
- 手で動かすとき: `python -m genmodelfit.gpuwatch [--json]`(通信する)。`--from-dir <dir>` は保存した生の応答(`<出典の名前>.<拡張子>`)から読む(通信しない)。毎週の更新で飛ばすなら `--no-gpu-watch`。

### 毎週のスキル(genmodelfit-weekly、#42)

2026-10-21 のメンテナの決定:

- 毎週の更新は、メンテナの PC の起動スクリプトが行う(Windows のタスク スケジューラで週 1 回)。
- 確認待ちの判断だけを Claude Code のスキル `genmodelfit-weekly` に任せる。
- **weekly-data.yml の定期の実行(schedule)はやめた。** 手動の予備(workflow_dispatch)としてだけ残す。

動かし方:

- スクリプトと設定はリポジトリの外(メンテナの PC)に置く。weekly-data.yml と同じコマンド(作り直し・確かめ・試験)を、
  この用途だけのクローン(毎回 main に合わせる)で流す。
- 確認待ちの判断だけを Claude Code のスキルに任せ、読み取り専用で動かす: ファイルを読むことと、huggingface.co・github.com・
  raw.githubusercontent.com のページを取ることだけができ、コマンド・MCP・ファイルの書き換えは使えない。
- approve は別のモデルが同じ制限で確かめ直す。
- 失敗したら Issue「毎週の更新が失敗しました」が立ち、次に成功したら閉じる。

役割を分ける。

- **Claude(スキル)はファイルを書き換えず、コマンドも動かさない**(2026-10-21 のメンテナの決定)。読むだけと、Hugging Face・GitHub の WebFetch だけ。
  - 実行役が渡す `review_queue.json` に、確認待ちの項目が入っている: id・name・modality・reasons・ファイルごとの repo・path・license・license_url・homepage・card_url。
  - Claude は一次資料(ライセンスの原文・モデルカード・上流のリポジトリ)で確かめ、判断の一覧の JSON だけを返す:

    ```json
    {"decisions": [
      {"id": "hf:org/model", "action": "approve", "evidence": ["https://huggingface.co/org/model/blob/main/LICENSE"], "note": "Apache-2.0 の原文を確かめた"},
      {"id": "hf:org/other", "action": "reject", "evidence": ["https://huggingface.co/org/other/blob/main/LICENSE"], "note": "非商用だけ", "safety": false},
      {"id": "hf:org/unclear", "action": "hold", "note": "カードにライセンスの記載なし"}
    ]}
    ```

  - 理由が安全(未成年の性的表現など)の reject は `"safety": true`(名前を残さず sha256 だけ記録する)。
  - 分からないものは hold(pending のまま。自動のおすすめにも data-latest にも出ない)。
- **実行役のスクリプト**(メンテナの PC。スキルの外)が、作り直し・試験・判断の当て込み・merge-check・PR を行う:
  1. `python -m genmodelfit.weekly --out <dir> --apply src/genmodelfit/data/db.json` を流す。引き継ぎの元と比べる元は main の db.json。終了コードが 1 なら止める(`result.json` の `problems`)。
  2. `<dir>/review_queue.json` を Claude に渡し、判断の一覧を受け取る。
  3. `python -m genmodelfit.review apply <decisions.json> --db src/genmodelfit/data/db.json --max-approve 10 --json` を流す。
     - 形を厳しく確かめる。断るもの: 知らない鍵・action、確認待ちでない id、同じ id、根拠の無い approve、https でない・ホストのない根拠、500 文字を超える・制御文字を含む note、approve の上限超え。
     - 1 つでもおかしければ何も当てない(終了コード 2)。
     - approve は `decided_by: claude-weekly`。
     - 出る要約(件数と項目ごとの結果)の文字は `weekly.md` で無害にしてあり、PR の本文にそのまま置ける。
  4. ブランチにコミットして push し、PR を作る。**機械の確かめがすべて通ったときだけ**マージする:
     - 変わったファイルが `src/genmodelfit/data/db.json` だけ。approve・reject を当てた週は status が変わるので、下の条件により必ずメンテナが見ることになる。
     - `python -m genmodelfit.weekly merge-check --base <main の db.json> --head <ブランチの db.json> --json` が終了コード 0(`safe: true`)。分類はファイルの JSON から計算し直す(スキルの報告を信じない)。人気のかたよりの注意(`popularity_bias:<種類>`。scoring.ja.md「人気」)が出た週も safe にしない(0.11.0)。
     - 確認の印の status が変わっていない。承認・確認待ちへの変化は分類でも判断が要るになる。
     - 手元の試験(ruff と pytest。新しい一覧を入れた状態)が通った。PR では ci.yml が動かないので(手元で通してから push する決まり。2026-10-22)、CI は待たない。マージの後に main で ci.yml が動く。

    どれかが通らなければマージせず、**メンテナが PR を見てマージする**。本文の「分類」「確認待ち」の節と、`review approve` が残した `evidence` を見る。
- 毎週のスキル以外で、Issue を直す PR が同梱の一覧の項目に `review approve` を付けることがある(`decided_by` が `claude-maker-<Issue の番号>`。0.14.0 から)。同梱の一覧に確認待ちを残さないため(`tests/test_review_42.py`)で、**メンテナが PR でその `evidence` を確かめてからマージする**。試験は `decided_by` が許す名前(`baseline-2026-10-21`・`claude-weekly`・`claude-maker-<番号>`・オーナーのログイン名)か、`evidence` が https でホストのある URL か、`decided_at` が 2026-10-21 から今日までかを見る。
- マージの後の署名と公開は data.yml が行う(いままでどおり)。**配る一覧からは確認待ちの項目を除く**(`python -m genmodelfit.review publish`)。0.5.x は `review` を知らず、確認待ちを自動のおすすめに出してしまうため。

安全な週の条件: classification が safe。前から確認待ちのまま変わらない項目は含んでよい。
分類は**許すものの表**で決める([schema.ja.md](schema.ja.md#diff)「diff」の classification)。許す変化:

- 人気の数(`downloads`・`downloads_all_time`・`likes`・テンプレートの `usage`)
- `updated`
- HEAD の結果(`head_status`・`head_checked_at`)
- ライセンスが厳しくなる
- 消えた項目
- 自動のおすすめから外れた項目
- `sources` の数
- 確認待ちのまま変わらない項目

それ以外の変化(知らない項目も)はすべて判断が要る。

- HEAD が直って自動のおすすめに戻ったものは、どのファイルの URL・sha256 も変わっていないときだけ安全。
- ファイルの大きさの変化は 0.9.0 から向きによらず判断が要る(`files_changed`。`update` も確認待ちにする)。
- 理由: アプリは一覧の URL をそのまま落とすので、同じ URL のファイルが大きくなった = 中身が差し替わった、を黙って通さない。
- HF のリポジトリでファイルが上書きされた週は、自動でマージされずメンテナの確認になる。以前は大きくなっただけなら安全な週としてマージしていた。

決めたこと:

- pending の項目も main の一覧には入る(0.6.0 以降の `recommend` / `fit` / `info` には出る)。自動のおすすめ(gate)は `reviewed` で保留にする。
  アプリは `entry.review is None or entry.review.status == "ok"` で絞る。
- 承認には https でホストのある根拠の URL が 1 つ以上要る(無ければ断る)。
- `review reject` した項目は `src/genmodelfit/data/review_rejected.txt` に残り、`update` は作り直しても入れない。
- 同梱の一覧は 0.6.0 で全部 `ok`(`decided_by: baseline-2026-10-21`)から始めた。

### 手元で同じ流れを試す

```powershell
.venv\Scripts\python -m genmodelfit.weekly --dry-run --out $env:TEMP\weekly-out                         # 上流から(書くのは out だけ)
.venv\Scripts\python -m genmodelfit.weekly --dry-run --out $env:TEMP\weekly-out --record $env:TEMP\weekly-rec   # 応答も残す
.venv\Scripts\python -m genmodelfit.weekly --dry-run --out $env:TEMP\weekly-out2 --offline-sources $env:TEMP\weekly-rec  # 通信しない
.venv\Scripts\genmodelfit diff src\genmodelfit\data\db.json $env:TEMP\weekly-out\db.json
```

`--offline-sources` について:

- `--record` で残した GET の応答と HEAD の応答コードだけで作り直す。ファイルの見出しとリポジトリの精度は、キャッシュ `GENMODELFIT_CACHE_DIR` にあるものだけ。
- HEAD の日時は実行した時刻になるので、期限の確かめは本物にならない。
- 失敗した応答(401・つながらない)は残らないので、読み直した結果は本物の実行と少し違う(2026-10-21 の試しでは 335 件に対して 334 件)。
- 流れを試す・PR の本文の形を見るためのもの。
- 手元のキャッシュを汚したくなければ `GENMODELFIT_CACHE_DIR` を別の場所にする。

終了コード: 0 = 通った、1 = 確かめに落ちた(`result.json` の `problems`)、2 = 使い方の誤り。

### メンテナが一度だけ設定すること

- Settings → Actions → General → Workflow permissions の **「Allow GitHub Actions to create and approve pull requests」を有効にする**(無効だと create-pull-request が失敗する)。
  確認: `gh api repos/laxshmate/genmodelfit/actions/permissions/workflow`(`can_approve_pull_request_reviews` が true)。
  - この設定は名前のとおり**承認(approve)も許す**(GitHub に PR の作成だけを許す設定は無い)。
  - weekly-data.yml は承認しない(`gh pr review` も auto-merge も使わない。tests/test_workflows.py が検査)。
  - レビューとマージはメンテナが行う。
- (定期の実行をやめたので今は関係しない)**60 日リポジトリに動き(コミットなど)が無いと、GitHub は schedule のワークフローを止める。**
  止まったら Actions の「Weekly data」で「Enable workflow」を押すか、手動実行(workflow_dispatch)すると戻る。
- ラベル `automated`・`data-update` を作る(ワークフローも作ろうとするが、`pull-requests: write` だけでは作れないことがある):
  `gh label create automated -R laxshmate/genmodelfit`、`gh label create data-update -R laxshmate/genmodelfit`
- 同じリポジトリの PR では ci.yml のジョブは飛ばされる(#68)。ブランチの保護の必須のチェックに ci.yml を入れない。
  手元の `tools/ci_local.py` の結果を PR の説明に書く。

## 鍵

- 公開鍵: `src/genmodelfit/data/signing_keys/*.pub`(パッケージに同梱)。取り込む側はこのどれか 1 つで合えば通す。今は `2026-10.pub` の 1 つ。
- 秘密鍵: environment `data-signing` のシークレット `GENMODELFIT_SIGNING_KEY`(laxshmate/genmodelfit)。
- 秘密鍵のバックアップ: メンテナがオフラインで暗号化して 1 か所に保管する(メンテナだけが読める。場所は書かない)。
  コミットしない。画面や Issue に貼らない。

### 鍵を替える(定期・または念のため)

公開鍵は版に同梱されるため、**その版が信じる鍵は版を出したときに決まる**。新旧の鍵を並べて同梱し、段階を踏んで替える。

1. 新しい鍵を作る(下記)。
   - 公開鍵を `src/genmodelfit/data/signing_keys/<年-月>.pub` として**足す**(古い鍵は残す)。
   - この版を出し、利用者(特に AI生成キット)に上げてもらう。
   - この時点では署名はまだ古い鍵。
   - 新旧どちらの版も、古い鍵の署名を通す。
2. 新しい版が普及したら、`data-signing` のシークレットを新しい鍵に替え、バックアップも置き換える。以後の署名は新しい鍵。
   - 手順 1 の版以降は通す。
   - 手順 1 より前の版は新しい署名を通せなくなる(`--from-release` が失敗し、手元のデータのまま動く)。
3. さらに後の版で古い公開鍵のファイルを消す。

鍵の作り方(秘密鍵はファイルにだけ書く。画面に出さない。上書きする前に古いバックアップを別名で残す):

```powershell
python -c "
import sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization as s
k = Ed25519PrivateKey.generate()
open(sys.argv[1], 'wb').write(k.private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()))
open(sys.argv[2], 'wb').write(k.public_key().public_bytes(s.Encoding.PEM, s.PublicFormat.SubjectPublicKeyInfo))
" "<鍵のパス>\signing_key.new.pem" src\genmodelfit\data\signing_keys\2027-04.pub
```

フォルダ `<鍵のパス>` はメンテナだけが読めるようにする(Windows では `icacls` で受け継いだ権限を外す)。

手順 2 でシークレットを替えるとき(値は標準入力から渡し、画面に出さない):

```powershell
cmd /c "gh secret set GENMODELFIT_SIGNING_KEY --env data-signing -R laxshmate/genmodelfit < ""<鍵のパス>\signing_key.new.pem"""
```

### 鍵が漏れたら

署名の信頼が失われるので急ぐ。

- `data-latest` を消しても、鍵を持つ攻撃者は通信の途中に割り込める立場(社内の TLS 検査の装置・乗っ取った DNS など)から偽の一覧を配れる。
- 漏れた鍵を信じている版が残る限り被害は止まらないので、**回復は鍵の入れ替えと新しい版の配布**で行う。

1. `data-signing` のシークレット `GENMODELFIT_SIGNING_KEY` を削除し、data.yml を無効にする。
2. 新しい鍵を作り、漏れた鍵の公開鍵ファイルを**消して**新しい公開鍵だけを同梱した版を**緊急で**出す(段階を踏む入れ替えはしない)。利用者(特に AI生成キット)に上げてもらう。
3. 漏れた鍵を信じている古い版には、`--from-release` を使わないよう案内する(同梱のデータだけで動く)。
4. シークレットを新しい鍵で入れ直し、data.yml で新しい鍵の署名の一覧を配る。

## 使わないものの確認

- PyPI の API トークンは作らない・保存しない(Trusted Publishing だけ)。
- 秘密鍵はリポジトリに入れない。リポジトリ全体のシークレットにも置かない。CI のログに出さない(`--key-env` で環境変数から読む)。
