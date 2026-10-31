# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""データの形(スキーマ)。JSON との相互変換もここで行う。docs/schema.md と必ず一致させる。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

from .i18n import tr

SCHEMA_VERSION = 1

# 生成の種類。music=音楽、sfx=効果音、tts=読み上げ
MODALITIES = ("image", "video", "music", "sfx", "tts", "3d")
# 内容の印。uncensored=無検閲(安全フィルタを外した、または成人向けの出力を想定したモデル)
CONTENT_LEVELS = ("sfw", "uncensored")
# 適合の段階(良い順)
FIT_LEVELS = ("perfect", "good", "marginal", "too_tight")
# 大きさが分からず判定できないときの印(段階ではない。表では「?」。並べるときは too_tight の後ろ。0.1.1)
FIT_UNKNOWN = "unknown"


# 動かし方(Estimate.run_mode)。none = 動かない(表では「-」= 当てはまらない)、unknown = 大きさが分からず
# 判定していない(表では「?」= 分からない)。0.2.0 から unknown を分けた(それまでは none)
RUN_MODES = ("gpu", "offload", "cpu", "none", "unknown")
RUN_UNKNOWN = "unknown"
# 精度の出どころ(ModelEntry.precision_source。0.2.0、#30)。上ほど確か。見出しの精度はほかで上書きしない
PRECISION_SOURCES = ("header", "filename", "hf_api", "config", "workflow")


def fit_rank(fit: str) -> int:
    """段階の順位(0 = perfect)。判定できない(FIT_UNKNOWN)は一番後ろ。"""
    return FIT_LEVELS.index(fit) if fit in FIT_LEVELS else len(FIT_LEVELS)


# 推定の根拠
BASIS = ("measured", "estimated")
# 根拠の細かい段階(Estimate.basis_detail。#34)。上ほど確か。basis は後方互換のため 2 値のまま:
# measured → basis "measured"、calibrated / estimated → basis "estimated"
#   measured           設定も同じ実測(作者・この PC の bench・ほかの利用者のまとめを分けない。#108 で
#                      measured_community をまとめた。別々の出どころの数は Estimate.sample_count)
#   calibrated         設定の違う実測でずらした推定
#   estimated          式だけの推定
BASIS_DETAILS = ("measured", "calibrated", "estimated")
# 実測の出どころ(Measurement.origin)。None = 同梱の作者の実測・この PC の bench の実測
ORIGIN_COMMUNITY = "community"
# 実測の結果。ok=最後まで動いた、oom=メモリ不足で失敗、error=ほかの理由で失敗
OUTCOMES = ("ok", "oom", "error")


