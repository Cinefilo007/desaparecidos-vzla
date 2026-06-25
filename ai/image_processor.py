"""
ai/image_processor.py — Registro inteligente por imagen con Gemini Vision.
Extrae datos de fichas "SE BUSCA", capturas de WhatsApp, fotos con texto, etc.
"""
import json
import re
import uuid
import asyncio
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import google.generativeai as genai
from PIL import Image
from loguru import logger

from config import settings

genai.configure(api_key=settings.gemini_api_key)

PROMPT_EXTRACCION = """
Analiza esta imagen. Puede ser una ficha "SE BUSCA", captura de WhatsApp,
foto con notas manuscritas, o simplemente una foto de una persona.

Extrae TODA la información visible y responde ÚNICAMENTE con JSON válido:

{
  "tipo_imagen": "ficha_se_busca|captura_whatsapp|foto_con_notas|foto_sola|recorte_noticia|desconocido",
  "tiene_cara_visible": true/false,
  "nombre": "nombre de pila o null",
  "apellidos": "apellidos completos o null",
  "cedula": "solo dígitos sin V- ni puntos, o null",
  "edad": número entero o null,
  "ultima_ubicacion": "lugar exacto donde fue visto por última vez o null",
  "zona": "ciudad o municipio principal o null",
  "fecha_desaparicion": "DD/MM/AAAA o null",
  "hora_desaparicion": "HH:MM o null",
  "descripcion_fisica": "altura, complexión, cabello, color de ojos, etc. o null",
  "ropa_ultima_vez": "descripción detallada de ropa o null",
  "senas_particulares": "cicatrices, tatuajes, lunares, usa lentes, etc. o null",
  "condicion_medica": "diabetes, hipertensión, embarazo, discapacidad, etc. o null",
  "es_vulnerable": true/false,
  "razon_vulnerabilidad": "menor_edad|adulto_mayor|embarazada|condicion_medica|discapacidad o null",
  "contacto_telefono": "número venezolano formato 04XX-XXX-XXXX o null",
  "contacto_nombre": "nombre de quien busca o null",
  "texto_completo": "TODO el texto visible en la imagen",
  "confianza": 0.0 a 1.0,
  "notas": "cualquier información relevante adicional o null"
}

Reglas importantes:
- Si el texto es manuscrito, intenta leerlo aunque sea difícil
- Marca es_vulnerable=true si: menor de 15 años, mayor de 65, embarazada, condición médica grave
- Para cédulas venezolanas: extrae SOLO los números (ej: "V-12.345.678" → "12345678")
- Si la imagen es solo una foto sin texto, devuelve null en todos los campos de texto
- El campo confianza refleja qué tan legible/completa es la información (0=ilegible, 1=perfecta)
"""


@dataclass
class DatosExtraidos:
    """Datos extraídos de una imagen."""
    nombre:               Optional[str] = None
    apellidos:            Optional[str] = None
    cedula:               Optional[str] = None
    edad:                 Optional[int] = None
    ultima_ubicacion:     Optional[str] = None
    zona:                 Optional[str] = None
    fecha_desaparicion:   Optional[str] = None
    hora_desaparicion:    Optional[str] = None
    descripcion_fisica:   Optional[str] = None
    ropa_ultima_vez:      Optional[str] = None
    senas_particulares:   Optional[str] = None
    condicion_medica:     Optional[str] = None
    es_vulnerable:        bool          = False
    razon_vulnerabilidad: Optional[str] = None
    contacto_telefono:    Optional[str] = None
    contacto_nombre:      Optional[str] = None
    tiene_cara_visible:   bool          = False
    tipo_imagen:          str           = "desconocido"
    confianza:            float         = 0.0
    notas:                Optional[str] = None
    campos_faltantes:     list          = field(default_factory=list)

    def nombre_completo(self) -> str:
        partes = [p for p in [self.nombre, self.apellidos] if p]
        return " ".join(partes) if partes else ""

    def to_persona_dict(self) -> dict:
        """Convierte a diccionario para crear una Persona en la BD."""
        return {k: v for k, v in {
            "nombre":               self.nombre,
            "apellidos":            self.apellidos,
            "cedula":               self.cedula,
            "edad":                 self.edad,
            "ultima_ubicacion":     self.ultima_ubicacion,
            "zona":                 self.zona,
            "fecha_desaparicion":   self.fecha_desaparicion,
            "descripcion_fisica":   self.descripcion_fisica,
            "ropa_ultima_vez":      self.ropa_ultima_vez,
            "senas_particulares":   self.senas_particulares,
            "condicion_medica":     self.condicion_medica,
            "es_vulnerable":        self.es_vulnerable,
            "razon_vulnerabilidad": self.razon_vulnerabilidad,
            "contacto_telefono":    self.contacto_telefono,
            "contacto_nombre":      self.contacto_nombre,
        }.items() if v is not None}


# ── Campos obligatorios y sus preguntas ───────────────────────────────

PREGUNTAS_CAMPOS = [
    # (campo, pregunta, obligatorio)
    ("nombre",           "¿Cuál es el *nombre completo* de la persona que buscas?", True),
    ("edad",             "¿Cuántos años tiene aproximadamente?", True),
    ("ultima_ubicacion", "¿Cuál fue el *último lugar* donde estuvo o de donde tenías noticias?", True),
    ("contacto_telefono","¿A qué *número de teléfono* debemos llamarte si tenemos noticias?", True),
    ("cedula",           "¿Tienes el *número de cédula*? (Escribe 'no sé' si no lo recuerdas)", False),
    ("ropa_ultima_vez",  "¿Recuerdas qué *ropa llevaba puesta* la última vez que la/lo viste?", False),
    ("senas_particulares","¿Tiene alguna *seña particular*? (tatuajes, cicatrices, lentes, etc.)", False),
    ("condicion_medica", "¿Tiene alguna *condición médica* importante? (diabetes, hipertensión, embarazo...)", False),
]


