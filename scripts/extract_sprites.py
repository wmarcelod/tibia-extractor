#!/usr/bin/env python3
"""
extract_sprites.py

Descompacta todos os sprites-*.bmp.lzma e fatia em PNGs individuais
(um por sprite_id). Rapido: multiprocessing.

Saida:
  out/sprites/sprite_<id>.png     (um png por sprite_id, transparencia aplicada)
  out/sprite_index.json           (mapa sprite_id -> arquivo)
"""
from __future__ import annotations

import io
import json
import lzma
import os
import struct
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS = Path(os.environ.get("TIBIA_ASSETS_DIR") or (ROOT / "assets"))
OUT = ROOT / "out"
SPRITES_DIR = OUT / "sprites"
COLORKEY = (255, 0, 255)  # magenta


# --- LZMA com header "alone-like" no offset 0x20 (Tibia custom) ---

@dataclass(frozen=True)
class LzmaParams:
    offset: int
    dict_size: int
    lc: int
    lp: int
    pb: int


def find_lzma(data: bytes) -> LzmaParams:
    for off in [0x20] + list(range(0, min(256, len(data) - 13))):
        if off + 13 > len(data):
            continue
        props = data[off]
        dict_size = struct.unpack_from("<I", data, off + 1)[0]
        if dict_size < (1 << 12) or dict_size > (1 << 30):
            continue
        lc = props % 9
        lp = (props // 9) % 5
        pb = (props // 9) // 5
        if not (0 <= lc <= 8 and 0 <= lp <= 4 and 0 <= pb <= 4):
            continue
        try:
            dec = lzma.LZMADecompressor(
                format=lzma.FORMAT_RAW,
                filters=[{
                    "id": lzma.FILTER_LZMA1, "dict_size": dict_size,
                    "lc": lc, "lp": lp, "pb": pb,
                }],
            )
            probe = dec.decompress(data[off + 13:], max_length=16)
            if probe.startswith(b"BM") or probe.startswith(b"\x89PNG"):
                return LzmaParams(off, dict_size, lc, lp, pb)
        except Exception:
            continue
    raise ValueError("LZMA header nao encontrado")


def decompress(data: bytes, p: LzmaParams) -> bytes:
    dec = lzma.LZMADecompressor(
        format=lzma.FORMAT_RAW,
        filters=[{
            "id": lzma.FILTER_LZMA1, "dict_size": p.dict_size,
            "lc": p.lc, "lp": p.lp, "pb": p.pb,
        }],
    )
    return dec.decompress(data[p.offset + 13:])


def apply_colorkey(img: Image.Image) -> Image.Image:
    import numpy as np
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    arr = np.array(img, dtype=np.uint8)
    kr, kg, kb = COLORKEY
    mask = (arr[..., 0] == kr) & (arr[..., 1] == kg) & (arr[..., 2] == kb)
    arr[mask, 3] = 0
    return Image.fromarray(arr)


def infer_grid(w: int, h: int, count: int) -> tuple[int, int, int, int]:
    """Descobre cols/rows/tw/th de uma spritesheet.

    Tibia sempre usa tiles de lados multiplos de 32 (32x32, 64x32, 32x64, 64x64).
    Alguns sheets tem `count` menor que `cols*rows` (tiles vazios no fim).
    """
    candidates = []
    # Opcao 1: divisao exata (comportamento original, preferivel)
    for cols in range(1, count + 1):
        if count % cols != 0:
            continue
        rows = count // cols
        if w % cols != 0 or h % rows != 0:
            continue
        tw, th = w // cols, h // rows
        if tw in (32, 64) and th in (32, 64):
            candidates.append(((cols, rows, tw, th), (0, abs(tw - th), abs(cols - rows), -min(tw, th))))

    # Opcao 2: permitir cols*rows > count (sheet com tiles sobrando)
    for tw in (32, 64):
        for th in (32, 64):
            if w % tw or h % th:
                continue
            cols, rows = w // tw, h // th
            if cols * rows < count:
                continue
            # penaliza se sobram MUITOS tiles (provavel grid errado)
            waste = cols * rows - count
            candidates.append(((cols, rows, tw, th), (1, waste, abs(tw - th), abs(cols - rows))))

    if not candidates:
        raise ValueError(f"Nao infere grid {w}x{h} count={count}")
    candidates.sort(key=lambda c: c[1])
    return candidates[0][0]


def process_sheet(entry: dict) -> tuple[int, list[int], str | None]:
    """Recebe uma entrada do catalog (type=sprite) e gera PNGs individuais."""
    try:
        file = entry["file"]
        first = entry["firstspriteid"]
        last = entry["lastspriteid"]
        count = last - first + 1

        # SKIP: se um PNG aleatorio no meio do range existe, assume sheet ja processado.
        # Heuristica rapida: checa o sprite do meio do range.
        mid = first + count // 2
        if (SPRITES_DIR / f"sprite_{mid}.png").exists():
            # Listar os que realmente existem para o index
            existing = [
                sid for sid in range(first, last + 1)
                if (SPRITES_DIR / f"sprite_{sid}.png").exists()
            ]
            if existing:
                return (first, existing, "skip")

        src = ASSETS / file
        raw = src.read_bytes()
        params = find_lzma(raw)
        bmp_bytes = decompress(raw, params)
        sheet = Image.open(io.BytesIO(bmp_bytes))
        w, h = sheet.size
        cols, rows, tw, th = infer_grid(w, h, count)
        sheet = apply_colorkey(sheet)

        written = []
        for idx in range(count):
            sid = first + idx
            col, row = idx % cols, idx // cols
            tile = sheet.crop((col * tw, row * th, (col + 1) * tw, (row + 1) * th))
            # pular totalmente transparente
            bbox = tile.split()[-1].getbbox()
            if bbox is None:
                continue
            out_path = SPRITES_DIR / f"sprite_{sid}.png"
            tile.save(out_path, optimize=True)
            written.append(sid)
        return (first, written, None)
    except Exception as e:
        return (entry.get("firstspriteid", -1), [], str(e))


def main() -> int:
    SPRITES_DIR.mkdir(parents=True, exist_ok=True)
    catalog = json.loads((ASSETS / "catalog-content.json").read_text(encoding="utf-8"))
    sheets = [e for e in catalog if e.get("type") == "sprite"]
    print(f"[+] Sheets a processar: {len(sheets)}")

    index: dict[int, str] = {}
    errors: list[tuple[int, str]] = []
    skipped_sheets = 0
    done = 0
    total = len(sheets)

    with ProcessPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(process_sheet, e) for e in sheets]
        for fut in as_completed(futs):
            first, sids, err = fut.result()
            done += 1
            if err and err != "skip":
                errors.append((first, err))
            if err == "skip":
                skipped_sheets += 1
            for sid in sids:
                index[sid] = f"sprites/sprite_{sid}.png"
            if done % 250 == 0 or done == total:
                print(f"  [{done}/{total}] sprites so far: {len(index)}  (sheets skipados: {skipped_sheets})")

    print(f"[+] Total sprites PNG: {len(index)}")
    print(f"[+] Sheets skipados (ja existiam): {skipped_sheets}")
    if errors:
        print(f"[!] Erros em {len(errors)} sheets (primeiros 3): {errors[:3]}")

    idx_path = OUT / "sprite_index.json"
    idx_path.write_text(
        json.dumps({str(k): v for k, v in sorted(index.items())}, indent=2),
        encoding="utf-8",
    )
    print(f"[+] Escrito: {idx_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
