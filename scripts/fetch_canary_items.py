#!/usr/bin/env python3
"""
fetch_canary_items.py

Baixa items.xml do opentibiabr/canary (GPL-2.0, 17k items, atualizado por
patch CipSoft) e gera out/canary_items.json com stats numericos achatados:
  armor, attack, defense, weight, level, vocation, skilldist/skillsword/...,
  absorbpercent_ice/fire/energy/..., imbuementslot, magiclevelpoints, etc.

Esses stats NAO existem nos arquivos do cliente Tibia (confirmado), mas sao
mantidos pela community OT brasileira (canary) por patch e batem 100% com
in-game (validado em embrace of nature, shiny blade, etc).

Atribuicao requerida: opentibiabr/canary (GPL-2.0).
"""
from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
URL = "https://raw.githubusercontent.com/opentibiabr/canary/main/data/items/items.xml"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    raw_path = OUT / "canary_items.xml"

    print(f"[+] Baixando {URL}")
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _proxy_helper import proxies_from_env
    from curl_cffi import requests
    # GitHub raw nao bate Cloudflare; proxy so se setado
    r = requests.get(URL, impersonate="chrome", timeout=120, proxies=proxies_from_env())
    r.raise_for_status()
    raw_path.write_text(r.text, encoding="utf-8")
    print(f"[+] Salvo: {raw_path.relative_to(ROOT)} ({len(r.text)/1024/1024:.1f} MB)")

    root = ET.fromstring(r.text)
    items: list[dict] = []
    for it in root.findall("item"):
        # itens podem ter id ou fromid+toid (range). Pra range, gera 1 row por id.
        ids: list[int] = []
        if it.get("id"):
            ids.append(int(it.get("id")))
        elif it.get("fromid") and it.get("toid"):
            for i in range(int(it.get("fromid")), int(it.get("toid")) + 1):
                ids.append(i)
        else:
            continue
        name = it.get("name") or None
        article = it.get("article") or None
        plural = it.get("plural") or None
        # achata atributos
        attrs: dict[str, str] = {}
        for a in it.findall("attribute"):
            k = (a.get("key") or "").lower()
            v = a.get("value")
            if k and v is not None:
                attrs[k] = v
        for sid in ids:
            items.append({
                "id": sid,
                "name": name,
                "article": article,
                "plural": plural,
                **attrs,
            })

    out_json = OUT / "canary_items.json"
    out_json.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[+] Items: {len(items)}  ({out_json.stat().st_size/1024/1024:.1f} MB)")
    # stats
    from collections import Counter
    keys = Counter()
    for it in items:
        for k in it.keys():
            if k not in {"id", "name", "article", "plural"}:
                keys[k] += 1
    print(f"[+] Top 20 attribute keys (de {len(keys)} distintas):")
    for k, n in keys.most_common(20):
        print(f"    {k:30s} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
