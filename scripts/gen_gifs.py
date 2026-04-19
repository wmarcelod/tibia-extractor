#!/usr/bin/env python3
"""
gen_gifs.py

Gera GIFs animados (1 por direcao) para cada outfit animado em out/outfits.json,
reagrupando os sprites flat extraidos por extract_sprites.py.

============================================================================
FORMATO DESCOBERTO (derivado de proto/appearances.proto + scripts/decode_outfits.py + inspecao)
============================================================================

Cada `Appearance` (outfit) tem 1+ `FrameGroup`. Criaturas/NPCs/player outfits
animados tem 2 frame groups:
  - frame_group[0]: IDLE   (fixed_frame_group = 0, tipicamente 1 phase)
  - frame_group[1]: MOVING (fixed_frame_group = 1, N phases -- animacao de caminhada)

Cada `FrameGroup.sprite_info` tem uma lista flat `sprite_id` cuja ordenacao e:

    index = layer + layers * (pw_i + pw * (ph_i + ph * (pd_i + pd * phase)))

ou seja -- a ordem de iteracao (mais lento -> mais rapido) eh:

    phase (animacao) -> pd_i (addons/variacoes) -> ph_i -> pw_i (direcao) -> layer

`sprite_id` tem tamanho total = phases * pd * ph * pw * layers.

Para outfits criatura/NPC:
  - layers = 1 (so a base)
  - pattern_width = 4 (direcoes: 0=N, 1=E, 2=S, 3=W)  [convencao Tibia]
  - pattern_height = 1
  - pattern_depth = 1
  - phases = N (variavel)

Para player outfits:
  - layers = 2 (base + template-mask pra colorir in-game)
  - pattern_depth = 4 (normal, addon1, addon2, addon1+2) -- as vezes maior p/ mount outfits
  - pattern_width = 4 (direcoes)
  - phases = N

NOTA: o comentario "[phase][pattern_depth][pattern_height][pattern_width][layers]"
em decode_outfits.py linha 75 confirma a ordem (row-major com phase outermost,
layers innermost).

============================================================================
LIMITACOES ATUAIS
============================================================================
1. Layered outfits (player_outfit) -- esse script pega APENAS layer=0 (base).
   O layer 1 eh uma mascara em escala de cinza usada pra colorizar roupas in-game;
   renderizar com cores corretas requer blending com as escolhas de head/body/legs/feet
   do jogador. Fora do escopo do MVP.

2. pattern_depth > 1 -- esse script pega APENAS pd_i=0 (variante "sem addon"
   pra player_outfits, variante base pra creatures). Os outros pds de addon nao
   sao exportados.

3. Bounding_square pode variar (32, 40, 50, 64...) -- o PNG ja esta salvo no
   tamanho correto na extracao, entao apenas montamos o GIF no tamanho do primeiro
   sprite. Sprites menores que bounding_square > 32 sao tipicamente 64x64.

4. Sprites 64x64+ -- alguns outfits maiores usam bounding_square > 32, entao o
   sprite_<id>.png eh 64x64. Funciona sem problema.

============================================================================
ITEMS (OBJETOS) ANIMADOS
============================================================================
A mesma logica serve pra items animados (tochas, bandeiras, teleporters, etc.).
Observado: ~4992 de 42099 objects tem animacao (>=2 phases). Geralmente:
  - 1 frame_group (FIXED_FRAME_GROUP_OBJECT_INITIAL = 2)
  - layers = 1
  - pw/ph/pd pequenos (frequentemente 1 ou 2)
  - phases = 2..13+
Implementar gifs de items e direto -- mesma formula de indice, mas sem conceito
de "direcao" (pw geralmente = 1). Fora de escopo neste ciclo -- ver comentario
no final pra sugestao.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "out"
SPRITES_DIR = OUT_DIR / "sprites"
GIFS_DIR = OUT_DIR / "gifs"
OUTFITS_JSON = OUT_DIR / "outfits.json"

# Duracao de cada frame do GIF em milissegundos.
FRAME_DURATION_MS = 200


def sprite_index(layer, pw_i, ph_i, pd_i, phase, fg) -> int:
    """Calcula o indice flat em sprite_ids para um conjunto de coordenadas."""
    layers = fg["layers"]
    pw = fg["pattern_width"]
    ph = fg["pattern_height"]
    pd = fg["pattern_depth"]
    return (
        layer
        + layers * (pw_i + pw * (ph_i + ph * (pd_i + pd * phase)))
    )


def load_sprite(sid: int) -> Image.Image | None:
    p = SPRITES_DIR / f"sprite_{sid}.png"
    if not p.exists():
        return None
    return Image.open(p).convert("RGBA")


def build_direction_frames(fg: dict, direction: int) -> list[Image.Image]:
    """
    Retorna uma lista de PIL.Image (RGBA) representando cada phase para uma
    direcao. Usa apenas layer=0 e pd_i=0 (MVP).
    """
    frames = []
    for phase in range(fg["phases"]):
        idx = sprite_index(
            layer=0,
            pw_i=direction,
            ph_i=0,
            pd_i=0,
            phase=phase,
            fg=fg,
        )
        if idx >= len(fg["sprite_ids"]):
            return []
        sid = fg["sprite_ids"][idx]
        img = load_sprite(sid)
        if img is None:
            return []
        frames.append(img)
    return frames


def rgba_frames_to_gif(frames: list[Image.Image], out_path: Path) -> None:
    """
    Converte frames RGBA em GIF com transparencia.
    Abordagem: quantizar cada frame em paleta, reservando index 0 pra transparencia,
    mapeando pixels com alpha<128 para 0.
    """
    palette_frames = []
    for fr in frames:
        # Garantir mesmo tamanho
        r, g, b, a = fr.split()
        # Converter para P mode com 255 cores (reservando index 0 p/ transparencia)
        rgb = Image.merge("RGB", (r, g, b))
        p = rgb.convert("P", palette=Image.Palette.ADAPTIVE, colors=255)
        # Shift palette: incrementar todos os index em 1, reservando 0
        # Mais simples: usar quantize com mascara direta
        mask = a.point(lambda v: 255 if v < 128 else 0)
        # Criar novo P com transparencia: pixels transparentes = 0
        # Metodo: usar putpalette e paste
        p_arr = p.load()
        mask_arr = mask.load()
        w, h = p.size
        # Remapear: index 0 sera transparencia; shift todos os index existentes +1
        shifted = Image.new("P", (w, h), 0)
        shifted_arr = shifted.load()
        # copiar palette: frame P tem palette RGB triplos; shifted pula o slot 0
        src_pal = p.getpalette() or []
        new_pal = [0, 0, 0]  # index 0 = preto (transparente via tRNS)
        new_pal.extend(src_pal[: 255 * 3])
        # pad para 256*3
        while len(new_pal) < 256 * 3:
            new_pal.append(0)
        shifted.putpalette(new_pal[: 256 * 3])
        for y in range(h):
            for x in range(w):
                if mask_arr[x, y]:
                    shifted_arr[x, y] = 0  # transparente
                else:
                    shifted_arr[x, y] = (p_arr[x, y] + 1) & 0xFF
        palette_frames.append(shifted)

    first, *rest = palette_frames
    first.save(
        out_path,
        save_all=True,
        append_images=rest,
        duration=FRAME_DURATION_MS,
        loop=0,
        disposal=2,
        transparency=0,
        optimize=False,
    )


def pick_frame_group(fgs: list[dict]) -> dict | None:
    """
    Prefere o frame_group de walking (fixed_frame_group=1). Fallback para idle.
    So retorna um fg com >=2 phases (senao GIF sem animacao n faz sentido).
    """
    moving = [f for f in fgs if f["fixed_frame_group"] == 1]
    idle = [f for f in fgs if f["fixed_frame_group"] == 0]
    # Preferir moving se tem >=2 phases, senao idle se tem >=2 phases
    for candidate in moving + idle:
        if candidate["phases"] >= 2:
            return candidate
    return None


def gen_gifs_for_outfit(
    outfit_id: int,
    fgs: list[dict],
    *,
    force: bool,
    outfits_mtime: float,
) -> tuple[int, list[Path]]:
    """Gera GIFs para 1 outfit. Retorna (count_gerado, lista_gifs)."""
    fg = pick_frame_group(fgs)
    if fg is None:
        return 0, []

    pw = fg["pattern_width"]
    generated = 0
    paths = []
    for direction in range(pw):
        out_path = GIFS_DIR / f"outfit_{outfit_id}_dir{direction}.gif"
        paths.append(out_path)
        if out_path.exists() and not force:
            try:
                if out_path.stat().st_mtime > outfits_mtime:
                    continue
            except OSError:
                pass
        frames = build_direction_frames(fg, direction)
        if not frames:
            continue
        try:
            rgba_frames_to_gif(frames, out_path)
            generated += 1
        except Exception as e:
            print(f"[WARN] outfit {outfit_id} dir{direction}: {e}")
    return generated, paths


def gen_gif_for_item(
    item_id: int,
    fgs: list[dict],
    *,
    force: bool,
    source_mtime: float,
) -> bool:
    """Gera 1 GIF (sem direcao) pra um item animado. Retorna True se gerou.

    Items usam fixed_frame_group=2 (OBJECT_INITIAL), entao nao usa
    pick_frame_group (que olha 0/1). Pega o primeiro fg com >=2 phases.
    """
    fg = next((f for f in fgs if f["phases"] >= 2), None)
    if fg is None:
        return False
    out_path = GIFS_DIR / f"item_{item_id}.gif"
    if out_path.exists() and not force:
        try:
            if out_path.stat().st_mtime > source_mtime:
                return False
        except OSError:
            pass
    # items raramente tem pattern_width>1; usamos direction=0 (primeira coluna)
    # e layer=0 pd_i=0 ph_i=0 — varia apenas phase
    frames = build_direction_frames(fg, direction=0)
    if not frames:
        return False
    try:
        rgba_frames_to_gif(frames, out_path)
        return True
    except Exception as e:
        print(f"[WARN] item {item_id}: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera GIFs animados de outfits")
    parser.add_argument("--force", action="store_true", help="Regerar mesmo se GIF mais novo que outfits.json")
    parser.add_argument("--limit", type=int, default=None, help="Processar apenas N outfits (pra teste)")
    args = parser.parse_args()

    if not OUTFITS_JSON.exists():
        print(f"[ERR] {OUTFITS_JSON} nao existe -- rode decode_outfits.py primeiro")
        return 1
    if not SPRITES_DIR.exists():
        print(f"[ERR] {SPRITES_DIR} nao existe -- rode extract_sprites.py primeiro")
        return 1

    GIFS_DIR.mkdir(parents=True, exist_ok=True)

    outfits_mtime = OUTFITS_JSON.stat().st_mtime

    with OUTFITS_JSON.open("r", encoding="utf-8") as f:
        outfits = json.load(f)

    candidates = [
        o for o in outfits
        if o.get("has_moving") and (o.get("total_sprites") or 0) > 1
    ]
    if args.limit is not None:
        candidates = candidates[: args.limit]

    print(f"[+] Carregando appearances.dat ...")
    # Precisamos do .dat pra particionar sprite_ids por frame_group corretamente
    ASSETS_DIR = Path(os.environ.get("TIBIA_ASSETS_DIR") or (ROOT / "assets"))
    dats = list(ASSETS_DIR.glob("appearances-*.dat")) + list(
        (ASSETS_DIR / "assets").glob("appearances-*.dat") if (ASSETS_DIR / "assets").exists() else []
    )
    if not dats:
        print(f"[ERR] appearances-*.dat nao encontrado em {ASSETS_DIR}")
        return 1
    sys.path.insert(0, str(ROOT / "proto"))
    import appearances_pb2  # noqa: E402
    ap = appearances_pb2.Appearances()
    ap.ParseFromString(dats[0].read_bytes())

    candidate_ids = {o["id"] for o in candidates}
    ap_cache = [o for o in ap.outfit if o.id in candidate_ids]
    by_id = {o.id: o for o in ap_cache}

    t0 = time.time()
    total_gifs = 0
    skipped = 0
    outfits_with_gifs = 0
    for i, outfit in enumerate(candidates):
        oid = outfit["id"]
        app = by_id.get(oid)
        if app is None:
            skipped += 1
            continue
        fgs = []
        for fg in app.frame_group:
            si = fg.sprite_info
            if not si:
                continue
            n_phases = (
                len(si.animation.sprite_phase)
                if si.animation and si.animation.sprite_phase
                else 1
            )
            fgs.append({
                "fixed_frame_group": int(fg.fixed_frame_group),
                "layers": si.layers or 1,
                "pattern_width": si.pattern_width or 1,
                "pattern_height": si.pattern_height or 1,
                "pattern_depth": si.pattern_depth or 1,
                "phases": n_phases,
                "sprite_ids": list(si.sprite_id),
            })
        n_gen, _ = gen_gifs_for_outfit(
            oid, fgs, force=args.force, outfits_mtime=outfits_mtime,
        )
        if n_gen > 0:
            outfits_with_gifs += 1
        total_gifs += n_gen

        if (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            print(f"  ... {i+1}/{len(candidates)} outfits processados ({total_gifs} gifs, {elapsed:.1f}s)")

    elapsed = time.time() - t0
    print()
    print("=" * 60)
    print(f"Outfits candidatos (has_moving && >1 sprite): {len(candidates)}")
    print(f"Outfits com GIF gerado:                       {outfits_with_gifs}")
    print(f"Total de GIFs gerados (outfits):              {total_gifs}")
    print(f"Skipped (nao encontrado no .dat):             {skipped}")
    print(f"Tempo total (outfits):                        {elapsed:.1f}s")

    # ====== ITEMS ANIMADOS ======
    print()
    print("[+] Gerando GIFs pra items animados...")
    t1 = time.time()
    item_candidates = [o for o in ap.object if any(
        fg.sprite_info and fg.sprite_info.animation and
        len(fg.sprite_info.animation.sprite_phase) >= 2
        for fg in o.frame_group
    )]
    if args.limit is not None:
        item_candidates = item_candidates[: args.limit]
    print(f"[+] Items candidatos (>=2 phases): {len(item_candidates)}")

    item_gifs = 0
    for i, obj in enumerate(item_candidates):
        fgs = []
        for fg in obj.frame_group:
            si = fg.sprite_info
            if not si:
                continue
            n_phases = (
                len(si.animation.sprite_phase)
                if si.animation and si.animation.sprite_phase
                else 1
            )
            fgs.append({
                "fixed_frame_group": int(fg.fixed_frame_group),
                "layers": si.layers or 1,
                "pattern_width": si.pattern_width or 1,
                "pattern_height": si.pattern_height or 1,
                "pattern_depth": si.pattern_depth or 1,
                "phases": n_phases,
                "sprite_ids": list(si.sprite_id),
            })
        if gen_gif_for_item(obj.id, fgs, force=args.force, source_mtime=outfits_mtime):
            item_gifs += 1
        if (i + 1) % 500 == 0:
            print(f"  ... {i+1}/{len(item_candidates)} items ({item_gifs} gifs, {time.time()-t1:.1f}s)")

    print()
    print("=" * 60)
    print(f"Items animados:         {len(item_candidates)}")
    print(f"Items com GIF gerado:   {item_gifs}")
    print(f"Tempo total (items):    {time.time()-t1:.1f}s")
    print(f"Output:                 {GIFS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
