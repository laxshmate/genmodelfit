# 判定の公平さ・並び・Score

[English](scoring.md) · **日本語**

`recommend` / `fit` / `search` の表・`--json`・Python の関数が、実測をどう使い、どう並べ、Score をどう決めるかの決まりです(0.11.0、Issue #95。段階の上限と確認済みの GPU の種類は #108)。
要るメモリの式と係数は [accuracy.ja.md](accuracy.ja.md)、段階の区切り(使用率 60 / 85 / 98%)は [schema.ja.md](schema.ja.md)「Estimate / 見積もり」、自動でおすすめに出せるかは [gate.ja.md](gate.ja.md) にあり、ここでは変えません。
手本は [llmfit](https://github.com/AlexsJones/llmfit)(2f35825。`llmfit-core/src/fit.rs`・`analysis.rs`・`benchmarks.rs`)で、違えるところは理由を書きます。

## 目次

- [4 つの確認](#4-つの確認)
- [全体の流れ](#全体の流れ)
- [判定に使う設定](#判定に使う設定)
- [既定で切ってある部品](#既定で切ってある部品)
- [段階の上限](#段階の上限)
- [確認済みの GPU の種類](#確認済みの-gpu-の種類)
- [実測で段階を決める条件](#実測で段階を決める条件)
- [実測で値をずらすこと](#実測で値をずらすこと)
- [大きさの入力の無いテンプレートの実測](#大きさの入力の無いテンプレートの実測)
- [Score](#score)
- [人気](#人気)
- [並び](#並び)
- [1 モデル 1 行の代表](#1-モデル-1-行の代表)
- [llmfit との違い](#llmfit-との違い)
- [試験](#試験)
- [まだ決めていないこと](#まだ決めていないこと)

## 4 つの確認

判定・点・並びに入れる項目は、次の 4 つをすべて満たすものだけです。満たさないものは、印・注意・絞り込みに回します。

| 確認 | 満たす条件 |
|---|---|
| 説明 | README に 1 文で書け、利用者が表の数から自分で確かめられる |
| 根拠 | 一次資料・実測・llmfit の実装と経緯で裏づけられる。根拠の無い定数は「決めの値」と書き、数を最小にする |
| フェア | 作者の PC・作者が測ったかどうか・作者のアプリの都合・実行した日で、ほかの利用者の判定・点・順位が変わらない。同じ一覧・同じ PC・同じ指定なら、誰が計算しても同じ |
| 試験 | 入力と期待値を決めて自動の試験にできる |

## 全体の流れ

1. PC: VRAM の量・GPU の会社と世代・メインメモリ([hardware.ja.md](hardware.ja.md))
2. 設定: 種類ごとの既定、または利用者の指定(下の「判定に使う設定」)
3. 見積もり: 動かし方ごとの要る VRAM とメモリ([accuracy.ja.md](accuracy.ja.md))。実測があれば値をずらす
4. 段階: 動かし方を選び(全部載せは、仕上げの VAE の分まで載せたままにできるときだけ。[accuracy.ja.md「タイル」](accuracy.ja.md#タイル))、使用率で Perfect / Good / Marginal / 動かない。実測で段階を決めるのは、同じ GPU で別々の出どころ 2 つ以上がそろったときだけ
5. 1 モデル 1 行にまとめ、並べ、Score を付ける

## 判定に使う設定

**1 文で**: 指定が無ければ、どのモデルも種類ごとに同じ大きさで判定する。

- 指定の無い項目は、実測の有無によらず種類ごとの既定(`estimate.DEFAULT_SETTINGS`)で埋める: 画像 1024x1024、動画 832x480・81 コマ、音楽 30 秒、効果音・読み上げ 10 秒、3D は大きさなし。
- 実測の設定は、実測から作業用メモリをずらす量を計算するときだけ使う。
- 0.10 までは、実測のあるモデルは指定が無いと実測の設定(作者のアプリの「はやい」画質: Z-Image 768px、SDXL 832px、Wan2.1 512x512・49 コマ、Wan2.2 5B 640x640・73 コマ、ACE-Step 20 秒)で判定していた。作者が測ったモデルだけ小さな設定で比べることになり、フェアでないのでやめた。
- そのため、作者の実測の大半(画像・動画・音楽)は既定の設定と合わず、`basis` は `estimated`(`basis_detail: calibrated`)になり、表の VRAM の `m` は付かない。3D と補助ツールの実測は設定が合うので `m` が残る。作者は既定の設定で測り直す予定(「まだ決めていないこと」)。
- アプリ(AI生成キットなど)が自分の設定で判定するには、モデルごとに `judge_model(..., settings=...)` か `--width` などを渡す。種類ごとの設定の表を足す API は作らない(アプリの設定はモデルごとに違い、表では再現できない)。
- 根拠: llmfit も全モデルを同じ前提(文脈長)で比べる。
- 試験: `tests/test_scoring.py::test_a_settings_do_not_depend_on_measurements`。

## 既定で切ってある部品

**1 文で**: 公式テンプレートの既定の値で読み込まれない部品(無効・素通しのノード、どの出力のノードにも届かないノード、
値の決まったスイッチの選ばれない枝にしかない部品)は VRAM とメインメモリに数えず、スイッチで切ってある部品を入れると
段階が変わるときだけ、そのことを理由に 1 文で出す(0.12.0、#81)。

- 分け方は 2 つ(決め方は [sources.ja.md](sources.ja.md)「任意の部品」、数え方は [accuracy.ja.md](accuracy.ja.md)「ファイルの数え方」):
  - `inactive`(無効・素通し・出力に届かない): prompt に入らず検証もされないので、VRAM・メインメモリ・ディスク・`download` の
    どれにも数えない。
  - `switch_off`(スイッチの選ばれない枝。指示の書き直し・高速化の LoRA など): 実行されないので VRAM・メインメモリには
    数えない。ComfyUI の検証は選ばれない枝のファイルも確かめる(execution.py:946-969・1061-1085@426f5ee)ので、
    ディスクと `download` には数える。
- スイッチを全部入れた上限も出す(JSON の `estimate.vram_gb_all_options`・`fit_all_options`。今までの値と同じ)。
  段階が変わるときだけ理由に出す。例(RTX 3070 8 GB): 「既定で切ってある部品:
  qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors(8.8 GB)。全部入れると目安 8.5 GB で、この PC では too_tight」。
  入れ替えのスイッチ(Pixal3D / Trellis2)は「切り替えると別の部品(trellis_2_int8_convrot.safetensors、… GB)を
  pixal3d_int8_convrot.safetensors の代わりに読み込みます」と書く(足す量ではないので)。部品の一覧は `estimate.optional_parts`。
- #81: Qwen-Image 2.1(4 件)は、既定で切ってある指示の書き直しの部品(8.8 GB)を文章の段に足していたので、8 GB で
  逃がしても 8.48 GB(106%)の too_tight だった。数えないと逃がして 4.96 GB(文章から画像)/ 5.14 GB(編集)で Good。
  利用者の実機(RTX 3070 8 GB、ピーク 7.1〜7.6 GB で完走)と合う。逃がすときに GPU に残す割合(0.4)は変えていない。
- #112(Wan 2.2 5B が 8 GB で too_tight)は 0.11.0 の段ごとの作業用メモリ(#100)で既に直っている(逃がして 6.08 GB、Good)。
  ここでは変えず、試験で守る。
- 4 つの確認: 説明は上の 1 文。根拠は ComfyUI の lazy の実行(comfy_execution/graph.py:153-175・
  comfy_extras/nodes_logic.py:146-150・178-188@426f5ee)と、作者が公開したテンプレートの既定の値。フェア: 上流の JSON だけで
  決まり、作者の PC・実測・アプリの都合・実行した日によらない(毎週の作り直しで上流が既定を変えれば一緒に変わる)。
  試験は下の表。
- 影響(同梱の一覧 329 件 × NVIDIA 6 / 8 / 12 / 16 / 24 GB × メインメモリ 32 / 64 GB、既定の設定・同梱の実測): 段階が変わるのは
  34 モデル・90 のセルで、どれも上がるだけ(下がるセル 0、VRAM を増やして段階が下がる例 0、根拠 `basis_detail` の変わるセル 0)。
  セルごとの数(括弧は too_tight から上がった数): 6/32 7(6)・6/64 7(6)・8/32 5(5)・8/64 5(5)・12/32 12(8)・12/64 12(8)・
  16/32 11(5)・16/64 11(6)・24/32 12(8)・24/64 8(4)。8 GB / 32 GB で上がるのは Qwen-Image 2.1 の 4 件と FLUX.2 Klein の
  文章から画像(どれも too_tight → Good)。上限の 1 文が出るのは 53 セル。

## 段階の上限

**1 文で**: 段階は使う置き場所の使用率(60 / 85 / 98%)で決め、遅い動かし方には上限がある(GPU~・GPU+RAM は Good まで、CPU の生成は Marginal まで)。GPU の会社による上限は無い(#108)。

段階の意味(表の凡例・README と同じ):

| 段階 | 意味 |
|---|---|
| Perfect | 全部 GPU に載り、メモリに 4 割以上の余裕がある。**速さの保証ではない**(DGX Spark のような帯域の狭い機械でも使用率で決める) |
| Good | 余裕が 15% 以上ある(順に載せ替え・逃がすでも、余裕があれば Good) |
| Marginal | 動くがメモリがぎりぎり(85〜98%)、または CPU で生成する(とても遅い) |
| 動かない | 98% を超える(確保の隙間が無く、実際には読み込めない)。**例外**: 実測で段階を決めた行は、98% を超えても Good になる(最後まで動いた記録なので。「実測で段階を決める条件」6) |

動かし方ごとの上限(`estimate.gpu_choice`・`cpu_top`):

| 動かし方 | 上限 | 理由 |
|---|---|---|
| GPU(全部載せ) | なし | |
| GPU~(部品を順に載せ替え) | Good | 段ごとに部品を読み込み直す。Perfect は「全部が載ったまま余裕がある」こと(llmfit の「Perfect は余裕があり、かつ GPU で動く」と同じ考え) |
| GPU+RAM(一部をメインメモリへ逃がす) | Good | 遅いので Perfect にはしないが、余裕があれば Good(llmfit と同じ)。diffusers の比較(PR #11682)で、逃がしたときの遅れは Wan で 1.14〜1.5 倍(モデル単位と組単位の逃がし方)、FLUX で 3.3〜10.4 倍 |
| CPU: CPU で実時間に動く読み上げ(表にあるもの)・1 回の計算の道具 | Good | 下の「CPU で動かすとき」 |
| CPU: 画像・動画・音楽・効果音・3D の生成 | Marginal | 下の「CPU で動かすとき」 |

### 逃がす動かし方を選ぶとき

**1 文で**: 逃がすのは、逃がしたほうが段階が上がるときだけ(同じ段階なら逃がさない)。

- 全部載せ・順に載せ替えが Good 以上なら逃がさない。逃がす行に替わるのは、全部載せ・順に載せ替えでも 98% を超える行と、85〜98%(Marginal、OOM の手前)で逃がせば Good になる行だけ。後者は理由に「GPU だけでは 91% でぎりぎりなので、一部をメインメモリへ逃がす(遅い。…)」のように出る。NVIDIA の ComfyUI もこの状況では Dynamic VRAM で重みを逃がす。
- 逃がす行の段階は、逃がしたときの VRAM の使用率と、要るメモリ ÷ メインメモリ(表の Mem% と同じ)のきついほうの帯で、Good まで。逃がす行の段階はメインメモリの量で決まることが多い。逃がす量は決めの値(重みの 4 割を GPU に残す。`Coefficients.offload_fraction`)。メインメモリが 98% を超える分は、今までどおりページファイルの判定(Marginal か動かない)。
- llmfit は「全部載せが 98% を超えるときだけ逃がす」。genmodelfit がそうしないのは、VRAM を増やして段階が下がらない約束([schema.ja.md](schema.ja.md))のため。98% を超えるときだけ逃がすと、Good の逃がす行が VRAM を少し増やすと Marginal の順に載せ替えになる(同梱の一覧で 136 モデル。NVIDIA 4〜32 GB)。
- NVIDIA 以外の GPU には ComfyUI の Dynamic VRAM(comfy-aimdo)が無い(NVIDIA の Windows・Linux だけ。ComfyUI のブログ「Dynamic VRAM in ComfyUI」。WSL は予定なし)ので、逃がす行の理由に「さらに遅い」と書く。段階は変えない。
- 限界(この PC で Good になる最大の解像度・長さ。`limits`)は、逃がさずに GPU で Good 以上になる行だけで数える。
- 影響(0.11.0。同梱の一覧、メインメモリ 32 GB(24 GB は 64 GB)): GPU・GPU~ の Marginal から GPU+RAM の Good に替わった行は 6 GB で 8、8 GB で 6、12 GB で 18、16 GB で 19、24 GB で 49。GPU の Perfect・Good の行が逃がす行に替わることは無い(試験 `test_gpu_perfect_or_good_rows_never_switch_to_offload`)。

### CPU で動かすとき

**1 文で**: CPU だけで動かすときは、CPU で実時間に動く設計の読み上げと 1 回の計算の道具は Good まで、手順を何十回も回す生成は Marginal まで。

段階はメインメモリの使用率の帯で決め、次の上限で抑える(`estimate.cpu_top`):

| 種類・用途 | 上限 | 例 |
|---|---|---|
| CPU で実時間に動く設計の読み上げ(`estimate.CPU_REALTIME_TTS`。下の表) | Good | MeloTTS・Piper・VieNeu-TTS・Supertonic |
| 用途がすべて 1 回の計算の道具(`tool`・`estimate`・`matting`・`interp`・`segment`・`separate`) | Good | 背景除去(BiRefNet)・深度・ポーズ・切り抜き・音の分離 |
| それ以外(画像・動画・音楽・効果音・3D の生成、CPU で実時間に動くと作者が書いていない読み上げ、高解像度化 `upscale`) | Marginal | SDXL・Stable Audio・Fun-CosyVoice3・Chatterbox・VibeVoice・Qwen3-TTS・Kokoro・HiFi-GAN |

CPU で実時間に動く設計の読み上げ(作者の資料の文。理由にも出す):

| 読み上げ | 当て方 | 作者の資料 |
|---|---|---|
| MeloTTS | `hf:myshell-ai/MeloTTS-*` | モデルカード「Fast enough for CPU real-time inference」 |
| Piper | 動かし方が `piper` | README(タグ 2023.11.14-2。github.com/rhasspy/piper/blob/2023.11.14-2/README.md)「A fast, local neural text to speech system that sounds great and is optimized for the Raspberry Pi 4.」 |
| VieNeu-TTS | `hf:pnnbao-ump/VieNeu-TTS*` | モデルカード「on CPU everything runs on ONNX Runtime」「RTF < 1, one stream」 |
| Supertonic | 動かし方が `supertonic` | README(コミット 1e9799e9。github.com/supertone-inc/supertonic/blob/1e9799e964ea4c0dad7cde993b65c3c813a7b373/README.md)「Watch Supertonic running on a Raspberry Pi, demonstrating on-device, real-time text-to-speech synthesis」 |

作者が CPU で実時間に動く・CPU に向くと書いていない読み上げ(Kokoro・HiFi-GAN など)は表に入れず、生成と同じ Marginal まで。

- 根拠: 拡散の生成は手順(20〜50 回)ごとに本体の全部を計算する。Hugging Face のブログ「Accelerating Stable Diffusion Inference on Intel CPUs」で、SD 1.5 の 20 手順は r7iz.metal-16xl(Sapphire Rapids)の fp32 で 32.3 秒(最適化の前)。CPU で実時間に動くと作者が書いていない読み上げは Marginal まで(Fun-CosyVoice3・Chatterbox・VibeVoice・Qwen3-TTS などは言語モデル・フローマッチング・拡散でトークンや手順を何十〜何百回も回す。Kokoro・HiFi-GAN は小さいが、作者が CPU の数字を書いていない)。1 回の計算の道具は手順を回さない。
- 高解像度化は一覧のものが拡散の系統なので、生成と同じ Marginal まで(SeedVR2 は 1 手順の拡散だが 3B・7B の DiT を高い解像度で回す。SUPIR・Z-Image は手順を何十回も回す)。
- llmfit は CPU を Good まで(LLM の生成はメモリの帯域で決まり、CPU でも実用の速さが出る)。拡散の生成は計算の量で決まるので分けた。
- GPU のある PC でも、GPU の判定が CPU の判定より悪ければ CPU の判定を取る(VRAM を足して段階が下がらないように。同じ段階なら GPU)。
- 影響: CPU だけの PC(32 / 64 GB)で、Marginal から Good になったのは 11 行(読み上げ 3・道具 8)。

## 確認済みの GPU の種類

**1 文で**: この種類の GPU で、2 機種以上で動いた記録があれば「✓ 確認済みの種類」と出す(段階には使わない)。

- 種類 = 会社 × メモリの形(専用の VRAM / メインメモリと共有)。例: `nvidia/dedicated`、`amd/dedicated`、`amd/shared`(内蔵 GPU)、`apple/shared`、`nvidia/shared`(DGX Spark)。実測にはメモリの形の記録が無いので、GPU 名から決める(GB10・Apple は共有、AMD・Intel は名前の表の内蔵の印)。
- 使う実測: 同梱の実測(作者の `measurements.json` とほかの利用者のまとめ。この PC の bench の保存は入れない)のうち、最後まで動いた(`outcome: ok`)標準の実測で、重みを CPU 側に置いた逃がし方(`offload` が lowvram・novram・cpu)でないもの。ほかの利用者のまとめは、名前の表にある GPU で、`runs_evidence` が false でないもの。作業用メモリだけの値(bench の動的 VRAM)も、最後まで GPU で動いた記録なので数える。
- 数えるのは**機種**(`estimate.kind_models`): 2 つの実測は、`same_gpu` と同じ決め方(両方に PCI の型番 ID があれば ID、無ければそろえた名前。専用の VRAM なら VRAM の比 0.95 以上も)で同じなら同じ機種。作者・この PC・ほかの利用者の実測を分けない。1 機種の実測は何件・何人でも 1 つ。汎用の名前(型番 ID も無い)は機種が決まらないので数えない。
- 汎用の名前(「AMD Radeon(TM) Graphics」など)の実測は内蔵 GPU とみなし、型番 ID が表にあればその名前で、無ければ共有のメモリの種類にする。
- 使うのは同梱の実測だけ(calibrate と同じく、同じ入力なら誰の PC でも同じ答え)。`--vram` で VRAM を仮定したときは、仮定の GPU は測った GPU ではないので false。
- 今(0.11.0)確認済みなのは `nvidia/dedicated` だけ(RTX 5080 と RTX 3070 の 2 機種)。NVIDIA 以外の GPU・DGX Spark は印なしで、段階は同じ基準で決める。
- 出し方: 表(`recommend` / `fit` / `search`)の PC の行に「✓ 確認済みの種類」、`info` の段階の行、`system` の行。確認済みでなければ何も出さない。表の凡例に意味を 1 行。`--vram` で VRAM を仮定したときは GPU が仮定なので出さない。
- JSON: `system.platform_verified`(true / false)と `system.platform_verified_by`(確認済みのとき `{"kind", "models"}`。`models` は動いた機種の名前。そうでなければ null)。ライブラリは `gmf.platform_verification(system)`。
- 自動のおすすめ(gate)には入れない。gate は PC によらない作り(モデルの情報だけ)で、入れると AMD・Apple などで自動のおすすめが 0 になる。使う側のアプリが自分で判断に使う。
- 0.11.0 より前は、NVIDIA 以外の GPU と DGX Spark の段階を、この種類の GPU で動いた実測が無いことを理由に Good までにしていた。ほかの利用者の提出が集まらないと外れないので、上限をやめて印にした(llmfit も確からしさは段階でなく印で示す)。同じ GPU の実測は段階を上げるだけで、下げない(#95 の「実測で perfect → good に下げる」分岐も外した)。
- 影響: AMD 16 GB で 65 行、Apple 16 GB で 46 行、Apple 32 GB で 87 行、DGX Spark で 317 行が Good → Perfect。AMD 16 GB の段階の分布(Perfect 65・Good 117・Marginal 96・動かない 47)は NVIDIA 16 GB(69・113・96・47)とほぼ同じ。

## 実測で段階を決める条件

**1 文で**: あなたと同じ GPU・同じ設定で、別々の 2 つ以上の出どころの記録がそろって最後まで動いていれば、それで段階を決める(段階に使う量は表の VRAM と同じ)。

`estimate.measured_level`。次の条件をすべて満たすときだけ、段階を実測で決めます(`Estimate.fit_from: measurement`)。実測で決めた段階が見積もりの段階より良いときだけ使います。

1. **同じ GPU**: 作者の実測もほかの利用者の実測も同じ規則(`estimate.same_gpu`)。
   - 両方に PCI の型番 ID があれば ID が同じ。無ければそろえた名前が同じ(汎用の名前は一致としない)。
   - 専用の VRAM の GPU では、さらに VRAM の比(小さい ÷ 大きい)が 0.95 以上(`MEASURED_VRAM_TOLERANCE`。下の「同じ GPU の VRAM の比」)。
   - 0.10 までは、作者の実測は「同じ会社で、VRAM が測った GPU の 0.97 倍以上」ならどの GPU にも効いていた(例: RTX 4060 Ti 16 GB で、RTX 5080 で測った Wan2.2 5B が good・GPU)。作者だけ有利なのでやめた。
2. **同じ設定**: 解像度・コマ数・長さ・枚数・精度が同じ。会社が同じ(`gpu_backend`、無ければ GPU 名)。今の一覧のファイルで測ったもの(`files_fp`)、GPU 名が汎用の名前・名前の代わりの値でないもの(`gpu_known`。0.11.0 で名前の表をやめた)。
3. **別々の出どころが 2 つ以上**(`MIN_LEVEL_SOURCES`):
   - 作者の実測(`measurements.json`。この PC の bench の保存も同じ扱い)は、同じモデル・GPU・設定なら**何行あっても合わせて 1 つ**(`estimate.count_sources`)。作者だけでは段階を決めない。
   - ほかの利用者の実測は、**同じ Pull Request か同じ人でつながった提出のまとまり**ごとに 1 つ(#113。`contrib.submission_sources`): 1 つの Pull Request は多くて 1 つ、1 つの GitHub アカウントは何件・何回の Pull Request や Issue で送っても 1 つです。人は **GitHub のアカウント** です: 実測の Issue なら Issue を書いた人(ボットがコミットに `Submitted-by: github:<アカウント>` と `Submitted-by-id: <アカウントの数の ID>` を書き、名前を変えても変わらない数の ID で数える。ID の無い前の版のボットのコミットはアカウント名で数える。これらの行を信じるのは、マージのコミットが `Merge pull request #N from laxshmate/measurement/issue-M`(このリポジトリの中のボット自身のブランチ)で、コミットがボットのもののときだけ)、Pull Request なら、GitHub がマージのコミットの 1 行目に書く Pull Request の元のアカウント(`Merge pull request #N from <アカウント>/<ブランチ>`。git から分かるのは名前だけ)。コミットのメールアドレスは誰でも書けるので、これには使いません。実測の Pull Request は「Create a merge commit」だけで入れます。それ以外(main に直接・squash・rebase)は、コミットを書いた人のメールアドレスで数えます(弱い)。**1 つのアカウントは 1 回だけ数えますが、別のアカウント・その人の組織・(Pull Request では)名前を変えたあとのアカウントから出したものは別の人として数えられる**ので、メンテナはマージのときに Pull Request や Issue を書いた人を確かめます(ボットの Pull Request には、同じアカウントがその GPU・モデルで既に送った数と、確かめる項目を書きます)。まとめの行は使った出どころの ID(`source_ids`: まとまりのいちばん小さい鍵の sha256 の先頭 12 桁。アドレスやアカウント名は出さない。か `file:<内容の sha256 の先頭 12 桁>`)を持ち、行をまたいで**ID の和集合**で数える。1 人の提出が、GPU の型番 ID の有無や測り方(`vram_metric`)の違いで別の行に分かれても 1 つ。ID の無い古い行は足さずに、その `n_sources` の一番大きいものとだけ比べる。
   - 作者とほかの利用者は、同じ GPU・同じ設定なら足して数える(作者 + 提出 1 つ = 2)。
   - 1 つだけのときは段階を決めず、値をずらすだけ(下の「実測で値をずらすこと」)。理由に「出どころが 1 つだけ」と出る。
4. **そろっている**(`CONSISTENT_REL`。`contrib aggregate` の `consistent` と同じ定義):
   - OOM の記録が無い: 同じ構成で `outcome: oom` の行、`n_oom` のあるまとめ、`runs_evidence: false`(OOM の出どころが ok の出どころ以上)のまとめが無い。
   - まとめの行の中もそろっている(出どころ 2 つ以上の行は `consistent: true`)。
   - 行どうしの値(文脈の分を足した量)の幅(最大 − 最小)が中央値の 15% 以内。まとめの行は中央値で比べる。
5. **段階に使う量** = max(そろった実測の一番大きいピーク + 文脈の分, 重み + 固定分)。
   - 文脈の分: `torch_reserved` の実測は CUDA / HIP の文脈を含まないので 0.5 GiB を足す(`TORCH_RESERVED_CONTEXT_GB`)。
   - 重み + 固定分: 作業用メモリ 0 の全部載せ(値の欄の物理的な下限と同じ)。
   - **ピーク + 文脈の分が重み + 固定分より小さいときは決めない**。全部載せの下限より小さいピークは、重みの一部をメインメモリに逃がして動いた記録で、「GPU に全部載る」根拠にならない(下の Wan2.2 5B と同じ考え方。値をずらすのには使う)。理由に「全部 GPU に載せたときの下限より小さい」と出る。
   - この量が表の VRAM・Mem% に出る値で、JSON の `estimate.measured_level_vram_gb`(`memory_required_gb` も同じ)。生のピークは `estimate.measured_peak_vram_gb`。
   - この量がこの PC の VRAM を超えれば決めない。
6. **段階** = この量 ÷ VRAM の帯で、少なくとも Good(最後まで動いた記録なので)。GPU の上限に張り付いた実測(動的 VRAM が空きを使い切った)があれば Good まで。動かし方は GPU。

`Estimate.sample_count` は、値の元になった別々の出どころの数です(作者の実測は何行あっても 1、ほかの利用者は `n_sources`、段階を実測で決めたときはそろった出どころの合計)。0.10 までは提出の件数(`n_used`)でした。

例(同梱の実測):
- **例: RTX 5080 16 GB の Wan2.2 5B**(640x640・73 コマを指定。作者の実測は 15.8 / 15.0 GB の 2 行で、合わせて 1 つの出どころ)。重み + 固定分は約 18.4 GB で、15.9 GB に全部は載らない。実測は ComfyUI の動的 VRAM で重みの一部をメインメモリに逃がして最後まで動いた(offload: dynamic、GPU の上限に張り付いた値)。つまり「逃がせば動く」記録なので、段階は実測では決めない(今の段ごとの式では、部品を順に載せ替えて Good・GPU~)。ほかの利用者の提出がそろっても、量の下限(重み + 固定分)が VRAM を超えるので、実測で GPU の段階にはならない。0.7.0〜0.10 は Good・GPU だった(#26)。
- RTX 5080 の SDXL(832px を指定): 作者の行は 1 つなので決めない。同じ GPU・同じ設定のほかの利用者の提出が 1 つそろえば決まる。

4 つの確認:
- 説明○(上の 1 文)。
- 根拠○: llmfit は実測を同じ構成の PC にだけ移し(`benchmarks.rs:546-558`)、段階は変えない。genmodelfit は「同じ GPU・同じ設定で最後まで動いた」記録を式より確かな根拠として段階に使う(#26、2026-10-22)ので、llmfit より厳しく 2 つ以上の出どころを求める。
- フェア○: 作り物の 1 件・1 人の水増し・作者だけでは段階が変わらない。作り物の小さな値を出しても、量は重み + 固定分より下がらない(例: 22 GB 要る Flux に「1.0 GB で動いた」を出しても、8 GB の RTX 3070 で Perfect にならない)。
- 試験○(「試験」)。
- 2026-10-22 の決定「ほかの利用者の実測は 1 件から」のうち、段階を決める部分を変えた(値に使うのは 1 件からのまま)。

**`--vram` との関係**: `--vram` は本物の GPU の名前を残すので、VRAM の比が 0.95 未満になると同じ GPU でなくなり、実測で段階を決めなくなります(例: RTX 5080 の PC で `--vram 17.5`)。違う量の GPU の判定なので、これは正しい動きです。[schema.ja.md](schema.ja.md) の「VRAM を増やしても段階は下がらない」の約束は、この例外つきです。

**同じ GPU の VRAM の比**(0.11.0 で 0.97 → 0.95。`estimate.MEASURED_VRAM_TOLERANCE`)

- **1 文で**: 同じカードなら、OS が違っても同じ GPU とみなし、量の違う版(8 GB と 16 GB など)は別の GPU とみなす。
- 根拠: Linux の CUDA は予約の分を引いた総量を報告する(6 GB のカードで 5934 MiB = 5.80 GiB。NVIDIA の開発者フォーラムの RTX 2060 の報告)。Linux の総量は 5.80 / 7.79 / 9.78 / 10.76 / 11.77 / 23.69 GiB、Windows は 6.0 / 7.99 / 15.92 など。6 GB の 5.80 ÷ 6.0 = 0.967 は 0.97 に届かなかった。量の違う版の比は 0.83 以下(RTX 4060 Ti の 8 / 16 GB は 0.5、RTX 3060 の 8 / 12 GB は 0.67、RTX 2060 の 6 / 12 GB は 0.5、RTX 3080 の 10 / 12 GB は 0.83)なので、0.95 との間に余裕がある。
- フェア: 誰の実測にも同じ値を使う。Linux と Windows の持ち主が、同じカードの実測を互いに使える。
- 試験: `tests/test_scoring.py::test_same_vram_ratio_linux_and_windows`(Linux 5.80 と Windows 6.0 は同じ、8 GB と 16 GB の版は別)。

**NVIDIA 以外の GPU**: 同じ条件です。0.11.0 から GPU の会社による段階の上限は無く(上の「確認済みの GPU の種類」)、実測は段階を上げるだけです([hardware.ja.md](hardware.ja.md))。

## 実測で値をずらすこと

**1 文で**: 実測のあるモデルは、どの GPU でも、式の作業用メモリを実測に合わせてずらす。

- 変えていない([accuracy.ja.md](accuracy.ja.md)「実測の使い方」)。ほかの利用者の実測は、同じ GPU の PC で 1 件から使う。
- 作者の測ったモデルだけ値が正確になり、ずらしで動かし方・段階が変わることもある(例: RTX 4060 8 GB の HY 3D 2.0(`3d_hunyuan3d_image_to_model`)は実測ありで Good・GPU(6.7 GB)、無しで Good・GPU~(全部載せ 6.85 GB))。値を正確にすることの裏返しとして受け入れ、表の `m` で値が実測から来ていることを示す(作者・ほかの利用者の実測を印で分けない。#108 で `c` をやめた)。
- #100 から、負のずらしは段ごとに 0 で切り(重み + 固定分より下げない)、正のずらしは全部載せにだけ足し、GPU の上限に張り付いた実測からのずらしは 3 つに足す([accuracy.ja.md](accuracy.ja.md)「実測の使い方」1)。
- 根拠: llmfit も実測で推定を補正する(同じ機種の実測から全モデルに同じ倍率。genmodelfit はモデルごと。ずらす量は #100 の段ごとの式で −1〜+2 GB 程度)。
- 試験: `tests/test_scoring.py::test_d_values_shift_by_at_most_the_delta`(実測の有無による差は、ずらした量以内)。

## 大きさの入力の無いテンプレートの実測

**1 文で**: 入力の画像で大きさが決まるモデルは、大きさを指定せずに判定する。

- #90 で入れた(`estimate.NO_SIZE_INPUT`。メンテナが確かめた手の表)。判定の設定に大きさを持たないので、bench が大きさを書かない実測(BiRefNet・MoGe)がそのモデルの判定と合い、使われる。
- 試験: `tests/test_scoring.py::test_g_measurements_without_size_are_used`。

## Score

**1 文で**: Score はこの PC のメモリの余裕で、その動かし方で一番きつい資源の使用率が 70% 以下なら 100、それを超えると下がる(速さ・品質・人気は入れない)。

### 式

```
r = 使用率(下の表。丸める前の値)
memory_fit = 100                                     (r ≤ 0.70)
           = 100 × exp(−0.5 × ((r − 0.70) / 0.20)²)    (r > 0.70)
動く行(Perfect / Good / Marginal)は 37.5 を下限にする
動かない・判定できない行は 0
Score = memory_fit を小数 1 桁に丸めたもの(Python の round)。表は整数で出す
```

r(動かし方ごと。表の Mem% と JSON の `utilization_pct` はこの r):

| 動かし方 | r |
|---|---|
| GPU | 全部載せの VRAM ÷ VRAM |
| GPU~ | 順に載せ替えたときの最大の VRAM ÷ VRAM |
| GPU+RAM | max(逃がしたときの VRAM ÷ VRAM、要るメモリ ÷ メインメモリ) |
| CPU | 要るメモリ ÷ メインメモリ |
| 実測で段階を決めた行 | 段階に使った量 ÷ VRAM |
| どの動かし方でも、メモリが足りない行 | max(上の値、要るメモリ ÷ メインメモリ) |

- 例: r = 0.60 → 100.0、0.80 → 88.2、0.85 → 75.5、0.90 → 60.7、0.98 → 37.5。
- 定数 0.70・0.20 は llmfit の `fit_score`(`fit.rs:2335-2349`)と同じ。片側の曲線にした経緯は llmfit PR #648(空きは損ではない)、崖をなくした経緯は PR #100。入力の r も llmfit と同じく「その動かし方で一番きつい資源の割合」。
- 下限 37.5 は r = 0.98(Marginal の端)の値。r が 0.98 を超えても動くと言える行のため: 実測で段階を決めた行と、メインメモリが足りずページファイルで動く行(CPU だけでなく GPU・GPU~・GPU+RAM でも。r はメモリの割合になり、Mem% が 110〜130% のことがある。段階は Marginal)。
- 段階との関係: Perfect(GPU で r ≤ 0.60)は必ず 100。Good(GPU)は 100〜75.5、Marginal(GPU)は 75.5〜37.5。GPU~・GPU+RAM は段階の上限が Good、CPU の生成は Marginal なので、Score が高くても速くはない。
- Score は並びではほとんど効かない(段階・人気が先)。役目は「メモリにどれだけ余裕があるか」を 1 つの数で見せること。`--sort score` で並べられる。
- 1 モデル 1 行の Score は**代表の行の値**(表の Mem% と合う)。0.10 までの「仲間の一番高い点」はやめた。
- JSON: `score`(= memory_fit)、`score_components = {"memory_fit": …}`。0.10 までの鍵(`fit`・`headroom`・`readiness`・`basis`・`popularity`)は無くした(意味を変えて同じ名前を使わない)。

### 約束の変更

0.10 までは「VRAM・メモリを増やしても Score は下がらない」と約束していました。新しい Score は、VRAM が増えて代表がより重い精度・動かし方に替わると下がることがあります(例: Z-Image Turbo(bf16)は 25 GB で GPU~ の 100、26 GB で全部載せの 78.7。llmfit も同じ)。約束は段階だけにしました(上の `--vram` の例外つき)。メモリを増やして Score が下がらないことは試験で確かめています。

### 入れなかったもの

| 候補 | 判定 | 理由 |
|---|---|---|
| 段階の点(0.10 は 45%) | 外す | 並びの鍵で先に使うので二重。llmfit も Score に段階を入れない |
| 人気(0.10 は 10%) | 並びの鍵にする | 混ぜると重みという決めの値が要り、点の意味(相性か人気か)が分からなくなる(下の「人気」) |
| 準備(0.10 は 20%) | 外す | 作者のアプリが自動でおすすめする条件で、PC との相性と無関係。`--json` の並びと `--auto-only` で扱う |
| 実測の有無(0.10 は 10%) | 外す | 作者が測ったモデルだけが全員の表で上がっていた。値をずらすのと印だけに使う |
| 精度による減点 | 入れない | 順(16 ビット ≳ 8 ビット > 4 ビット)は FLUX.1 の論文(SVDQuant arXiv 2411.05007)で言えるが、点数は言えない。同じモデルの中で高い精度を選ぶ規則(「1 モデル 1 行の代表」)だけ |
| 大きさ | 入れない | 系統の違う生成モデルの間で、大きさと品質の関係を示す資料が無い |
| 新しさ | 入れない | 一覧にあるのはテンプレートの作られた日。実行した日で変わる(llmfit の弱点) |
| 系統の評判 | 入れない | 主観の表(llmfit も出典なし) |
| 速さ(秒数) | 今は入れない | 式も較正も無い(「まだ決めていないこと」) |

## 人気

**1 文で**: 人気は、同じ種類で同じところから数えた使われ方の順位(0〜100)で、並びの鍵にだけ使う(Score には入れない)。

`popularity.py`。JSON の `popularity`。

- 単位はモデル(1 行。同じモデルの仲間のワークフロー全部。`--all-workflows` の行もモデルの値)。数は仲間の中の一番大きいもの。利用者のテンプレートの数も数える(同じモデルが使われている数なので)。
- 出どころを 2 つに分け、数の意味が違うので混ぜない。
  - ComfyUI のテンプレート: index.json の `usage`(上流の説明は "Usage count"。期間・単位は公開されていない: Comfy-Org/workflow_templates `templates/index.schema.json:144`、`scripts/sync/sync_data.py:1252`)。
  - Hugging Face: 全期間のダウンロード数(`downloads_all_time`)。手で足したモデル(manual)も、HF のリポジトリがあればここに入る。
  - 仲間に両方あるモデル(例: 効果音の Stable Audio Open)はテンプレートの数を使う。
- 順位 = 同じ種類・同じ出どころの、判定に使う一覧(同梱か `update` 後の一覧)の全モデル(絞り込みの前。無検閲も含む)の中の百分位(中央の順位):
  `100 × (数が少ないモデルの数 + 自分を含む同じ数のモデルの数 ÷ 2) ÷ 仲間の数`
  - 例: 数 10, 20, 20, 40, 0 の 5 件 → 30 / 60 / 60 / 90 / 0。
- 数が無い・0 のモデル: 0(一番下)。数のある仲間の計算には、数 0 として入る。
- 手で足したモデル(manual。今は 3 件)は数を持たないので順位は 0(一番下)。通信して数を取ってくることはしない。不利になるのは作者が手で足したモデルだけなので、フェアは崩れない。
- 仲間が 5 件未満の出どころ: 数のあるモデルは 50(順位の差があてにならないので付けない)。
- テンプレートの順位と HF の順位を同じ鍵で比べるのは「出どころごとの上位を同じ扱いにする」決めの値。
- 0.10 までは Score の 10% で、HF のダウンロード数(30 日)を ÷100 してテンプレートの数と同じ目盛りにしていた。その換算はやめた。
- 4 つの確認: 説明○ / 根拠△(数の意味が公開されていない。それでも使われている数は、llmfit の評判の表より客観的)/ フェア△(下で確かめる)/ 試験○。

**かたよりの確認**: 普通の PC の人が多いので、軽いモデルほど人気になるかたよりがあるかを確かめました。
同梱の一覧(2026-10-27)で、テンプレートのモデルの「全部載せの VRAM(既定の設定)」と「使用数」の Spearman の相関は、画像 −0.05(78 モデル)、動画 +0.22(63)、音楽 +0.48(10)、3D +0.59(9)。軽いほど人気、のかたよりは見られません。

毎週の更新(`python -m genmodelfit.weekly`)は、テンプレートのモデルが 20 以上ある種類について同じ相関を出し(PR の本文の「人気のかたよりの確認」)、−0.3 を下回ったら注意に出します(人気を並びの鍵から外すかを考える合図。`popularity.vram_bias`)。注意は結果の `warnings` にも入り、`merge-check` を safe にしない(`popularity_bias:<種類>`)ので、その週は自動でマージせずオーナーが見ます。音楽・3D・読み上げは件数が少なく、この確認はできません。

## 並び

### `recommend` の表

鍵の順:
1. 動く → 判定できない → 動かない
2. 段階: 逃がさずに載る(Perfect / Good で GPU・GPU~)→ Good の GPU+RAM・CPU → Marginal
3. Marginal の中だけ: 動かし方 GPU → GPU~ → GPU+RAM → CPU(速い順)。逃がさずに載る中では使わない(24 GB の Z-Image で bf16 の Good・GPU~ を代表にした行が沈まないように。#28)
4. 公式のテンプレート → 利用者のテンプレート
5. 人気の順位の高い順
6. Score の高い順
7. ID(同点の順を入力の順によらず決める。llmfit も名前で決める)

理由(llmfit との違い): llmfit は動かないものを最後にするだけで、あとは Score で並べます。genmodelfit は段階を先に見ます。段階は「動くか」の答えそのもので、Score(メモリの余裕)と人気より大事だから(#84)。

### `fit` / `search`

- 既定(`--sort type`): 種類の順 → 段階(Perfect / Good / Marginal / 判定できない / 動かない)→ 公式 → 人気 → Score → ID
- `--sort score`: 鍵 1 → 段階(逃がさずに載る / Marginal)→ Score → 人気 → ID
- `--sort vram`・`disk`・`name`: 鍵 1 の後にその値(変えていない)

### `--json` とライブラリ

- 鍵 1 の次に「自動でおすすめしてよいもの」(`auto: true`)を先にする。使う側のアプリが人の確認なしにおすすめを出すため。条件(ライセンスの許可リストなど)は `GatePolicy` / `--license-allowlist` で変えられる。
- 鍵 1 の順(動く → 判定できない → 動かない)は表とそろえた(0.10 までのライブラリは 動かない → 判定できない)。
- 表(`recommend` の文字の出力)は自動のおすすめを並びに使わない。だから日付(HEAD の確認の期限)や許可リストを変えても、表の並びと Score は変わらない。

## 1 モデル 1 行の代表

変えていません([schema.ja.md](schema.ja.md)「Models / モデルのまとめ方」)。用途の順 → 逃がさずに載るものがあればその中で精度の高いもの → 無ければ段階の良いもの → 同じ段階なら逃がさない動かし方(`recommend.variant_key`)。精度の順は「16 ビット ≳ 8 ビット > 4 ビット」の資料と矛盾しません。

#108(逃がす行が Good まで)で代表が替わったかを数えました(同梱の一覧。NVIDIA 6 / 8 / 12 / 16 / 24 GB・AMD 16 GB・Apple 16 / 32 GB・DGX Spark・CPU だけ 32 / 64 GB)。替わったのは 4 モデル(8 GB の Z-Image は bf16 の Marginal → int8 の Good、12 GB の FLUX.1 schnell は bf16 の Marginal → fp8 の Good、24 GB の Wan 2.2 14B は i2v の GPU~ Marginal → flf2v の Good、24 GB の SCAIL は fp16 の Marginal → int8 の Good。どれも逃がす行の中で段階の良いほうへ)で、bf16 の逃がす行が int8 の GPU の行に勝つ例は 0 です。逃がす Good の行が GPU の Marginal の行より上に来る組も、これらの PC では 0 でした(GPU の Marginal の行は、逃がせば Good になるなら逃がす行になるため)。不自然な並びは無いので、選び方は変えていません。

## llmfit との違い

| 点 | llmfit | genmodelfit | 理由 |
|---|---|---|---|
| Score の中身 | 品質・速さ・収まり・文脈長 | 収まりだけ | 生成モデルの品質と速さに根拠のある測り方がまだ無い。根拠ができたら足す |
| 並び | 動かないものを最後 → Score | 動く → 段階 → … → 人気 → Score | 「並び」 |
| 遅い動かし方の上限 | 逃がす・CPU は Good まで | GPU~・GPU+RAM は Good まで。CPU は読み上げ・道具が Good まで、生成が Marginal まで | 「段階の上限」。拡散の生成は計算の量で決まり、CPU ではとても遅い(LLM の生成はメモリの帯域で決まる) |
| 逃がす動かし方を選ぶとき | 全部載せが 98% を超えるとき | 逃がしたほうが段階が上がるとき | VRAM を増やして段階が下がらない約束のため(「逃がす動かし方を選ぶとき」) |
| GPU の種類 | 段階は同じ、確からしさは印 | 段階は同じ、確認済みの種類に ✓ | 「確認済みの GPU の種類」 |
| 実測と段階 | 変えない | 同じ GPU・同じ設定で、別々の 2 つ以上の出どころがそろえば段階に使う | 「実測で段階を決める条件」 |
| 同じハードの決め方 | CPU 名 + GPU 名 | GPU の型番 ID か名前 + VRAM の比 | CPU は判定に使わない |
| 推論に読み込まない重み | 投機的デコードの draft head(EAGLE など)は本体の大きさに入れず、順位にも出さない(`llmfit-core/src/models.rs` の `SanitizationReason::SpecDecodeDraft`)。入れたときの量の注意は無い | テンプレートの既定の値で読み込まない部品を VRAM・メモリに数えず、スイッチを入れると段階が変わるときだけ 1 文で知らせる | 「既定で切ってある部品」。生成のテンプレートはスイッチ 1 つで部品が足されるので、入れたときの量も要る |

## 試験

`tests/test_scoring.py`(と、期待値を合わせた既存の試験)。

| 内容 | 試験 |
|---|---|
| memory_fit の決まった値(r = 0.50〜0.98)・崖が無い・動く行の下限・動かない行は 0 | `test_memory_fit_fixed_values`、`test_memory_fit_shape_floor_and_not_runnable` |
| r の表の各行(GPU+RAM でメモリがきつい組・メモリ不足の組を含む) | `test_usage_ratio_per_run_mode` |
| VRAM を増やして Score が下がる例・段階は下がらない | `test_score_can_drop_with_more_vram`、`tests/test_invariants.py` |
| 人気の順位の例・小さな仲間・出どころを混ぜない・manual | `test_popularity_percentile_example`、`test_popularity_sources_are_not_mixed`、`test_popularity_is_per_model_and_in_json` |
| かたよりの確認(Spearman) | `test_spearman_and_bias_check`、`tests/test_weekly.py` |
| 並びの各鍵(段階・Marginal の動かし方・公式・人気・Score・ID・自動のおすすめ)、`--sort score` | `test_recommend_order_keys`、`test_fit_sort_type_and_score` |
| (a) 設定が実測の有無で同じ | `test_a_settings_do_not_depend_on_measurements` |
| (b) 測った GPU と違う PC(RTX 4060 Ti 16 GB・RTX 4090)では実測で段階を決めない | `test_b_no_measured_level_on_other_gpus` |
| (c) 同じ GPU・同じ設定で、出どころ 2 つがそろえば決まる | `test_c_measured_level_on_the_same_gpu` |
| (d) 値の差はずらした量以内 | `test_d_values_shift_by_at_most_the_delta` |
| (e) 実行した日で表の並びと Score が変わらない | `test_e_run_date_does_not_change_table_order_or_score` |
| (f) 許可リストで表の並びと Score が変わらない | `test_f_allowlist_does_not_change_table_order_or_score` |
| (g) 大きさの無い実測が使われる | `test_g_measurements_without_size_are_used` |
| 作り物の 1.0 GB の Flux で Perfect にならない・1 人の 4 件では決めない・作者 1 + 提出 1 で決まる・作者の 2 行は 1 つ | `test_fake_small_submission_does_not_make_flux_perfect`、`test_one_source_with_many_files_does_not_set_the_level`、`test_author_one_and_community_one_agree`、`test_author_rows_are_one_source` |
| 逃がすのは Good まで・段階が上がるときだけ・GPU の Perfect / Good は替わらない・メインメモリも見る・限界は GPU の行だけ(#108) | `tests/test_levels.py`(`test_offload_is_capped_at_good_and_chosen_only_when_it_raises_the_level` ほか) |
| CPU の上限(読み上げ・道具は Good、生成は Marginal)・メインメモリの帯 | `tests/test_levels.py::test_cpu_only_caps`、`test_cpu_top_table`、`test_cpu_level_uses_the_ram_band` |
| 既定で切ってある部品: 型の表の出力のノード(サブグラフの中・lazy の枝)から届けば数える・Soft Switch は選んだ側がつながっていなければもう一方・入れ子の素通しの実体の中身は使う・出力に届かないノードの部品は download にも数えない・素通しのノード自身のファイルは数えない・#81 の値(8 GB で 4.96 / 5.14 GB の Good)・FLUX.2 Klein・#112(Wan 2.2 5B が 8 GB で Good)・上限の 1 文・段階が下がらない | `tests/test_optional.py` |
| 確認済みの種類(NVIDIA の専用 VRAM は true、AMD・Apple・GB10 は false。1 機種は何件でも未確認、2 機種で確認済み、出どころを分けない)・✓ は確認済みだけ・gate は種類によらない・AMD と NVIDIA の分布が近い・VRAM とメモリを増やして段階が下がらない | `tests/test_levels.py` |

`SCHEMA_VERSION`(同梱・配布の db.json の形の版)は変えていません。出力の意味が変わったので、パッケージは 0.11.0 です([CHANGELOG.md](../CHANGELOG.md))。

## まだ決めていないこと

1. 作者の実測を既定の設定で取り直す(画像 1024x1024、動画 832x480・81 コマ、音楽 30 秒)。取り直した分は `m` が戻る。
2. 速さ(秒数)の見積もり: GPU の計算性能 × 大きさ × 解像度・コマ数 × ステップ数を実測で較正する(別の仕様)。
3. 品質の実測: 同じ seed で精度ごとの差(LPIPS など)を bench で測る。精度の減点はその後。
4. 逃がしたときの速さの実測(#102 の測定で `--disable-dynamic-vram` + lowvram の時間を比べる)。逃がす行を Good までにしている根拠の確かめ。
5. 音楽・3D・読み上げの人気のかたよりの確認(件数が増えたら)。
6. bench・contrib の実測に、読み込んだ任意の部品(スイッチを入れた・無効のノードを有効にした)を記録する。今は実測を
   テンプレートの既定の値で測ったものとして扱い、ずらしを既定で読み込む部品で決めている。スイッチを入れて測った実測が
   混じると、作業用メモリが大きく見える。
