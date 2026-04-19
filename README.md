# tibia-extractor

Pipeline em Python que baixa os assets oficiais do Tibia direto do servidor da CipSoft
(`static.tibia.com/launcher/assets-current/`) e gera um **banco de dados + CSVs + PNGs**
com todos os itens, criaturas, NPCs e outfits do jogo — sem abrir o client.

O que você faz com esses dados é com você.

---

## O que ele produz

Tudo dentro de `out/`:

| Arquivo | Conteúdo |
|---|---|
| `items.db` | SQLite com as tabelas: `items`, `npc_sales`, `item_sprites`, `outfits`, `outfit_sprites` |
| `items.json` | JSON completo de todos os objetos (~42k entradas, 44 MB) |
| `items_named.csv` | Os ~8.8k itens com nome — ideal para importar no fansite |
| `items_all.csv` | Todas as ~42k entradas (inclui tiles de mapa, sem nome) |
| `outfits.csv` / `outfits.json` | Criaturas, NPCs e player outfits (1.4k entradas) |
| `sprite_index.json` | Mapa `sprite_id` → caminho do PNG |
| `sprites/sprite_<id>.png` | Todos os sprites individuais (~214k PNGs com transparência aplicada) |
| `.assets_version` | SHA-256 da última versão processada |

---

## O fluxo

```
  static.tibia.com           scripts/pipeline.py           out/
  ───────────────            ──────────────────            ────
  assets.json.sha256   ─→    compara com
                              out/.assets_version
                                     │
                          igual?──Sim──→ sai, nada a fazer
                                     │
                                     Não
                                     │
                          baixa tudo para assets/
                          (pula arquivos com hash correto)
                                     │
                          decode_items.py   (appearances.dat → items)
                          decode_outfits.py (outfits)
                          extract_sprites.py (sprites-*.bmp.lzma → PNGs)
                          build_final.py    (SQLite + CSV final)
                                     │
                          grava versão em out/.assets_version
```

A pasta `assets/` com os arquivos brutos do jogo (~2 GB) é **apagada ao final**
por padrão — só os outputs em `out/` e o marcador de versão ficam. Se quiser
preservar os arquivos brutos para inspeção ou debug, passe `--keep-assets`.

Nas rodadas seguintes, se a versão do Tibia não mudou, o script sai em cerca de
1 segundo sem baixar nada. Se a versão mudou, o downloader só puxa os arquivos que
realmente mudaram (hash por hash), e o `extract_sprites.py` só fatia as sprite sheets
novas (skip-if-exists por PNG).

---

## Setup

Precisa de Python 3.10+ (usa type hints novos).

```bash
git clone https://github.com/wmarcelod/tibia-extractor.git
cd tibia-extractor
pip install -r requirements.txt
```

---

## Uso

```bash
# Pipeline completo (primeira vez: ~10 min; rodadas seguintes sem mudança: ~1s)
python scripts/pipeline.py

# Força reprocessamento mesmo se a versão local == remota
python scripts/pipeline.py --force

# Preserva a pasta assets/ depois de processar (default: apaga)
python scripts/pipeline.py --keep-assets

# Mais workers no download (default 8)
python scripts/pipeline.py --workers 16
```

Cada script também roda sozinho se você quiser depurar uma etapa:

```bash
# Linux / macOS
TIBIA_ASSETS_DIR=/path/to/assets python scripts/decode_items.py
TIBIA_ASSETS_DIR=/path/to/assets python scripts/extract_sprites.py
python scripts/build_final.py   # usa os JSONs já gerados em out/
```

No Windows (PowerShell):
```powershell
$env:TIBIA_ASSETS_DIR="C:\path\to\assets"; python scripts\decode_items.py
```

---

## Automação via GitHub Actions

O workflow `.github/workflows/daily-pipeline.yml` roda o pipeline no runner do
GitHub (cujo IP passa pelo Cloudflare do `static.tibia.com`), empacota `out/`
em tar.gz, quebra em pedaços de 50 MB e faz upload chunked para o viewer
(`/admin/upload`). Isso cobre o caso em que a VPS do viewer bate 403 ao tentar
baixar os assets direto.

### Secrets necessários

Configure no repositório (`Settings -> Secrets and variables -> Actions`) ou via
`gh`:

```bash
# obrigatório — token do endpoint /admin/upload do viewer
gh secret set TIBIADB_ADMIN_TOKEN -b "<token>"

# opcional — host do viewer (default: tibiadb.marcelod.com.br)
gh secret set TIBIADB_HOST -b "tibiadb.marcelod.com.br"
```

### Quando roda