class ProcesadorImagenes:

    def __init__(self):
        self.model = genai.GenerativeModel(settings.gemini_model)

    async def extraer_datos(self, ruta_imagen: str) -> DatosExtraidos:
        """
        Extrae todos los datos posibles de una imagen usando Gemini Vision.
        Soporta: fichas SE BUSCA, capturas WhatsApp, fotos con texto, fotos solas.
        """
        try:
            img = Image.open(ruta_imagen)
            img = self._optimizar(img)

            response = await self.model.generate_content_async([img, PROMPT_EXTRACCION])
            datos_raw = self._parsear_json(response.text)

            datos = DatosExtraidos(
                nombre=datos_raw.get("nombre"),
                apellidos=datos_raw.get("apellidos"),
                cedula=self._limpiar_cedula(datos_raw.get("cedula")),
                edad=datos_raw.get("edad"),
                ultima_ubicacion=datos_raw.get("ultima_ubicacion"),
                zona=datos_raw.get("zona"),
                fecha_desaparicion=datos_raw.get("fecha_desaparicion"),
                hora_desaparicion=datos_raw.get("hora_desaparicion"),
                descripcion_fisica=datos_raw.get("descripcion_fisica"),
                ropa_ultima_vez=datos_raw.get("ropa_ultima_vez"),
                senas_particulares=datos_raw.get("senas_particulares"),
                condicion_medica=datos_raw.get("condicion_medica"),
                es_vulnerable=datos_raw.get("es_vulnerable", False),
                razon_vulnerabilidad=datos_raw.get("razon_vulnerabilidad"),
                contacto_telefono=datos_raw.get("contacto_telefono"),
                contacto_nombre=datos_raw.get("contacto_nombre"),
                tiene_cara_visible=datos_raw.get("tiene_cara_visible", False),
                tipo_imagen=datos_raw.get("tipo_imagen", "desconocido"),
                confianza=float(datos_raw.get("confianza", 0.5)),
                notas=datos_raw.get("notas"),
            )
            datos.campos_faltantes = self._calcular_faltantes(datos)
            logger.info(f"Imagen analizada: tipo={datos.tipo_imagen}, confianza={datos.confianza:.2f}")
            return datos

        except Exception as e:
            logger.error(f"Error analizando imagen: {e}")
            datos = DatosExtraidos()
            datos.campos_faltantes = self._calcular_faltantes(datos)
            return datos

    def generar_resumen_telegram(self, datos: DatosExtraidos) -> str:
        """Genera el mensaje de confirmación con los datos detectados."""
        lineas = []
        if datos.nombre_completo(): lineas.append(f"👤 *Nombre:*    {datos.nombre_completo()}")
        if datos.cedula:            lineas.append(f"🪪 *Cédula:*    V-{datos.cedula}")
        if datos.edad:              lineas.append(f"🎂 *Edad:*      {datos.edad} años")
        if datos.ultima_ubicacion:  lineas.append(f"📍 *Visto en:*  {datos.ultima_ubicacion}")
        if datos.fecha_desaparicion:lineas.append(f"📅 *Fecha:*     {datos.fecha_desaparicion}")
        if datos.ropa_ultima_vez:   lineas.append(f"👕 *Ropa:*      {datos.ropa_ultima_vez}")
        if datos.senas_particulares:lineas.append(f"⚠️ *Señas:*     {datos.senas_particulares}")
        if datos.condicion_medica:  lineas.append(f"🏥 *Condición:* {datos.condicion_medica}")
        if datos.contacto_telefono: lineas.append(f"📞 *Contacto:*  {datos.contacto_telefono}")

        emoji_conf = "🟢" if datos.confianza > 0.75 else ("🟡" if datos.confianza > 0.4 else "🔴")
        tabla = "\n".join(lineas) if lineas else "_No se pudo extraer información del texto_"
        vulnerable_aviso = "\n\n⚠️ *PERSONA VULNERABLE — PRIORIDAD ALTA*" if datos.es_vulnerable else ""

        return (
            f"✅ *Encontré la siguiente información:*\n\n"
            f"📋 *DATOS DETECTADOS* {emoji_conf}\n"
            f"─────────────────────\n"
            f"{tabla}\n"
            f"─────────────────────"
            f"{vulnerable_aviso}"
        )

    def _calcular_faltantes(self, datos: DatosExtraidos) -> list:
        faltantes = []
        for campo, pregunta, obligatorio in PREGUNTAS_CAMPOS:
            if not getattr(datos, campo, None):
                faltantes.append({
                    "campo": campo,
                    "pregunta": pregunta,
                    "obligatorio": obligatorio,
                })
        return faltantes

    def _optimizar(self, img: Image.Image) -> Image.Image:
        img.thumbnail((1024, 1024), Image.LANCZOS)
        return img.convert("RGB")

    def _limpiar_cedula(self, cedula: Optional[str]) -> Optional[str]:
        if not cedula:
            return None
        limpia = re.sub(r"[^0-9]", "", str(cedula))
        return limpia if 5 <= len(limpia) <= 9 else None

    def _parsear_json(self, texto: str) -> dict:
        try:
            return json.loads(texto)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", texto, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except Exception:
                    pass
        return {}


# Instancia global
procesador_imagenes = ProcesadorImagenes()
