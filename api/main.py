"""
api/main.py — Backend FastAPI: sirve la Mini App y expone el REST API del bot.
"""
import json
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

# Add file logging
logger.add("app.log", rotation="10 MB", retention="5 days", level="INFO")

from config import settings
from database.crud import (
    init_db, crear_persona, buscar_por_nombre, listar_personas,
    get_persona, get_estadisticas, actualizar_estado, buscar_posible_duplicado
)
from database.models import EstadoPersona
from ai.image_processor import procesador_imagenes

# ── Inicialización ─────────────────────────────────────────────────────

app = FastAPI(
    title="Desaparecidos Venezuela API",
    description="API para el bot de búsqueda de personas desaparecidas",
    version="1.0.0",
    docs_url="/docs" if not settings.is_production else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOADS_DIR = Path("uploads")
UPLOADS_DIR.mkdir(exist_ok=True)

FOTOS_TEMP_DIR = Path("fotos_temp")
FOTOS_TEMP_DIR.mkdir(exist_ok=True)

# Servir uploads y fotos temporales (importante para mostrar rostros extraídos de registros en la app)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/fotos_temp", StaticFiles(directory="fotos_temp"), name="fotos_temp")

# Servir la Mini App como archivos estáticos
MINIAPP_DIR = Path("miniapp")
if MINIAPP_DIR.exists():
    app.mount("/app", StaticFiles(directory="miniapp", html=True), name="miniapp")


@app.on_event("startup")
async def startup():
    await init_db()
    logger.info("API iniciada ✓")


# ── Health Check ───────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "desaparecidos-vzla-api"}


@app.get("/")
async def root():
    index = MINIAPP_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"status": "ok", "docs": "/docs"}


# ── Estadísticas y Logs ──────────────────────────────────────────────────

@app.get("/api/stats")
async def get_stats():
    return await get_estadisticas()

