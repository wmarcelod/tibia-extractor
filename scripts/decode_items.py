#!/usr/bin/env python3
"""
decode_items.py

Decodifica o appearances.dat do Tibia usando o schema .proto oficial
e gera:
  - out/items.json  : todos os itens com metadados completos
  - out/items.csv   : planilha com colunas principais
  - out/outfits.json, effects.json, missiles.json (bonus)

Uso:
  python decode_items.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTO_DIR = ROOT / "proto"
ASSETS_DIR = Path(os.environ.get("TIBIA_ASSETS_DIR") or (ROOT / "assets"))
OUT_DIR = ROOT / "out"

sys.path.insert(0, str(PROTO_DIR))

import appearances_pb2  # noqa: E402
from google.protobuf.json_format import MessageToDict  # noqa: E402


def _json_default(obj):
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    raise TypeError(f"Not serializable: {type(obj).__name__}")


SLOT_NAMES = {
    1: "HELMET",
    2: "AMULET",
    3: "BACKPACK",
    4: "ARMOR",
    5: "RIGHT_HAND",
    6: "LEFT_HAND",
    7: "LEGS",
    8: "BOOTS",
    9: "RING",
    10: "AMMO",
}

CATEGORY_NAMES = {
    1: "Armors", 2: "Amulets", 3: "Boots", 4: "Containers", 5: "Decoration",
    6: "Food", 7: "Helmets/Hats", 8: "Legs", 9: "Others", 10: "Potions",
    11: "Rings", 12: "Runes", 13: "Shields", 14: "Tools", 15: "Valuables",
    16: "Ammunition", 17: "Axes", 18: "Clubs", 19: "Distance", 20: "Swords",
    21: "Wands/Rods", 22: "Premium Scrolls", 23: "Tibia Coins",
    24: "Creature Products",
}

PROFESSION_NAMES = {
    -1: "ANY", 0: "NONE", 1: "KNIGHT", 2: "PALADIN",
    3: "SORCERER", 4: "DRUID", 10: "PROMOTED",
}


def find_dat() -> Path:
    dats = list(ASSETS_DIR.glob("appearances-*.dat"))
    if not dats:
        sys.exit(f"appearances-*.dat NAO encontrado em {ASSETS_DIR}")
    return dats[0]


def first_sprite_id(app) -> int | None:
    """Retorna o primeiro sprite_id do primeiro frame_group (sprite 'principal')."""
    for fg in app.frame_group:
        si = fg.sprite_info
        if si and si.sprite_id:
            return si.sprite_id[0]
    return None


def all_sprite_ids(app) -> list[int]:
    """Todos os sprite_ids de todos os frame_groups (inclui animacao + direcoes)."""
    ids: list[int] = []
    for fg in app.frame_group:
        si = fg.sprite_info
        if si:
            ids.extend(si.sprite_id)
    # preservar ordem, remover duplicatas
    seen = set()
    out = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


VOCATION_NAMES = {0: "all", 1: "knight", 2: "paladin", 3: "druid", 4: "sorcerer", 5: "monk"}
WEAPON_TYPE_NAMES = {1: "sword", 2: "club", 3: "axe", 4: "distance", 5: "wand", 6: "rod", 7: "shield", 8: "ammunition"}


def _parse_hidden_flags(flags) -> dict:
    """Decifra os campos 45-64 de AppearanceFlags que o proto oficial nao declara.

    Referencia (descoberto via varint parsing + cross-ref com in-game tooltips):
      45 = is_ammunition, 46 = is_podium, 47 = is_writable_paper
      48 = {1: upgrade_classification (tier 1-4)}
      53 = is_fiery, 54 = is_shimmer, 55 = is_ring, 56 = ring_twin_id
      57 = is_decoration_kit, 58 = {1: gem_tier, 2: gem_slot}
      59 = is_martial_arts
      60 = {1: equipable_category (1-3)}
      61 = {1: classification_family_id}
      62 = vocation mask (0=all,1=knight,2=paladin,3=druid,4=sorc,5=monk)
      63 = minimum_level (redundante com market.minimum_level em muitos casos)
      64 = weapon_type enum (1-8)
    """
    from google.protobuf.internal import decoder
    raw = flags.SerializeToString()
    out: dict = {}
    pos = 0
    while pos < len(raw):
        tag, pos = decoder._DecodeVarint(raw, pos)
        field_num = tag >> 3
        wire = tag & 0x7
        if wire == 0:
            val, pos = decoder._DecodeVarint(raw, pos)
        elif wire == 2:
            ln, pos = decoder._DecodeVarint(raw, pos)
            val = raw[pos:pos + ln]
            pos += ln
        elif wire == 1:
            val = raw[pos:pos + 8]; pos += 8
        elif wire == 5:
            val = raw[pos:pos + 4]; pos += 4
        else:
            break
        if field_num < 45:
            continue
        # submsg parsing (f48, f58, f60, f61)
        if wire == 2 and field_num in (48, 58, 60, 61):
            sub = {}
            sp = 0
            try:
                while sp < len(val):
                    st, sp = decoder._DecodeVarint(val, sp)
                    sfn, swt = st >> 3, st & 0x7
                    if swt == 0:
                        sv, sp = decoder._DecodeVarint(val, sp)
                    else:
                        break
                    sub[sfn] = sv
            except Exception:
                pass
            out[field_num] = sub
        else:
            out[field_num] = val
    return out


def item_to_row(app) -> dict:
    """Converte Appearance (tipo 'object') numa linha achatada p/ CSV/BD."""
    flags = app.flags
    market = flags.market if flags.HasField("market") else None
    clothes = flags.clothes if flags.HasField("clothes") else None
    light = flags.light if flags.HasField("light") else None
    height = flags.height if flags.HasField("height") else None
    cyclo = flags.cyclopediaitem if flags.HasField("cyclopediaitem") else None
    hidden = _parse_hidden_flags(flags)

    slot_val = clothes.slot if clothes else None
    market_cat = market.category if market and market.HasField("category") else None
    min_level = market.minimum_level if market and market.HasField("minimum_level") else None
    professions = [PROFESSION_NAMES.get(p, str(p)) for p in (market.restrict_to_profession if market else [])]

    npcs = []
    for npc in flags.npcsaledata:
        # Preco 0 no appearances.dat = "nao faz essa operacao" (nao compra/nao vende)
        # CipSoft preenche o campo mesmo quando NPC nao executa aquela transacao.
        sale = npc.sale_price if npc.HasField("sale_price") else None
        buy = npc.buy_price if npc.HasField("buy_price") else None
        if sale == 0: sale = None
        if buy == 0: buy = None
        # Se NPC nao compra nem vende esse item, pula o registro inteiro
        if sale is None and buy is None:
            continue
        npcs.append({
            "name": npc.name or None,
            "location": npc.location or None,
            "sale_price": sale,
            "buy_price": buy,
            "currency_object_type_id": npc.currency_object_type_id if npc.HasField("currency_object_type_id") else None,
        })

    sprite_ids = all_sprite_ids(app)
    # strip espacos extras no nome (CipSoft deixa vazar " vial of corrosive blood")
    raw_name = (app.name or "").strip()
    return {
        "id": app.id,
        "name": raw_name or None,
        "description": (app.description or "").strip() or None,
        "slot": SLOT_NAMES.get(slot_val) if slot_val else None,
        "slot_id": slot_val,
        "market_category": CATEGORY_NAMES.get(market_cat) if market_cat else None,
        "market_category_id": market_cat,
        "market_name": market.trade_as_object_id if market and market.HasField("trade_as_object_id") else None,
        "minimum_level": min_level,
        "professions": ",".join(professions) if professions else None,
        "stackable": flags.cumulative,
        "container": flags.container,
        "usable": flags.usable,
        "multiuse": flags.multiuse,
        "pickupable": flags.take,
        "liquid_container": flags.liquidcontainer,
        "liquid_pool": flags.liquidpool,
        "unpass": flags.unpass,
        "unmove": flags.unmove,
        "rotatable": flags.rotate,
        "corpse": flags.corpse,
        "is_player_corpse": flags.player_corpse,
        "hangable": flags.hang,
        "wrap": flags.wrap,
        "unwrap": flags.unwrap,
        "writable_max_len": flags.write.max_text_length if flags.HasField("write") else None,
        "readonly_max_len": flags.write_once.max_text_length_once if flags.HasField("write_once") else None,
        "light_brightness": light.brightness if light else None,
        "light_color": light.color if light else None,
        "elevation": height.elevation if height else None,
        "cyclopedia_type": cyclo.cyclopedia_type if cyclo and cyclo.HasField("cyclopedia_type") else None,
        # Campos ocultos (descobertos via varint parsing; proto oficial nao declara)
        "tier": (hidden.get(48) or {}).get(1),  # upgrade_classification 1..4
        "vocation_id": hidden.get(62) if isinstance(hidden.get(62), int) else None,
        "vocation": VOCATION_NAMES.get(hidden.get(62)) if isinstance(hidden.get(62), int) else None,
        "min_level_req": hidden.get(63) if isinstance(hidden.get(63), int) else None,
        "weapon_type_id": hidden.get(64) if isinstance(hidden.get(64), int) else None,
        "weapon_type": WEAPON_TYPE_NAMES.get(hidden.get(64)) if isinstance(hidden.get(64), int) else None,
        "is_ammunition": bool(hidden.get(45)),
        "is_podium": bool(hidden.get(46)),
        "is_writable_paper": bool(hidden.get(47)),
        "is_fiery": bool(hidden.get(53)),
        "is_shimmer": bool(hidden.get(54)),
        "is_ring": bool(hidden.get(55)),
        "is_decoration_kit": bool(hidden.get(57)),
        "is_martial_arts": bool(hidden.get(59)),
        "gem_tier": (hidden.get(58) or {}).get(1),
        "gem_slot": (hidden.get(58) or {}).get(2),
        "equipable_category": (hidden.get(60) or {}).get(1),
        "classification_family": (hidden.get(61) or {}).get(1),
        "npc_sources_count": len(npcs),
        "npc_sources": npcs,
        "main_sprite_id": first_sprite_id(app),
        "sprite_ids": sprite_ids,
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dat_path = find_dat()
    print(f"[+] Lendo {dat_path.name} ({dat_path.stat().st_size/1024/1024:.1f} MB)")

    appearances = appearances_pb2.Appearances()
    appearances.ParseFromString(dat_path.read_bytes())

    print(f"[+] Objects (items): {len(appearances.object)}")
    print(f"[+] Outfits: {len(appearances.outfit)}")
    print(f"[+] Effects: {len(appearances.effect)}")
    print(f"[+] Missiles: {len(appearances.missile)}")

    items = [item_to_row(o) for o in appearances.object]

    # Filtra lixo conhecido do appearances.dat (test items e placeholders Theons)
    import re as _re
    _TEST_RE = _re.compile(r"\btest\b|\bTEST\b", _re.IGNORECASE)
    _PLACEHOLDER_SPRITE = 191968
    _NUMERIC_PREFIX_RE = _re.compile(r"^\d+\s+\w+$")

    def _is_garbage(it):
        n = it.get("name") or ""
        # 1) test items com "test" no nome (CipSoft internal/debug)
        if _TEST_RE.search(n):
            return True
        # 2) placeholders "50 Theons" e similares: nome N+palavra, sem cat, sem slot, sprite placeholder
        if (it["main_sprite_id"] == _PLACEHOLDER_SPRITE
            and it["market_category"] is None and it["slot"] is None
            and _NUMERIC_PREFIX_RE.match(n)):
            return True
        return False

    garbage = [i for i in items if _is_garbage(i)]
    items = [i for i in items if not _is_garbage(i)]
    print(f"[+] Filtrados {len(garbage)} items lixo (test/placeholders): "
          f"{[i['name'] for i in garbage[:10]]}")

    # Stats
    named = sum(1 for i in items if i["name"])
    with_market = sum(1 for i in items if i["market_category"])
    with_npc = sum(1 for i in items if i["npc_sources_count"] > 0)
    with_sprite = sum(1 for i in items if i["main_sprite_id"] is not None)
    print(f"[+] Com nome: {named}  | no market: {with_market}  | com NPC seller/buyer: {with_npc}  | com sprite: {with_sprite}")

    # JSON completo
    json_path = OUT_DIR / "items.json"
    json_path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    print(f"[+] Escrito: {json_path.relative_to(ROOT)} ({json_path.stat().st_size/1024/1024:.1f} MB)")

    # CSV achatado (sem listas)
    csv_path = OUT_DIR / "items.csv"
    csv_cols = [
        "id", "name", "description", "slot", "market_category", "minimum_level",
        "professions", "stackable", "container", "usable", "multiuse",
        "pickupable", "rotatable", "hangable", "corpse",
        "light_brightness", "light_color", "elevation",
        "cyclopedia_type", "npc_sources_count", "main_sprite_id",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_cols, extrasaction="ignore")
        w.writeheader()
        for row in items:
            w.writerow(row)
    print(f"[+] Escrito: {csv_path.relative_to(ROOT)}")

    # Effects / missiles (bonus). 'outfits' e responsabilidade de decode_outfits.py
    # (inclui kind/preview_sprite_id/pattern_*) -- nao sobrescrever aqui.
    for key, collection in [
        ("effects", appearances.effect),
        ("missiles", appearances.missile),
    ]:
        out_path = OUT_DIR / f"{key}.json"
        simple = [
            {
                "id": a.id,
                "name": a.name or None,
                "description": a.description or None,
                "sprite_ids": all_sprite_ids(a),
            }
            for a in collection
        ]
        out_path.write_text(json.dumps(simple, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
        print(f"[+] Escrito: {out_path.relative_to(ROOT)} ({len(simple)} entries)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
