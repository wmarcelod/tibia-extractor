#!/usr/bin/env python3
"""
decode_staticdata.py

Decodifica o arquivo staticdata-*.dat do client do Tibia (protobuf).
Contem nomes de monstros (race + name + outfit) e achievements.
Gera:
  - out/monsters.json      (lista flat com campos do Outfit achatados)
  - out/achievements.json  (lista)

Imprime stats no final.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTO_DIR = ROOT / "proto"
ASSETS_DIR = Path(os.environ.get("TIBIA_ASSETS_DIR") or (ROOT / "assets" / "assets"))
OUT_DIR = ROOT / "out"

sys.path.insert(0, str(PROTO_DIR))
import staticdata_pb2  # noqa: E402


def monster_to_row(m) -> dict:
    """Flattens Monster.outfit fields into the dict."""
    o = m.outfit
    return {
        "race": m.race if m.HasField("race") else None,
        "name": m.name if m.HasField("name") else None,
        "outfit_id": o.outfit_id if o.HasField("outfit_id") else None,
        "head_color": o.head_color if o.HasField("head_color") else None,
        "torso_color": o.torso_color if o.HasField("torso_color") else None,
        "legs_color": o.legs_color if o.HasField("legs_color") else None,
        "detail_color": o.detail_color if o.HasField("detail_color") else None,
        "addons": o.addons if o.HasField("addons") else None,
        "object_appearance_type_id": (
            o.object_appearance_type_id
            if o.HasField("object_appearance_type_id")
            else None
        ),
    }


def achievement_to_row(a) -> dict:
    return {
        "id": a.achievement_id if a.HasField("achievement_id") else None,
        "name": a.name if a.HasField("name") else None,
        "description": a.description if a.HasField("description") else None,
        "grade": a.grade if a.HasField("grade") else None,
    }


def main() -> int:
    dats = list(ASSETS_DIR.glob("staticdata-*.dat"))
    if not dats:
        sys.exit(f"staticdata-*.dat nao encontrado em {ASSETS_DIR}")
    path = dats[0]
    print(f"[+] Lendo {path.name}")

    data = path.read_bytes()
    sd = staticdata_pb2.StaticData()
    sd.ParseFromString(data)

    monsters = [monster_to_row(m) for m in sd.monster]
    achievements = [achievement_to_row(a) for a in sd.achievements]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "monsters.json").write_text(
        json.dumps(monsters, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "achievements.json").write_text(
        json.dumps(achievements, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[+] Escrito: out/monsters.json     ({len(monsters)} entries)")
    print(f"[+] Escrito: out/achievements.json ({len(achievements)} entries)")

    # stats
    races = Counter(m["race"] for m in monsters if m["race"] is not None)
    distinct_outfits = len({m["outfit_id"] for m in monsters if m["outfit_id"] is not None})
    print()
    print("=== Stats ===")
    print(f"Total monstros:         {len(monsters)}")
    print(f"Racas unicas:           {len(races)}")
    print(f"Outfits distintos:      {distinct_outfits}")
    print(f"Total achievements:     {len(achievements)}")

    # top races (if >1 monster share the same race id)
    dup_races = [(r, c) for r, c in races.most_common() if c > 1]
    if dup_races:
        print(f"Racas com >1 monstro:   {len(dup_races)} (top 5: {dup_races[:5]})")
    else:
        print("Racas com >1 monstro:   0 (race eh unica por monstro)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