def _from_dict(cls, data: dict[str, Any]):
    """未知のキーは捨てる(新しい版のデータを古い版でも読めるように)。"""
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class ModelFile:
    """モデルを構成する 1 ファイル(本体・テキストエンコーダ・VAE など)。"""

    name: str
    url: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    directory: str | None = None  # ComfyUI の置き場所(diffusion_models / text_encoders / vae など)
    repo_id: str | None = None  # Hugging Face のリポジトリ(URL から求めたもの)
    license: str | None = None  # このファイルのライセンス(SPDX 風。不明は None、独自は "other")
    license_name: str | None = None  # license が other のときの名前(例: flux-1-dev-non-commercial-license)
    gated: bool | None = None  # 利用申請が要るか(None=不明)
    role: str | None = None  # main / text_encoder / vae / clip_vision / lora / other
    license_from: str | None = None  # license を取った HF リポジトリ(再配布なら本家)
    license_url: str | None = None  # license の原文・記載の場所
    repo_license: str | None = None  # 置き場所のリポジトリの表記が本家と違うとき、その表記
    # ライセンスを決めた道(置き場所 → base_model … → 決め手。部品の表・系統の表で決めたときは表の本家)
    license_path: list[str] = field(default_factory=list)
    # 決め方の根拠。"license_unverified: …"(確かめられず不明にした)、"component:<表の名前>"、"family:<系統>"
    license_evidence: list[str] = field(default_factory=list)
    head_status: int | None = None  # 匿名の HEAD(転送を追わない)の応答コード(None=未確認)
    head_checked_at: str | None = None  # その HEAD を送った日時(UTC, ISO 8601)
    # ファイルの見出し(safetensors の JSON・GGUF の頭)から読んだ精度と要素の数(update の --inspect-only。
    # 重み本体は落とさない)。None は読んでいない・読めない。後から足した項目(0.1.1)
    header_precision: str | None = None
    header_params: int | None = None
    # 見出しも名前も精度を示さないとき、置き場所のリポジトリから読んだ精度(#30、0.2.0)。
    # repo_precision_source は hf_api(Hub API の safetensors の dtype ごとの要素の数。一つの dtype が
    # 9 割以上のときだけ)か config(config.json の torch_dtype / dtype)。None は読んでいない・読めない
    repo_precision: str | None = None
    repo_precision_source: str | None = None
    # 任意の部品(#81。0.11.0 で足した任意の項目で schema_version は上げない。古い版は知らないキーを捨てて
    # 今までどおり全部を数える)。公式テンプレートの既定の値で読み込まれないファイル(optional.py):
    #   inactive    無効・素通しのノードや出力につながらないノードにしかない(VRAM・メモリ・ディスク・
    #               download に数えない)
    #   switch_off  値の決まったスイッチの選ばれない枝にしかない(VRAM・メモリに数えない。検証で要るので
    #               ディスク・download には数える)
    # None は既定で読み込む(または分からない)。update がテンプレートのつながりから決める
    optional: str | None = None
    # optional が switch_off のとき、そのスイッチを切り替えると読み込まなくなる既定の部品(ファイル名)。
    # 空・None は足すだけ
    optional_replaces: list[str] | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ModelFile:
        f = _from_dict(cls, d)
        f.license_path = list(f.license_path or [])  # null は空の並び(エントリ側と同じ)
        f.license_evidence = list(f.license_evidence or [])
        return f


@dataclass
class Template:
    """ComfyUI 公式テンプレートの情報(source が comfyui-template のとき)。"""

    name: str
    title: str | None = None
    category: str | None = None
    min_comfyui_version: str | None = None
    open_source: bool | None = None
    requires_custom_nodes: list[str] = field(default_factory=list)
    usage: int | None = None
    date: str | None = None
    size_bytes: int | None = None  # index.json の size
    username: str | None = None  # index.json の username(作った人。ComfyUI 以外は利用者のテンプレート)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Template:
        t = _from_dict(cls, d)
        t.requires_custom_nodes = list(t.requires_custom_nodes or [])
        return t


# 確認の印(ModelEntry.review。0.6.0、#42)。pending = 確認待ち(自動のおすすめに出さない。gate の reviewed)
REVIEW_STATUSES = ("ok", "pending")
# pending にした理由(update が前の一覧と比べて付ける。docs/schema.md「review」)
REVIEW_REASONS = ("new", "license_permissive", "need_decreased", "uncensored_new", "files_changed")


@dataclass
class Review:
    """人(または毎週のスキル)が一次資料で確かめたかの印。無い(None)は古い一覧で、確認済みとして扱う。"""

    status: str = "ok"  # REVIEW_STATUSES
    reasons: list[str] = field(default_factory=list)  # REVIEW_REASONS(確認済みでも、確かめた理由を残す)
    decided_by: str | None = None  # 確かめた人・仕組み(例 "baseline-2026-10-21"、"owner")
    decided_at: str | None = None  # 確かめた日(YYYY-MM-DD)
    evidence: list[str] = field(default_factory=list)  # 根拠の URL(一次資料)
    note: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Review:
        r = _from_dict(cls, d)
        r.reasons = list(r.reasons or [])
        r.evidence = list(r.evidence or [])
        return r


