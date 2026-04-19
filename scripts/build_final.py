#!/usr/bin/env python3
"""
build_final.py

Gera o CSV + JSON final de itens com:
  - colunas 'image_file' e 'image_exists' cruzando sprite_id com o PNG
  - SQLite DB (items.db) opcional para consulta rapida
Requer que decode_items.py e extract_sprites.py ja tenham sido rodados.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
SPRITES_DIR = OUT / "sprites"


def main() -> int:
    items = json.loads((OUT / "items.json").read_text(encoding="utf-8"))
    print(f"[+] Itens: {len(items)}")

    # Enriquecer com path da imagem
    for it in items:
        sid = it.get("main_sprite_id")
        if sid is None:
            it["image_file"] = None
            it["image_exists"] = False
            continue
        rel = f"sprites/sprite_{sid}.png"
        it["image_file"] = rel
        it["image_exists"] = (SPRITES_DIR / f"sprite_{sid}.png").exists()

    with_img = sum(1 for i in items if i["image_exists"])
    print(f"[+] Itens com imagem presente: {with_img}/{len(items)}")

    # JSON final (sobrescreve items.json com dados enriquecidos)
    (OUT / "items.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # CSV principal — itens com nome (8812) sao os relevantes pro fansite
    named = [i for i in items if i["name"]]
    csv_cols = [
        "id", "name", "description", "slot", "market_category", "minimum_level",
        "professions", "stackable", "container", "usable", "pickupable",
        "light_brightness", "light_color", "cyclopedia_type",
        "npc_sources_count", "main_sprite_id", "image_file", "image_exists",
    ]
    csv_path = OUT / "items_named.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_cols, extrasaction="ignore")
        w.writeheader()
        for row in named:
            w.writerow(row)
    print(f"[+] Escrito: {csv_path.relative_to(ROOT)} ({len(named)} itens com nome)")

    # CSV completo (inclui props do mapa sem nome, 42099 linhas)
    csv_all = OUT / "items_all.csv"
    with csv_all.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_cols, extrasaction="ignore")
        w.writeheader()
        for row in items:
            w.writerow(row)
    print(f"[+] Escrito: {csv_all.relative_to(ROOT)} ({len(items)} entradas)")

    # SQLite com tabela de items e npc_sales normalizada
    db_path = OUT / "items.db"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE items (
            id INTEGER PRIMARY KEY,
            name TEXT,
            description TEXT,
            slot TEXT,
            market_category TEXT,
            minimum_level INTEGER,
            professions TEXT,
            stackable INTEGER, container INTEGER, usable INTEGER,
            multiuse INTEGER, pickupable INTEGER,
            liquid_container INTEGER, liquid_pool INTEGER,
            rotatable INTEGER, hangable INTEGER, corpse INTEGER,
            light_brightness INTEGER, light_color INTEGER,
            elevation INTEGER, cyclopedia_type INTEGER,
            main_sprite_id INTEGER,
            image_file TEXT,
            -- campos ocultos decifrados dos bytes raw do appearances.dat
            tier INTEGER,
            vocation TEXT,
            vocation_id INTEGER,
            min_level_req INTEGER,
            weapon_type TEXT,
            weapon_type_id INTEGER,
            is_ammunition INTEGER, is_podium INTEGER, is_writable_paper INTEGER,
            is_fiery INTEGER, is_shimmer INTEGER, is_ring INTEGER,
            is_decoration_kit INTEGER, is_martial_arts INTEGER,
            gem_tier INTEGER, gem_slot INTEGER,
            equipable_category INTEGER,
            classification_family INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE npc_sales (
            item_id INTEGER,
            npc_name TEXT,
            location TEXT,
            sale_price INTEGER,
            buy_price INTEGER,
            FOREIGN KEY (item_id) REFERENCES items(id)
        )
    """)
    c.execute("""
        CREATE TABLE item_sprites (
            item_id INTEGER,
            sprite_id INTEGER,
            position INTEGER,
            FOREIGN KEY (item_id) REFERENCES items(id)
        )
    """)
    c.execute("""
        CREATE TABLE outfits (
            id INTEGER PRIMARY KEY,
            kind TEXT,
            preview_sprite_id INTEGER,
            frame_group_count INTEGER,
            layers INTEGER,
            pattern_width INTEGER,
            pattern_height INTEGER,
            pattern_depth INTEGER,
            has_moving INTEGER,
            total_sprites INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE monsters (
            race INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            outfit_id INTEGER,
            head_color INTEGER,
            torso_color INTEGER,
            legs_color INTEGER,
            detail_color INTEGER,
            addons INTEGER,
            object_appearance_type_id INTEGER,
            FOREIGN KEY (outfit_id) REFERENCES outfits(id)
        )
    """)
    c.execute("""
        CREATE TABLE achievements (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            grade INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE areas (
            area_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            area_type TEXT,
            parent_area_id INTEGER,
            label_x INTEGER, label_y INTEGER, label_z INTEGER,
            alias TEXT
        )
    """)
    c.execute("""
        CREATE TABLE npc_locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            npc_name TEXT NOT NULL,
            x INTEGER NOT NULL, y INTEGER NOT NULL, z INTEGER NOT NULL,
            subarea_id INTEGER,
            area_name TEXT
        )
    """)
    c.execute("""
        CREATE TABLE map_files (
            file_name TEXT PRIMARY KEY,
            file_type TEXT,
            top_left_x INTEGER, top_left_y INTEGER, top_left_z INTEGER,
            fields_width INTEGER, fields_height INTEGER,
            area_id INTEGER,
            scale_factor REAL
        )
    """)

    def _b(v):
        return int(bool(v)) if v is not None else 0

    for it in items:
        c.execute(
            """INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                it["id"], it["name"], it["description"], it["slot"],
                it["market_category"], it["minimum_level"], it["professions"],
                _b(it["stackable"]), _b(it["container"]),
                _b(it["usable"]), _b(it["multiuse"]),
                _b(it["pickupable"]), _b(it["liquid_container"]),
                _b(it["liquid_pool"]), _b(it["rotatable"]),
                _b(it["hangable"]), _b(it["corpse"]),
                it["light_brightness"], it["light_color"],
                it["elevation"], it["cyclopedia_type"],
                it["main_sprite_id"], it["image_file"],
                # hidden fields
                it.get("tier"),
                it.get("vocation"), it.get("vocation_id"),
                it.get("min_level_req"),
                it.get("weapon_type"), it.get("weapon_type_id"),
                _b(it.get("is_ammunition")), _b(it.get("is_podium")),
                _b(it.get("is_writable_paper")),
                _b(it.get("is_fiery")), _b(it.get("is_shimmer")),
                _b(it.get("is_ring")),
                _b(it.get("is_decoration_kit")), _b(it.get("is_martial_arts")),
                it.get("gem_tier"), it.get("gem_slot"),
                it.get("equipable_category"),
                it.get("classification_family"),
            ),
        )
        for npc in it.get("npc_sources") or []:
            c.execute(
                "INSERT INTO npc_sales (item_id, npc_name, location, sale_price, buy_price) VALUES (?,?,?,?,?)",
                (it["id"], npc.get("name"), npc.get("location"),
                 npc.get("sale_price"), npc.get("buy_price")),
            )
        for pos, sid in enumerate(it.get("sprite_ids") or []):
            c.execute(
                "INSERT INTO item_sprites (item_id, sprite_id, position) VALUES (?,?,?)",
                (it["id"], sid, pos),
            )

    # Outfits / monsters / achievements (opcionais — so popula se o JSON existir)
    outfits_json = OUT / "outfits.json"
    if outfits_json.exists():
        outfits = json.loads(outfits_json.read_text(encoding="utf-8"))
        for o in outfits:
            c.execute(
                "INSERT INTO outfits VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    o["id"], o.get("kind"), o.get("preview_sprite_id"),
                    o.get("frame_group_count"), o.get("layers"),
                    o.get("pattern_width"), o.get("pattern_height"),
                    o.get("pattern_depth"),
                    int(bool(o.get("has_moving"))),
                    o.get("total_sprites"),
                ),
            )
        print(f"[+] Outfits na DB: {len(outfits)}")

    monsters_json = OUT / "monsters.json"
    if monsters_json.exists():
        monsters = json.loads(monsters_json.read_text(encoding="utf-8"))
        for m in monsters:
            c.execute(
                "INSERT OR REPLACE INTO monsters VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    m.get("race"), m.get("name"), m.get("outfit_id"),
                    m.get("head_color"), m.get("torso_color"),
                    m.get("legs_color"), m.get("detail_color"),
                    m.get("addons"), m.get("object_appearance_type_id"),
                ),
            )
        print(f"[+] Monsters na DB: {len(monsters)}")

    ach_json = OUT / "achievements.json"
    if ach_json.exists():
        achs = json.loads(ach_json.read_text(encoding="utf-8"))
        for a in achs:
            c.execute(
                "INSERT OR REPLACE INTO achievements VALUES (?,?,?,?)",
                (a.get("id"), a.get("name"), a.get("description"), a.get("grade")),
            )
        print(f"[+] Achievements na DB: {len(achs)}")

    areas_json = OUT / "areas.json"
    if areas_json.exists():
        areas = json.loads(areas_json.read_text(encoding="utf-8"))
        for a in areas:
            c.execute(
                "INSERT OR REPLACE INTO areas VALUES (?,?,?,?,?,?,?,?)",
                (a.get("area_id"), a.get("name"), a.get("area_type"),
                 a.get("parent_area_id"),
                 a.get("label_x"), a.get("label_y"), a.get("label_z"),
                 json.dumps(a.get("alias") or [])),
            )
        print(f"[+] Areas na DB: {len(areas)}")

    npcs_loc_json = OUT / "npcs_locations.json"
    if npcs_loc_json.exists():
        npls = json.loads(npcs_loc_json.read_text(encoding="utf-8"))
        for n in npls:
            c.execute(
                "INSERT INTO npc_locations (npc_name, x, y, z, subarea_id, area_name) VALUES (?,?,?,?,?,?)",
                (n.get("name"), n.get("x"), n.get("y"), n.get("z"),
                 n.get("subarea_id"), n.get("area_name")),
            )
        print(f"[+] NPC locations na DB: {len(npls)}")

    mfiles_json = OUT / "map_files.json"
    if mfiles_json.exists():
        mfs = json.loads(mfiles_json.read_text(encoding="utf-8"))
        for m in mfs:
            c.execute(
                "INSERT OR REPLACE INTO map_files VALUES (?,?,?,?,?,?,?,?,?)",
                (m.get("file_name"), m.get("file_type"),
                 m.get("top_left_x"), m.get("top_left_y"), m.get("top_left_z"),
                 m.get("fields_width"), m.get("fields_height"),
                 m.get("area_id"), m.get("scale_factor")),
            )
        print(f"[+] Map files na DB: {len(mfs)}")

    c.execute("CREATE INDEX idx_items_name ON items(name)")
    c.execute("CREATE INDEX idx_items_cat ON items(market_category)")
    c.execute("CREATE INDEX idx_npc_item ON npc_sales(item_id)")
    c.execute("CREATE INDEX idx_npc_name ON npc_sales(npc_name)")
    c.execute("CREATE INDEX idx_sprites_item ON item_sprites(item_id)")
    c.execute("CREATE INDEX idx_monsters_name ON monsters(name)")
    c.execute("CREATE INDEX idx_monsters_outfit ON monsters(outfit_id)")
    c.execute("CREATE INDEX idx_areas_name ON areas(name)")
    c.execute("CREATE INDEX idx_npc_loc_name ON npc_locations(npc_name COLLATE NOCASE)")
    c.execute("CREATE INDEX idx_npc_loc_area ON npc_locations(area_name)")
    c.execute("CREATE INDEX idx_map_files_area ON map_files(area_id)")
    c.execute("CREATE INDEX idx_map_files_type ON map_files(file_type)")
    c.execute("CREATE INDEX idx_map_files_xyz ON map_files(top_left_x, top_left_y, top_left_z)")
    conn.commit()

    # Estatisticas rapidas
    print("\n=== Stats do DB ===")
    for q, label in [
        ("SELECT COUNT(*) FROM items", "items totais"),
        ("SELECT COUNT(*) FROM items WHERE name IS NOT NULL", "com nome"),
        ("SELECT COUNT(*) FROM items WHERE market_category IS NOT NULL", "no market"),
        ("SELECT COUNT(*) FROM items WHERE image_file IS NOT NULL", "com sprite"),
        ("SELECT COUNT(*) FROM npc_sales", "linhas NPC sales"),
        ("SELECT COUNT(DISTINCT npc_name) FROM npc_sales", "NPCs distintos"),
    ]:
        (n,) = c.execute(q).fetchone()
        print(f"  {label}: {n}")

    print("\n=== Top 5 categorias ===")
    for row in c.execute(
        "SELECT market_category, COUNT(*) FROM items WHERE market_category IS NOT NULL "
        "GROUP BY market_category ORDER BY 2 DESC LIMIT 5"
    ):
        print(f"  {row[0]}: {row[1]}")

    conn.close()
    print(f"\n[+] DB: {db_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
