"""
scheduler/main.py — Planificador adaptativo con gestión de presupuesto.
Ajusta frecuencias automáticamente según consumo de tokens y carga.
"""
import asyncio
import json
import os
import time
import uuid
from datetime import date
from enum import Enum, IntEnum

import redis.asyncio as aioredis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from config import settings


# ── Modos de operación ─────────────────────────────────────────────────

class Modo(str, Enum):
    PLENO      = "pleno"
    MODERADO   = "moderado"
    ECONOMICO  = "economico"
    EMERGENCIA = "emergencia"


# ── Frecuencias por fuente y modo (minutos; 9999 = deshabilitado) ──────

FRECUENCIAS = {
    "web_desaparecidos": [10, 15, 30, 60],
    "twitter_hashtags":  [15, 20, 60, 9999],
    "tiktok_search":     [20, 30, 9999, 9999],
    "instagram":         [25, 40, 9999, 9999],
    "telegram_channels": [5,  10,  20,  30],
    "noticias_vzla":     [30, 30,  60,  60],
    "live_streams":      [1,   2, 9999, 9999],
    "procesar_cola":     [2,   5,  9999, 9999],
}

MODO_IDX = {Modo.PLENO: 0, Modo.MODERADO: 1, Modo.ECONOMICO: 2, Modo.EMERGENCIA: 3}

HASHTAGS = [
    "#DesaparecidosVenezuela", "#TerremotoVenezuela", "#SismoVenezuela",
    "#Maturin", "#Cumana", "#BarcelonaVzla", "#RescateVenezuela",
]

SITIOS_WEB = [
    "https://desaparecidosterremotovenezuela.com",
]

CANALES_LIVE = [
    "https://youtube.com/@VPItv/live",
    "https://youtube.com/@Televen/live",
]


