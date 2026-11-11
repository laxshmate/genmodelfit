# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""一覧(データベース)の読み書き。

読む順: 1) 明示したパス 2) 利用者のキャッシュ(`genmodelfit update` が書く) 3) 同梱のデータ。
実測(measurements)は同梱のもの(作者の measurements.json と、ほかの利用者の実測のまとめ
measurements_community.json)とキャッシュのものを合わせて使う。
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any

from platformdirs import user_cache_dir

from .i18n import tr
from .models import SCHEMA_VERSION, Measurement, ModelEntry


class DatabaseError(RuntimeError):
    pass


def cache_dir() -> Path:
    env = os.environ.get("GENMODELFIT_CACHE_DIR")
    return Path(env) if env else Path(user_cache_dir("genmodelfit", appauthor=False))


def cached_db_path() -> Path:
    return cache_dir() / "db.json"


def bundled_text(name: str) -> str:
    return resources.files("genmodelfit.data").joinpath(name).read_text(encoding="utf-8")


def parse_generated_at(value: Any) -> datetime | None:
    """一覧の generated_at → UTC の aware な datetime(配布の取り込みと同じ読み方: remote.parse_time)。
    読めなければ None。"""
    from .remote import parse_time

    try:
        return parse_time(value)
    except ValueError:
        return None


@dataclass
class Database:
    models: list[ModelEntry] = field(default_factory=list)
    measurements: list[Measurement] = field(default_factory=list)
    generated_at: str | None = None
    sources: dict[str, Any] = field(default_factory=dict)
    origin: str = "bundled"  # bundled / cache / path / release(load の source="release")

    @property
    def generated_time(self) -> datetime | None:
        """generated_at を UTC の datetime にしたもの(#96。2 つの一覧のどちらが新しいかを比べる)。
        無い・形が違う・タイムゾーンが無いときは None。"""
        return parse_generated_at(self.generated_at)

    def get(self, model_id: str) -> ModelEntry | None:
        for m in self.models:
            if m.id == model_id:
                return m
        return None

    def find(self, query: str) -> list[ModelEntry]:
        q = query.lower()
        exact = self.get(query)
        if exact:
            return [exact]
        return [m for m in self.models if q in m.id.lower() or q in m.name.lower()]

    def measurements_for(self, model_id: str) -> list[Measurement]:
        return [m for m in self.measurements if m.model_id == model_id]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "sources": self.sources,
            "models": [m.to_dict() for m in self.models],
        }

    def save(self, path: Path) -> None:
        dup = duplicate_ids(self.models)
        if dup:
            raise DatabaseError(
                tr(
                    f"同じモデル ID が 2 回以上あります: {', '.join(dup[:5])}",
                    f"the same model id appears more than once: {', '.join(dup[:5])}",
                )
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8", newline="\n"
        )
        tmp.replace(path)