- **Agendado**: todo dia por volta de **10:30 em Europe/Berlin**. Como o GitHub
  Actions só aceita cron em UTC, o workflow tem dois schedules — `30 8 * * *`
  (cobre CEST/verão, UTC+2) e `30 9 * * *` (cobre CET/inverno, UTC+1). Um dos
  dois vai disparar no horário certo dependendo do DST; o outro dispara 1h
  antes/depois. Como o pipeline dá early-exit quando a versão local == remota,
  o disparo redundante custa ~1s.
- **Manual**: a qualquer momento via `workflow_dispatch`.

### Como disparar manualmente

```bash
gh workflow run daily-pipeline.yml
gh run watch
```

---

## Como funciona cada etapa

### 1. `download_tibia.py` — baixa os assets

Reimplementa o auto-updater do client oficial do Tibia. Fluxo:

1. `GET assets.json` com a lista de arquivos (~6800 entradas) e seus hashes SHA-256.
2. Para cada arquivo, compara com o que já tem localmente — **pula se o hash bate**.
3. Baixa o `.lzma` faltante, valida hash packed e unpacked.
4. Descomprime. O Tibia usa um formato LZMA ALONE custom em alguns arquivos com
   header de 32 bytes + campo de tamanho corrompido; testamos 5 variantes até
   uma funcionar (`cip`, `alone_patched`, `alone_raw`, `xz`, `auto`).

Usa `curl_cffi` com impersonation do Chrome para passar pelo Cloudflare.

### 2. `decode_items.py` — parse do `appearances.dat`

O `appearances.dat` é um arquivo protobuf. Os schemas `.proto` estão em `proto/`.
Cada `Appearance` tem:

- `id`, `name`, `description`
- `flags.market` — categoria, nível mínimo, profissão
- `flags.npcsaledata` — quem vende/compra, localização, preço
- `flags.clothes.slot` — HELMET/AMULET/ARMOR/etc
- `frame_group[].sprite_info.sprite_id` — IDs dos sprites para renderizar

Gera `items.json`, `items.csv`, e também `outfits.json`/`effects.json`/`missiles.json`.

### 3. `decode_outfits.py` — criaturas, NPCs, player outfits

Classifica cada outfit via heurística sobre `frame_group` + `layers`:

- `player_outfit` (~381): 2 layers (base + template colorizável) + tem animação de movimento
- `creature_or_npc` (~1055): 1 layer + tem animação de movimento
- `static` / `other`: demais

**IMPORTANTE:** os **nomes** de criaturas e NPCs **não estão no client** — são
enviados pelo servidor em runtime. Este script extrai ID + sprites + metadata
estrutural. Para ter o nome você precisa de outra fonte (ex.: TibiaWiki).

### 4. `extract_sprites.py` — sprites-*.bmp.lzma → PNGs

Descomprime cada sprite sheet (LZMA custom com header no offset 0x20), fatia
em tiles individuais e salva como PNG com transparência (colorkey magenta
`#FF00FF`). Roda em `ProcessPoolExecutor` com 8 workers.

Skip-if-exists: se `sprite_<meio-do-range>.png` já existe, assume que a sheet
foi processada e pula.

### 5. `build_final.py` — cross-reference

Lê `items.json`, adiciona `image_file` / `image_exists` cruzando com os PNGs
em `out/sprites/`, e monta o SQLite final com índices em `name`, `market_category`,
`npc_name` e `item_id`.

---

## Exemplos de query no SQLite

```sql
-- itens com nome do market
SELECT name, market_category, minimum_level, image_file
  FROM items WHERE name = 'Magic Longsword';

-- tudo que NPC X vende
SELECT i.name, ns.sale_price
  FROM items i JOIN npc_sales ns ON i.id = ns.item_id
 WHERE ns.npc_name = 'Baltim' AND ns.sale_price IS NOT NULL;

-- armas de 2-hand para knight acima do nível 100
SELECT name, minimum_level, market_category
  FROM items
 WHERE slot = 'LEFT_HAND'
   AND professions LIKE '%KNIGHT%'
   AND minimum_level > 100
 ORDER BY minimum_level;
```

---

## Observações

- Os arquivos `proto/*_pb2.py` foram gerados com `grpc_tools.protoc` a partir
  dos `.proto` incluídos. Se você quiser regenerar:
  ```bash
  python -m grpc_tools.protoc --proto_path=proto --python_out=proto proto/*.proto
  ```
- Os outputs (`out/`) **não** estão no repo — cada instalação roda o pipeline
  e gera os seus.

---

## Licença

MIT no código do extrator. Os assets do Tibia em si são propriedade da CipSoft GmbH.
