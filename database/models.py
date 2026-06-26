"""
database/models.py — Modelos SQLAlchemy para la base de datos.
"""
import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime,
    Text, Enum as SAEnum, ForeignKey, Index
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ── Enumeraciones ──────────────────────────────────────────────────────

class EstadoPersona(str, enum.Enum):
    BUSCADO    = "buscado"      # Sin noticias
    POSIBLE    = "posible"      # Hay un posible avistamiento
    LOCALIZADO = "localizado"   # Fue encontrado
    FALLECIDO  = "fallecido"    # Confirmado fallecido (manejo delicado)

class Prioridad(str, enum.Enum):
    CRITICA = "critica"   # Vulnerable: menor, embarazada, anciano, condición médica
    ALTA    = "alta"      # +24h sin contacto
    MEDIA   = "media"     # Búsqueda normal
    BAJA    = "baja"      # Registro reciente

class FuenteRegistro(str, enum.Enum):
    TELEGRAM   = "telegram"
    MINIAPP    = "miniapp"
    SCRAPER    = "scraper"
    ADMIN      = "admin"


# ── Tabla principal: Personas ──────────────────────────────────────────

class Persona(Base):
    __tablename__ = "personas"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    
    # Datos de identidad
    nombre          = Column(String(150), nullable=False, index=True)
    apellidos       = Column(String(150), nullable=True)
    cedula          = Column(String(100),  nullable=True, index=True)
    edad            = Column(Integer,     nullable=True)
    fecha_nacimiento= Column(String(100),  nullable=True)
    genero          = Column(String(100),  nullable=True)
    
    # Ubicación y tiempo
    ultima_ubicacion= Column(String(300), nullable=True)
    zona            = Column(String(100), nullable=True, index=True)
    lat             = Column(Float,       nullable=True)
    lng             = Column(Float,       nullable=True)
    fecha_desaparicion = Column(String(100), nullable=True)
    hora_desaparicion  = Column(String(100), nullable=True)

    # Descripción física
    descripcion_fisica = Column(Text, nullable=True)
    ropa_ultima_vez    = Column(Text, nullable=True)
    senas_particulares = Column(Text, nullable=True)
    condicion_medica   = Column(Text, nullable=True)

    # Estado y prioridad
    estado    = Column(SAEnum(EstadoPersona), default=EstadoPersona.BUSCADO, index=True)
    prioridad = Column(SAEnum(Prioridad),     default=Prioridad.MEDIA,       index=True)
    es_vulnerable = Column(Boolean, default=False, index=True)
    razon_vulnerabilidad = Column(String(100), nullable=True)

    # Fotos y biometría
    foto_url         = Column(String(500), nullable=True)
    foto_local_path  = Column(String(500), nullable=True)
    foto_rostro_local_path = Column(String(500), nullable=True)
    foto_rostro_url        = Column(String(500), nullable=True)
    tiene_embedding  = Column(Boolean, default=False)   # Cara en FAISS
    faiss_index_id   = Column(Integer, nullable=True)   # ID en el índice FAISS

    # Contacto del familiar
    contacto_nombre   = Column(String(150), nullable=True)
    contacto_telefono = Column(String(100),  nullable=True)
    contacto_chat_id  = Column(String(50),  nullable=True)  # Telegram chat ID

    # Metadatos
    fuente_registro = Column(SAEnum(FuenteRegistro), default=FuenteRegistro.TELEGRAM)
    creado_en       = Column(DateTime, default=datetime.utcnow, index=True)
    actualizado_en  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    hash_dedup      = Column(String(64), nullable=True, index=True)  # Anti-duplicados

    # Relaciones
    avistamientos = relationship("Avistamiento", back_populates="persona", cascade="all, delete-orphan")
    alertas       = relationship("Alerta",       back_populates="persona", cascade="all, delete-orphan")

    def nombre_completo(self) -> str:
        if self.apellidos:
            return f"{self.nombre} {self.apellidos}"
        return self.nombre

    def __repr__(self):
        return f"<Persona id={self.id} nombre='{self.nombre_completo()}' estado={self.estado}>"


# ── Avistamientos / Evidencias ─────────────────────────────────────────

