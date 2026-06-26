"""
database/crud.py — Operaciones CRUD asíncronas sobre la base de datos.
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select, update, func, and_, or_, text
from sqlalchemy.orm import selectinload
from typing import Optional, List, Dict
from datetime import datetime
import hashlib
from loguru import logger

from config import settings
from database.models import Base, Persona, Avistamiento, Alerta, Voluntario, EstadoPersona, SuscripcionAlerta, FuenteScraping, IngresoHospital

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
    """Crea todas las tablas si no existen y corre migraciones necesarias."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        # Migraciones en caliente para columnas de rostro y ampliación de campos VARCHAR
        try:
            # Sintaxis PostgreSQL - Crear nuevas columnas
            await conn.execute(text("ALTER TABLE personas ADD COLUMN IF NOT EXISTS foto_rostro_local_path VARCHAR(500);"))
            await conn.execute(text("ALTER TABLE personas ADD COLUMN IF NOT EXISTS foto_rostro_url VARCHAR(500);"))
            
            # Sintaxis PostgreSQL - Ampliar longitud de columnas para evitar truncamientos
            await conn.execute(text("ALTER TABLE personas ALTER COLUMN cedula TYPE VARCHAR(100);"))
            await conn.execute(text("ALTER TABLE personas ALTER COLUMN fecha_nacimiento TYPE VARCHAR(100);"))
            await conn.execute(text("ALTER TABLE personas ALTER COLUMN genero TYPE VARCHAR(100);"))
            await conn.execute(text("ALTER TABLE personas ALTER COLUMN fecha_desaparicion TYPE VARCHAR(100);"))
            await conn.execute(text("ALTER TABLE personas ALTER COLUMN hora_desaparicion TYPE VARCHAR(100);"))
            await conn.execute(text("ALTER TABLE personas ALTER COLUMN contacto_telefono TYPE VARCHAR(100);"))
            
            # Migraciones para ingresos_hospitales
            await conn.execute(text("ALTER TABLE ingresos_hospitales ADD COLUMN IF NOT EXISTS cedula VARCHAR(100);"))
            await conn.execute(text("ALTER TABLE ingresos_hospitales ADD COLUMN IF NOT EXISTS genero VARCHAR(50);"))
            await conn.execute(text("ALTER TABLE ingresos_hospitales ADD COLUMN IF NOT EXISTS estatus_actual VARCHAR(100);"))
            await conn.execute(text("ALTER TABLE ingresos_hospitales ADD COLUMN IF NOT EXISTS observaciones TEXT;"))
            await conn.execute(text("ALTER TABLE ingresos_hospitales ADD COLUMN IF NOT EXISTS persona_id_vinculada INTEGER;"))
            
            logger.info("Migración en caliente (PostgreSQL): Columnas verificadas, añadidas y ampliadas a 100 caracteres ✓")
        except Exception as e:
            # Fallback para SQLite de desarrollo
            cols = [
                "ALTER TABLE personas ADD COLUMN foto_rostro_local_path VARCHAR(500);",
                "ALTER TABLE personas ADD COLUMN foto_rostro_url VARCHAR(500);",
                "ALTER TABLE ingresos_hospitales ADD COLUMN cedula VARCHAR(100);",
                "ALTER TABLE ingresos_hospitales ADD COLUMN genero VARCHAR(50);",
                "ALTER TABLE ingresos_hospitales ADD COLUMN estatus_actual VARCHAR(100);",
                "ALTER TABLE ingresos_hospitales ADD COLUMN observaciones TEXT;",
                "ALTER TABLE ingresos_hospitales ADD COLUMN persona_id_vinculada INTEGER;"
            ]
            for col_query in cols:
                try:
                    await conn.execute(text(col_query))
                except Exception:
                    pass
            logger.info("Migración en caliente (SQLite): Columnas revisadas ✓")


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