_STR = (str,)
_OPT_STR = (str, type(None))
_OPT_NUM = (int, float, type(None))
# 項目の型(読めない値で後から落ちないように、読むときに確かめる。docs/schema.md)
_ENTRY_TYPES = {
    "id": _STR,
    "name": _STR,
    "modality": _STR,
    "tasks": (list,),
    "files": (list,),
    "template": (dict, type(None)),
    "source": _OPT_STR,
    "params_b": _OPT_NUM,
    "precision": _OPT_STR,
    "license": _OPT_STR,
    "license_name": _OPT_STR,  # 文字でないと、ライセンスの段階の判定(diff・gate)が落ちる(#59)
    "license_url": _OPT_STR,
    "base_model": _OPT_STR,
    "content": _STR,
    "content_evidence": (list, type(None)),
    "discovered_from": (list, type(None)),
    "updated": _OPT_STR,
    "homepage": _OPT_STR,
    "likes": (int, type(None)),
    "license_evidence": (list, type(None)),
    "comfy_family": _OPT_STR,  # 文字でないと、bf16 を持たない GPU の判定(estimate)が落ちる(#63)
    "comfy_vae": _OPT_STR,
    "sampler_stages": (list, type(None)),
    "workflow_precision": _OPT_STR,
    "precision_source": _OPT_STR,
    "downloads": (int, type(None)),
    "downloads_all_time": (int, type(None)),
    "files_complete": (bool, type(None)),
    "size_floor_bytes": (int, type(None)),
    # 後から足した任意の項目(0.5.0、#36)。古い版はこのキーを見ずに捨てる
    "languages": (dict, type(None)),
    "runtime": _OPT_STR,
    "description": (dict, type(None)),
    "card_languages": (list, type(None)),
    # 確認の印(0.6.0、#42)。無い・null は確認済みとして扱う
    "review": (dict, type(None)),
}
_REVIEW_TYPES = {
    "status": _STR,
    "reasons": (list,),
    "decided_by": _OPT_STR,
    "decided_at": _OPT_STR,
    "evidence": (list,),
    "note": _OPT_STR,
}
_FILE_TYPES = {
    "name": _STR,
    "url": _OPT_STR,
    "sha256": _OPT_STR,
    "size_bytes": (int, type(None)),
    "directory": _OPT_STR,
    "license": _OPT_STR,
    "license_name": _OPT_STR,
    "gated": (bool, type(None)),
    "role": _OPT_STR,
    "repo_id": _OPT_STR,  # 文字でないと、ファイルの鍵(license・estimate)が落ちる(#63)
    "license_from": _OPT_STR,
    "license_url": _OPT_STR,
    "repo_license": _OPT_STR,
    "license_path": (list, type(None)),
    "license_evidence": (list, type(None)),
    "head_status": (int, type(None)),
    "head_checked_at": _OPT_STR,
    "header_precision": _OPT_STR,
    "header_params": (int, type(None)),
    "repo_precision": _OPT_STR,
    "repo_precision_source": _OPT_STR,
    # 任意の部品(#81)。知らない値は今までどおり数える(estimate.optional_kind)
    "optional": _OPT_STR,
    "optional_replaces": (list, type(None)),
}
_TEMPLATE_TYPES = {
    "name": _STR,
    "title": _OPT_STR,
    "category": _OPT_STR,
    "min_comfyui_version": _OPT_STR,
    "open_source": (bool, type(None)),
    "requires_custom_nodes": (list, type(None)),
    "usage": (int, type(None)),
    "date": _OPT_STR,
    "size_bytes": (int, type(None)),
    "username": _OPT_STR,
}


def _check_str_list(d: dict, key: str, where: str) -> None:
    v = d.get(key)
    if v is not None and not all(isinstance(x, str) for x in v):
        raise DatabaseError(
            tr(f"{where} の {key} は文字列の並びです", f"{where}: {key} must be a list of strings")
        )


def _check_types(d: Any, types: dict[str, tuple], where: str) -> None:
    if not isinstance(d, dict):
        raise DatabaseError(
            tr(
                f"{where} の形が正しくありません(オブジェクトではない)",
                f"{where} is malformed (not an object)",
            )
        )
    for key, ok in types.items():
        if key not in d:
            if ok is _STR:
                raise DatabaseError(tr(f"{where} に {key} がありません", f"{where} has no {key}"))
            continue
        v = d[key]
        bad = not isinstance(v, ok) or (isinstance(v, bool) and bool not in ok)
        if bad:
            raise DatabaseError(
                tr(
                    f"{where} の {key} の型が正しくありません: {v!r}",
                    f"{where}: {key} has the wrong type: {v!r}",
                )
            )


def check_entry(d: Any, i: int) -> None:
    """一覧の 1 項目の型を確かめる。違えば DatabaseError。"""
    where = f"models[{i}]"
    _check_types(d, _ENTRY_TYPES, where)
    if not all(isinstance(t, str) for t in d.get("tasks", [])):
        raise DatabaseError(
            tr(f"{where} の tasks は文字列の並びです", f"{where}: tasks must be a list of strings")
        )
    for key in ("content_evidence", "discovered_from", "license_evidence"):
        _check_str_list(d, key, where)
    ss = d.get("sampler_stages")
    if ss is not None and not all(isinstance(g, list) and all(isinstance(x, str) for x in g) for g in ss):
        raise DatabaseError(
            tr(
                f"{where} の sampler_stages は文字列の並びの並びです: {ss!r}",
                f"{where}: sampler_stages must be a list of lists of strings: {ss!r}",
            )
        )
    if d.get("template") is not None:
        _check_types(d["template"], _TEMPLATE_TYPES, f"{where}.template")
        _check_str_list(d["template"], "requires_custom_nodes", f"{where}.template")
    langs = d.get("languages")
    for k, v in (langs or {}).items():
        if not isinstance(k, str) or not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise DatabaseError(
                tr(
                    f"{where} の languages は {{種類: 言語コードの並び}} です: {langs!r}",
                    f"{where}: languages must be {{kind: [language codes]}}: {langs!r}",
                )
            )
    cl = d.get("card_languages")
    if cl is not None and not all(isinstance(x, str) for x in cl):
        raise DatabaseError(
            tr(
                f"{where} の card_languages は言語コードの並びです: {cl!r}",
                f"{where}: card_languages must be a list of language codes: {cl!r}",
            )
        )
    desc = d.get("description")
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in (desc or {}).items()):
        raise DatabaseError(
            tr(
                f"{where} の description は {{言語: 文}} です: {desc!r}",
                f"{where}: description must be {{language: text}}: {desc!r}",
            )
        )
    rv = d.get("review")
    if rv is not None:
        # status の値は確かめない(知らない値は ok でないので、確認待ちと同じく自動のおすすめに出ない)
        _check_types(rv, _REVIEW_TYPES, f"{where}.review")
        for key in ("reasons", "evidence"):
            if not all(isinstance(x, str) for x in rv.get(key, [])):
                raise DatabaseError(
                    tr(
                        f"{where}.review の {key} は文字列の並びです",
                        f"{where}.review: {key} must be a list of strings",
                    )
                )
    for j, f in enumerate(d.get("files", [])):
        _check_types(f, _FILE_TYPES, f"{where}.files[{j}]")
        for key in ("license_path", "license_evidence", "optional_replaces"):
            _check_str_list(f, key, f"{where}.files[{j}]")


