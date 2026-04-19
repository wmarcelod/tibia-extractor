#!/usr/bin/env python3
"""
visualizer/app.py

Visualizador web do items.db. Sobe um Flask em http://localhost:5000
com abas Items / NPCs / Outfits, sidebar de subcategorias e sprites.

Uso:
  pip install flask
  python visualizer/app.py          # http://localhost:5000
  python visualizer/app.py --port 8080
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import quote

from flask import Flask, Response, abort, g, redirect, render_template, request, send_from_directory, url_for

ADMIN_TOKEN = os.environ.get("TIBIADB_ADMIN_TOKEN", "")

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "out" / "items.db"
OUTFITS_JSON = ROOT / "out" / "outfits.json"
SPRITES_DIR = ROOT / "out" / "sprites"
GIFS_DIR = ROOT / "out" / "gifs"
MAP_TILES_DIR = ROOT / "out" / "map_tiles"

_OUTFITS_CACHE: list | None = None


def load_outfits() -> list:
    global _OUTFITS_CACHE
    if _OUTFITS_CACHE is None:
        if not OUTFITS_JSON.exists():
            _OUTFITS_CACHE = []
        else:
            _OUTFITS_CACHE = json.loads(OUTFITS_JSON.read_text(encoding="utf-8"))
    return _OUTFITS_CACHE

app = Flask(__name__)
PAGE_SIZE = 60


def db():
    if "db" not in g:
        if not DB_PATH.exists():
            abort(500, description=f"items.db nao encontrado em {DB_PATH}. Rode 'python scripts/pipeline.py' primeiro.")
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d is not None:
        d.close()


# ---------- sprite serving ----------

@app.route("/sprite/<int:sprite_id>.png")
def sprite(sprite_id: int):
    fname = f"sprite_{sprite_id}.png"
    if not (SPRITES_DIR / fname).exists():
        abort(404)
    return send_from_directory(SPRITES_DIR, fname, max_age=60 * 60 * 24)


@app.route("/gif/outfit/<int:outfit_id>/dir<int:direction>.gif")
def outfit_gif(outfit_id: int, direction: int):
    fname = f"outfit_{outfit_id}_dir{direction}.gif"
    if not (GIFS_DIR / fname).exists():
        abort(404)
    return send_from_directory(GIFS_DIR, fname, max_age=60 * 60 * 24)


@app.route("/map_tile/<kind>/<filename>")
def map_tile(kind: str, filename: str):
    if kind not in {"minimap_32", "minimap_64",
                    "satellite_16", "satellite_32", "satellite_64", "subarea"}:
        abort(404)
    d = MAP_TILES_DIR / kind
    if not (d / filename).exists():
        abort(404)
    return send_from_directory(d, filename, max_age=60 * 60 * 24 * 7)


@app.route("/gif/item/<int:item_id>.gif")
def item_gif(item_id: int):
    fname = f"item_{item_id}.gif"
    if not (GIFS_DIR / fname).exists():
        abort(404)
    return send_from_directory(GIFS_DIR, fname, max_age=60 * 60 * 24)


def item_has_gif(item_id: int) -> bool:
    return (GIFS_DIR / f"item_{item_id}.gif").exists() if GIFS_DIR.exists() else False


# ---------- helpers ----------

def paginate(total: int, page: int) -> dict:
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, pages))
    return {"page": page, "pages": pages, "total": total, "size": PAGE_SIZE, "offset": (page - 1) * PAGE_SIZE}


@app.context_processor
def inject_nav():
    return {"active": request.path.split("/")[1] or "items"}


@app.template_filter("npc_url")
def npc_url_filter(name: str) -> str:
    return "/npcs/" + quote(name or "", safe="")


# ---------- admin ----------

def _require_admin():
    if not ADMIN_TOKEN:
        abort(404)
    if request.args.get("token") != ADMIN_TOKEN:
        abort(403)


@app.route("/admin/status")
def admin_status():
    _require_admin()
    out_dir = ROOT / "out"
    sprites = SPRITES_DIR
    files = sorted(out_dir.glob("*")) if out_dir.exists() else []
    sprite_count = sum(1 for _ in sprites.iterdir()) if sprites.exists() else 0
    db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    lines = [
        f"ROOT:         {ROOT}",
        f"DB_PATH:      {DB_PATH}  exists={DB_PATH.exists()}  size={db_size}",
        f"SPRITES_DIR:  {SPRITES_DIR}  exists={SPRITES_DIR.exists()}  count={sprite_count}",
        f"OUT listing   ({len(files)}):",
    ]
    for f in files[:30]:
        s = f.stat().st_size if f.is_file() else "-"
        lines.append(f"  {f.name:<40}  {s}")
    ver = ROOT / "out" / ".assets_version"
    if ver.exists():
        lines.append(f"assets_version: {ver.read_text().strip()[:80]}")
    return Response("\n".join(lines), mimetype="text/plain")


UPLOAD_TMP = Path("/tmp/tibiadb_upload.tgz")


@app.route("/admin/upload", methods=["POST"])
def admin_upload():
    """Upload chunked de um tar.gz.

    Uso:
      ?action=init            -> zera o arquivo temp
      ?action=chunk (body)    -> append do body no arquivo temp
      ?action=finalize        -> extrai o temp em /app/out (sobrescreve)

    Cloudflare limita bodies a ~100MB, por isso o upload vem em chunks.
    """
    _require_admin()
    action = request.args.get("action", "")
    out_dir = ROOT / "out"

    if action == "init":
        UPLOAD_TMP.parent.mkdir(parents=True, exist_ok=True)
        UPLOAD_TMP.write_bytes(b"")
        return Response("init ok\n", mimetype="text/plain")

    if action == "chunk":
        data = request.get_data(cache=False)
        if not data:
            return Response("body vazio", status=400, mimetype="text/plain")
        with UPLOAD_TMP.open("ab") as f:
            f.write(data)
        size = UPLOAD_TMP.stat().st_size
        return Response(f"chunk ok: +{len(data)} bytes, total={size}\n", mimetype="text/plain")

    if action == "finalize":
        import tarfile
        if not UPLOAD_TMP.exists():
            return Response("nada pra finalizar (chame action=init primeiro)", status=400, mimetype="text/plain")
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(UPLOAD_TMP, mode="r:gz") as tf:
                members = [m for m in tf.getmembers() if not m.name.startswith("/") and ".." not in m.name.split("/")]
                tf.extractall(out_dir, members=members, filter="data")
                n = len(members)
        except tarfile.TarError as e:
            return Response(f"tar error: {e}", status=400, mimetype="text/plain")
        total = UPLOAD_TMP.stat().st_size
        UPLOAD_TMP.unlink(missing_ok=True)
        return Response(f"ok: {n} membros extraidos em {out_dir}\ntamanho do tar.gz: {total}\n", mimetype="text/plain")

    return Response(
        "use ?action=init | ?action=chunk (POST body) | ?action=finalize",
        status=400, mimetype="text/plain",
    )


@app.route("/admin/run-pipeline")
def admin_run_pipeline():
    _require_admin()
    force = request.args.get("force") == "1"
    cmd = ["python", "scripts/pipeline.py"] + (["--force"] if force else [])
    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, timeout=900
        )
    except subprocess.TimeoutExpired as e:
        body = f"TIMEOUT apos 900s\n--- STDOUT ---\n{e.stdout or ''}\n--- STDERR ---\n{e.stderr or ''}"
        return Response(body, mimetype="text/plain", status=504)
    body = f"cmd: {' '.join(cmd)}\nreturncode: {proc.returncode}\n--- STDOUT ---\n{proc.stdout}\n--- STDERR ---\n{proc.stderr}"
    return Response(body, mimetype="text/plain", status=200 if proc.returncode == 0 else 500)


# ---------- routes: items ----------

@app.route("/")
def home():
    return redirect(url_for("items"))


@app.route("/items")
def items():
    cat = request.args.get("cat") or None
    slot = request.args.get("slot") or None
    q = (request.args.get("q") or "").strip()
    page = int(request.args.get("page", 1))

    where = ["name IS NOT NULL"]
    params: list = []
    if cat:
        where.append("market_category = ?")
        params.append(cat)
    if slot:
        where.append("slot = ?")
        params.append(slot)
    if q:
        where.append("name LIKE ?")
        params.append(f"%{q}%")
    where_sql = " AND ".join(where)

    # Dedup: agrupa por (name, sprite) — ou so por name se rotatable (4 direcoes).
    # O canonical de cada grupo e' o com metadata mais completa (market_category,
    # slot, minimum_level NOT NULL). N_variants mostra quantos ids tem o nome.
    group_key = ("name || '|' || CASE WHEN rotatable=1 THEN '' "
                 "ELSE COALESCE(main_sprite_id,'') END")
    c = db().cursor()
    (total,) = c.execute(
        f"SELECT COUNT(*) FROM ("
        f"  SELECT 1 FROM items WHERE {where_sql} GROUP BY {group_key}"
        f")", params,
    ).fetchone()
    p = paginate(total, page)
    rows = c.execute(
        f"""SELECT id, name, market_category, slot, minimum_level, main_sprite_id, n_variants
              FROM (
                SELECT *,
                  ROW_NUMBER() OVER (
                    PARTITION BY {group_key}
                    ORDER BY
                      (market_category IS NULL),
                      (slot IS NULL),
                      (minimum_level IS NULL),
                      id
                  ) AS rn,
                  COUNT(*) OVER (PARTITION BY {group_key}) AS n_variants
                FROM items WHERE {where_sql}
              ) WHERE rn = 1
             ORDER BY name LIMIT ? OFFSET ?""",
        params + [PAGE_SIZE, p["offset"]],
    ).fetchall()

    cats = c.execute(
        "SELECT market_category, COUNT(*) n FROM items WHERE name IS NOT NULL AND market_category IS NOT NULL "
        "GROUP BY market_category ORDER BY market_category"
    ).fetchall()
    slots = c.execute(
        "SELECT slot, COUNT(*) n FROM items WHERE name IS NOT NULL AND slot IS NOT NULL "
        "GROUP BY slot ORDER BY slot"
    ).fetchall()
    (no_cat,) = c.execute(
        "SELECT COUNT(*) FROM items WHERE name IS NOT NULL AND market_category IS NULL"
    ).fetchone()

    # Descobre quais items da pagina tem GIF (fast: disk check)
    gif_ids = set()
    if GIFS_DIR.exists():
        for r in rows:
            if (GIFS_DIR / f"item_{r['id']}.gif").exists():
                gif_ids.add(r["id"])

    return render_template("items.html",
                           rows=rows, page=p, cats=cats, slots=slots,
                           no_cat=no_cat, cat=cat, slot=slot, q=q,
                           gif_ids=gif_ids)


@app.route("/items/<int:item_id>")
def item_detail(item_id: int):
    c = db().cursor()
    item = c.execute("SELECT * FROM items WHERE id = ?", [item_id]).fetchone()
    if not item:
        abort(404)
    npcs = c.execute(
        "SELECT npc_name, location, sale_price, buy_price FROM npc_sales WHERE item_id = ? "
        "ORDER BY npc_name", [item_id]
    ).fetchall()
    sprites = c.execute(
        "SELECT sprite_id, position FROM item_sprites WHERE item_id = ? "
        "ORDER BY position LIMIT 24", [item_id]
    ).fetchall()
    has_gif = item_has_gif(item_id)
    return render_template("item.html", item=item, npcs=npcs, sprites=sprites, has_gif=has_gif)


# ---------- routes: NPCs ----------

@app.route("/npcs")
def npcs():
    q = (request.args.get("q") or "").strip()
    c = db().cursor()
    if q:
        rows = c.execute(
            "SELECT npc_name, COUNT(DISTINCT item_id) n_items, "
            "SUM(CASE WHEN sale_price IS NOT NULL THEN 1 ELSE 0 END) n_sells, "
            "SUM(CASE WHEN buy_price IS NOT NULL THEN 1 ELSE 0 END) n_buys "
            "FROM npc_sales WHERE npc_name LIKE ? "
            "GROUP BY npc_name ORDER BY npc_name",
            [f"%{q}%"],
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT npc_name, COUNT(DISTINCT item_id) n_items, "
            "SUM(CASE WHEN sale_price IS NOT NULL THEN 1 ELSE 0 END) n_sells, "
            "SUM(CASE WHEN buy_price IS NOT NULL THEN 1 ELSE 0 END) n_buys "
            "FROM npc_sales GROUP BY npc_name ORDER BY npc_name"
        ).fetchall()
    return render_template("npcs.html", rows=rows, q=q)


@app.route("/npcs/<path:name>")
def npc_detail(name: str):
    c = db().cursor()
    sells = c.execute(
        "SELECT i.id, i.name, i.main_sprite_id, i.market_category, ns.sale_price, ns.location "
        "FROM npc_sales ns JOIN items i ON ns.item_id = i.id "
        "WHERE ns.npc_name = ? AND ns.sale_price IS NOT NULL ORDER BY i.name",
        [name],
    ).fetchall()
    buys = c.execute(
        "SELECT i.id, i.name, i.main_sprite_id, i.market_category, ns.buy_price, ns.location "
        "FROM npc_sales ns JOIN items i ON ns.item_id = i.id "
        "WHERE ns.npc_name = ? AND ns.buy_price IS NOT NULL ORDER BY i.name",
        [name],
    ).fetchall()
    locations = sorted({r["location"] for r in sells + buys if r["location"]})
    if not sells and not buys:
        abort(404)
    return render_template("npc.html", name=name, sells=sells, buys=buys, locations=locations)


# ---------- routes: outfits ----------

@app.route("/outfits")
def outfits():
    kind = request.args.get("kind") or None
    page = int(request.args.get("page", 1))

    all_outfits = load_outfits()
    filtered = [o for o in all_outfits if not kind or o.get("kind") == kind]

    kind_counts: dict[str, int] = {}
    for o in all_outfits:
        k = o.get("kind") or ""
        kind_counts[k] = kind_counts.get(k, 0) + 1
    kinds = [{"kind": k, "n": n} for k, n in sorted(kind_counts.items(), key=lambda kv: -kv[1])]

    total = len(filtered)
    p = paginate(total, page)
    rows = filtered[p["offset"]: p["offset"] + PAGE_SIZE]
    # Preferimos GIF dir2 (sul, olhando pro jogador) no card
    gif_dir_by_outfit: dict[int, int | None] = {}
    if GIFS_DIR.exists():
        for r in rows:
            oid = r["id"]
            for d in (2, 0, 1, 3):
                if (GIFS_DIR / f"outfit_{oid}_dir{d}.gif").exists():
                    gif_dir_by_outfit[oid] = d
                    break
            else:
                gif_dir_by_outfit[oid] = None
    return render_template("outfits.html", rows=rows, page=p, kinds=kinds, kind=kind,
                           gif_dir_by_outfit=gif_dir_by_outfit)


@app.route("/outfits/<int:outfit_id>")
def outfit_detail(outfit_id: int):
    o = next((x for x in load_outfits() if x.get("id") == outfit_id), None)
    if not o:
        abort(404)
    sprite_ids = o.get("sprite_ids") or []
    sprites = [{"sprite_id": sid, "position": i} for i, sid in enumerate(sprite_ids[:64])]
    # Quais direcoes tem GIF gerado?
    dirs = []
    if GIFS_DIR.exists():
        for d in range(max(1, o.get("pattern_width") or 1)):
            if (GIFS_DIR / f"outfit_{outfit_id}_dir{d}.gif").exists():
                dirs.append(d)
    # Criaturas que usam esse outfit
    monsters = []
    try:
        monsters = db().execute(
            "SELECT race, name FROM monsters WHERE outfit_id = ? ORDER BY name",
            [outfit_id],
        ).fetchall()
    except sqlite3.OperationalError:
        pass  # DB antiga sem tabela monsters
    return render_template("outfit.html", outfit=o, sprites=sprites,
                           gif_dirs=dirs, monsters=monsters)


# ---------- routes: monsters ----------

@app.route("/monsters")
def monsters_list():
    q = (request.args.get("q") or "").strip()
    c = db().cursor()
    try:
        if q:
            rows = c.execute(
                "SELECT m.race, m.name, m.outfit_id, o.preview_sprite_id "
                "FROM monsters m LEFT JOIN outfits o ON o.id = m.outfit_id "
                "WHERE m.name LIKE ? ORDER BY m.name",
                [f"%{q}%"],
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT m.race, m.name, m.outfit_id, o.preview_sprite_id "
                "FROM monsters m LEFT JOIN outfits o ON o.id = m.outfit_id "
                "ORDER BY m.name"
            ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    # Resolve GIF por outfit_id (dir2 = front). Usa direct disk check.
    gif_dir_by_outfit: dict[int, int | None] = {}
    if GIFS_DIR.exists():
        for r in rows:
            oid = r["outfit_id"]
            if oid is None or oid in gif_dir_by_outfit:
                continue
            for d in (2, 0, 1, 3):
                if (GIFS_DIR / f"outfit_{oid}_dir{d}.gif").exists():
                    gif_dir_by_outfit[oid] = d
                    break
            else:
                gif_dir_by_outfit[oid] = None
    return render_template("monsters.html", rows=rows, q=q,
                           gif_dir_by_outfit=gif_dir_by_outfit)


@app.route("/monsters/<path:name>")
def monster_detail(name: str):
    c = db().cursor()
    try:
        m = c.execute(
            "SELECT m.*, o.preview_sprite_id, o.pattern_width, o.kind "
            "FROM monsters m LEFT JOIN outfits o ON o.id = m.outfit_id "
            "WHERE m.name = ? LIMIT 1", [name]
        ).fetchone()
    except sqlite3.OperationalError:
        abort(404)
    if not m:
        abort(404)
    dirs = []
    if m["outfit_id"] and GIFS_DIR.exists():
        for d in range(max(1, m["pattern_width"] or 1)):
            if (GIFS_DIR / f"outfit_{m['outfit_id']}_dir{d}.gif").exists():
                dirs.append(d)
    return render_template("monster.html", m=m, gif_dirs=dirs)


# ---------- routes: map ----------

@app.route("/map")
def map_view():
    c = db().cursor()
    # bbox do mundo
    try:
        bbox = c.execute(
            "SELECT MIN(top_left_x), MAX(top_left_x+fields_width), "
            "MIN(top_left_y), MAX(top_left_y+fields_height), "
            "MIN(top_left_z), MAX(top_left_z) FROM map_files"
        ).fetchone()
        areas = c.execute(
            "SELECT area_id, name, area_type, label_x, label_y, label_z "
            "FROM areas WHERE area_type='AREA' ORDER BY name"
        ).fetchall()
    except sqlite3.OperationalError:
        bbox = (32000, 34000, 31000, 33000, 0, 7)
        areas = []
    return render_template("map.html", bbox=bbox, areas=areas)


@app.route("/api/map/tiles/<int:z>")
def api_map_tiles(z: int):
    """Retorna lista de tiles disponiveis pra um piso (z)."""
    kind = request.args.get("kind", "satellite_32")
    # Mapping de kind -> (file_type, scale_factor)
    kind_spec = {
        "satellite_16": ("SATELLITE", 0.0625),   # zoom out
        "satellite_32": ("SATELLITE", 0.03125),  # zoom medio
        "satellite_64": ("SATELLITE", 0.015625), # zoom in (mais detalhe por pixel)
        "minimap_32":   ("MINIMAP",   0.03125),
        "minimap_64":   ("MINIMAP",   0.015625),
    }
    spec = kind_spec.get(kind)
    if not spec:
        from flask import jsonify
        return jsonify({"kind": kind, "z": z, "tiles": []})
    file_type, scale = spec
    c = db().cursor()
    rows = c.execute(
        "SELECT file_name, top_left_x, top_left_y, fields_width, fields_height "
        "FROM map_files WHERE file_type=? AND top_left_z=? AND scale_factor=?",
        [file_type, z, scale],
    ).fetchall()
    from flask import jsonify
    import re as _re
    tiles = []
    for r in rows:
        png = r["file_name"].replace(".bmp.lzma", ".png")
        m = _re.match(r"(minimap|satellite)-(\d+)-(\d{4})-(\d{4})-(\d{2})-.*", r["file_name"])
        if m:
            png = f"{m.group(3)}-{m.group(4)}-{m.group(5)}.png"
        tiles.append({
            "name": png,
            "x": r["top_left_x"], "y": r["top_left_y"],
            "w": r["fields_width"], "h": r["fields_height"],
        })
    return jsonify({"kind": kind, "z": z, "tiles": tiles})


@app.route("/api/map/npcs")
def api_map_npcs():
    """NPCs com coord, filtrado por piso opcional."""
    from flask import jsonify
    z = request.args.get("z", type=int)
    c = db().cursor()
    if z is not None:
        rows = c.execute(
            "SELECT npc_name, x, y, z, area_name FROM npc_locations WHERE z=? ORDER BY npc_name",
            [z],
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT npc_name, x, y, z, area_name FROM npc_locations ORDER BY npc_name"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------- search ----------

@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return redirect(url_for("items"))
    c = db().cursor()
    items_r = c.execute(
        "SELECT id, name, market_category, main_sprite_id FROM items "
        "WHERE name LIKE ? ORDER BY name LIMIT 50",
        [f"%{q}%"],
    ).fetchall()
    npcs_r = c.execute(
        "SELECT DISTINCT npc_name FROM npc_sales WHERE npc_name LIKE ? "
        "ORDER BY npc_name LIMIT 50",
        [f"%{q}%"],
    ).fetchall()
    monsters_r = []
    try:
        monsters_r = c.execute(
            "SELECT m.race, m.name, o.preview_sprite_id "
            "FROM monsters m LEFT JOIN outfits o ON o.id = m.outfit_id "
            "WHERE m.name LIKE ? ORDER BY m.name LIMIT 50",
            [f"%{q}%"],
        ).fetchall()
    except sqlite3.OperationalError:
        pass
    return render_template("search.html", q=q, items=items_r, npcs=npcs_r,
                           monsters=monsters_r)


# ---------- entry ----------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    print(f"[>] DB:      {DB_PATH}")
    print(f"[>] Sprites: {SPRITES_DIR}")
    print(f"[>] Abra:    http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