@dataclass
class ModelEntry:
    """一覧の 1 件。1 つのワークフロー(または 1 つの HF リポジトリ)で使うモデル一式。"""

    id: str
    name: str
    modality: str
    tasks: list[str] = field(default_factory=list)  # t2i / i2i / t2v / i2v / t2a / tts / t23d / i23d など
    source: str = "comfyui-template"  # comfyui-template / huggingface / manual
    files: list[ModelFile] = field(default_factory=list)
    template: Template | None = None
    params_b: float | None = None  # 本体のパラメータ数(十億)
    precision: str | None = None  # fp32 / bf16 / fp16 / fp8 / int8 / nf4 / gguf-q4 など
    license: str | None = None  # モデル一式のライセンス(最も厳しいもの)
    license_url: str | None = None
    base_model: str | None = None
    content: str = "sfw"
    content_evidence: list[str] = field(default_factory=list)  # 無検閲と判断した根拠
    discovered_from: list[str] = field(default_factory=list)  # どこから拾ったか(URL)
    updated: str | None = None
    homepage: str | None = None
    downloads: int | None = None  # HF の直近 30 日のダウンロード数(source が huggingface のとき)
    likes: int | None = None
    downloads_all_time: int | None = None  # HF の全期間のダウンロード数(一覧に入れるかの下限に使う)
    license_name: str | None = None  # license が other のときの名前(一番厳しいファイルのもの)
    # 不明にした理由など(ファイルの license_evidence のうち license_unverified をまとめたもの)
    license_evidence: list[str] = field(default_factory=list)
    # ファイルの一覧で一式がそろっているか。False: 本体が無い・合計が index の size の半分未満・
    # GGUF の本体だけ(テキストエンコーダ・VAE が無い)。None は判定していない(古いデータ)。docs/schema.md
    files_complete: bool | None = None
    # 一式の大きさの下限(files_complete が False のとき、index の size)。見積もりはこれより小さく見ない
    size_floor_bytes: int | None = None
    # ComfyUI のモデルの系統と VAE の種類(families.FAMILIES / VAES の鍵。None は分からない)。
    # bf16 を持たない GPU で fp32 で計算するかを決めるのに使う(docs/hardware.md「精度」)。後から足した項目
    comfy_family: str | None = None
    comfy_vae: str | None = None
    # ワークフローの中で、サンプラーごとに使う本体の組(ファイル名。本体 + その本体に掛ける LoRA など)。
    # 2 つ以上あれば ComfyUI は順に動かす(Wan 2.2 14B の high_noise → low_noise など)。テンプレートの
    # つながりから update が求める(workflow.sampler_stages)。None は調べていない、[] は 1 組だけ。0.1.1
    sampler_stages: list[list[str]] | None = None
    # 公式テンプレートの本体の読み込みノード(UNETLoader の weight_dtype、WanVideoModelLoader の quantization、
    # 読み込むファイル名など)から推した精度(workflow.loader_precision。#30、0.2.0)。
    # None は推せない・調べていない
    workflow_precision: str | None = None
    # precision をどこから決めたか(PRECISION_SOURCES。None は精度が分からない・古いデータ)。#30、0.2.0
    precision_source: str | None = None
    # アプリが一覧だけで画面を作るための任意の項目(0.5.0、#36。古い版は知らないキーとして捨てるので読める)。
    #   languages    {"prompt": ["en", ...], "speech": ["ja", ...]}(ISO 639-1。どちらも省略可、
    #                分からなければ省く)。prompt = 指示(プロンプト)で通じる言語、
    #                speech = 読み上げ・歌詞で出せる言語。appinfo.py が決める
    #   runtime      動かす仕組み(comfyui / piper / irodori-tts / supertonic など)。None は分からない
    #   description  {"ja": "...", "en": "..."}。手で書いたものがあるモデルだけ
    #                (data/overrides.json・manual.json)
    languages: dict[str, list[str]] = field(default_factory=dict)
    runtime: str | None = None
    description: dict[str, str] = field(default_factory=dict)
    # HF のカードの language(appinfo が languages を作り直す材料。取得できたときに保存し、通信しない当て直しで
    # 使う)。None = まだ読んでいない、[] = 読んだがカードに言語が無い
    card_languages: list[str] | None = None
    # 確認の印(0.6.0、#42。任意の項目で schema_version は上げない)。
    # None は確認済みとして扱う(印の無い古い一覧)。
    # update は前の一覧と比べて必ず付ける。アプリは `review is None or review.status == "ok"` で絞る
    review: Review | None = None

    @property
    def reviewed(self) -> bool:
        """確認済みか(印が無い古い一覧は確認済みとみなす)。"""
        return self.review is None or self.review.status == "ok"

    @property
    def license_unknown(self) -> bool:
        """ライセンスの分からないファイルがあるか(ファイルが無ければ一式のライセンス)。
        `recommend` / `fit_list` などは既定でこれを出さない(include_unknown_license=True で出す。#28)。
        同じ判定を 1 件ずつ使う(#96)。"""
        from .recommend import license_unknown

        return license_unknown(self)

    @property
    def is_tool(self) -> bool:
        """補助ツール(前処理・音の分離など。tasks がすべて補助の作業か、題がそれのもの)か。`recommend` /
        `fit_list` は既定でこれを出さない(include_tools=True で出す)。同じ判定を 1 件ずつ使う(#96)。"""
        from .tasks import is_tool

        return is_tool(self)

    @property
    def total_size_bytes(self) -> int | None:
        sizes = [f.size_bytes for f in self.files if f.size_bytes]
        if sizes:
            return sum(sizes)
        return self.template.size_bytes if self.template else None

    @property
    def estimate_size_bytes(self) -> int | None:
        """見積もりに使う大きさ。ファイルの合計が size_floor_bytes より小さければ下限を使う
        (一式が欠けた一覧を小さく見せない)。"""
        total = self.total_size_bytes
        if self.size_floor_bytes and (total or 0) < self.size_floor_bytes:
            return self.size_floor_bytes
        return total

    @property
    def largest_file_bytes(self) -> int | None:
        sizes = [f.size_bytes for f in self.files if f.size_bytes]
        return max(sizes) if sizes else None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["total_size_bytes"] = self.total_size_bytes
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ModelEntry:
        e = _from_dict(cls, d)
        # null で入っていても空の辞書として扱う(後から足した項目。型は db.check_entry が確かめる)
        e.languages = dict(e.languages or {})
        e.description = dict(e.description or {})
        # 型の表が null を許す並びは空の並びに直す(使う側は None を想定しない)
        e.content_evidence = list(e.content_evidence or [])
        e.discovered_from = list(e.discovered_from or [])
        e.license_evidence = list(e.license_evidence or [])
        e.files = [ModelFile.from_dict(f) if isinstance(f, dict) else f for f in (d.get("files") or [])]
        t = d.get("template")
        e.template = Template.from_dict(t) if isinstance(t, dict) else t
        r = d.get("review")
        e.review = Review.from_dict(r) if isinstance(r, dict) else None
        return e


