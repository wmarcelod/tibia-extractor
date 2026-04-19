#!/usr/bin/env python3
"""Exibe alguns itens conhecidos para validar a extracao."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
items = json.loads((ROOT / "out" / "items.json").read_text(encoding="utf-8"))
by_name = {i["name"].lower(): i for i in items if i["name"]}

targets = [
    "magic longsword",
    "great health potion",
    "boots of haste",
    "gold coin",
    "crystal coin",
    "demon",  # pode nao existir (e outfit/creature, nao item)
    "might ring",
    "rope",
    "stealth ring",
    "backpack",
]

for t in targets:
    item = by_name.get(t)
    if not item:
        print(f"--- '{t}' NAO ENCONTRADO como item ---")
        continue
    print(f"\n=== {item['name']} (id={item['id']}) ===")
    print(json.dumps({
        k: v for k, v in item.items()
        if v not in (None, False, [], 0) or k in ("stackable",)
    }, indent=2, ensure_ascii=False))