class PlanificadorAdaptativo:

    def __init__(self):
        self.redis: aioredis.Redis = None
        self.scheduler = AsyncIOScheduler(timezone="America/Caracas")
        self.jobs: dict = {}

    # ── Conexión Redis ─────────────────────────────────────────────────

    async def conectar_redis(self):
        self.redis = await aioredis.from_url(settings.redis_url, decode_responses=True)
        logger.info("Redis conectado ✓")

    # ── Presupuesto de tokens ──────────────────────────────────────────

    def _key(self, metrica: str) -> str:
        return f"budget:{date.today().isoformat()}:{metrica}"

    async def tokens_disponibles(self) -> int:
        usados = int(await self.redis.get(self._key("tokens")) or 0)
        return max(0, settings.gemini_daily_token_limit - usados)

    async def requests_disponibles(self) -> int:
        usados = int(await self.redis.get(self._key("requests")) or 0)
        return max(0, settings.gemini_daily_request_limit - usados)

    async def registrar_uso(self, tokens: int):
        pipe = self.redis.pipeline()
        pipe.incr(self._key("tokens"), tokens)
        pipe.incr(self._key("requests"))
        pipe.expire(self._key("tokens"), 86400)
        pipe.expire(self._key("requests"), 86400)
        await pipe.execute()

    async def get_modo(self) -> Modo:
        tk  = await self.tokens_disponibles()
        rq  = await self.requests_disponibles()
        pct = min(tk / settings.gemini_daily_token_limit,
                  rq / settings.gemini_daily_request_limit)
        if pct > 0.50: return Modo.PLENO
        if pct > 0.25: return Modo.MODERADO
        if pct > 0.10: return Modo.ECONOMICO
        return Modo.EMERGENCIA

    # ── Encolar tarea ──────────────────────────────────────────────────

    async def encolar(self, tipo: str, datos: dict, prioridad: int = 3):
        """Agrega tarea a la cola Redis con prioridad (1=crítica, 4=baja)."""
        tarea = {"id": str(uuid.uuid4()), "tipo": tipo,
                 "datos": datos, "prioridad": prioridad, "ts": time.time()}
        queue = f"queue:p{prioridad}"
        await self.redis.rpush(queue, json.dumps(tarea))

    # ── Jobs individuales ──────────────────────────────────────────────

    async def job_scrape_web(self):
        modo = await self.get_modo()
        logger.info(f"[web_scraper] modo={modo.value}")
        for url in SITIOS_WEB:
            await self.encolar("scrape_web", {"url": url}, prioridad=2)

    async def job_twitter(self):
        modo = await self.get_modo()
        if modo == Modo.EMERGENCIA:
            return
        tags = HASHTAGS if modo == Modo.PLENO else HASHTAGS[:3]
        for tag in tags:
            await self.encolar("scrape_hashtag",
                               {"hashtag": tag, "plataforma": "twitter", "limite": 50},
                               prioridad=3)
        logger.info(f"[twitter] {len(tags)} hashtags encolados")

    async def job_tiktok(self):
        modo = await self.get_modo()
        if modo in (Modo.ECONOMICO, Modo.EMERGENCIA):
            return
        keywords = ["rescate venezuela terremoto", "sismo maturin 2026",
                    "desaparecidos venezuela sismo"]
        for kw in keywords:
            await self.encolar("buscar_videos_tiktok",
                               {"keyword": kw, "max_videos": 5}, prioridad=4)
        logger.info(f"[tiktok] {len(keywords)} búsquedas encoladas")

    async def job_telegram_channels(self):
        canales = [
            "@DesaparecidosVenezuela", "@SismoVzla2026",
            "@ProteccionCivilVzla", "@BomberosVenezuela",
        ]
        for canal in canales:
            await self.encolar("scrape_telegram", {"canal": canal}, prioridad=2)
        logger.info(f"[telegram] {len(canales)} canales encolados")

    async def job_noticias(self):
        urls = [
            "https://www.elnacional.com/venezuela/",
            "https://efectococuyo.com/",
            "https://runrun.es/",
        ]
        for url in urls:
            await self.encolar("scrape_noticias", {"url": url}, prioridad=3)
        logger.info(f"[noticias] {len(urls)} sitios encolados")

    async def job_live_streams(self):
        modo = await self.get_modo()
        if modo != Modo.PLENO:
            return
        for url in CANALES_LIVE:
            await self.encolar("monitor_live",
                               {"url": url, "duracion_seg": 30}, prioridad=2)
        logger.info(f"[live] {len(CANALES_LIVE)} streams encolados")

    async def job_procesar_cola(self):
        """Señaliza al worker que procese la cola."""
        await self.redis.publish("worker:procesar", "1")

    # ── Auto-ajuste de frecuencias ─────────────────────────────────────

    async def auto_ajustar(self):
        modo = await self.get_modo()
        idx  = MODO_IDX[modo]
        tk   = await self.tokens_disponibles()
        rq   = await self.requests_disponibles()
        logger.info(f"[auto-ajuste] modo={modo.value} tokens={tk:,} requests={rq}")

        for nombre, job in self.jobs.items():
            freqs = FRECUENCIAS.get(nombre)
            if not freqs:
                continue
            freq = freqs[idx]
            if freq >= 9999:
                job.pause()
            else:
                job.reschedule(trigger=IntervalTrigger(minutes=freq))
                job.resume()

        # Publicar estado para el dashboard
        estado = {
            "modo": modo.value,
            "tokens_disponibles": tk,
            "requests_disponibles": rq,
            "ts": time.time(),
        }
        await self.redis.set("scheduler:estado", json.dumps(estado), ex=900)

        # Alerta si presupuesto crítico
        if modo == Modo.EMERGENCIA:
            await self.redis.publish("admin:alerta",
                f"⚠️ PRESUPUESTO CRÍTICO — Tokens disponibles: {tk:,}")

    # ── Reporte horario ────────────────────────────────────────────────

    async def reporte_horario(self):
        tk = await self.tokens_disponibles()
        rq = await self.requests_disponibles()
        modo = await self.get_modo()
        emoji = {"pleno": "🟢", "moderado": "🟡", "economico": "🟠", "emergencia": "🔴"}[modo.value]

        # Tamaño de colas
        colas = {}
        for p in range(1, 5):
            colas[f"p{p}"] = await self.redis.llen(f"queue:p{p}")

        reporte = (
            f"📊 Reporte horario\n"
            f"{emoji} Modo: {modo.value.upper()}\n"
            f"Tokens disponibles: {tk:,}\n"
            f"Requests disponibles: {rq}\n"
            f"Colas: p1={colas['p1']} p2={colas['p2']} p3={colas['p3']} p4={colas['p4']}"
        )
        await self.redis.publish("admin:reporte", reporte)
        logger.info(reporte)

    # ── Arranque ───────────────────────────────────────────────────────

    async def iniciar(self):
        await self.conectar_redis()
        modo_inicial = await self.get_modo()
        idx = MODO_IDX[modo_inicial]

        jobs_config = {
            "web_desaparecidos": self.job_scrape_web,
            "twitter_hashtags":  self.job_twitter,
            "tiktok_search":     self.job_tiktok,
            "telegram_channels": self.job_telegram_channels,
            "noticias_vzla":     self.job_noticias,
            "live_streams":      self.job_live_streams,
            "procesar_cola":     self.job_procesar_cola,
        }

        for nombre, func in jobs_config.items():
            freq = FRECUENCIAS[nombre][idx]
            job = self.scheduler.add_job(
                func,
                trigger=IntervalTrigger(minutes=max(1, freq if freq < 9999 else 9999)),
                id=nombre,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )
            if freq >= 9999:
                job.pause()
            self.jobs[nombre] = job
            logger.info(f"  Job '{nombre}': {'PAUSADO' if freq >= 9999 else f'cada {freq} min'}")

        # Meta-jobs
        self.scheduler.add_job(self.auto_ajustar,   IntervalTrigger(minutes=15), id="auto_ajuste", max_instances=1)
        self.scheduler.add_job(self.reporte_horario, IntervalTrigger(hours=1),   id="reporte",     max_instances=1)

        self.scheduler.start()
        logger.info(f"✅ Planificador iniciado — Modo inicial: {modo_inicial.value}")

        # Ejecutar un ciclo inmediato al arrancar
        await self.job_scrape_web()
        await self.job_telegram_channels()

        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            self.scheduler.shutdown()
            await self.redis.close()


if __name__ == "__main__":
    asyncio.run(PlanificadorAdaptativo().iniciar())