async def listar_hospitales_refugios() -> Dict[str, List[dict]]:
    """Devuelve personas agrupadas por su ultima_ubicacion (hospitales/refugios)."""
    async with db_session() as s:
        # Traer a todos los que tengan ultima_ubicacion (generalmente LOCALIZADOS)
        q = select(Persona).where(Persona.ultima_ubicacion != None, Persona.ultima_ubicacion != "")
        result = await s.execute(q)
        personas = result.scalars().all()
        
        agrupados = {}
        for p in personas:
            ubi = p.ultima_ubicacion.strip()
            if not ubi:
                continue
            if ubi not in agrupados:
                agrupados[ubi] = []
            
            agrupados[ubi].append({
                "id": p.id,
                "nombre": p.nombre_completo(),
                "edad": p.edad,
                "cedula": p.cedula,
                "foto_url": p.foto_url,
                "estado": p.estado.value if p.estado else None
            })
            
        # Consultar también la nueva tabla de ingresos_hospitales
        from database.models import IngresoHospital
        q_ingresos = select(IngresoHospital)
        result_ing = await s.execute(q_ingresos)
        ingresos = result_ing.scalars().all()
        
        for ing in ingresos:
            ubi = ing.hospital_nombre.strip()
            if not ubi:
                continue
            if ubi not in agrupados:
                agrupados[ubi] = []
                
            agrupados[ubi].append({
                "id": f"ingreso_{ing.id}",
                "nombre": ing.nombre_completo,
                "edad": ing.edad,
                "cedula": getattr(ing, 'cedula', None),
                "foto_url": None, # Los ingresos directos usualmente no tienen foto individual
                "estado": "Ingresado",
                "detalles": ing.detalles_ingreso
            })
            
        # Filtrar duplicados dentro de la misma ubicación por nombre y cédula
        for ubi, lista in agrupados.items():
            unicos = []
            vistos = set()
            for p in lista:
                clave = p.get("cedula")
                if not clave:
                    clave = p["nombre"].lower()
                if clave not in vistos:
                    vistos.add(clave)
                    unicos.append(p)
            agrupados[ubi] = unicos
            
        return agrupados


async def buscar_personas(filtros: dict, limite: int = 50) -> List[Persona]:
    async with db_session() as s:
        q = select(Persona).order_by(Persona.creado_en.desc())
        
        # Filtros básicos
        if "estado" in filtros:
            q = q.where(Persona.estado == filtros["estado"])
        if "prioridad" in filtros:
            q = q.where(Persona.prioridad == filtros["prioridad"])
            
        # Filtro por ubicación / zona
        if "zona" in filtros:
            q = q.where(Persona.zona.ilike(f"%{filtros['zona']}%"))
            
        # Filtro de búsqueda de texto
        text_query = filtros.get("query", "").strip()
        if text_query:
            # Buscar en nombre, apellidos o cedula
            q = q.where(
                or_(
                    Persona.nombre.ilike(f"%{text_query}%"),
                    Persona.apellidos.ilike(f"%{text_query}%"),
                    Persona.cedula.ilike(f"%{text_query}%")
                )
            )
            
        q = q.limit(limite)
        result = await s.execute(q)
        return list(result.scalars().all())

