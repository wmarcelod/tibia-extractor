#!/usr/bin/env python3
"""
extract_map_tiles.py

Descompacta tiles de mapa do cliente Tibia (minimap, satellite, subarea)
de .bmp.lzma para PNG, usando o mesmo header LZMA custom dos sprites.

Entradas em assets/assets/:
  - minimap-32-XXXX-YYYY-FF-<hash>.bmp.lzma       (512x512, step 16, floors 00-07)
  - satellite-16-XXXX-YYYY-FF-<hash>.bmp.lzma     (512x512, step 8 -- zoom out)
  - satellite-32-XXXX-YYYY-FF-<hash>.bmp.lzma     (512x512, step 16 -- zoom med)
  - satellite-64-XXXX-YYYY-FF-<hash>.bmp.lzma     (512x512, step 32 -- zoom in)
  - subarea-NNNN-<hash>.bmp.lzma                  (dimensoes variaveis)

Saida:
  out/map_tiles/minimap/XXXX-YYYY-FF.png
  out/map_tiles/satellite_16/XXXX-YYYY-FF.png
  out/map_tiles/satellite_32/XXXX-YYYY-FF.png
  out/map_tiles/satellite_64/XXXX-YYYY-FF.png
  out/map_tiles/subarea/NNNN.png

CLI:
  python scripts/extract_map_tiles.py [--limit N] [--force]
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Reusa helpers LZMA do extract_sprites.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_sprites import find_lzma, decompress  # noqa: E402

from PIL import Image  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ASSETS = Path(os.environ.get("TIBIA_ASSETS_DIR") or (ROOT / "assets"))
ASSETS_SUB = ASSETS / "assets" if (ASSETS / "assets").is_dir() else ASSETS
OUT = ROOT / "out"
TILES_DIR = OUT / "map_tiles"

SUBDIRS = {
    "minimap": TILES_DIR / "minimap",
    "satellite_16": TILES_DIR / "satellite_16",
    "satellite_32": TILES_DIR / "satellite_32",
    "satellite_64": TILES_DIR / "satellite_64",
    "subarea": TILES_DIR / "subarea",
}

RE_TILE = re.compile(
    r"^(?P<kind>minimap|satellite)-(?P<zoom>16|32|64)-"
    r"(?P<x>\d{4})-(?P<y>\d{4})-(?P<z>\d{2})-[0-9a-f]+\.bmp\.lzma$"
)
RE_SUBAREA = re.compile(r"^subarea-(?P<n>\d{4})-[0-9a-f]+\.bmp\.lzma$")


def classify(name: str) -> tuple[str, str] | None:
    """Retorna (categoria, stem_saida) ou None se nao for tile de mapa."""
    m = RE_TILE.match(name)
    if m:
        kind = m.group("kind")
        zoom = m.group("zoom")
        stem = f"{m.group('x')}-{m.group('y')}-{m.group('z')}"
        if kind == "minimap":
            return ("minimap", stem)
        return (f"satellite_{zoom}", stem)
    m = RE_SUBAREA.match(name)
    if m:
        return ("subarea", m.group("n"))
    return None


def process_one(task: tuple[str, str, str, bool]) -> tuple[str, str, str | None]:
    """(src_path, category, stem, force) -> (category, stem, err_or_None|'skip')."""
    src_path, category, stem, force = task
    out_path = SUBDIRS[category] / f"{stem}.png"
    if out_path.exists() and not force:
        return (category, stem, "skip")
    try:
        raw = Path(src_path).read_bytes()
        params = find_lzma(raw)
        bmp_bytes = decompress(raw, params)
        img = Image.open(io.BytesIO(bmp_bytes))
        img.load()
        img.save(out_path, optimize=True)
        return (category, stem, None)
    except Exception as e:  # pragma: no cover
        return (category, stem, f"{type(e).__name__}: {e}")


def build_tasks(limit_per_cat: int | None, force: bool) -> list[tuple[str, str, str, bool]]:
    tasks: list[tuple[str, str, str, bool]] = []
    counts: dict[str, int] = {k: 0 for k in SUBDIRS}
    for entry in sorted(ASSETS_SUB.iterdir()):
        if not entry.is_file() or not entry.name.endswith(".bmp.lzma"):
            continue
        cls = classify(entry.name)
        if cls is None:
            continue
        category, stem = cls
        if limit_per_cat is not None and counts[category] >= limit_per_cat:
            continue
        counts[category] += 1
        tasks.append((str(entry), category, stem, force))
    return tasks


def main() -> int:
    ap = argparse.ArgumentParser(description="Extrai tiles de mapa Tibia (.bmp.lzma -> .png).")
    ap.add_argument("--limit", type=int, default=None,
                    help="Processa no maximo N arquivos por categoria (pra teste).")
    ap.add_argument("--force", action="store_true",
                    help="Reextrai mesmo se o PNG destino ja existir.")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    if not ASSETS_SUB.is_dir():
        print(f"[!] Diretorio de assets nao existe: {ASSETS_SUB}", file=sys.stderr)
        return 2

    for d in SUBDIRS.values():
        d.mkdir(parents=True, exist_ok=True)

    tasks = build_tasks(args.limit, args.force)
    print(f"[+] Tiles a processar: {len(tasks)}  (assets dir: {ASSETS_SUB})")
    if not tasks:
        return 0

    done = 0
    total = len(tasks)
    per_cat_ok: dict[str, int] = {k: 0 for k in SUBDIRS}
    per_cat_skip: dict[str, int] = {k: 0 for k in SUBDIRS}
    errors: list[tuple[str, str, str]] = []

    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(process_one, t) for t in tasks]
        for fut in as_completed(futs):
            category, stem, err = fut.result()
            done += 1
            if err == "skip":
                per_cat_skip[category] += 1
            elif err is None:
                per_cat_ok[category] += 1
            else:
                errors.append((category, stem, err))
            if done % 200 == 0 or done == total:
                print(f"  [{done}/{total}]")

    print("[+] Resumo por categoria (ok / skip):")
    for k in SUBDIRS:
        print(f"    {k:14s} ok={per_cat_ok[k]:5d}  skip={per_cat_skip[k]:5d}")
    if errors:
        print(f"[!] {len(errors)} erros (primeiros 5): {errors[:5]}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
