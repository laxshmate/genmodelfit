# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""データの署名(Ed25519)。

配る一覧(db.json)に署名(db.json.sig)を付け、取り込む側が同梱の公開鍵で確かめる。
信じる公開鍵は `data/signing_keys/*.pub`(複数可。どれか 1 つで署名が合えば通す。
鍵を替えるときに新旧を並べるため)。秘密鍵は GitHub の environment `data-signing` のシークレット
`GENMODELFIT_SIGNING_KEY` にだけ置く(docs/release.md)。

コマンド(配る側の CI が使う):
    python -m genmodelfit.signing sign <ファイル> [--key-env 環境変数名]   # <ファイル>.sig を書く
    python -m genmodelfit.signing verify <ファイル> [<署名ファイル>]       # 同梱の公開鍵で確かめる
"""

from __future__ import annotations

import argparse
import base64
import binascii
import os
import sys
from importlib import resources
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .i18n import tr


class SigningError(ValueError):
    pass


KeyLike = bytes | str | Ed25519PublicKey


def embedded_public_keys_pem() -> list[bytes]:
    """同梱の信じる公開鍵(`data/signing_keys/*.pub`、名前の順)。"""
    d = resources.files("genmodelfit.data").joinpath("signing_keys")
    return [p.read_bytes() for p in sorted(d.iterdir(), key=lambda p: p.name) if p.name.endswith(".pub")]


def load_public_key(key: KeyLike) -> Ed25519PublicKey:
    """公開鍵を読む。PEM か、素の 32 バイトを受け付ける。"""
    if isinstance(key, Ed25519PublicKey):
        return key
    raw = key.encode() if isinstance(key, str) else key
    try:
        if not raw.lstrip().startswith(b"-----BEGIN"):
            return Ed25519PublicKey.from_public_bytes(raw)
        pub = serialization.load_pem_public_key(raw)
    except ValueError as e:
        raise SigningError(tr(f"公開鍵を読めません: {e}", f"cannot read the public key: {e}")) from e
    if not isinstance(pub, Ed25519PublicKey):
        raise SigningError(tr("Ed25519 の公開鍵ではありません", "not an Ed25519 public key"))
    return pub


def load_private_key(pem: bytes | str) -> Ed25519PrivateKey:
    raw = pem.encode() if isinstance(pem, str) else pem
    try:
        key = serialization.load_pem_private_key(raw.strip(), password=None)
    except (ValueError, TypeError) as e:
        raise SigningError(
            tr(
                "秘密鍵を読めません(PEM 形式の Ed25519 鍵が要ります)",
                "cannot read the private key (a PEM Ed25519 key is needed)",
            )
        ) from e
    if not isinstance(key, Ed25519PrivateKey):
        raise SigningError(tr("Ed25519 の秘密鍵ではありません", "not an Ed25519 private key"))
    return key


def sign(data: bytes, private_key_pem: bytes | str) -> bytes:
    """data に署名する。返すのは 64 バイトの署名。"""
    return load_private_key(private_key_pem).sign(data)


def trusted_public_keys() -> list[Ed25519PublicKey]:
    """同梱の信じる公開鍵をすべて読む。1 つも無い・読めないときは SigningError。"""
    keys = [load_public_key(k) for k in embedded_public_keys_pem()]
    if not keys:
        raise SigningError(tr("同梱の公開鍵がありません", "the bundled public key is missing"))
    return keys


def verify(data: bytes, sig: bytes, public_key: KeyLike | list[KeyLike] | None = None) -> bool:
    """署名が正しければ True。壊れた署名・別の鍵・改ざんは False(例外は出さない)。

    public_key を省くと同梱の信じる鍵を使い、どれか 1 つで合えば True。リストを渡したときも同じ。
    """
    try:
        if public_key is None:
            keys = trusted_public_keys()
        else:
            items = public_key if isinstance(public_key, list) else [public_key]
            keys = [load_public_key(k) for k in items]
    except SigningError:
        return False
    for k in keys:
        try:
            k.verify(sig, data)
        except InvalidSignature:
            continue
        return True
    return False


def encode_sig(sig: bytes) -> str:
    """`.sig` ファイルに書く形(base64 の 1 行)。"""
    return base64.b64encode(sig).decode("ascii") + "\n"


def decode_sig(text: bytes | str) -> bytes:
    try:
        return base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError) as e:
        raise SigningError(tr("署名ファイルの形が正しくありません", "the signature file is malformed")) from e


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m genmodelfit.signing")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("sign", help=tr("<ファイル>.sig を書く", "write <file>.sig"))
    sp.add_argument("file")
    sp.add_argument(
        "--key-env",
        default="GENMODELFIT_SIGNING_KEY",
        help=tr("秘密鍵(PEM)を入れた環境変数", "environment variable holding the private key (PEM)"),
    )
    sp.add_argument(
        "--key-file",
        help=tr("環境変数の代わりに鍵のファイルを使う", "use a key file instead of the environment variable"),
    )
    vp = sub.add_parser(
        "verify", help=tr("同梱の信じる公開鍵で確かめる", "verify with the bundled trusted public key")
    )
    vp.add_argument("file")
    vp.add_argument("sig", nargs="?")
    args = p.parse_args(argv)
    path = Path(args.file)
    try:
        if args.cmd == "sign":
            pem = Path(args.key_file).read_bytes() if args.key_file else os.environ.get(args.key_env, "")
            if not pem:
                print(
                    tr(
                        f"error: 秘密鍵がありません(環境変数 {args.key_env})",
                        f"error: no private key (environment variable {args.key_env})",
                    ),
                    file=sys.stderr,
                )
                return 1
            out = path.with_name(path.name + ".sig")
            out.write_text(encode_sig(sign(path.read_bytes(), pem)), encoding="ascii", newline="\n")
            print(tr(f"署名しました: {out}", f"signed: {out}"))
            return 0
        sig_path = Path(args.sig) if args.sig else path.with_name(path.name + ".sig")
        ok = verify(path.read_bytes(), decode_sig(sig_path.read_text(encoding="ascii")))
    except (
        OSError,
        ValueError,
        SigningError,
    ) as e:  # ValueError: .sig に ASCII でない文字(UnicodeDecodeError)
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(
        tr("署名は正しいです", "the signature is valid")
        if ok
        else tr("署名が合いません", "the signature does not match")
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
