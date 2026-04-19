#!/usr/bin/env python3
"""
decode_map.py

Decodifica o arquivo map-*.dat do client do Tibia (protobuf).
Contem areas/subareas (com coords), NPCs (com posicoes x/y/z) e referencias
para arquivos de mapa (minimap / satellite / subarea overlays).

Gera:
  - out/areas.json           (lista de Areas: id, name, type, subarea_ids, label_x/y/z, alias[])
  - out/npcs_locations.json  (lista de NPCs: name, x, y, z, subarea_id, area_name)
  - out/map_files.json       (lista de MapFile refs)

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
import map_pb2  # noqa: E402


# AREA_TYPE enum name mapping (shared in map.proto):
#   0 = AREA_TYPE_NONE, 1 = AREA_TYPE_AREA, 2 = AREA_TYPE_SUBAREA
AREA_TYPE_NAMES = {0: "NONE", 1: "AREA", 2: "SUBAREA"}

# MAP_FILE_TYPE:
#   0 = SUBAREA, 1 = SATELLITE, 2 = MINIMAP
MAP_FILE_TYPE_NAMES = {0: "SUBAREA", 1: "SATELLITE", 2: "MINIMAP"}


def coord_fields(msg, field_name: str) -> tuple[int | None, int | None, int | None]:
    """Extract (x, y, z) from a shared.Coordinate optional submessage, or (None,None,None)."""
    if not msg.HasField(field_name):
        return None, None, None
    c = getattr(msg, field_name)
    x = c.x if c.HasField("x") else None
    y = c.y if c.HasField("y") else None
    z = c.z if c.HasField("z") else None
    return x, y, z


def area_to_row(a) -> dict:
    lx, ly, lz = coord_fields(a, "label_coordinate")
    # `alias` is defined as optional string in the proto, but in practice the
    # client uses repeated-style aliases across versions. Normalize to a list.
    alias_val = a.alias if a.HasField("alias") else None
    aliases = [alias_val] if alias_val else []
    return {
        "area_id": a.area_id if a.HasField("area_id") else None,
        "name": a.name if a.HasField("name") else None,
        "area_type": (
            AREA_TYPE_NAMES.get(a.area_type, str(a.area_type))
            if a.HasField("area_type")
            else None
        ),
        "subarea_ids": list(a.subarea_ids),
        "label_x": lx,
        "label_y": ly,
        "label_z": lz,
        "alias": aliases,
        "reject_donations": (
            a.reject_donations if a.HasField("reject_donations") else None
        ),
    }


def npc_to_row(n, subarea_to_area_name: dict[int, str]) -> dict:
    x, y, z = coord_fields(n, "tile_coordinate")
    sub_id = n.subarea_id if n.HasField("subarea_id") else None
    return {
        "name": n.name if n.HasField("name") else None,
        "x": x,
        "y": y,
        "z": z,
        "subarea_id": sub_id,
        "area_name": subarea_to_area_name.get(sub_id) if sub_id is not None else None,
    }


def map_file_to_row(mf) -> dict:
    tx, ty, tz = coord_fields(mf, "top_left_coordinate")
    return {
        "file_type": (
            MAP_FILE_TYPE_NAMES.get(mf.file_type, str(mf.file_type))
            if mf.HasField("file_type")
            else None
        ),
        "top_left_x": tx,
        "top_left_y": ty,
        "top_left_z": tz,
        "file_name": mf.file_name if mf.HasField("file_name") else None,
        "fields_width": mf.fields_width if mf.HasField("fields_width") else None,
        "fields_height": mf.fields_height if mf.HasField("fields_height") else None,
        "area_id": mf.area_id if mf.HasField("area_id") else None,
        "scale_factor": mf.scale_factor if mf.HasField("scale_factor") else None,
    }


def main() -> int:
    dats = list(ASSETS_DIR.glob("map-*.dat"))
    if not dats:
        sys.exit(f"map-*.dat nao encontrado em {ASSETS_DIR}")
    path = dats[0]
    print(f"[+] Lendo {path.name}")

    data = path.read_bytes()
    m = map_pb2.Map()
    m.ParseFromString(data)

    areas = [area_to_row(a) for a in m.areas]

    # Build subarea_id -> parent area name mapping.
    # Parent areas (type AREA) reference their subareas via subarea_ids;
    # we invert that so each NPC can be labeled with its parent area.
    subarea_to_area_name: dict[int, str] = {}
    areas_by_id: dict[int, dict] = {a["area_id"]: a for a in areas if a["area_id"] is not None}
    for a in areas:
        if a["area_type"] == "AREA" and a["name"]:
            for sub_id in a["subarea_ids"]:
                subarea_to_area_name[sub_id] = a["name"]

    # For subareas without an explicit AREA parent (rare), fall back to the subarea's own name.
    for a in areas:
        if a["area_type"] == "SUBAREA" and a["area_id"] is not None:
            subarea_to_area_name.setdefault(a["area_id"], a["name"])

    npcs = [npc_to_row(n, subarea_to_area_name) for n in m.npcs]
    map_files = [map_file_to_row(mf) for mf in m.resource_files]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "areas.json").write_text(
        json.dumps(areas, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "npcs_locations.json").write_text(
        json.dumps(npcs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "map_files.json").write_text(
        json.dumps(map_files, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[+] Escrito: out/areas.json           ({len(areas)} entries)")
    print(f"[+] Escrito: out/npcs_locations.json  ({len(npcs)} entries)")
    print(f"[+] Escrito: out/map_files.json       ({len(map_files)} entries)")

    # stats
    area_type_counts = Counter(a["area_type"] for a in areas)
    file_type_counts = Counter(mf["file_type"] for mf in map_files)
    z_counts = Counter(n["z"] for n in npcs if n["z"] is not None)
    named_npcs = sum(1 for n in npcs if n["name"])
    npcs_with_area = sum(1 for n in npcs if n["area_name"])

    print()
    print("=== Stats ===")
    print(f"Total areas:            {len(areas)}")
    for t, c in area_type_counts.most_common():
        print(f"  {t}:                 {c}")
    print(f"Total NPCs:             {len(npcs)}")
    print(f"  with name:            {named_npcs}")
    print(f"  with area_name:       {npcs_with_area}")
    print(f"  z-floor distribution: {dict(sorted(z_counts.items()))}")
    print(f"Total map_files:        {len(map_files)}")
    for t, c in file_type_counts.most_common():
        print(f"  {t}:              {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
