"""
Reimplementacao do auto-updater do Tibia.
Baixa package.json/assets.json do servidor, busca cada .lzma, descomprime, valida hashes
e grava tudo numa pasta de saida — sem rodar o launcher oficial.

Feature principal para o pipeline: SKIP arquivos que ja existem localmente com hash
correto. Assim rodar de novo soh baixa o que mudou/falta.

Uso: python download_tibia.py [--target client|assets|launcher] [--out DIR] [--workers N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from curl_cffi import requests

BASES = {
    "launcher": "https://static.tibia.com/launcher/launcher-windows-current/",
    "client":   "https://static.tibia.com/launcher/tibiaclient-windows-current/",
    "assets":   "https://static.tibia.com/launcher/assets-current/",
}
PKG_FILE = {"launcher": "package.json", "client": "package.json", "assets": "assets.json"}


def _proxies() -> dict | None:
    url = (os.environ.get("WEBSHARE_PROXY")
           or os.environ.get("HTTPS_PROXY")
           or os.environ.get("HTTP_PROXY"))
    return {"http": url, "https": url} if url else None


def fetch(url: str, binary: bool = False, retries: int = 3) -> bytes | str:
    proxies = _proxies()
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, impersonate="chrome", timeout=60, proxies=proxies)
            r.raise_for_status()
            return r.content if binary else r.text
        except Exception as e:
            last_exc = e
            time.sleep(0.5 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def decompress_lzma(data: bytes) -> bytes:
    def cip():
        s = data[32:]
        fixed = s[:5] + b"\xff" * 8 + s[13:]
        return lzma.decompress(fixed, format=lzma.FORMAT_ALONE)

    def alone_patched():
        fixed = data[:5] + b"\xff" * 8 + data[13:]
        return lzma.decompress(fixed, format=lzma.FORMAT_ALONE)

    def alone_raw():
        return lzma.decompress(data, format=lzma.FORMAT_ALONE)

    def xz():
        return lzma.decompress(data, format=lzma.FORMAT_XZ)

    def auto():
        return lzma.decompress(data, format=lzma.FORMAT_AUTO)

    errs = []
    for fn in (cip, alone_patched, alone_raw, xz, auto):
        try:
            return fn()
        except Exception as e:
            errs.append(f"{fn.__name__}={type(e).__name__}")
    raise RuntimeError("all lzma formats failed: " + "; ".join(errs))


def download_one(base: str, out_dir: Path, entry: dict) -> tuple[str, str, int]:
    rel_url = entry["url"]
    local_rel = entry["localfile"]
    expected_packed = entry["packedhash"]
    do_unpack = entry.get("unpack", True) and "unpackedhash" in entry
    expected_unpacked = entry.get("unpackedhash", expected_packed)

    dst = out_dir / local_rel

    # SKIP: arquivo ja existe com hash correto
    if dst.exists():
        try:
            if sha256_file(dst) == expected_unpacked:
                return (local_rel, "skip", 0)
        except Exception:
            pass

    try:
        packed = fetch(base + rel_url, binary=True)
    except Exception as e:
        return (local_rel, f"download error: {e}", 0)

    got_packed = sha256(packed)
    if got_packed != expected_packed:
        return (local_rel, f"packed hash mismatch ({got_packed} != {expected_packed})", 0)

    if not do_unpack or expected_packed == expected_unpacked:
        unpacked = packed
    else:
        try:
            unpacked = decompress_lzma(packed)
        except Exception as e:
            dbg = out_dir / "_failed_raw" / local_rel
            dbg.parent.mkdir(parents=True, exist_ok=True)
            dbg.write_bytes(packed)
            return (local_rel, f"decompress error: {e}", 0)

    got_unpacked = sha256(unpacked)
    if got_unpacked != expected_unpacked:
        return (local_rel, f"unpacked hash mismatch ({got_unpacked} != {expected_unpacked})", 0)

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(unpacked)
    return (local_rel, "ok", len(unpacked))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=list(BASES), default="assets")
    ap.add_argument("--out", default=None, help="pasta de saida (default: ./<target>)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="baixar so N primeiros arquivos (teste)")
    ap.add_argument("--only", default=None,
                    help="regex para filtrar localfiles (ex.: '^(appearances|catalog|sprites-)'")
    args = ap.parse_args()

    base = BASES[args.target]
    pkg_name = PKG_FILE[args.target]
    out_dir = Path(args.out or args.target).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[>] target:   {args.target}")
    print(f"[>] base URL: {base}")
    print(f"[>] out dir:  {out_dir}")

    print(f"[>] baixando {pkg_name}...")
    pkg_text = fetch(base + pkg_name)
    pkg = json.loads(pkg_text)
    (out_dir / pkg_name).write_text(pkg_text, encoding="utf-8")

    version = pkg.get("version") or pkg.get("assetsversion") or "?"
    files = pkg.get("files", [])

    if args.only:
        import re
        pat = re.compile(args.only)
        files = [f for f in files if pat.search(f["localfile"])]
        print(f"[>] filtro '{args.only}': {len(files)} arquivos")

    if args.limit:
        files = files[: args.limit]

    print(f"[>] versao:   {version}")
    print(f"[>] arquivos: {len(files)}")

    try:
        ver_text = fetch(base + pkg_name + (".version" if args.target != "assets" else ".sha256"))
        suffix = ".version" if args.target != "assets" else ".sha256"
        (out_dir / (pkg_name + suffix)).write_text(ver_text, encoding="utf-8")
    except Exception as e:
        print(f"[!] arquivo de versao auxiliar: {e}")

    t0 = time.time()
    total_bytes = 0
    skipped = 0
    fails: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(download_one, base, out_dir, e): e for e in files}
        done = 0
        for fut in as_completed(futs):
            try:
                name, status, size = fut.result()
            except Exception as e:
                entry = futs[fut]
                name, status, size = entry["localfile"], f"crash: {e}", 0
            done += 1
            if status == "ok":
                total_bytes += size
            elif status == "skip":
                skipped += 1
            else:
                fails.append((name, status))
                print(f"  [{done:>4}/{len(files)}] FAIL  {name}  :: {status}")
            if done % 250 == 0 or done == len(files):
                print(f"  [{done:>5}/{len(files)}]  ok so far: bytes={total_bytes/1_048_576:.1f}MB  skipped={skipped}  fails={len(fails)}")

    dt = time.time() - t0
    print(f"\n[=] baixados:    {(total_bytes/1_048_576):.2f} MB em {dt:.1f}s")
    print(f"[=] ja existiam: {skipped}")
    print(f"[=] falhas:      {len(fails)}")
    for name, reason in fails[:10]:
        print(f"     - {name}: {reason}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
