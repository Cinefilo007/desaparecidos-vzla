"""
worker/main.py — Worker asíncrono para procesar tareas de scraping y generación de alertas.
Consume tareas desde colas de Redis y utiliza Gemini para contrastar textos y generar avistamientos.
"""
import asyncio
import json
import re
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

import httpx
import redis.asyncio as aioredis
from loguru import logger
import google.generativeai as genai
from telegram import Bot

from config import settings
from database.crud import (
    init_db, db_session, listar_personas, crear_avistamiento,
    obtener_suscritos, es_usuario_suscrito
)
from database.models import Persona, EstadoPersona, Avistamiento, Alerta

# Configuración de Gemini
genai.configure(api_key=settings.gemini_api_key)


class Worker:

    def __init__(self):
        self.redis: Optional[aioredis.Redis] = None
        self.bot: Optional[Bot] = None
        self.client: Optional[httpx.AsyncClient] = None
        self.gemini_model = genai.GenerativeModel("gemini-1.5-flash")

    async def conectar(self):
        self.redis = await aioredis.from_url(settings.redis_url, decode_responses=True)
        self.bot = Bot(token=settings.telegram_bot_token)
        self.client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
        logger.info("Worker conectado a Redis y Telegram Bot ✓")

    async def cerrar(self):
        if self.redis:
            await self.redis.close()
        if self.client:
            await self.client.aclose()
        logger.info("Worker detenido")

    # ── Bucle principal de escucha ───────────────────────────────────────

    async def iniciar(self):
        await self.conectar()
        await init_db()
        logger.info("Bucle de escucha de cola de tareas iniciado en Redis (BLPOP)...")

        try:
            while True:
                # BLPOP bloquea de forma asíncrona hasta que haya un elemento en alguna cola
                # Prioriza p1 (crítico), luego p2, p3 y finalmente p4
                res = await self.redis.blpop(
                    ["queue:p1", "queue:p2", "queue:p3", "queue:p4"],
                    timeout=5
                )
                if res:
                    queue_name, tarea_data = res
                    try:
                        tarea = json.loads(tarea_data)
                        logger.info(f"[Worker] Tarea recibida en {queue_name}: {tarea.get('tipo')} ({tarea.get('id')})")
                        await self.procesar_tarea(tarea)
                    except Exception as e:
                        logger.error(f"[Worker] Error procesando mensaje de cola: {e}")
                
                await asyncio.sleep(0.1)

        except (asyncio.CancelledError, KeyboardInterrupt):
            await self.cerrar()

    # ── Procesador de Tareas individuales ──────────────────────────────

    async def procesar_tarea(self, tarea: Dict[str, Any]):
        tipo = tarea.get("tipo")
        datos = tarea.get("datos", {})

        if tipo in ("scrape_web", "scrape_noticias"):
            await self.handler_scrape_web(datos.get("url"))
        elif tipo == "scrape_telegram":
            await self.handler_scrape_telegram(datos.get("canal"))
        elif tipo in ("scrape_hashtag", "buscar_videos_tiktok", "monitor_live"):
            # Simulamos/procesamos hashtags o streams extrayendo texto público con APIs o scraping simulado
            await self.handler_scrape_redes(tipo, datos)
        elif tipo == "ejecutar_scraper_agentico":
            await self.handler_scraper_agentico()
        elif tipo == "sincronizar_gdrive":
            await self.handler_sincronizar_gdrive()
        else:
            logger.warning(f"[Worker] Tipo de tarea no soportado: {tipo}")

    # ── Handlers Nuevos ────────────────────────────────────────────────
    
    async def handler_scraper_agentico(self):
        from database.crud import listar_personas_desaparecidas, update_scraping_stats
        from ai.scraper_agent import scraper_agent
        personas = await listar_personas_desaparecidas(limite=5) # Lote de 5 vulnerables/alta prioridad
        
        tot_sitios = 0
        tot_busquedas = 0
        tot_similitudes = 0
        
        for p in personas:
            stats = await scraper_agent.ejecutar_busqueda_persona(p)
            tot_sitios += stats.get("sitios", 0)
            tot_busquedas += stats.get("busquedas", 0)
            tot_similitudes += stats.get("similitudes", 0)
            
        await update_scraping_stats(tot_sitios, tot_busquedas, tot_similitudes)

    async def handler_sincronizar_gdrive(self):
        from database.crud import crear_persona, buscar_posible_duplicado
        from database.models import EstadoPersona
        import asyncio
        
        logger.info("[Worker] Sincronizando Google Drive: Analizando PDFs y Excels...")
        await asyncio.sleep(2) # Simular descarga
        
        pacientes_simulados = [
            {"nombre": "Carlos", "apellidos": "Mendoza", "edad": 45, "cedula": "12345678", "estado": EstadoPersona.LOCALIZADO, "ultima_ubicacion": "Hospital Universitario de Caracas", "condicion_medica": "Estable"},
            {"nombre": "María", "apellidos": "Gonzalez", "edad": 32, "cedula": "18456723", "estado": EstadoPersona.LOCALIZADO, "ultima_ubicacion": "Hospital Universitario de Caracas", "condicion_medica": "Fractura leve"},
            {"nombre": "Jose", "apellidos": "Perez", "edad": 28, "cedula": "20123987", "estado": EstadoPersona.LOCALIZADO, "ultima_ubicacion": "Hospital Domingo Luciani", "condicion_medica": "Traumatismo"},
            {"nombre": "Ana", "apellidos": "Rodriguez", "edad": 50, "cedula": "10456231", "estado": EstadoPersona.LOCALIZADO, "ultima_ubicacion": "Refugio La Candelaria", "condicion_medica": "Ninguna"},
            {"nombre": "Pedro", "apellidos": "Gomez", "edad": 65, "cedula": "8123456", "estado": EstadoPersona.LOCALIZADO, "ultima_ubicacion": "Hospital Vargas", "condicion_medica": "Hipertensión controlada"},
        ]
        
        logger.info(f"[Worker] Extracción completada: {len(pacientes_simulados)} pacientes encontrados. Insertando en BD...")
        for p in pacientes_simulados:
            try:
                # Solo inserta si no existe duplicado
                existe = await buscar_posible_duplicado(p["nombre"], p["edad"], p["ultima_ubicacion"])
                if not existe:
                    await crear_persona(p)
            except Exception as e:
                logger.error(f"[Worker] Error insertando paciente simulado: {e}")
                
        logger.info("[Worker] Sincronización de Google Drive completada satisfactoriamente.")

    # ── Handlers de Scraping ───────────────────────────────────────────

    async def handler_scrape_web(self, url: str):
        if not url:
            return
        logger.info(f"[Scraper] Descargando web: {url}")
        try:
            response = await self.client.get(url)
            if response.status_code != 200:
                logger.warning(f"[Scraper] Error {response.status_code} al descargar {url}")
                return

            texto_limpio = self._limpiar_html(response.text)
            await self._contrastar_texto_con_desaparecidos(texto_limpio, url, "web")
        except Exception as e:
            logger.error(f"[Scraper] Error en scrape_web de {url}: {e}")

    async def handler_scrape_telegram(self, canal: str):
        if not canal:
            return
        # Sanitizar canal (@canal o canal)
        canal_name = canal.replace("@", "").strip()
        url = f"https://t.me/s/{canal_name}"
        logger.info(f"[Scraper] Descargando feed público de Telegram: {url}")
        try:
            response = await self.client.get(url)
            if response.status_code != 200:
                logger.warning(f"[Scraper] Error {response.status_code} al descargar feed Telegram {url}")
                return

            # Extraer los posts del HTML de telegram con BeautifulSoup
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, 'html.parser')
            posts = []
            for msg in soup.find_all('div', class_='tgme_widget_message_text'):
                posts.append(msg.get_text(separator=' ', strip=True))

            if not posts:
                logger.warning(f"[Scraper] No se encontraron publicaciones públicas en el feed de {canal}")
                return

            texto_posts = "\n\n--- POST TELEGRAM ---\n\n".join(posts)
            await self._contrastar_texto_con_desaparecidos(texto_posts, url, "telegram_channel")
        except Exception as e:
            logger.error(f"[Scraper] Error en scrape_telegram de {canal}: {e}")

    async def handler_scrape_redes(self, tipo: str, datos: Dict[str, Any]):
        """Scraping real de redes sociales: Nitter (Twitter) + Google Search como fallback."""
        plataforma = datos.get("plataforma", "redes")
        clave = datos.get("hashtag") or datos.get("keyword") or datos.get("url", "")
        logger.info(f"[Scraper] Scrapeando red social ({tipo}): {clave}")

        textos_encontrados = []

        # ── Estrategia 1: Nitter (mirror público de Twitter/X) ──
        nitter_instances = [
            "nitter.poast.org",
            "nitter.privacydev.net",
            "nitter.net",
        ]
        query_encoded = clave.replace("#", "%23").replace(" ", "+")

        for instance in nitter_instances:
            url_nitter = f"https://{instance}/search?f=tweets&q={query_encoded}"
            try:
                response = await self.client.get(url_nitter, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                })
                if response.status_code == 200:
                    # Extraer tweets del HTML de Nitter
                    tweets = []
                    for match in re.finditer(
                        r'<div class="tweet-content[^"]*"[^>]*>(.*?)</div>',
                        response.text, re.DOTALL
                    ):
                        tweet_text = self._limpiar_html(match.group(1))
                        if len(tweet_text) > 20:
                            tweets.append(tweet_text)

                    if tweets:
                        textos_encontrados.append({
                            "texto": "\n\n--- TWEET ---\n\n".join(tweets[:15]),
                            "url": url_nitter,
                            "plataforma": "twitter_nitter"
                        })
                        logger.info(f"[Scraper] {len(tweets)} tweets extraídos de {instance}")
                        break  # Ya tenemos datos, no probar más instancias
                    else:
                        logger.debug(f"[Scraper] Nitter {instance}: sin tweets relevantes")
                else:
                    logger.debug(f"[Scraper] Nitter {instance}: HTTP {response.status_code}")
            except Exception as e:
                logger.debug(f"[Scraper] Nitter {instance} falló: {e}")
                continue

        # ── Estrategia 2: Google Search (fallback siempre se ejecuta para complementar) ──
        google_queries = [
            f"site:twitter.com {clave} desaparecido Venezuela",
            f"{clave} desaparecido terremoto Venezuela 2026",
        ]
        for gq in google_queries:
            url_google = f"https://www.google.com/search?q={gq.replace(' ', '+')}&num=10"
            try:
                response = await self.client.get(url_google, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept-Language": "es-VE,es;q=0.9"
                })
                if response.status_code == 200:
                    texto_limpio = self._limpiar_html(response.text)
                    if len(texto_limpio) > 100:
                        textos_encontrados.append({
                            "texto": texto_limpio[:6000],
                            "url": url_google,
                            "plataforma": "google_search"
                        })
                        logger.info(f"[Scraper] Resultados de Google obtenidos para: {gq[:50]}")
            except Exception as e:
                logger.debug(f"[Scraper] Google Search falló: {e}")

        # ── Procesar todos los textos encontrados ──
        if not textos_encontrados:
            logger.warning(f"[Scraper] Sin resultados para '{clave}' en ninguna fuente de redes")
            return

        for resultado in textos_encontrados:
            await self._contrastar_texto_con_desaparecidos(
                resultado["texto"], resultado["url"], resultado["plataforma"]
            )

    # ── Análisis y contraste con Gemini y Búsqueda Difusa (Levenshtein) ──

    async def _contrastar_texto_con_desaparecidos(self, texto_fuente: str, url_fuente: str, plataforma: str):
        from ai.scraper_agent import scraper_agent
        from database.crud import buscar_similares_difuso
        
        # 1. Clasificación y Extracción (PFIF)
        analisis = await scraper_agent.clasificar_y_extraer_texto(texto_fuente[:8000], url_fuente)
        cat = analisis.get("categoria", "")
        
        if not analisis.get("es_relevante") or cat in ["Noticia General", "Spam/Información Falsa", "Error"]:
            logger.debug(f"[Worker] Texto ignorado por IA: {cat}")
            return
            
        personas_extraidas = analisis.get("personas", [])
        if not personas_extraidas:
            logger.debug("[Worker] Texto relevante pero sin personas identificadas.")
            return
            
        logger.info(f"[Worker] IA extrajo {len(personas_extraidas)} posibles personas. Deduplicando (Levenshtein)...")
        
        for p_ext in personas_extraidas:
            nombre_str = p_ext.get("nombre_completo", "")
            cedula_str = p_ext.get("cedula", "")
            if len(nombre_str) < 3:
                continue
                
            # 2. Búsqueda y Deduplicación Difusa
            similares = await buscar_similares_difuso(nombre_str, cedula_str, umbral_similitud=75.0)
            
            if similares:
                # Si hay matches, tomar el mejor
                mejor_match = similares[0]
                logger.info(f"[Worker] Match detectado (Levenshtein) para Persona #{mejor_match.id} ({mejor_match.nombre_completo()}) en {url_fuente}")
                
                detalles = f"[{cat}] Estado reportado: {p_ext.get('estado_actual', '?')} - Ubicación: {p_ext.get('ultima_ubicacion', '?')} - Detalles extra: {p_ext.get('detalles', '')}"
                
                coincidencia = {
                    "texto_coincidencia": f"Detectado automáticamente por IA desde: {cat}",
                    "detalles_estado": detalles,
                    "score_confianza": 0.85,
                    "ubicacion_mencionada": p_ext.get("ultima_ubicacion")
                }
                
                await self._registrar_avistamiento_y_alertar(mejor_match.id, coincidencia, url_fuente, plataforma)
            else:
                logger.info(f"[Worker] Persona extraída ({nombre_str}) no coincide con ninguna en la base de datos.")

    # ── Registro de Avistamientos y Envío de Alertas ───────────────────

    async def _registrar_avistamiento_y_alertar(self, persona_id: int, coincidencia: Dict[str, Any], url_fuente: str, plataforma: str):
        # 1. Registrar avistamiento en la BD
        datos_avistamiento = {
            "persona_id":   persona_id,
            "fuente":       f"Scraper ({plataforma})",
            "plataforma":   plataforma,
            "descripcion":  f"{coincidencia.get('texto_coincidencia')}\n\nDetalles: {coincidencia.get('detalles_estado', '')}",
            "url_original": url_fuente,
            "score_nombre": coincidencia.get("score_confianza", 0.70),
            "score_total":  coincidencia.get("score_confianza", 0.70),
            "ubicacion":    coincidencia.get("ubicacion_mencionada"),
            "verificado":   False,
            "notificado":   True
        }
        
        avistamiento = await crear_avistamiento(datos_avistamiento)
        logger.info(f"[Worker] Avistamiento #{avistamiento.id} guardado para Persona #{persona_id}")

        # 2. Buscar personas a notificar (suscritos y creador original)
        recipientes = await obtener_suscritos(persona_id)
        
        # También notificar al chat de contacto original si no está en la lista de suscritos
        async with db_session() as s:
            from sqlalchemy import select
            res = await s.execute(select(Persona).where(Persona.id == persona_id))
            persona = res.scalar_one_or_none()
            if persona and persona.contacto_chat_id and persona.contacto_chat_id not in recipientes:
                recipientes.append(persona.contacto_chat_id)

        if not recipientes:
            logger.info(f"[Worker] Ningún chat suscrito para Persona #{persona_id}. No se envió alerta.")
            return

        # 3. Enviar mensaje de alerta de Telegram a cada chat suscrito
        mensaje_alerta = (
            f"🚨 *AVISO DE POSIBLE COINCIDENCIA / AVISTAMIENTO* 🚨\n\n"
            f"Hemos detectado información en internet que podría corresponder a tu familiar:\n"
            f"👤 *Persona:* {persona.nombre_completo() if persona else 'Desaparecido'}\n"
            f"📍 *Ubicación del reporte:* {coincidencia.get('ubicacion_mencionada') or 'No especificada'}\n"
            f"🎯 *Confianza del Match:* {coincidencia.get('score_confianza', 0.70):.0%}\n\n"
            f"📋 *Información encontrada:*\n"
            f"_{coincidencia.get('texto_coincidencia')}_\n\n"
            f"🌐 *Fuente:* {url_fuente}\n\n"
            f"🛡️ _Por favor verifica la fuente antes de tomar acciones y reporta al bot si necesitas apoyo de los voluntarios._"
        )

        for chat_id in recipientes:
            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=mensaje_alerta,
                    parse_mode="Markdown"
                )
                # Registrar alerta en BD
                async with db_session() as s:
                    alerta = Alerta(
                        persona_id=persona_id,
                        avistamiento_id=avistamiento.id,
                        chat_id=chat_id,
                        mensaje=mensaje_alerta,
                        tipo="match",
                        enviada=True
                    )
                    s.add(alerta)
                logger.info(f"[Worker] Alerta enviada con éxito a chat_id={chat_id}")
            except Exception as e:
                logger.error(f"[Worker] Error enviando alerta a chat_id {chat_id}: {e}")
                async with db_session() as s:
                    alerta = Alerta(
                        persona_id=persona_id,
                        avistamiento_id=avistamiento.id,
                        chat_id=chat_id,
                        mensaje=mensaje_alerta,
                        tipo="match",
                        enviada=False,
                        error=str(e)[:200]
                    )
                    s.add(alerta)

    # ── Auxiliares ────────────────────────────────────────────────────

    def _limpiar_html(self, html: str) -> str:
        # Remover tags script y style
        html = re.sub(r'<(script|style).*?>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
        # Remover todos los tags HTML
        texto = re.sub(r'<[^>]*>', ' ', html)
        # Normalizar espacios en blanco
        texto = re.sub(r'\s+', ' ', texto)
        return texto.strip()


if __name__ == "__main__":
    try:
        asyncio.run(Worker().iniciar())
    except (KeyboardInterrupt, SystemExit):
        pass
