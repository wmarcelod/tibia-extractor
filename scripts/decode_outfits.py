#!/usr/bin/env python3
"""
decode_outfits.py

Extrai outfits (= criaturas + NPCs + player outfits) do appearances.dat
com TODOS os sprites e pattern layout. Gera CSV + atualiza DB.

IMPORTANTE: nomes de criaturas/NPCs nao estao no client (sao enviados pelo
servidor em runtime). Esse script extrai ID + sprites + metadata estrutural.
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTO_DIR = ROOT / "proto"
ASSETS_DIR = Path(os.environ.get("TIBIA_ASSETS_DIR") or (ROOT / "assets"))
OUT_DIR = ROOT / "out"

sys.path.insert(0, str(PROTO_DIR))
import appearances_pb2  # noqa: E402


# FIXED_FRAME_GROUP
FG_OUTFIT_IDLE = 0
FG_OUTFIT_MOVING = 1


def classify_outfit(app) -> str:
    """Heuristica para classificar: player_outfit vs creature/npc vs simple."""
    # Player outfits tipicamente tem 2 frame_groups (IDLE+MOVING),
    # layers=2 (sprite normal + template pra colorir), pattern_depth=4 (addons variations).
    fgs = list(app.frame_group)
    has_moving = any(fg.fixed_frame_group == FG_OUTFIT_MOVING for fg in fgs)
    first = fgs[0] if fgs else None
    if first is None or not first.sprite_info:
        return "empty"
    si = first.sprite_info
    layers = si.layers or 1
    pd = si.pattern_depth or 1
    pw = si.pattern_width or 1
    ph = si.pattern_height or 1

    if layers >= 2 and pd >= 1 and has_moving:
        return "player_outfit"  # template-colorizable + walks = player
    if has_moving and layers == 1:
        return "creature_or_npc"  # criaturas e NPCs animados
    if len(fgs) == 1 and pw == 1 and ph == 1:
        return "static"  # sprite parado simples (ex.: cadaveres, algumas NPCs sem animacao)
    return "other"


def preview_sprite_id(app) -> int | None:
    """
    Retorna o sprite de 'preview' do outfit:
    primeiro frame do IDLE, direcao sul, primeira cor, camada normal.
    """
    idle = None
    for fg in app.frame_group:
        if fg.fixed_frame_group == FG_OUTFIT_IDLE:
            idle = fg
            break
    fg = idle or (app.frame_group[0] if app.frame_group else None)
    if fg is None or not fg.sprite_info:
        return None
    si = fg.sprite_info
    if not si.sprite_id:
        return None
    # layout do Tibia: sprites flat em ordem
    # [phase][pattern_depth][pattern_height][pattern_width][layers]
    # sul = pattern_width index 2 tipicamente (0=N, 1=E, 2=S, 3=W);
    # mas pra simplificar pegar o primeiro sprite funciona em ~todos os casos.
    return si.sprite_id[0]


def outfit_to_row(app) -> dict:
    fgs = list(app.frame_group)
    first = fgs[0] if fgs else None
    si = first.sprite_info if first and first.sprite_info else None

    layers = si.layers if si else None
    pw = si.pattern_width if si else None
    ph = si.pattern_height if si else None
    pd = si.pattern_depth if si else None

    all_sids: list[int] = []
    for fg in fgs:
        if fg.sprite_info:
            all_sids.extend(fg.sprite_info.sprite_id)

    return {
        "id": app.id,
        "kind": classify_outfit(app),
        "preview_sprite_id": preview_sprite_id(app),
        "frame_group_count": len(fgs),
        "layers": layers,
        "pattern_width": pw,
        "pattern_height": ph,
        "pattern_depth": pd,
        "has_moving": any(fg.fixed_frame_group == FG_OUTFIT_MOVING for fg in fgs),
        "total_sprites": len(all_sids),
        "sprite_ids": all_sids,
    }


def main() -> int:
    dats = list(ASSETS_DIR.glob("appearances-*.dat"))
    if not dats:
        sys.exit("appearances-*.dat nao encontrado")

    ap = appearances_pb2.Appearances()
    ap.ParseFromString(dats[0].read_bytes())
    outfits = ap.outfit
    print(f"[+] Total outfits: {len(outfits)}")

    rows = [outfit_to_row(o) for o in outfits]

    # Classificacao
    from collections import Counter
    counts = Counter(r["kind"] for r in rows)
    print("[+] Classificacao:")
    for k, v in counts.most_common():
        print(f"    {k}: {v}")

    # Enriquecer com caminho do sprite preview
    sprites_dir = OUT_DIR / "sprites"
    for r in rows:
        sid = r["preview_sprite_id"]
        if sid is None:
            r["image_file"] = None
            r["image_exists"] = False
        else:
            rel = f"sprites/sprite_{sid}.png"
            r["image_file"] = rel
            r["image_exists"] = (sprites_dir / f"sprite_{sid}.png").exists()
    with_img = sum(1 for r in rows if r["image_exists"])
    print(f"[+] Com imagem de preview: {with_img}/{len(rows)}")

    # JSON completo
    (OUT_DIR / "outfits.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # CSV
    csv_path = OUT_DIR / "outfits.csv"
    cols = ["id", "kind", "preview_sprite_id", "image_file", "image_exists",
            "frame_group_count", "layers", "pattern_width", "pattern_height",
            "pattern_depth", "has_moving", "total_sprites"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[+] Escrito: out/outfits.csv ({len(rows)} entries)")

    # Inserir no DB existente (items.db)
    db_path = OUT_DIR / "items.db"
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS outfits")
    c.execute("""CREATE TABLE outfits (
        id INTEGER PRIMARY KEY,
        kind TEXT,
        preview_sprite_id INTEGER,
        image_file TEXT,
        frame_group_count INTEGER,
        layers INTEGER,
        pattern_width INTEGER,
        pattern_height INTEGER,
        pattern_depth INTEGER,
        has_moving INTEGER,
        total_sprites INTEGER
    )""")
    c.execute("DROP TABLE IF EXISTS outfit_sprites")
    c.execute("""CREATE TABLE outfit_sprites (
        outfit_id INTEGER,
        sprite_id INTEGER,
        position INTEGER
    )""")
    for r in rows:
        c.execute(
            "INSERT INTO outfits VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (r["id"], r["kind"], r["preview_sprite_id"], r["image_file"],
             r["frame_group_count"], r["layers"], r["pattern_width"],
             r["pattern_height"], r["pattern_depth"],
             int(bool(r["has_moving"])), r["total_sprites"]),
        )
        for pos, sid in enumerate(r["sprite_ids"]):
            c.execute(
                "INSERT INTO outfit_sprites VALUES (?,?,?)",
                (r["id"], sid, pos),
            )
    c.execute("CREATE INDEX idx_outfit_kind ON outfits(kind)")
    c.execute("CREATE INDEX idx_outfit_sprites_oid ON outfit_sprites(outfit_id)")
    conn.commit()
    conn.close()
    print("[+] Tabelas 'outfits' e 'outfit_sprites' atualizadas em items.db")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