@dataclass
class Settings:
    """生成の設定。種類ごとに使う項目が違う(画像は width/height、動画は frames も、音声は seconds)。"""

    width: int | None = None
    height: int | None = None
    frames: int | None = None
    seconds: float | None = None
    steps: int | None = None
    precision: str | None = None
    batch: int = 1

    @property
    def megapixels(self) -> float | None:
        if self.width and self.height:
            return self.width * self.height / 1_000_000
        return None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Settings:
        return _from_dict(cls, d)


@dataclass
class Measurement:
    """実機での実測 1 件。"""

    model_id: str
    gpu_name: str
    gpu_vram_gb: float
    peak_vram_gb: float
    settings: Settings = field(default_factory=Settings)
    peak_ram_gb: float | None = None
    seconds: float | None = None
    driver: str | None = None
    comfyui_version: str | None = None
    offload: str | None = None  # none / dynamic / partial / lowvram / oom(ComfyUI の動き)
    source: str = ""  # 誰がどこで測ったか
    date: str | None = None
    note: str | None = None
    # 以下は後から足した項目(無い古い行は ok・標準として読む)
    outcome: str = "ok"  # OUTCOMES。ok 以外は「動く」根拠に使わない
    standard: bool = True  # 公式テンプレートのまま(幅・高さ・フレーム数・長さ・ステップ数だけ変えた)か
    # peak_vram_gb の測り方。torch_reserved = ComfyUI のプロセスの torch の確保量の最大
    # (bench。CUDA の文脈は含まない)。
    # None = 記録が無い(AI生成キットで測った RTX 5080 の 9 件)。測り方が違う値どうしで係数を合わせない
    vram_metric: str | None = None
    # ほかの利用者の実測をまとめたもの(#34。measurements_community.json)の印と件数。
    # origin が "community" のとき、ok の行は 1 件でも見積もりに使う(2026-10-22、llmfit に合わせた。
    # estimate.usable)。None は同梱の作者の実測・この PC の bench
    origin: str | None = None
    n_submissions: int | None = None  # まとめた提出の数(外れ値・OOM も含む)
    # 同じ GPU・設定の 2 件以上で、ばらつきが小さく OOM が無い。係数の合わせ直し(calibrate)に入れる条件
    consistent: bool | None = None
    # 測った GPU の PyTorch の backend(cuda / rocm / xpu / mps。0.9.0、bench が書く)。None は記録が無い
    # (同梱の実測はすべて NVIDIA の cuda)
    gpu_backend: str | None = None
    n_used: int | None = None  # 中央値に使った ok の件数(外れ値を除く。0.11.0 から sample_count は n_sources)
    n_oom: int | None = None  # 同じ構成で OOM だった提出の数(1 件以上なら perfect の根拠にしない)
    # 測った GPU の PCI の型番 ID("1002:744c" = vendor:device。機種の番号で、個体・差し場所は表さない。
    # 0.9.0、bench が ComfyUI と同じ PC の検出から書く)。同じ GPU かの照合に名前より先に使う
    # (estimate.same_gpu)
    gpu_pci_id: str | None = None
    # 測ったときの一覧のモデルのファイルの指紋(#90。estimate.files_fingerprint)。一覧のファイルが変わったら
    # (指紋が違えば)その実測は使わない。None は記録が無い(古い行・作者の measurements.json)
    files_fp: str | None = None
    # テンプレートの設定(幅・高さ・コマ数・長さ・ステップ数)を変えて測ったか(#116。bench・measure が
    # 変えたときだけ True を書く)。None・False はテンプレートのまま(記録の無い古い行もそう扱う)。
    # measure の「テンプレートのままの実測がこの GPU にあるか」に使う
    settings_override: bool | None = None
    # 以下はほかの利用者の実測のまとめの行だけ(#90)
    gpu_known: bool | None = None  # False = 名前の表に無い GPU(記録として残すだけで判定に使わない)
    n_sources: int | None = None  # 使った提出の、別々の出どころ(リポジトリに足したコミット = PR)の数
    # 使った出どころの短い ID(#95。contrib.source_id)。同じ人の提出が別の行に分かれても、出どころを
    # ID の和集合で数える(estimate.count_sources)。None は記録の無い古いまとめ
    source_ids: list[str] | None = None
    n_error: int | None = None  # 同じ構成で ok・oom 以外(error)で終わった提出の数(行の値には使わない)
    runs_evidence: bool | None = None  # False = OOM の出どころが ok の出どころ以上(「動く」根拠にしない)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Measurement:
        m = _from_dict(cls, d)
        s = d.get("settings")
        m.settings = Settings.from_dict(s) if isinstance(s, dict) else (s or Settings())
        return m


