# BCRA Scraper → Grafana Cloud Loki

Scraper en Python que monitorea las comunicaciones del Banco Central de la República Argentina (BCRA) y las envía a Grafana Cloud Loki para visualización y alertas.

## Qué hace

- Consulta el buscador oficial del BCRA cada hora
- Descarga comunicaciones de los últimos 30 días (tipos A, B, C, P)
- Extrae circular regulatoria del asunto via regex
- Envía a Grafana Cloud Loki como logs estructurados (JSON)
- Deduplica via archivo de caché (`seen_uids.txt`) — no re-envía comunicaciones ya procesadas, incluso si el container se reinicia

## Stack

- **Python 3** — requests, BeautifulSoup, lxml
- **Grafana Cloud Loki** — ingesta via HTTP Push API
- **Docker** — container con restart automático
- **AWS EC2** — Debian

## Estructura

```
bcra-scraper/
├── scraper/
│   ├── bcra_scraper.py    # Scraper principal
│   ├── loki_sender.py     # Push a Loki HTTP API
│   └── requirements.txt
├── docker-compose.yml
├── Dockerfile
├── .env.example
└── .gitignore
```

## Setup

```bash
cp .env.example .env
# Editar .env con credenciales de Grafana Cloud Loki

docker compose build
docker compose up -d

# Ver logs
docker logs bcra-scraper --tail 20 -f
```

## Variables de entorno

| Variable | Descripción |
|---|---|
| `LOKI_URL` | URL de Grafana Cloud Loki (ej: `https://logs-prod-xxx.grafana.net`) |
| `LOKI_USER` | ID numérico del datasource |
| `LOKI_API_KEY` | API key de Grafana Cloud |
| `SCRAPE_INTERVAL_HOURS` | Frecuencia del scraper (default: 1) |

## Labels en Loki

| Label | Descripción |
|---|---|
| `job` | Identificador del scraper |
| `tipo` | A, B, C, P |
| `circular` | RUNOR, OPASI, CREFI, etc. |
| `fecha` | Fecha real de la comunicación (YYYY-MM-DD) |

