"""
BCRA Scraper - Comunicaciones Por Tipo y Fecha

Estrategia:
- Usa el endpoint POST /buscador-por-tipo-y-fecha con rango dinámico (últimos 30 días)
- Itera sobre TIPOS: A, B, C, P
- Extrae `circular` del campo asunto via regex
- Deduplica via archivo seen_uids.txt: persiste entre reinicios del container
- Envía a Loki con timestamp = NOW (Grafana Cloud rechaza timestamps viejos)
  La fecha real de la comunicación viaja como stream label `fecha` y en el JSON
"""

import os
import time
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from loki_sender import LokiSender

# ── Logger ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/app/logs/scraper.log"),
    ],
)
log = logging.getLogger("bcra-scraper")

# ── Constantes ────────────────────────────────────────────────────────────────
BCRA_BASE_URL   = "https://www.bcra.gob.ar"
BCRA_SEARCH_URL = f"{BCRA_BASE_URL}/buscador-por-tipo-y-fecha/"
CACHE_FILE      = Path("/app/data/seen_uids.txt")
PAGE_SIZE       = 30
DAYS_WINDOW     = 30

TIPOS = ["A", "B", "C", "P"]

CIRCULARES = [
    "CAMCO", "CAMEX", "CIRMO", "CONAU", "COPEX", "CREFI",
    "LISOL", "MICOF", "OPASI", "OPRAC", "REFEX", "REMON",
    "RUNOR", "SEPEX", "SERVI", "SINAP", "TINAC",
]

CIRCULAR_RE = re.compile(
    r"(?i)\bCircular\s+(" + "|".join(CIRCULARES) + r")\b"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "Referer": BCRA_BASE_URL,
    "Content-Type": "application/x-www-form-urlencoded",
}


# ── Cache de UIDs (archivo plano) ─────────────────────────────────────────────

def load_seen_uids() -> set:
    """Carga UIDs ya enviados a Loki desde el archivo de caché."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not CACHE_FILE.exists():
        log.info("Cache vacío — primer arranque")
        return set()
    uids = set(CACHE_FILE.read_text().splitlines())
    log.info(f"Cache cargado: {len(uids)} UIDs previos")
    return uids


def save_new_uids(new_uids: list) -> None:
    """Agrega los UIDs nuevos al archivo de caché."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_FILE.open("a") as f:
        for uid in new_uids:
            f.write(uid + "\n")


# ── Scraping ──────────────────────────────────────────────────────────────────

def extract_circular(asunto: str) -> str:
    m = CIRCULAR_RE.search(asunto)
    return m.group(1).upper() if m else ""


def fetch_page(
    session: requests.Session,
    tipo: str,
    fecha_desde: str,
    fecha_hasta: str,
    page: int,
) -> list[dict]:
    data = {
        "tipo":           tipo,
        "fecha_desde":    fecha_desde,
        "fecha_hasta":    fecha_hasta,
        "paginaabsoluta": page,
    }
    try:
        resp = session.post(BCRA_SEARCH_URL, data=data, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"Error tipo={tipo} pág {page}: {e}")
        return []
    return parse_results(resp.text)


def parse_results(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="tabla-rowcolspan-int") or soup.find("table")
    if not table:
        return []
    results = []
    for row in table.find_all("tr")[1:]:
        cols = row.find_all("td")
        if len(cols) < 3:
            continue
        try:
            com = extract_row(cols)
            if com:
                results.append(com)
        except Exception as e:
            log.warning(f"Error parseando fila: {e}")
    return results