# 判定に使える GPU の backend(0.7.0、#22)。cuda = NVIDIA、rocm = AMD(ROCm 版 PyTorch)、
# xpu = Intel(XPU 版 PyTorch)、mps = Apple silicon(MPS)。none は判定に使わない
USABLE_BACKENDS = ("cuda", "rocm", "xpu", "mps")
# GPU のメモリの種類(Gpu.memory)。dedicated = 専用の VRAM、shared = メインメモリと共有する内蔵 GPU・APU
# (Windows の共有 GPU メモリ・Linux の GTT)、unified = Apple silicon の統合メモリ
GPU_MEMORY_KINDS = ("dedicated", "shared", "unified")


@dataclass
class Gpu:
    """判定に使う GPU(docs/hardware.md)。NVIDIA は nvidia-smi で確かめたもの。AMD・Intel・Apple は
    画面の部品の一覧から、一次資料の対応表で ComfyUI + PyTorch が動く組み合わせと決めたもの(0.7.0、#22)。
    判定に使うのは backend が USABLE_BACKENDS のものだけ。"""

    name: str
    vendor: str  # nvidia / amd / intel / apple / unknown
    # VRAM の総量。shared・unified は、GPU が使えるとみなしたメインメモリの分(docs/hardware.md「共有メモリ」)
    vram_total_gb: float
    vram_free_gb: float | None = None  # 今の空き(nvidia-smi の memory.free)。上書きしたときは None
    driver: str | None = None
    # cuda / rocm / xpu / mps(判定に使う)/ none(動かない世代・古いドライバ・対応表に無い GPU)
    backend: str = "none"
    vram_used_gb: float | None = None  # 今ほかのアプリ(画面・ブラウザなど)が使っている量(memory.used)
    compute_capability: str | None = None  # 例 "8.6"。None は分からない
    compute_capability_source: str | None = None  # nvidia-smi / name(GPU 名の表から推した)/ None
    arch: str | None = None  # kepler / maxwell / pascal / volta / turing / ampere / ada / hopper / blackwell
    # 今の PyTorch(docs/hardware.md の版)の CUDA 版のうち、この GPU とドライバで動くもの(新しい順)。参考の情報
    torch_cuda: list[str] = field(default_factory=list)
    # ComfyUI がこの GPU で選ぶ計算の精度(fp16 / bf16 / fp32)と、bf16・fp8 の計算に対応するか。
    # None は分からない
    compute_dtype: str | None = None
    bf16: bool | None = None
    fp8: bool | None = None
    notes: list[str] = field(default_factory=list)  # backend が none の理由・注意
    # 以下は 0.7.0 で足した項目(#22)。memory は GPU_MEMORY_KINDS、
    # gfx_target は AMD の LLVM の名前(gfx1100 など)
    memory: str = "dedicated"
    gfx_target: str | None = None
    # memory が shared のとき、BIOS で取り分けた VRAM(GiB。メインメモリの搭載量に入らない分)。ほかは None
    carveout_gb: float | None = None
    # PCI の型番 ID("1002:744c"。0.9.0)。Windows の PNPDeviceID・Linux の lspci -nn から。NVIDIA(nvidia-smi)は
    # None(名前がどこでも同じなので名前で照合する)。個体・差し場所は表さない
    pci_id: str | None = None
    # --vram で VRAM だけを上書きしたか(#150)。True のとき name・世代・精度はこの PC の GPU のまま、
    # 量だけが仮定。理由・注意の文では name でなく label を出す
    vram_overridden: bool = False

    @property
    def label(self) -> str:
        """理由・注意の文の GPU の表示。上書きなしは name、ありは「<name> の世代」(照合は name)。"""
        if self.vram_overridden:
            return tr(f"{self.name} の世代", f"the {self.name} generation")
        return self.name


