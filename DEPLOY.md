# Deploy en Debian AWS

## Estructura del proyecto

```
bcra-scraper/
├── docker-compose.yml
├── Dockerfile
├── .env.example          ← copiar a .env y completar
├── bcra-scraper.service  ← alternativa sin Docker (systemd)
├── scraper/
│   ├── bcra_scraper.py   ← scraper principal
│   ├── topic_classifier.py
│   ├── loki_sender.py
│   └── requirements.txt
├── data/                 ← cache de IDs procesados (auto-creado)
└── logs/                 ← logs del scraper (auto-creado)
```

---

## 1. Transferir archivos al server

```bash
# Desde tu máquina local
rsync -avz --exclude '.git' \
  bcra-scraper/ \
  ubuntu@<TU_IP_AWS>:/opt/bcra-scraper/
```

---

## 2. Obtener credenciales de Grafana Cloud Loki

1. Ingresá a [grafana.com](https://grafana.com) → tu organización
2. En el menú izquierdo: **My Account** → stack → **Details**
3. En la sección **Loki**, copiá:
   - **URL** (ej: `https://logs-prod-us-central1.grafana.net`)
   - **User** (número, ej: `123456`)
4. Crear API Key: **Security** → **API Keys** → **Add API key**
   - Role: **MetricsPublisher** (tiene permisos de escritura a Loki)
   - Copiá el token generado

---

## Opción A: Docker (recomendado)

```bash
# En el server
sudo apt update && sudo apt install -y docker.io docker-compose-plugin

# Configurar variables de entorno
cd /opt/bcra-scraper
cp .env.example .env
nano .env   # completar LOKI_URL, LOKI_USER, LOKI_API_KEY

# Construir y levantar
sudo docker compose up -d --build

# Ver logs
sudo docker compose logs -f
```

---

## Opción B: Systemd (sin Docker)

```bash
# Instalar dependencias
sudo apt update && sudo apt install -y python3 python3-venv python3-pip

# Crear usuario dedicado
sudo useradd -r -s /bin/false bcra

# Configurar el proyecto
sudo mkdir -p /opt/bcra-scraper
sudo chown bcra:bcra /opt/bcra-scraper
cd /opt/bcra-scraper

python3 -m venv venv
venv/bin/pip install -r scraper/requirements.txt

cp .env.example .env
nano .env   # completar credenciales

# Instalar servicio
sudo cp bcra-scraper.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bcra-scraper
sudo systemctl start bcra-scraper

# Ver logs
sudo journalctl -u bcra-scraper -f
```

---

## 3. Verificar que los logs lleguen a Grafana Cloud

En Grafana Cloud:
1. Ir a **Explore**
2. Seleccionar datasource **Loki**
3. Query de prueba:
   ```logql
   {job="bcra-scraper"} | limit 20
   ```

---

## 4. Queries LogQL útiles para dashboards

```logql
# Todas las comunicaciones
{job="bcra-scraper"}

# Solo Comunicaciones tipo A
{job="bcra-scraper", tipo="A"}

# Comunicaciones sobre tipo de cambio
{job="bcra-scraper", tema="tipo_de_cambio"}

# Contar comunicaciones por tema (para gráfico de barras)
sum by (tema) (count_over_time({job="bcra-scraper"}[7d]))

# Tendencia de comunicaciones tipo A en el tiempo
count_over_time({job="bcra-scraper", tipo="A"}[1d])

# Buscar texto en el asunto
{job="bcra-scraper"} |= "exportación"

# Comunicaciones de la última semana por tipo
sum by (tipo) (count_over_time({job="bcra-scraper"}[7d]))
```

---

## 5. Agregar más temas al clasificador

Editá `scraper/topic_classifier.py` → diccionario `TOPICS`.
Cada entrada es `"nombre_tema": ["keyword1", "regex2", ...]`.
El nombre del tema se convierte en el label `tema` en Loki.
