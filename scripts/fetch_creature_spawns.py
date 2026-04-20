#!/usr/bin/env python3
"""
fetch_creature_spawns.py

Baixa otservbr-monster.xml do opentibiabr/canary (MIT) e gera:
  out/creature_spawns.json — lista de spawns com coords
  out/otservbr-monster.xml — copia raw

Cada spawn = 1 placement de monstro com (creature, x, y, z, spawntime, radius_center).

opentibiabr/canary mantem este XML como dataset comunitario, herdado de anos
de packet-sniffing/data-mining do mundo real do Tibia. MIT license.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
URL = "https://raw.githubusercontent.com/opentibiabr/canary/main/data-otservbr-global/world/otservbr-monster.xml"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    raw_path = OUT / "otservbr-monster.xml"

    print(f"[+] Baixando {URL}")
    import os
    from curl_cffi import requests
    proxy_url = os.environ.get("WEBSHARE_PROXY") or os.environ.get("HTTPS_PROXY")
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    r = requests.get(URL, impersonate="chrome", timeout=120, proxies=proxies)
    r.raise_for_status()
    raw_path.write_text(r.text, encoding="utf-8")
    print(f"[+] Salvo: {raw_path.relative_to(ROOT)} ({len(r.text)/1024/1024:.1f} MB)")

    root = ET.fromstring(r.text)
    spawns: list[dict] = []
    creatures: dict[str, int] = {}
    for spawn_node in root:
        cx = int(spawn_node.get("centerx") or 0)
        cy = int(spawn_node.get("centery") or 0)
        cz = int(spawn_node.get("centerz") or 0)
        radius = int(spawn_node.get("radius") or 0)
        for m in spawn_node:
            name = m.get("name")
            if not name:
                continue
            x = cx + int(m.get("x") or 0)
            y = cy + int(m.get("y") or 0)
            z = int(m.get("z") or cz)
            spawns.append({
                "creature": name,
                "x": x, "y": y, "z": z,
                "spawntime": int(m.get("spawntime") or 0),
                "center_x": cx, "center_y": cy, "radius": radius,
            })
            creatures[name] = creatures.get(name, 0) + 1

    out_json = OUT / "creature_spawns.json"
    out_json.write_text(
        json.dumps(spawns, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"[+] Spawns: {len(spawns)}  unique creatures: {len(creatures)}")
    print(f"[+] Top 10 mais spawns:")
    for c, n in sorted(creatures.items(), key=lambda kv: -kv[1])[:10]:
        print(f"    {c:30s} {n}")
    print(f"[+] Salvo: {out_json.relative_to(ROOT)} ({out_json.stat().st_size/1024/1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