@dataclass
class Adapter:
    """NVIDIA 以外も含めて見つけた画面の部品(GPU)。0.7.0 から、AMD・Intel・Apple は対応表で判定に使えるかを
    決める(usable と reason。判定に使うものは SystemInfo.gpus にも Gpu として入る)。"""

    name: str
    vendor: str  # nvidia / amd / intel / apple / unknown
    vram_gb: float | None = None  # Windows はレジストリの qwMemorySize、Linux の AMD は sysfs
    vram_source: str | None = None  # registry / wmi / sysfs / name / None
    source: str = ""  # wmi / lspci / system_profiler
    # 以下は 0.7.0 で足した項目(#22)
    integrated: bool | None = None  # 内蔵 GPU・APU か(None は分からない)
    usable: bool | None = None  # 判定に使える組み合わせか(None は決めていない: NVIDIA は nvidia-smi で決める)
    reason: str | None = None  # 使える・使えない理由(日本語の 1 文)
    shared_gb: float | None = (
        None  # Linux の amdgpu の GTT(メインメモリから使える分。sysfs mem_info_gtt_total)
    )
    needs: str | None = None  # 使えるようにする条件(今は "Windows 11" だけ。OS の版だけで断ったとき)
    pci_id: str | None = None  # PCI の型番 ID("1002:744c"。0.9.0。Gpu.pci_id)
    # 以下は 0.11.0 で足した項目(#101。docs/hardware.md「すべての環境」)
    driver: str | None = None  # Windows の WMI の DriverVersion(Intel の WSL2 の条件に使う)
    # AMD の世代(gfx1100 など)を名前より確かな所から読めたとき: Linux の amdkfd の gfx_target_version、
    # WSL2 の rocminfo の Name: gfx…
    gfx_target: str | None = None
    # WSL2 で Windows 側から読んだ部品のとき、WSL の中の準備(AMD は rocminfo、Intel は clinfo -l)が
    # できているか
    wsl_runtime: bool | None = None
    # Windows の WMI の ConfigManagerErrorCode(0 以外のとき。22・28・43 の部品は判定に使わない)
    config_error: int | None = None


