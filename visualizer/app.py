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

    c = db().cursor()
    (total,) = c.execute(f"SELECT COUNT(*) FROM items WHERE {where_sql}", params).fetchone()
    p = paginate(total, page)
    rows = c.execute(
        f"""SELECT id, name, market_category, slot, minimum_level, main_sprite_id
              FROM items WHERE {where_sql}
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

    return render_template("items.html",
                           rows=rows, page=p, cats=cats, slots=slots,
                           no_cat=no_cat, cat=cat, slot=slot, q=q)


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
    return render_template("item.html", item=item, npcs=npcs, sprites=sprites)


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
    return render_template("outfits.html", rows=rows, page=p, kinds=kinds, kind=kind)


@app.route("/outfits/<int:outfit_id>")
def outfit_detail(outfit_id: int):
    o = next((x for x in load_outfits() if x.get("id") == outfit_id), None)
    if not o:
        abort(404)
    sprite_ids = o.get("sprite_ids") or []
    sprites = [{"sprite_id": sid, "position": i} for i, sid in enumerate(sprite_ids[:64])]
    return render_template("outfit.html", outfit=o, sprites=sprites)


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
    return render_template("search.html", q=q, items=items_r, npcs=npcs_r)


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