def duplicate_ids(models: list[Any]) -> list[str]:
    """2 回以上出てくる ID(並びは最初に出た順)。"""
    seen: set[str] = set()
    dup: list[str] = []
    for m in models:
        i = m.get("id") if isinstance(m, dict) else getattr(m, "id", None)
        if i in seen and i not in dup:
            dup.append(i)
        seen.add(i)
    return dup


def parse_db(data: dict[str, Any], origin: str) -> Database:
    if not isinstance(data, dict):
        raise DatabaseError(
            tr(
                "一覧の形が正しくありません(JSON のオブジェクトではない)",
                "the list is malformed (not a JSON object)",
            )
        )
    ver = data.get("schema_version")
    if ver != SCHEMA_VERSION:
        raise DatabaseError(
            tr(
                f"未対応のデータ形式です(schema_version={ver}、対応={SCHEMA_VERSION})",
                f"unsupported data format (schema_version={ver}, supported={SCHEMA_VERSION})",
            )
        )
    if not isinstance(data.get("models"), list):
        raise DatabaseError(tr("models がありません", "no models"))
    if not isinstance(data.get("generated_at"), (str, type(None))):
        raise DatabaseError(
            tr(
                f"generated_at の型が正しくありません: {data.get('generated_at')!r}",
                f"generated_at has the wrong type: {data.get('generated_at')!r}",
            )
        )
    if not isinstance(data.get("sources"), (dict, type(None))):
        raise DatabaseError(
            tr(
                "sources の型が正しくありません(オブジェクトではない)",
                "sources has the wrong type (not an object)",
            )
        )
    for i, m in enumerate(data["models"]):
        check_entry(m, i)
    dup = duplicate_ids(data["models"])
    if dup:
        # 同じ ID が 2 つあると get() が片方だけを黙って選ぶ(差し替えた項目を紛れ込ませられる)。#42
        raise DatabaseError(
            tr(
                f"同じモデル ID が 2 回以上あります: {', '.join(dup[:5])}",
                f"the same model id appears more than once: {', '.join(dup[:5])}",
            )
        )
    try:
        models = [ModelEntry.from_dict(m) for m in data["models"]]
    except (TypeError, AttributeError, ValueError) as e:
        raise DatabaseError(
            tr(f"一覧のモデルの形が正しくありません: {e}", f"a model in the list is malformed: {e}")
        ) from e
    return Database(
        models=models,
        generated_at=data.get("generated_at"),
        sources=data.get("sources") or {},
        origin=origin,
    )


def _no_constants(name: str) -> Any:
    raise ValueError(
        tr(
            f"JSON に {name} があります(NaN・Infinity は受け付けません)",
            f"the JSON has {name} (NaN and Infinity are not accepted)",
        )
    )


def _loads_measurements(text: str) -> Any:
    """実測のファイルを読む。NaN・Infinity は断る(0.9.0。Python の json は既定で読んでしまう)。"""
    return json.loads(text, parse_constant=_no_constants)