@dataclass
class SystemInfo:
    os: str
    cpu: str
    cpu_cores: int
    ram_total_gb: float
    ram_available_gb: float
    gpus: list[Gpu] = field(default_factory=list)
    disk_free_gb: float | None = None
    overrides: list[str] = field(default_factory=list)  # --vram などで上書きした項目
    # 仮想メモリ。swap_total_gb は今のページファイル(Windows)・スワップ(Linux)の大きさ。
    # swap_max_gb は伸びる上限(固定の大きさのページファイル・Linux のスワップ)。None は分からない・
    # システム管理で伸びる(swap_auto=True)。判定の「少し足りなければ仮想メモリで遅い」はこれで決める
    swap_total_gb: float | None = None
    swap_max_gb: float | None = None
    swap_auto: bool | None = None
    # Windows のコミット(物理メモリ + ページファイル)の今の上限と、今使っている量
    commit_limit_gb: float | None = None
    commit_used_gb: float | None = None
    other_gpus: list[Adapter] = field(default_factory=list)  # NVIDIA 以外も含めた画面の部品(知らせるだけ)
    other_gpus_checked: bool = False  # other_gpus を調べたか(NVIDIA の GPU があれば既定では調べない)
    notes: list[str] = field(default_factory=list)  # 検出で分かったこと・分からなかった理由

    @property
    def best_gpu(self) -> Gpu | None:
        """判定に使う GPU。NVIDIA(cuda)があればその最大の 1 枚(これまでどおり)、無ければ AMD・Intel・Apple の
        使えるもののうち最大の 1 枚。"""
        usable = [g for g in self.gpus if g.backend == "cuda"] or [
            g for g in self.gpus if g.backend in USABLE_BACKENDS
        ]
        # 専用の VRAM の GPU を内蔵 GPU・APU より先にする(共有の分がメインメモリから大きく見えても)
        return max(usable, key=lambda g: (g.memory == "dedicated", g.vram_total_gb)) if usable else None

    @property
    def vram_gb(self) -> float:
        g = self.best_gpu
        return g.vram_total_gb if g else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["best_gpu"] = asdict(self.best_gpu) if self.best_gpu else None
        return d