@app.get("/api/logs")
async def get_logs(lines: int = 50):
    try:
        with open("app.log", "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            return {"logs": all_lines[-lines:]}
    except Exception as e:
        return {"logs": [f"Error al leer logs: {e}"]}


@app.get("/api/scraper-status")
async def scraper_status():
    """Estado real del scraper. Lee de Redis si está disponible, o genera datos de las fuentes en BD."""
    try:
        fuentes = await listar_fuentes_scraping()
        fuentes_activas = [
            {"nombre": f.nombre, "tipo": f.tipo, "url": f.url}
            for f in fuentes if f.activa
        ]
    except Exception:
        fuentes_activas = []

    # Intentar leer estado del scheduler desde Redis
    estado_scheduler = None
    try:
        import redis.asyncio as aioredis
        r = await aioredis.from_url(settings.redis_url, decode_responses=True)
        raw = await r.get("scheduler:estado")
        await r.close()
        if raw:
            import json as _json
            estado_scheduler = _json.loads(raw)
    except Exception:
        pass

    return {
        "activo": True,
        "fuentes_activas": len(fuentes_activas),
        "fuentes": fuentes_activas,
        "scheduler": estado_scheduler,
    }


# ── Personas ───────────────────────────────────────────────────────────

@app.get("/api/personas")
async def listar(
    q:      Optional[str] = None,
    estado: Optional[str] = None,
    zona:   Optional[str] = None,
    vulnerable: bool = False,
    limit:  int = 50,
    offset: int = 0,
):
    if q:
        personas = await buscar_por_nombre(q, limit=limit)
    else:
        personas = await listar_personas(
            estado=estado, zona=zona,
            solo_vulnerables=vulnerable,
            limit=limit, offset=offset,
        )
    return {
        "personas": [_persona_to_dict(p) for p in personas],
        "total": len(personas),
    }


@app.get("/api/personas/{persona_id}")
async def get_one(persona_id: int):
    p = await get_persona(persona_id)
    if not p:
        raise HTTPException(404, "Persona no encontrada")
    return _persona_to_dict(p)


@app.post("/api/personas", status_code=201)
async def registrar_persona(
    datos: str = Form(...),
    foto:  Optional[UploadFile] = File(None),
):
    datos_dict = json.loads(datos)

    # Prevención estricta de duplicados
    cedula = datos_dict.get("cedula")
    if cedula:
        from database.crud import buscar_por_nombre
        matches = await buscar_por_nombre(cedula, limit=1)
        if matches:
            raise HTTPException(409, "Ya existe una persona registrada con esta cédula.")
            
    nombre = datos_dict.get("nombre")
    if nombre:
        duplicado = await buscar_posible_duplicado(
            nombre=nombre + " " + datos_dict.get("apellidos", ""),
            edad=datos_dict.get("edad"),
            zona=datos_dict.get("zona")
        )
        if duplicado:
            raise HTTPException(409, "Ya existe una persona registrada con estos datos.")

    foto_path = None
    if foto and foto.filename:
        ext  = Path(foto.filename).suffix.lower()
        fname = f"{uuid.uuid4()}{ext}"
        fpath = UPLOADS_DIR / fname
        with open(fpath, "wb") as f:
            shutil.copyfileobj(foto.file, f)
        foto_path = str(fpath)
        datos_dict["foto_local_path"] = foto_path

    persona = await crear_persona(datos_dict)
    logger.info(f"[API] Persona registrada #{persona.id} — {persona.nombre_completo()}")
    return {"id": persona.id, "nombre": persona.nombre_completo()}


@app.patch("/api/personas/{persona_id}/estado")
async def cambiar_estado(persona_id: int, body: dict):
    nuevo = body.get("estado")
    if nuevo not in EstadoPersona.__members__.values():
        raise HTTPException(400, f"Estado inválido: {nuevo}")
    ok = await actualizar_estado(persona_id, EstadoPersona(nuevo))
    if not ok:
        raise HTTPException(404, "Persona no encontrada")
        
    if nuevo == "fallecido":
        persona = await get_persona(persona_id)
        if persona and persona.contacto_chat_id:
            import httpx
            from config import settings
            msg_compasivo = (
                f"🕊️ *Lamentamos informarte...*\n\n"
                f"Hemos recibido información confirmada sobre *{persona.nombre_completo()}*.\n"
                f"Con profundo pesar te informamos que ha sido reportado(a) como fallecido(a).\n\n"
                f"Estamos contigo en este difícil momento. 🙏"
            )
            url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
            async with httpx.AsyncClient() as client:
                try:
                    await client.post(url, json={
                        "chat_id": persona.contacto_chat_id,
                        "text": msg_compasivo,
                        "parse_mode": "Markdown"
                    })
                except Exception as e:
                    logger.error(f"Error enviando mensaje compasivo: {e}")
                    
    return {"ok": True}

# ── Análisis de imagen con Gemini Vision ──────────────────────────────

@app.post("/api/analizar-imagen")
async def analizar_imagen(imagen: UploadFile = File(...)):
    """
    Recibe una imagen (ficha SE BUSCA, foto, captura de WhatsApp)
    y extrae los datos con Gemini Vision.
    """
    ext   = Path(imagen.filename or "foto.jpg").suffix.lower()
    fname = f"tmp_{uuid.uuid4()}{ext}"
    fpath = UPLOADS_DIR / fname

    with open(fpath, "wb") as f:
        shutil.copyfileobj(imagen.file, f)

    try:
        lista_datos = await procesador_imagenes.extraer_datos(str(fpath))
        return [d.__dict__ for d in lista_datos]
    finally:
        fpath.unlink(missing_ok=True)


# ── Estadísticas de Scraping ──────────────────────────────────────────

@app.get("/api/stats")
async def stats_scraping():
    from database.crud import get_scraping_stats
    stats = await get_scraping_stats()
    return stats


# ── Mapa ───────────────────────────────────────────────────────────────

@app.get("/api/mapa")
async def mapa_personas(estado: Optional[str] = None):
    """Retorna personas con coordenadas para el mapa."""
    personas = await listar_personas(estado=estado, limit=500)
    puntos = [
        {
            "id":               p.id,
            "nombre":           p.nombre_completo(),
            "estado":           p.estado,
            "ultima_ubicacion": p.ultima_ubicacion,
            "lat":              p.lat,
            "lng":              p.lng,
            "es_vulnerable":    p.es_vulnerable,
        }
        for p in personas
        if p.lat and p.lng
    ]
    return puntos


# ── Fuentes de Scraping ────────────────────────────────────────────────

@app.get("/api/fuentes")
async def listar_fuentes(solo_activas: bool = True):
    from database.crud import listar_fuentes_scraping
    fuentes = await listar_fuentes_scraping(solo_activas=solo_activas)
    return [
        {
            "id": f.id,
            "nombre": f.nombre,
            "url": f.url,
            "tipo": f.tipo,
            "activa": f.activa
        }
        for f in fuentes
    ]


@app.post("/api/fuentes", status_code=201)
async def agregar_fuente(body: dict):
    nombre = body.get("nombre")
    url = body.get("url")
    tipo = body.get("tipo", "web")
    
    if not nombre or not url:
        raise HTTPException(400, "Nombre y URL son obligatorios")
        
    from database.crud import crear_fuente_scraping
    fuente = await crear_fuente_scraping(nombre=nombre, url=url, tipo=tipo)
    return {
        "id": fuente.id,
        "nombre": fuente.nombre,
        "url": fuente.url,
        "tipo": fuente.tipo
    }


# ── Ingresos en Hospitales ─────────────────────────────────────────────

@app.get("/api/hospitales/ingresos")
async def listar_ingresos(limite: int = 100):
    from database.crud import listar_ingresos_hospitales
    ingresos = await listar_ingresos_hospitales(limite=limite)
    return [
        {
            "id": i.id,
            "nombre_completo": i.nombre_completo,
            "edad": i.edad,
            "hospital_nombre": i.hospital_nombre,
            "fecha_ingreso": i.fecha_ingreso,
            "detalles_ingreso": i.detalles_ingreso,
            "persona_id_vinculada": i.persona_id_vinculada,
            "creado_en": i.creado_en.isoformat() if i.creado_en else None
        }
        for i in ingresos
    ]


@app.post("/api/hospitales/ingresos", status_code=201)
async def registrar_ingreso(body: dict):
    nombre = body.get("nombre_completo")
    hospital = body.get("hospital_nombre")
    
    if not nombre or not hospital:
        raise HTTPException(400, "nombre_completo y hospital_nombre son obligatorios")
        
    from database.crud import registrar_ingreso_hospital
    ingreso = await registrar_ingreso_hospital(body)
    
    # Intentar enviar alerta al familiar si se vinculó una persona desaparecida
    if ingreso.persona_id_vinculada:
        from database.crud import get_persona
        from telegram import Bot
        persona = await get_persona(ingreso.persona_id_vinculada)
        if persona and persona.contacto_chat_id:
            bot = Bot(token=settings.telegram_bot_token)
            msg_familiar = (
                f"🚨 *¡NOTIFICACIÓN URGENTE DE HOSPITAL!* 🚨\n\n"
                f"El sistema ha detectado una coincidencia en un hospital para tu familiar:\n"
                f"👤 *Nombre:* {persona.nombre_completo()}\n"
                f"🏥 *Ubicación:* {ingreso.hospital_nombre}\n"
                f"📋 *Reporte Médico:* {ingreso.detalles_ingreso}\n"
                f"📅 *Fecha:* {ingreso.fecha_ingreso}\n\n"
                f"Por favor, ponte en contacto con este hospital para verificar. ¡Esperamos que todo esté bien! 🙏"
            )
            try:
                await bot.send_message(chat_id=persona.contacto_chat_id, text=msg_familiar, parse_mode="Markdown")
            except Exception as err:
                logger.error(f"Error notificando al familiar desde API: {err}")

    return {
        "id": ingreso.id,
        "nombre_completo": ingreso.nombre_completo,
        "persona_id_vinculada": ingreso.persona_id_vinculada
    }


# ── Helpers ────────────────────────────────────────────────────────────

def _persona_to_dict(p) -> dict:
    return {
        "id":               p.id,
        "nombre":           p.nombre,
        "apellidos":        p.apellidos,
        "cedula":           p.cedula,
        "edad":             p.edad,
        "ultima_ubicacion": p.ultima_ubicacion,
        "zona":             p.zona,
        "estado":           p.estado,
        "prioridad":        p.prioridad,
        "es_vulnerable":    p.es_vulnerable,
        "descripcion_fisica": p.descripcion_fisica,
        "condicion_medica": p.condicion_medica,
        "fecha_desaparicion": p.fecha_desaparicion,
        "foto_url":         p.foto_url or (f"/{p.foto_rostro_local_path.replace(chr(92), '/')}" if p.foto_rostro_local_path else (f"/{p.foto_local_path.replace(chr(92), '/')}" if p.foto_local_path else None)),
        "lat":              p.lat,
        "lng":              p.lng,
        "creado_en":        p.creado_en.isoformat() if p.creado_en else None,
    }