def parse_measurements(data: Any) -> list[Measurement]:
    items = data.get("measurements", []) if isinstance(data, dict) else data
    out = [Measurement.from_dict(m) for m in items]
    for m in out:
        for k in ("gpu_vram_gb", "peak_vram_gb", "peak_ram_gb", "seconds"):
            v = getattr(m, k)
            if v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0):
                raise ValueError(
                    tr(
                        f"実測の {k} が有限の 0 以上の数ではありません: {v!r}",
                        f"measurement {k} is not a finite number of 0 or more: {v!r}",
                    )
                )
    return out


# ほかの利用者の実測をまとめたもの(#34。`genmodelfit contrib aggregate --output` が data/contrib から作る)
COMMUNITY_FILE = "measurements_community.json"


def load_measurements(
    extra_dirs: list[Path] | None = None, *, include_cache: bool = True
) -> list[Measurement]:
    """同梱の実測(作者)+ 同梱のほかの利用者の実測のまとめ + この PC の bench の実測(キャッシュ)。
    include_cache=False なら同梱の 2 つだけ(calibrate が使う。どの PC で作っても calibration.json が
    同じになる。#100)。"""
    out = parse_measurements(_loads_measurements(bundled_text("measurements.json")))
    out.extend(parse_measurements(_loads_measurements(bundled_text(COMMUNITY_FILE))))
    cache = cache_dir() / "measurements"
    dirs = [cache] if include_cache else []
    for d in [*dirs, *(extra_dirs or [])]:
        if d.is_dir():
            for p in sorted(d.glob("*.json")):
                try:
                    out.extend(parse_measurements(_loads_measurements(p.read_text(encoding="utf-8"))))
                except (OSError, ValueError, TypeError) as e:
                    if d is cache:
                        # この PC のキャッシュの 1 つが壊れていても(書きかけ・手で直した)、
                        # ほかの判定は止めない。壊れた一覧のキャッシュを同梱へ戻すのと同じ扱い。
                        # 渡されたフォルダ(extra_dirs)は厳しく見る
                        logging.getLogger("genmodelfit").warning(
                            "skipping a broken measurements file: %s: %s", p, e
                        )
                        continue
                    raise DatabaseError(
                        tr(
                            f"実測のファイルを読めません: {p}: {e}",
                            f"cannot read the measurements file: {p}: {e}",
                        )
                    ) from e
    return out


PATH_SOURCES = ("path", "release")


def load(
    path: str | os.PathLike[str] | None = None,
    *,
    use_cache: bool = True,
    user_measurements: bool = True,
    source: str | None = None,
) -> Database:
    """一覧を読む(`gmf.load_db`)。ネットに触らない。

    path: 一覧のファイル。None は、利用者のキャッシュ(`genmodelfit update` が書いた db.json。use_cache=False
        なら読まない)、無ければ同梱の一覧。
    user_measurements: この PC の bench の実測(キャッシュの measurements)も合わせるか。False は同梱の実測
        (作者の実測とほかの利用者の実測のまとめ)だけ。どの PC でも同じ判定にしたいアプリ用(#96)。
    source: path で渡したファイルの出どころ(Database.origin。`gmf.data_status()` の source になる)。
        "path"(既定)か、`update_from_release` の取り込みを自分で置いたファイルなら "release"。
        path を渡さないときに渡す・ほかの値は ValueError。
    """
    if source is not None:
        if not path:
            raise ValueError(
                tr("source は path と一緒に渡してください", "source can be given only together with path")
            )
        if source not in PATH_SOURCES:
            raise ValueError(
                tr(
                    f"source は {' / '.join(PATH_SOURCES)} のどれかです: {source!r}",
                    f"source must be one of {' / '.join(PATH_SOURCES)}: {source!r}",
                )
            )
    if path:
        p = Path(path)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:  # 無い・読めない・JSON でない(UnicodeDecodeError も ValueError)
            raise DatabaseError(
                tr(f"一覧のファイルを読めません: {p}: {e}", f"cannot read the list file: {p}: {e}")
            ) from e
        db = parse_db(data, source or "path")
    else:
        db = None
        cp = cached_db_path()
        if use_cache and cp.exists():
            try:
                db = parse_db(json.loads(cp.read_text(encoding="utf-8")), "cache")
            except (OSError, ValueError, DatabaseError):
                db = None  # 壊れたキャッシュは使わず、同梱のデータへ戻る
        if db is None:
            db = parse_db(json.loads(bundled_text("db.json")), "bundled")
    db.measurements = load_measurements(include_cache=user_measurements)
    return db