@dataclass
class Estimate:
    """1 モデル × 1 設定 × 1 PC の見積もりと判定。"""

    vram_gb: float  # 全部を GPU に載せたときの最大 VRAM
    vram_offload_gb: float  # 重みの一部(offload_fraction 以外)をメインメモリに逃がしたときの最大 VRAM
    ram_gb: float  # 判定した動かし方で要るメインメモリ(GiB)
    disk_gb: float | None
    basis: str  # measured / estimated
    fit: str  # FIT_LEVELS、大きさが分からないときは FIT_UNKNOWN
    run_mode: str  # RUN_MODES。gpu / offload / cpu / none(動かない)/ unknown(大きさが分からず判定していない)
    reasons: list[str] = field(default_factory=list)
    measured_on: str | None = None  # basis が measured のとき、どの GPU で測ったか
    vram_sequential_gb: float | None = None  # 部品を段ごとに載せ替えたときの最大 VRAM
    settings: dict[str, Any] | None = None  # 判定に使った設定(省いた項目は既定や実測の設定で埋めたもの)
    limits: dict[str, Any] | None = None  # この PC で good 以上になる最大の設定(info で出す)
    unknown_size: bool = False  # 重みの大きさが分からず判定できない(fit は FIT_UNKNOWN、VRAM の値は 0)
    # GPU で動く判定だが、今の空き VRAM(nvidia-smi の memory.free)では足りない(ほかのアプリを閉じれば動く)。
    # 段階は総量で決めるので、これは知らせるだけ
    vram_free_short: bool = False
    # 根拠の細かい段階(BASIS_DETAILS。#34)。basis は measured / estimated のまま(後方互換)
    basis_detail: str = "estimated"
    # 段階を何で決めたか(0.7.0、#26)。estimate = 見積もり、
    # measurement = この PC と同じ GPU・同じ設定で、別々の 2 つ以上の出どころの実測がそろった(#95)
    # (そのとき measured_peak_vram_gb にそろった実測のピークの一番大きいもの)
    fit_from: str = "estimate"
    measured_peak_vram_gb: float | None = None
    # fit_from が measurement のとき、段階に使った量(#95): max(ピーク + 文脈の分, 重み + 固定分)。
    # 表の VRAM と Mem% はこの値
    measured_level_vram_gb: float | None = None
    # 値の元になった実測の、別々の出どころの数(2026-10-22。llmfit の sample_count。#95 で件数から出どころの
    # 数に)。作者・この PC の実測は 1(何行あっても合わせて 1)、ほかの利用者のまとめは n_sources。
    # 段階を実測で決めたときは、そろった出どころの合計。実測を使っていない(basis_detail が estimated)
    # ときは None
    sample_count: int | None = None
    # 全部を載せたまま仕上げまで動かすときに ComfyUI が空けようとする量(#102。VAE はタイルにしない V)。
    # 98% を超えると全部載せを選ばない(順に載せ替え、good まで)。vram_gb 以上。古い Estimate は None
    vram_resident_gb: float | None = None
    # 任意の部品(#81。テンプレートの既定の値で読み込まれない部品。上の量には入れていない)。1 つずつ
    # {"name", "size_gb", "optional": inactive / switch_off, "kind": add / replace, "replaces": [...]}
    optional_parts: list[dict[str, Any]] = field(default_factory=list)
    # スイッチで切ってある部品(switch_off)を全部入れたときの、判定に使う VRAM(上限。recommend.gpu_need と
    # 同じ選び方)と段階。switch_off が無ければ None。GPU が無い・大きさ不明なら VRAM は None
    vram_gb_all_options: float | None = None
    fit_all_options: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON の形。大きさが分からず判定していないとき、VRAM・RAM の値は分からない(null)にする
        (Python の属性は計算のため 0.0 のまま。unknown_size を見る)。"""
        d = asdict(self)
        if self.unknown_size:
            for k in ("vram_gb", "vram_offload_gb", "ram_gb", "vram_sequential_gb", "vram_resident_gb"):
                d[k] = None
        return d
