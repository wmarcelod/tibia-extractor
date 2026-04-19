#!/usr/bin/env python3
"""
pipeline.py

Fluxo:
  1. Ve a versao do Tibia remota (assets.json em static.tibia.com)
  2. Compara com a ultima versao processada (out/.assets_version)
  3. Se igual: sai, nada a fazer.
  4. Se diferente (ou nunca rodou):
     - Baixa todos os assets numa pasta de trabalho
     - Roda decode_items / decode_outfits / extract_sprites / build_final
       apontando pra a pasta via env var TIBIA_ASSETS_DIR
     - Grava a versao nova em out/.assets_version
     - Apaga a pasta assets/ (use --keep-assets pra preservar pra debug/testes)

Uso:
  python pipeline.py               # pipeline completo (apaga assets/ no fim)
  python pipeline.py --force       # forca reprocessamento mesmo sem mudanca
  python pipeline.py --keep-assets # preserva assets/ (pra debug/testes)
  python pipeline.py --workers 16
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from curl_cffi import requests

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
OUT = ROOT / "out"
LEGACY_ASSETS = ROOT / "assets"
VERSION_FILE = OUT / ".assets_version"

ASSETS_BASE = "https://static.tibia.com/launcher/assets-current/"
VERSION_URL = ASSETS_BASE + "assets.json.sha256"


def run(cmd: list[str], label: str, env: dict | None = None) -> None:
    print(f"\n{'='*70}\n  {label}\n  $ {' '.join(cmd)}\n{'='*70}")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=SCRIPTS, env=env)
    dt = time.time() - t0
    if r.returncode != 0:
        sys.exit(f"[!] Etapa falhou (exit {r.returncode}) depois de {dt:.1f}s: {label}")
    print(f"[OK] {label}  ({dt:.1f}s)")


def fetch_remote_version() -> str:
    r = requests.get(VERSION_URL, impersonate="chrome", timeout=30)
    r.raise_for_status()
    return r.text.strip()


def read_local_version() -> str | None:
    if VERSION_FILE.exists():
        v = VERSION_FILE.read_text(encoding="utf-8").strip()
        return v or None
    legacy_sha = LEGACY_ASSETS / "assets.json.sha256"
    if legacy_sha.exists():
        v = legacy_sha.read_text(encoding="utf-8").strip()
        return v or None
    return None


def outputs_present() -> bool:
    return (OUT / "items.db").exists() and (OUT / "items.json").exists()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="Reprocessa mesmo se versao local == remota")
    ap.add_argument("--keep-assets", action="store_true",
                    help="Preserva a pasta assets/ depois de processar (default: apaga)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    t_start = time.time()

    print("[>] Consultando versao remota em static.tibia.com...")
    remote = fetch_remote_version()
    local = read_local_version()
    print(f"[>] Remote: {remote}   Local: {local or '(nenhum)'}")

    if not args.force and local == remote and outputs_present():
        print(f"\n[=] Ja temos a versao {remote} processada. Nada a fazer.")
        print(f"    (use --force pra reprocessar)")
        return 0

    # Pasta de trabalho: sempre 'assets/' dentro do projeto.
    # Assim, com skip-if-hash-matches, reruns nao rebaixam nada.
    # (So e apagada no fim se --delete-assets for passado)
    tmp_assets = LEGACY_ASSETS
    if tmp_assets.exists() and any(tmp_assets.iterdir()):
        print(f"[>] Reusando pasta existente: {tmp_assets}")
    else:
        print(f"[>] Pasta de assets: {tmp_assets}")

    success = False
    try:
        # 1. Download (com skip-if-hash-matches interno)
        run(
            [sys.executable, "download_tibia.py",
             "--target", "assets",
             "--out", str(tmp_assets),
             "--workers", str(args.workers)],
            "1/4 Baixando assets do Tibia (skip arquivos com hash correto)",
        )

        # Auto-detecta o layout: o downloader cria tmp_assets/assets/<files>,
        # mas um bootstrap manual pode ter colocado os arquivos direto em tmp_assets/.
        nested = tmp_assets / "assets"
        if (nested / "catalog-content.json").exists():
            effective = nested
        else:
            effective = tmp_assets
        env = {**os.environ, "TIBIA_ASSETS_DIR": str(effective)}
        print(f"[>] TIBIA_ASSETS_DIR = {effective}")

        run([sys.executable, "decode_items.py"],
            "2/4 Decodificando items (appearances.dat -> items.json/csv)",
            env=env)

        run([sys.executable, "decode_outfits.py"],
            "3/6 Decodificando outfits (criaturas/NPCs/player outfits)",
            env=env)

        run([sys.executable, "decode_staticdata.py"],
            "4/6 Decodificando staticdata (monsters + achievements)",
            env=env)

        run([sys.executable, "extract_sprites.py"],
            "5a/6 Extraindo sprites PNG individuais",
            env=env)

        run([sys.executable, "gen_gifs.py"],
            "5b/6 Gerando GIFs animados por direcao",
            env=env)

        run([sys.executable, "build_final.py"],
            "6/6 Gerando SQLite + CSV final (items + npcs + monsters + imagens)",
            env=env)

        VERSION_FILE.write_text(remote, encoding="utf-8")
        print(f"\n[+] Versao registrada em {VERSION_FILE.relative_to(ROOT)}: {remote}")
        success = True

    finally:
        if success and not args.keep_assets:
            print(f"\n[>] Apagando pasta de assets: {tmp_assets}")
            shutil.rmtree(tmp_assets, ignore_errors=True)
        elif not success:
            print(f"\n[!] Falha no pipeline. Mantendo {tmp_assets} pra debug.")
        else:
            print(f"\n[>] --keep-assets: mantendo {tmp_assets}")

    dt = time.time() - t_start
    print(f"\n{'='*70}\n  PIPELINE COMPLETO em {dt:.1f}s ({dt/60:.1f} min)\n{'='*70}")
    print(f"  Versao processada: {remote}")
    print(f"  Outputs em: {OUT.resolve()}")
    print("  Principais:")
    for f in ["items.db", "items_named.csv", "items.json",
              "outfits.csv", "sprite_index.json"]:
        p = OUT / f
        if p.exists():
            print(f"   - {f:25} ({p.stat().st_size/1024/1024:.1f} MB)")
    sprites_dir = OUT / "sprites"
    if sprites_dir.exists():
        n = sum(1 for _ in sprites_dir.iterdir())
        print(f"   - sprites/                 ({n} PNGs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