async def listar_personas_desaparecidas(limite: int = 5) -> List[Persona]:
    """Retorna un lote de personas buscadas, priorizando las vulnerables."""
    async with db_session() as s:
        result = await s.execute(
            select(Persona)
            .where(Persona.estado == EstadoPersona.BUSCADO)
            .order_by(Persona.es_vulnerable.desc(), Persona.creado_en.asc())
            .limit(limite)
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


# ── CRUD: Suscripciones de Alertas ─────────────────────────────────────

async def suscribir_a_persona(persona_id: int, chat_id: str) -> SuscripcionAlerta:
    async with db_session() as s:
        # Evitar duplicados
        result = await s.execute(
            select(SuscripcionAlerta).where(
                and_(
                    SuscripcionAlerta.persona_id == persona_id,
                    SuscripcionAlerta.chat_id == chat_id
                )
            )
        )
        sub = result.scalar_one_or_none()
        if not sub:
            sub = SuscripcionAlerta(persona_id=persona_id, chat_id=chat_id)
            s.add(sub)
            await s.flush()
        return sub


async def desuscribir_de_persona(persona_id: int, chat_id: str) -> bool:
    async with db_session() as s:
        result = await s.execute(
            select(SuscripcionAlerta).where(
                and_(
                    SuscripcionAlerta.persona_id == persona_id,
                    SuscripcionAlerta.chat_id == chat_id
                )
            )
        )
        sub = result.scalar_one_or_none()
        if sub:
            await s.delete(sub)
            return True
        return False


async def obtener_suscritos(persona_id: int) -> List[str]:
    async with db_session() as s:
        result = await s.execute(
            select(SuscripcionAlerta.chat_id).where(SuscripcionAlerta.persona_id == persona_id)
        )
        return list(result.scalars().all())


async def es_usuario_suscrito(persona_id: int, chat_id: str) -> bool:
    async with db_session() as s:
        result = await s.execute(
            select(SuscripcionAlerta).where(
                and_(
                    SuscripcionAlerta.persona_id == persona_id,
                    SuscripcionAlerta.chat_id == chat_id
                )
            )
        )
        return result.scalar_one_or_none() is not None


# ── CRUD: Fuentes de Scraping ──────────────────────────────────────────

async def crear_fuente_scraping(nombre: str, url: str, tipo: str = "web") -> FuenteScraping:
    async with db_session() as s:
        # Comprobar si ya existe
        result = await s.execute(select(FuenteScraping).where(FuenteScraping.url == url))
        fuente = result.scalar_one_or_none()
        if not fuente:
            fuente = FuenteScraping(nombre=nombre, url=url, tipo=tipo, activa=True)
            s.add(fuente)
            await s.flush()
            await s.refresh(fuente)
        return fuente


async def listar_fuentes_scraping(solo_activas: bool = True) -> List[FuenteScraping]:
    async with db_session() as s:
        q = select(FuenteScraping)
        if solo_activas:
            q = q.where(FuenteScraping.activa == True)
        result = await s.execute(q)
        return list(result.scalars().all())


async def desactivar_fuente_scraping(fuente_id: int) -> bool:
    async with db_session() as s:
        result = await s.execute(select(FuenteScraping).where(FuenteScraping.id == fuente_id))
        fuente = result.scalar_one_or_none()
        if fuente:
            fuente.activa = False
            return True
        return False


# ── CRUD: Ingresos Hospitales ──────────────────────────────────────────

async def registrar_ingreso_hospital(datos: dict) -> IngresoHospital:
    """Registra el ingreso de una persona en un hospital y busca coincidencias con desaparecidos."""
    nombre_completo = datos.get("nombre_completo", "")
    cedula_ingreso = datos.get("cedula")
    from loguru import logger
    
    async with db_session() as s:
        # 1. Crear el registro del ingreso en hospital
        ingreso = IngresoHospital(**datos)
        s.add(ingreso)
        await s.flush()
        
        # 2. Intentar coincidencia por cédula primero
        persona_coincidente = None
        if cedula_ingreso:
            # Normalizar cédula básica
            ced_limpia = "".join(filter(str.isdigit, str(cedula_ingreso)))
            if ced_limpia:
                coincidencia_ced = await s.execute(
                    select(Persona).where(
                        and_(
                            Persona.estado == EstadoPersona.BUSCADO,
                            Persona.cedula.like(f"%{ced_limpia}%")
                        )
                    )
                )
                persona_coincidente = coincidencia_ced.scalars().first()

        # 3. Si no hay cédula, buscar por coincidencias de nombre
        if not persona_coincidente and nombre_completo:
            partes_nombre = nombre_completo.split()
            if partes_nombre:
                query_parts = []
                for p in partes_nombre[:3]:  # primeras 3 palabras
                    if len(p) > 2:
                        query_parts.append(Persona.nombre.ilike(f"%{p}%"))
                        query_parts.append(Persona.apellidos.ilike(f"%{p}%"))
                
                if query_parts:
                    coincidencias = await s.execute(
                        select(Persona).where(
                            and_(
                                Persona.estado == EstadoPersona.BUSCADO,
                                or_(*query_parts)
                            )
                        )
                    )
                    persona_coincidente = coincidencias.scalars().first()
                
        if persona_coincidente:
            ingreso.persona_id_vinculada = persona_coincidente.id
            logger.info(f"[Hospital] Coincidencia detectada: Ingreso '{nombre_completo}' vinculado a Persona #{persona_coincidente.id}")
                
        await s.refresh(ingreso)
        return ingreso


# ── CRUD: Estadísticas de Scraping ─────────────────────────────────────

async def get_scraping_stats() -> dict:
    async with db_session() as s:
        result = await s.execute(select(ScrapingStat).where(ScrapingStat.clave == 'global_stats'))
        stat = result.scalars().first()
        if not stat:
            stat = ScrapingStat(clave='global_stats')
            s.add(stat)
            await s.commit()
            await s.refresh(stat)
        return {
            "sitios_revisados": stat.sitios_revisados,
            "busquedas_realizadas": stat.busquedas_realizadas,
            "similitudes_halladas": stat.similitudes_halladas,
            "ultima_ejecucion": stat.ultima_ejecucion.isoformat() if stat.ultima_ejecucion else None
        }

async def update_scraping_stats(sitios: int = 0, busquedas: int = 0, similitudes: int = 0):
    async with db_session() as s:
        result = await s.execute(select(ScrapingStat).where(ScrapingStat.clave == 'global_stats'))
        stat = result.scalars().first()
        if not stat:
            stat = ScrapingStat(clave='global_stats')
            s.add(stat)
        
        stat.sitios_revisados += sitios
        stat.busquedas_realizadas += busquedas
        stat.similitudes_halladas += similitudes
        stat.ultima_ejecucion = datetime.utcnow()
        await s.commit()


async def listar_ingresos_hospitales(limite: int = 100) -> List[IngresoHospital]:
    async with db_session() as s:
        result = await s.execute(
            select(IngresoHospital)
            .options(selectinload(IngresoHospital.persona))
            .order_by(IngresoHospital.creado_en.desc())
            .limit(limite)
        )
        return list(result.scalars().all())
