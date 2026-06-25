"""
database/crud.py — Operaciones CRUD asíncronas sobre la base de datos.
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select, update, func, and_, or_
from sqlalchemy.orm import selectinload
from typing import Optional, List
from datetime import datetime
import hashlib

from config import settings
from database.models import Base, Persona, Avistamiento, Alerta, Voluntario, EstadoPersona

# ── Motor y sesión ─────────────────────────────────────────────────────

engine = create_async_engine(
    settings.database_url,
    echo=(settings.environment == "development"),
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def init_db():
    """Crea todas las tablas si no existen."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ── Context manager para sesiones ─────────────────────────────────────

class db_session:
    def __init__(self):
        self.session = AsyncSessionLocal()

    async def __aenter__(self) -> AsyncSession:
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type:
            await self.session.rollback()
        else:
            await self.session.commit()
        await self.session.close()


# ── CRUD: Personas ─────────────────────────────────────────────────────

async def crear_persona(datos: dict) -> Persona:
    """Crea una persona nueva. Calcula hash para anti-duplicados."""
    # Hash basado en nombre normalizado + edad + zona
    nombre_norm = datos.get("nombre", "").lower().strip()
    hash_str = f"{nombre_norm}_{datos.get('edad', '')}_{datos.get('zona', '')}"
    datos["hash_dedup"] = hashlib.md5(hash_str.encode()).hexdigest()

    async with db_session() as s:
        persona = Persona(**datos)
        s.add(persona)
        await s.flush()
        await s.refresh(persona)
        return persona


async def buscar_posible_duplicado(nombre: str, edad: int = None, zona: str = None) -> Optional[Persona]:
    """Verifica si ya existe una persona similar antes de registrar."""
    nombre_norm = nombre.lower().strip()
    hash_str = f"{nombre_norm}_{edad or ''}_{zona or ''}"
    hash_val = hashlib.md5(hash_str.encode()).hexdigest()

    async with db_session() as s:
        result = await s.execute(
            select(Persona).where(Persona.hash_dedup == hash_val)
        )
        return result.scalar_one_or_none()


async def get_persona(persona_id: int) -> Optional[Persona]:
    async with db_session() as s:
        result = await s.execute(
            select(Persona)
            .options(selectinload(Persona.avistamientos))
            .where(Persona.id == persona_id)
        )
        return result.scalar_one_or_none()


async def listar_personas(
    estado: Optional[str] = None,
    zona: Optional[str]   = None,
    solo_vulnerables: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> List[Persona]:
    async with db_session() as s:
        q = select(Persona)
        filtros = []
        if estado:
            filtros.append(Persona.estado == estado)
        if zona:
            filtros.append(Persona.zona.ilike(f"%{zona}%"))
        if solo_vulnerables:
            filtros.append(Persona.es_vulnerable == True)
        if filtros:
            q = q.where(and_(*filtros))
        q = q.order_by(Persona.prioridad, Persona.creado_en.desc())
        q = q.limit(limit).offset(offset)
        result = await s.execute(q)
        return list(result.scalars().all())


async def buscar_por_nombre(texto: str, limit: int = 20) -> List[Persona]:
    """Búsqueda SQL básica por nombre (complementa el fuzzy matching)."""
    async with db_session() as s:
        result = await s.execute(
            select(Persona).where(
                or_(
                    Persona.nombre.ilike(f"%{texto}%"),
                    Persona.apellidos.ilike(f"%{texto}%"),
                    Persona.cedula.ilike(f"%{texto}%"),
                )
            ).limit(limit)
        )
        return list(result.scalars().all())


async def actualizar_estado(persona_id: int, nuevo_estado: EstadoPersona) -> bool:
    async with db_session() as s:
        result = await s.execute(
            update(Persona)
            .where(Persona.id == persona_id)
            .values(estado=nuevo_estado, actualizado_en=datetime.utcnow())
        )
        return result.rowcount > 0


async def get_estadisticas() -> dict:
    """Estadísticas globales para el dashboard."""
    async with db_session() as s:
        total     = await s.scalar(select(func.count(Persona.id)))
        buscados  = await s.scalar(select(func.count(Persona.id)).where(Persona.estado == EstadoPersona.BUSCADO))
        localizados = await s.scalar(select(func.count(Persona.id)).where(Persona.estado == EstadoPersona.LOCALIZADO))
        vulnerables = await s.scalar(select(func.count(Persona.id)).where(
            and_(Persona.es_vulnerable == True, Persona.estado == EstadoPersona.BUSCADO)
        ))
        return {
            "total":       total or 0,
            "buscados":    buscados or 0,
            "localizados": localizados or 0,
            "vulnerables": vulnerables or 0,
        }


# ── CRUD: Avistamientos ────────────────────────────────────────────────

async def crear_avistamiento(datos: dict) -> Avistamiento:
    async with db_session() as s:
        av = Avistamiento(**datos)
        s.add(av)
        await s.flush()
        await s.refresh(av)
        return av


async def get_avistamientos_pendientes_notificar() -> List[Avistamiento]:
    """Avistamientos con score alto que aún no fueron notificados."""
    async with db_session() as s:
        result = await s.execute(
            select(Avistamiento)
            .options(selectinload(Avistamiento.persona))
            .where(and_(
                Avistamiento.score_total >= 0.60,
                Avistamiento.notificado == False,
            ))
            .order_by(Avistamiento.score_total.desc())
            .limit(50)
        )
        return list(result.scalars().all())


async def marcar_avistamiento_notificado(av_id: int):
    async with db_session() as s:
        await s.execute(
            update(Avistamiento)
            .where(Avistamiento.id == av_id)
            .values(notificado=True)
        )


# ── CRUD: Voluntarios ──────────────────────────────────────────────────

async def registrar_voluntario(chat_id: str, nombre: str, zona: str) -> Voluntario:
    async with db_session() as s:
        # Upsert: actualizar si ya existe
        result = await s.execute(select(Voluntario).where(Voluntario.chat_id == chat_id))
        vol = result.scalar_one_or_none()
        if vol:
            vol.zona = zona
            vol.activo = True
        else:
            vol = Voluntario(chat_id=chat_id, nombre=nombre, zona=zona)
            s.add(vol)
        await s.flush()
        return vol


async def get_voluntarios_en_zona(zona: str, limit: int = 100) -> List[Voluntario]:
    async with db_session() as s:
        result = await s.execute(
            select(Voluntario).where(
                and_(Voluntario.activo == True, Voluntario.zona.ilike(f"%{zona}%"))
            ).limit(limit)
        )
        return list(result.scalars().all())