class Avistamiento(Base):
    __tablename__ = "avistamientos"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    persona_id  = Column(Integer, ForeignKey("personas.id"), nullable=False, index=True)

    fuente      = Column(String(100), nullable=False)  # URL, nombre de canal, etc.
    plataforma  = Column(String(50),  nullable=True)   # twitter, tiktok, web, etc.
    descripcion = Column(Text,        nullable=True)
    url_original= Column(String(500), nullable=True)
    foto_url    = Column(String(500), nullable=True)

    # Score de confianza 0.0–1.0
    score_nombre = Column(Float, default=0.0)
    score_cara   = Column(Float, default=0.0)
    score_total  = Column(Float, default=0.0, index=True)

    ubicacion    = Column(String(200), nullable=True)
    lat          = Column(Float, nullable=True)
    lng          = Column(Float, nullable=True)

    verificado   = Column(Boolean, default=False)
    notificado   = Column(Boolean, default=False)

    creado_en    = Column(DateTime, default=datetime.utcnow, index=True)

    persona = relationship("Persona", back_populates="avistamientos")

    def nivel_confianza(self) -> str:
        if self.score_total >= 0.75: return "🔴 ALTO"
        if self.score_total >= 0.50: return "🟡 POSIBLE"
        return "⚪ DÉBIL"


# ── Alertas enviadas ───────────────────────────────────────────────────

class Alerta(Base):
    __tablename__ = "alertas"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    persona_id     = Column(Integer, ForeignKey("personas.id"), nullable=False, index=True)
    avistamiento_id= Column(Integer, ForeignKey("avistamientos.id"), nullable=True)

    chat_id        = Column(String(50),  nullable=False)
    mensaje        = Column(Text,        nullable=False)
    tipo           = Column(String(50),  default="match")  # match | encontrado | actualización
    enviada        = Column(Boolean,     default=False)
    error          = Column(String(200), nullable=True)

    creado_en      = Column(DateTime, default=datetime.utcnow)

    persona = relationship("Persona", back_populates="alertas")


# ── Suscripciones a alertas de personas desaparecidas ──────────────────

class SuscripcionAlerta(Base):
    __tablename__ = "suscripciones_alertas"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    persona_id = Column(Integer, ForeignKey("personas.id"), nullable=False, index=True)
    chat_id    = Column(String(50), nullable=False, index=True)
    creado_en  = Column(DateTime, default=datetime.utcnow)

    persona = relationship("Persona", backref="suscripciones")


# ── Fuentes dinámicas de scraping ───────────────────────────────────────

class FuenteScraping(Base):
    __tablename__ = "fuentes_scraping"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    nombre    = Column(String(150), nullable=False)
    url       = Column(String(500), nullable=False, unique=True)
    tipo      = Column(String(50), default="web")  # web | twitter_profile | telegram_channel | rss
    activa    = Column(Boolean, default=True, index=True)
    creado_en = Column(DateTime, default=datetime.utcnow)


# ── Reportes de Ingresos en Hospitales ─────────────────────────────────

class IngresoHospital(Base):
    __tablename__ = "ingresos_hospitales"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    nombre_completo     = Column(String(300), nullable=False, index=True)
    edad                = Column(Integer, nullable=True)
    hospital_nombre     = Column(String(200), nullable=False)
    fecha_ingreso       = Column(String(30), nullable=True)
    detalles_ingreso    = Column(Text, nullable=True)
    persona_id_vinculada= Column(Integer, ForeignKey("personas.id"), nullable=True, index=True)
    creado_en           = Column(DateTime, default=datetime.utcnow)

    persona = relationship("Persona", backref="ingresos_hospitales")


# ── Voluntarios de búsqueda colectiva ─────────────────────────────────

class Voluntario(Base):
    __tablename__ = "voluntarios"

    id       = Column(Integer, primary_key=True, autoincrement=True)
    chat_id  = Column(String(50),  unique=True, nullable=False)
    nombre   = Column(String(150), nullable=True)
    zona     = Column(String(100), nullable=True, index=True)
    lat      = Column(Float, nullable=True)
    lng      = Column(Float, nullable=True)
    activo   = Column(Boolean, default=True, index=True)
    creado_en= Column(DateTime, default=datetime.utcnow)


# ── Índices compuestos para búsquedas frecuentes ──────────────────────
Index("ix_personas_estado_prioridad", Persona.estado, Persona.prioridad)
Index("ix_personas_zona_estado",      Persona.zona,   Persona.estado)
Index("ix_avistamientos_score",       Avistamiento.score_total, Avistamiento.notificado)