def extract_row(cols) -> Optional[dict]:
    fecha_str = cols[0].get_text(strip=True)
    try:
        if "-" in fecha_str:
            fecha_iso = datetime.strptime(fecha_str, "%Y-%m-%d").strftime("%Y-%m-%d")
        else:
            fecha_iso = datetime.strptime(fecha_str, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        fecha_iso = fecha_str

    tipo_numero_raw = cols[1].get_text(strip=True).upper()
    link_tag = cols[1].find("a")
    link_url = ""
    if link_tag and link_tag.get("href"):
        href = link_tag["href"]
        link_url = href if href.startswith("http") else f"{BCRA_BASE_URL}{href}"

    m = re.match(r"^([A-Z]+)(\d+)$", tipo_numero_raw)
    if not m:
        return None
    tipo   = m.group(1)
    numero = m.group(2)

    asunto    = cols[2].get_text(strip=True) if len(cols) > 2 else ""
    boletin   = cols[3].get_text(strip=True) if len(cols) > 3 else ""
    fecha_pub = cols[4].get_text(strip=True) if len(cols) > 4 else ""
    circular  = extract_circular(asunto)
    uid       = f"{tipo}{numero}"

    return {
        "uid": uid, "fecha": fecha_iso, "tipo": tipo, "numero": numero,
        "circular": circular, "asunto": asunto, "url": link_url,
        "boletin": boletin, "fecha_pub": fecha_pub,
    }


# ── Pipeline ──────────────────────────────────────────────────────────────────

def scrape_tipo(
    session: requests.Session,
    tipo: str,
    seen_uids: set,
    loki: LokiSender,
    fecha_desde: str,
    fecha_hasta: str,
) -> int:
    page = 1
    new_count = 0

    while True:
        results = fetch_page(session, tipo, fecha_desde, fecha_hasta, page)
        if not results:
            break

        new_batch = [r for r in results if r["uid"] not in seen_uids]

        if new_batch:
            loki.send_batch(new_batch)
            new_uids = [r["uid"] for r in new_batch]
            save_new_uids(new_uids)
            for uid in new_uids:
                seen_uids.add(uid)
            new_count += len(new_batch)
            log.info(
                f"tipo={tipo} pág {page}: {len(new_batch)} nuevas "
                f"({len(results) - len(new_batch)} ya enviadas)"
            )
        else:
            log.debug(f"tipo={tipo} pág {page}: {len(results)} ya enviadas")

        if len(results) < PAGE_SIZE:
            break
        page += 1
        time.sleep(0.5)

    return new_count


def run_all(loki: LokiSender, seen_uids: set) -> int:
    cutoff      = datetime.now() - timedelta(days=DAYS_WINDOW)
    fecha_desde = cutoff.strftime("%Y-%m-%d")
    fecha_hasta = datetime.now().strftime("%Y-%m-%d")
    log.info(f"Scrapeando {fecha_desde} → {fecha_hasta} | cache: {len(seen_uids)} UIDs")

    session = requests.Session()
    session.headers.update(HEADERS)
    total = 0

    for tipo in TIPOS:
        total += scrape_tipo(session, tipo, seen_uids, loki, fecha_desde, fecha_hasta)
        time.sleep(1)

    log.info(f"Ciclo completo: {total} nuevas comunicaciones")
    return total


def main():
    required_env = ["LOKI_URL", "LOKI_USER", "LOKI_API_KEY"]
    missing = [v for v in required_env if not os.getenv(v)]
    if missing:
        log.error(f"Faltan variables de entorno: {missing}")
        raise SystemExit(1)

    loki = LokiSender(
        url=os.environ["LOKI_URL"],
        user=os.environ["LOKI_USER"],
        api_key=os.environ["LOKI_API_KEY"],
    )

    interval_hours = int(os.getenv("SCRAPE_INTERVAL_HOURS", "1"))

    # Cargar cache desde archivo — persiste entre reinicios del container
    seen_uids = load_seen_uids()

    log.info("Iniciando scraper BCRA por Tipo y Fecha")
    run_all(loki, seen_uids)

    log.info(f"Entrando en loop: cada {interval_hours}h")
    while True:
        time.sleep(interval_hours * 3600)
        run_all(loki, seen_uids)


if __name__ == "__main__":
    main()
