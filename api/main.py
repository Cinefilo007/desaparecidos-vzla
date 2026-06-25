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

from config import settings
from database.crud import (
    init_db, crear_persona, buscar_por_nombre, listar_personas,
    get_persona, get_estadisticas, actualizar_estado,
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


# ── Estadísticas ───────────────────────────────────────────────────────

@app.get("/api/stats")
async def get_stats():
    return await get_estadisticas()


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
        datos = await procesador_imagenes.extraer_datos(str(fpath))
        return datos.__dict__
    finally:
        fpath.unlink(missing_ok=True)


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
        "foto_url":         p.foto_url,
        "lat":              p.lat,
        "lng":              p.lng,
        "creado_en":        p.creado_en.isoformat() if p.creado_en else None,
    }
