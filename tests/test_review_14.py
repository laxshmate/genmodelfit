"""PR #14(gate)のレビューの指摘のテスト。ネットワークには触らない。

- 本体のリポジトリの表記が、中の別のモデルの部品(DINOv3・RT-DETRv4 など)に付いていた
- Stable Diffusion から作ったモデル(SDPose・Lotus)が、カードの寛容な表記のまま通っていた
- 名前の変わったリポジトリの 307 は、無いファイルでも返る
- HEAD の確認に期限が無かった
- 版のプレリリースの印・heads-only の版の取り直し・--limit 0・cc-by-4.0
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest

from genmodelfit import cli
from genmodelfit.db import load
from genmodelfit.fetch import FetchError, HttpFetcher, follow_rename, is_resolve_cache
from genmodelfit.gate import (
    DEFAULT_LICENSE_ALLOWLIST,
    GatePolicy,
    evaluate_offline,
    resolve_file_license,
    version_le,
)
from genmodelfit.hf import HfClient, foreign_marker
from genmodelfit.models import ModelEntry, ModelFile, Template

SHA = "a" * 64
SOURCES = {"comfyui": {"latest_release": "v0.42.1"}}


def _file(name="model.safetensors", **kw) -> ModelFile:
    repo = kw.pop("repo_id", "Org/Repo")
    d = dict(
        url=f"https://huggingface.co/{repo}/resolve/main/{name}",
        sha256=SHA,
        size_bytes=1_000_000_000,
        repo_id=repo,
        license="mit",
        gated=False,
        role="main",
        license_from=repo,
        head_status=302,
        head_checked_at="2026-10-20T00:00:00Z",
    )
    d.update(kw)
    return ModelFile(name=name, **d)


def _entry(files, **kw) -> ModelEntry:
    d = dict(
        id="t",
        name="t",
        modality="3d",
        files=files,
        template=Template(name="t", min_comfyui_version="0.3.0", open_source=True),
        license="mit",
        files_complete=True,
    )
    d.update(kw)
    return ModelEntry(**d)


# --- 1. 別のモデルの部品(gate の二重の守り) ----------


def test_dinov3_in_any_repo_is_restricted_and_gated():
    # 実例: VAST-AI/TripoSplat(表記 mit)の clip_vision/dino_v3_vit_h.safetensors、
    # Comfy-Org/Pixal3D(本家 TencentARC/Pixal3D は mit)の dino_v3_L_naf_fp32.safetensors
    for f in (
        _file("clip_vision/dino_v3_vit_h.safetensors", repo_id="VAST-AI/TripoSplat"),
        _file(
            "clip_vision/dino_v3_L_naf_fp32.safetensors",
            repo_id="Comfy-Org/Pixal3D",
            license_from="TencentARC/Pixal3D",
            license_path=["Comfy-Org/Pixal3D", "TencentARC/Pixal3D"],
        ),
        _file("dinov3_vitl16.safetensors", repo_id="facebook/dinov3-vitl16-pretrain-lvd1689m"),
    ):
        r = resolve_file_license(f)
        assert r.license == "other" and r.gated, f.name
        res = evaluate_offline(_entry([f]), sources=SOURCES)
        assert {"license", "gated"} <= set(res.held_by)


def test_other_restricted_components():
    cases = {
        "sam3.1_multiplex_fp16.safetensors": "other",
        "depth_anything_v2_vitl.pth": "cc-by-nc-4.0",
        "NetaYumev35_pretrained_all_in_one.safetensors": "gemma",
        "lumina_2.safetensors": "gemma",
    }
    for name, lic in cases.items():
        assert resolve_file_license(_file(name, license="apache-2.0")).license == lic, name
    # 寛容なもの(本家を確かめた)は、その部品の本家の名前のリポジトリでは通る
    assert (
        resolve_file_license(_file("depth_anything_v2_vits.pth", repo_id="depth-anything/X")).license == "mit"
    )


def test_rt_detr_v4_is_unverified_even_inside_sdpose_repo():
    # Comfy-Org/SDPose の README は RT-DETRv4 を別の本家(GitHub)として挙げる。SDPose の一部ではない
    f = _file(
        "diffusion_models/rt_detr_v4-x-hgnet_fp16.safetensors",
        repo_id="Comfy-Org/SDPose",
        license_from="teemosliang/SDPose-Wholebody",
    )
    r = resolve_file_license(f)
    assert r.license is None and "RT-DETRv4" in r.note


@pytest.mark.parametrize(
    "name,repo,source",
    [
        # 実例: utility_sdpose_*(カードは mit、U-Net は Stable Diffusion v2 から)
        ("sdpose_wholebody_fp16.safetensors", "Comfy-Org/SDPose", "teemosliang/SDPose-Wholebody"),
        # 実例: image_lotus_depth_v1_1(カードは apache-2.0、stable-diffusion-2-base から学習)
        ("lotus-depth-d-v1-1.safetensors", "Comfy-Org/lotus", "jingheya/lotus-depth-d-v1-1"),
        ("model.safetensors", "someone/sd2-finetune", "someone/sd2-finetune"),
    ],
)
def test_sd_derived_with_permissive_label_is_held(name, repo, source):
    f = _file(name, repo_id=repo, license="mit", license_from=source, license_path=[repo, source])
    r = resolve_file_license(f)
    assert r.license is None and "系統" in r.note
    assert evaluate_offline(_entry([f]), sources=SOURCES).held_by[0] == "license"


def test_marigold_v1_is_openrail_but_v2_is_not():
    f = _file("marigold_depth_v1_1.safetensors", repo_id="someone/marigold", license="apache-2.0")
    assert resolve_file_license(f).license is None  # 系統の表(openrail++)より寛容な表記
    f2 = _file(
        "marigold_v2_depth_log_stage2.safetensors",
        repo_id="Comfy-Org/marigold-v2-0",
        license="apache-2.0",
        license_from="huawei-bayerlab/marigold-v2-0",
    )
    assert resolve_file_license(f2).license == "apache-2.0"


def test_third_party_component_in_non_repackager_repo():
    # 置き場所(TripoSplat)と別の系統の部品で、本家を確かめた行が無いもの → 確かめられない
    f = _file("text_encoder/qwen2_vl_custom.safetensors", repo_id="VAST-AI/TripoSplat")
    r = resolve_file_license(f)
    assert r.license is None and "別の系統(qwen)" in r.note
    # 本家を確かめた行に当たれば、その本家のライセンス(厳しいほう)
    f = _file("vae/flux2-vae.safetensors", repo_id="VAST-AI/TripoSplat", license="mit")
    assert resolve_file_license(f).license == "apache-2.0"
    f = _file("background_removal/birefnet.safetensors", repo_id="VAST-AI/TripoSplat")
    assert resolve_file_license(f).license == "mit"
    # 自分の系統の名前なら別の部品ではない
    assert foreign_marker("qwen_image_vae.safetensors", ["Comfy-Org/Qwen-Image_ComfyUI"]) is None
    assert foreign_marker("qwen_image_vae.safetensors", ["FireRedTeam/FireRed-Image-Edit"]) == "qwen"


# --- 1b. update 側(hf.resolve_file) ----------


class _Api:
    def __init__(self, repos):
        self.repos = repos

    def get_json(self, url):
        rid = re.match(r"https://huggingface\.co/api/models/(.+?)(\?|/revision/)", url).group(1)
        if rid not in self.repos:
            raise FetchError(url, "HTTP 401", 401)
        d = self.repos[rid]
        card = {k: v for k, v in d.items() if k in ("license", "license_name", "base_model")}
        return {"id": rid, "cardData": card, "tags": [], "siblings": []}


def _resolve(repos, repo_id, path):
    c = HfClient(_Api(repos))
    return c.resolve_file(c.repo(repo_id), path)


def test_update_resolves_dinov3_inside_other_repos():
    repos = {
        "VAST-AI/TripoSplat": {"license": "mit"},
        "Comfy-Org/Pixal3D": {"license": "mit", "base_model": ["TencentARC/Pixal3D"]},
        "TencentARC/Pixal3D": {"license": "mit"},
    }
    for repo, path in [
        ("VAST-AI/TripoSplat", "clip_vision/dino_v3_vit_h.safetensors"),
        ("Comfy-Org/Pixal3D", "clip_vision/dino_v3_L_naf_fp32.safetensors"),
    ]:
        r = _resolve(repos, repo, path)
        assert (r.license, r.license_name) == ("other", "dinov3-license"), repo
        assert any(e.startswith("upstream_gated") for e in r.evidence)
    # Pixal3D の本体は本家の mit、TRELLIS.2 の VAE は microsoft/TRELLIS.2-4B の mit
    assert _resolve(repos, "Comfy-Org/Pixal3D", "pixal3d_int8_convrot.safetensors").license == "mit"
    r = _resolve(repos, "Comfy-Org/Pixal3D", "vae/trellis_2_shape_vae_bf16.safetensors")
    assert (r.license, r.source) == ("mit", "microsoft/TRELLIS.2-4B")


def test_update_resolves_sdpose_and_rt_detr():
    repos = {
        "Comfy-Org/SDPose": {"license": "mit", "base_model": ["teemosliang/SDPose-Wholebody"]},
        "teemosliang/SDPose-Wholebody": {"license": "mit"},
        "Comfy-Org/lotus": {"license": "apache-2.0", "base_model": ["jingheya/lotus-depth-d-v1-1"]},
        "jingheya/lotus-depth-d-v1-1": {"license": "apache-2.0"},
    }
    r = _resolve(repos, "Comfy-Org/SDPose", "checkpoints/sdpose_wholebody_fp16.safetensors")
    assert r.license is None and "sdpose" in r.evidence[0]
    r = _resolve(repos, "Comfy-Org/SDPose", "diffusion_models/rt_detr_v4-x-hgnet_fp16.safetensors")
    assert r.license is None and "RT-DETRv4" in r.evidence[0] and "SDPose-Wholebody" not in r.path
    r = _resolve(repos, "Comfy-Org/lotus", "lotus-depth-d-v1-1.safetensors")
    assert r.license is None and "lotus" in r.evidence[0]


def test_update_third_party_component_without_row_is_unknown():
    repos = {"someone/image-model": {"license": "apache-2.0"}}
    r = _resolve(repos, "someone/image-model", "text_encoder/llava_custom.safetensors")
    assert r.license == "llama3.1" or r.license is None
    r = _resolve(repos, "someone/image-model", "text_encoder/mistral_small.safetensors")
    assert r.license is None and "mistral" in r.evidence[0]
    # 本家を確かめた行があれば通る(Qwen3-VL は apache-2.0)
    r = _resolve(repos, "someone/image-model", "qwen3vl_8b_custom_int8.safetensors")
    assert r.license == "apache-2.0"


# --- 3. 307 ----------


def test_follow_rename_one_hop():
    hops = []

    def hop(url):
        hops.append(url)
        return {
            "https://huggingface.co/new/repo/resolve/main/x": (302, "https://cdn/x"),
            "https://huggingface.co/new/repo/resolve/main/missing": (404, None),
            "https://huggingface.co/new/repo/resolve/main/loop": (307, "/other/repo/resolve/main/loop"),
            "https://huggingface.co/new/repo/resolve/main/small.json": (
                307,
                "/api/resolve-cache/models/new/repo/abc/small.json",
            ),
        }[url]

    old = "https://huggingface.co/old/repo/resolve/main/"
    cache = "/api/resolve-cache/models/old/repo/abc/model_index.json"
    assert is_resolve_cache(cache) and not is_resolve_cache("/new/repo/resolve/main/x")
    assert follow_rename(old + "model_index.json", 307, cache, hop) == 307 and hops == []
    assert follow_rename(old + "x", 307, "/new/repo/resolve/main/x", hop) == 302
    assert follow_rename(old + "missing", 307, "/new/repo/resolve/main/missing", hop) == 404
    assert follow_rename(old + "loop", 307, "/new/repo/resolve/main/loop", hop) is None
    assert follow_rename(old + "small.json", 307, "/new/repo/resolve/main/small.json", hop) == 307
    assert follow_rename(old + "y", 302, "https://cdn/y", hop) == 302
    assert follow_rename(old + "z", 307, None, hop) is None


def test_http_head_status_follows_rename_once():
    class R:
        def __init__(self, status, loc=None):
            self.status_code, self.headers = status, ({"location": loc} if loc else {})

    calls = []
    replies = {
        "https://huggingface.co/old/r/resolve/main/gone.safetensors": R(
            307, "/new/r/resolve/main/gone.safetensors"
        ),
        "https://huggingface.co/new/r/resolve/main/gone.safetensors": R(404),
    }

    class S:
        def request(self, method, url, **kw):
            calls.append((method, url, kw["allow_redirects"]))
            return replies[url]

    f = HttpFetcher(sleep=lambda s: None, clock=lambda: 0.0)
    f._local.session = S()
    assert f.head_status("https://huggingface.co/old/r/resolve/main/gone.safetensors") == 404
    assert [c[2] for c in calls] == [False, False] and len(calls) == 2


# --- 4. HEAD の期限 ----------


def test_head_age_limit():
    e = _entry([_file(head_checked_at="2026-09-01T00:00:00Z")], license="mit")
    now = datetime(2026, 10, 20, tzinfo=UTC)
    r = evaluate_offline(e, sources=SOURCES, now=now)
    c = next(c for c in r.checks if c.id == "url_reachable")
    assert not c.passed and "確認が古い" in c.detail
    assert evaluate_offline(e, GatePolicy(max_head_age_days=60), sources=SOURCES, now=now).auto
    assert evaluate_offline(e, GatePolicy(max_head_age_days=None), sources=SOURCES, now=now).auto
    no_time = _entry([_file(head_checked_at=None)])
    assert "url_reachable" in evaluate_offline(no_time, sources=SOURCES, now=now).held_by


def test_cli_head_age_option(tmp_path, capsys):
    db = load(use_cache=False)
    db.models = [_entry([_file(head_checked_at="2026-01-01T00:00:00Z")], id="old", name="old")]
    db.sources = dict(SOURCES)
    p = tmp_path / "db.json"
    db.save(p)
    assert cli.main(["gate", "--json", "--db", str(p)]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["auto"] == 0
    assert cli.main(["gate", "--json", "--db", str(p), "--max-head-age-days", "none"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["auto"] == 1


# --- 5. 版 ----------


def test_prerelease_is_lower_on_both_sides():
    assert not version_le("0.4.0", "0.4.0-rc1")  # 使う版がプレリリース → リリースが要るテンプレートは保留
    assert not version_le("0.4.0", "v0.4.0.dev3")
    assert not version_le("0.4.0", "0.4.0rc2")
    assert version_le("0.4.0-rc1", "0.4.0")
    assert version_le("0.4.0-rc1", "0.4.0-rc1")
    assert version_le("0.3.9", "0.4.0-rc1")
    assert version_le("0.4.0", "0.4.0+build5")
    assert version_le("0.4.0", "0.4.0-garbage") is None


def test_library_default_without_sources_is_held():
    e = _entry([_file()])
    r = evaluate_offline(e)  # 版の指定も sources も無い
    assert "comfyui_version" in r.held_by
    assert evaluate_offline(e, GatePolicy(comfyui_version="0.40.0")).auto  # AI生成キットの版


# --- 6. heads-only・--limit 0・cc-by-4.0 ----------


def test_heads_only_keeps_previous_comfyui_version(tmp_path, monkeypatch, capsys):
    from genmodelfit import update

    class GithubDown:
        def __init__(self, *a, **k):
            pass

        def head_status(self, url):
            return 302

        def get_json(self, url):
            raise FetchError(url, "HTTP 403", 403)

    monkeypatch.setattr(update, "HttpFetcher", GithubDown)
    out = tmp_path / "db.json"
    assert cli.main(["update", "--heads-only", "--no-cache", "--output", str(out)]) == 0
    db = load(out)
    before = load(use_cache=False).sources["comfyui"]["latest_release"]
    assert before and db.sources["comfyui"]["latest_release"] == before
    assert any("前に記録した" in w for w in db.sources["warnings"])


def test_limit_zero_is_rejected(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["recommend", "--auto-only", "--limit", "0", "--vram", "8", "--ram", "32"])
    assert e.value.code == 2


def test_cc_by_is_not_in_default_allowlist():
    assert "cc-by-4.0" not in DEFAULT_LICENSE_ALLOWLIST
    e = _entry([_file(license="cc-by-4.0")])
    assert evaluate_offline(e, sources=SOURCES).held_by == ["license"]
    allow = DEFAULT_LICENSE_ALLOWLIST | {"cc-by-4.0"}
    assert evaluate_offline(e, GatePolicy(license_allowlist=allow), sources=SOURCES).auto
