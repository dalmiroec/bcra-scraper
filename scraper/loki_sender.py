"""
Loki Sender - Envía logs estructurados a Grafana Cloud Loki via HTTP Push API.

Timestamp: siempre NOW (Grafana Cloud rechaza timestamps viejos).
La fecha real de la comunicación viaja como stream label `fecha`
y como campo en el JSON — así es filtrable en el Label Explorer.

Stream labels (indexados):
  job, tipo, circular, fecha
Campos en JSON (accesibles con | json):
  uid, numero, asunto, url, boletin, fecha_pub
"""

import json
import logging
import time

import requests

log = logging.getLogger("bcra-scraper.loki")


class LokiSender:
    def __init__(self, url: str, user: str, api_key: str):
        self.push_url = f"{url.rstrip('/')}/loki/api/v1/push"
        self.auth = (user, api_key)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update({"Content-Type": "application/json"})

    def _now_ns(self) -> str:
        """Timestamp de ahora en nanosegundos. Grafana Cloud solo acepta logs recientes."""
        return str(int(time.time() * 1e9))

    def _build_streams(self, batch: list) -> list:
        """
        Agrupa por (tipo, circular, fecha).
        Timestamp = NOW (evita rechazo de Loki).
        La fecha real de la comunicación queda en el label `fecha`
        y dentro del JSON para filtrar en Grafana.
        """
        groups: dict = {}

        for com in batch:
            key = (com.get("tipo", "X"), com.get("circular", "?"), com.get("fecha", ""))
            if key not in groups:
                groups[key] = []
            groups[key].append(com)

        streams = []
        for (tipo, circular, fecha), comunicaciones in groups.items():
            values = []
            for com in comunicaciones:
                log_line = json.dumps(
                    {
                        "uid":       com["uid"],
                        "fecha":     com["fecha"],
                        "numero":    com["numero"],
                        "asunto":    com["asunto"],
                        "url":       com["url"],
                        "boletin":   com.get("boletin", ""),
                        "fecha_pub": com.get("fecha_pub", ""),
                    },
                    ensure_ascii=False,
                )
                values.append([self._now_ns(), log_line])

            if values:
                streams.append(
                    {
                        "stream": {
                            "job":      "bcra-scraper-v4",
                            "tipo":     tipo,
                            "circular": circular,
                            "fecha":    fecha,
                        },
                        "values": values,
                    }
                )

        return streams

    def send_batch(self, batch: list) -> None:
        if not batch:
            return

        streams = self._build_streams(batch)
        if not streams:
            return

        payload = {"streams": streams}
        total = sum(len(s["values"]) for s in streams)

        try:
            resp = self.session.post(
                self.push_url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=30,
            )
            if resp.status_code == 204:
                log.info(f"Loki: {total} entradas enviadas OK")
            else:
                log.error(f"Loki respondió {resp.status_code}: {resp.text[:300]}")
                resp.raise_for_status()

        except requests.RequestException as e:
            log.error(f"Error enviando a Loki: {e}")
            raise
