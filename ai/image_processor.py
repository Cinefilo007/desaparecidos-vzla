"""
ai/image_processor.py — Registro inteligente por imagen con Gemini Vision.
Extrae datos de fichas "SE BUSCA", capturas de WhatsApp, fotos con texto, etc.
"""
import json
import re
import uuid
import asyncio
import cv2
import numpy as np
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
foto con notas manuscritas, un listado de personas o simplemente una foto de una persona.
Puede contener UNA o MÁS personas.

Extrae TODA la información visible y responde ÚNICAMENTE con JSON válido:

{
  "tipo_imagen": "ficha_se_busca|captura_whatsapp|foto_con_notas|foto_sola|recorte_noticia|listado|desconocido",
  "personas": [
    {
      "tiene_cara_visible": true/false,
      "caja_delimitadora_rostro": [ymin, xmin, ymax, xmax] o null,
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
      "contacto_nombre": "nombre de quien busca o null"
    }
  ],
  "hospital_nombre_detectado": "nombre del hospital, refugio o centro asistencial si se menciona, o null",
  "texto_completo": "TODO el texto visible en la imagen",
  "confianza": 0.0 a 1.0,
  "notas": "cualquier información relevante adicional o null"
}

Reglas importantes:
- Extrae a CADA persona que se mencione o se muestre de forma clara. Si hay 3 personas en la ficha, el array "personas" debe tener 3 elementos.
- Si el texto es manuscrito, intenta leerlo aunque sea difícil.
- Marca es_vulnerable=true si: menor de 15 años, mayor de 65, embarazada, condición médica grave.
- Para cédulas venezolanas: extrae SOLO los números (ej: "V-12.345.678" → "12345678").
- Si la imagen es solo una foto sin texto, devuelve null en todos los campos de texto.
- Para "caja_delimitadora_rostro": si "tiene_cara_visible" es verdadero, devuelve la caja delimitadora del rostro de ESA persona como una lista de cuatro enteros [ymin, xmin, ymax, xmax] normalizados de 0 a 1000 con respecto al alto y ancho de la imagen (donde [0, 0, 1000, 1000] representa la imagen completa). Si no es visible, devuelve null.
- El campo confianza refleja qué tan legible/completa es la información (0=ilegible, 1=perfecta).
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
    genero:               Optional[str] = None
    estatus_actual:       Optional[str] = None
    es_vulnerable:        bool          = False
    razon_vulnerabilidad: Optional[str] = None
    contacto_telefono:    Optional[str] = None
    contacto_nombre:      Optional[str] = None
    tiene_cara_visible:   bool          = False
    caja_delimitadora_rostro: Optional[list] = None
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

    async def extraer_datos(self, ruta_imagen: str) -> tuple[list[DatosExtraidos], Optional[str]]:
        """
        Extrae todos los datos posibles de una imagen usando Gemini Vision.
        Retorna una tupla: (lista_personas, hospital_detectado).
        """
        try:
            img = Image.open(ruta_imagen)
            img = self._optimizar(img)

            response = await self.model.generate_content_async([img, PROMPT_EXTRACCION])
            datos_raw = self._parsear_json(response.text)

            personas_raw = datos_raw.get("personas", [])
            # Compatibilidad hacia atrás o si Gemini falla en usar el nuevo formato
            if not personas_raw and any(k in datos_raw for k in ["nombre", "apellidos", "cedula", "ropa_ultima_vez"]):
                personas_raw = [datos_raw]

            resultados = []
            for p_raw in personas_raw:
                datos = DatosExtraidos(
                    nombre=p_raw.get("nombre"),
                    apellidos=p_raw.get("apellidos"),
                    cedula=self._limpiar_cedula(p_raw.get("cedula")),
                    edad=p_raw.get("edad"),
                    ultima_ubicacion=p_raw.get("ultima_ubicacion"),
                    zona=p_raw.get("zona"),
                    fecha_desaparicion=p_raw.get("fecha_desaparicion"),
                    hora_desaparicion=p_raw.get("hora_desaparicion"),
                    descripcion_fisica=p_raw.get("descripcion_fisica"),
                    ropa_ultima_vez=p_raw.get("ropa_ultima_vez"),
                    senas_particulares=p_raw.get("senas_particulares"),
                    condicion_medica=p_raw.get("condicion_medica"),
                    es_vulnerable=p_raw.get("es_vulnerable", False),
                    razon_vulnerabilidad=p_raw.get("razon_vulnerabilidad"),
                    contacto_telefono=p_raw.get("contacto_telefono"),
                    contacto_nombre=p_raw.get("contacto_nombre"),
                    tiene_cara_visible=p_raw.get("tiene_cara_visible", False),
                    caja_delimitadora_rostro=p_raw.get("caja_delimitadora_rostro"),
                    tipo_imagen=datos_raw.get("tipo_imagen", "desconocido"),
                    confianza=float(datos_raw.get("confianza", 0.5)),
                    notas=datos_raw.get("notas"),
                )
                datos.campos_faltantes = self._calcular_faltantes(datos)
                resultados.append(datos)
                
            logger.info(f"Imagen analizada: {len(resultados)} personas detectadas. Tipo={datos_raw.get('tipo_imagen')}")
            return resultados, datos_raw.get("hospital_nombre_detectado")

        except Exception as e:
            logger.error(f"Error analizando imagen: {e}")
            return [], None

    async def extraer_datos_de_texto(self, texto_bruto: str) -> tuple[list[DatosExtraidos], Optional[str]]:
        """
        Extrae datos estructurados desde texto en crudo (ej: extraído de un PDF, Word, o Excel).
        Retorna una tupla: (lista_personas, hospital_detectado).
        """
        try:
            prompt_texto = f"""
{PROMPT_EXTRACCION}

--- TEXTO A ANALIZAR ---
{texto_bruto}
"""
            response = await self.model.generate_content_async(prompt_texto)
            datos_raw = self._parsear_json(response.text)

            personas_raw = datos_raw.get("personas", [])
            if not personas_raw and any(k in datos_raw for k in ["nombre", "apellidos", "cedula"]):
                personas_raw = [datos_raw]

            resultados = []
            for p_raw in personas_raw:
                datos = DatosExtraidos(
                    nombre=p_raw.get("nombre"),
                    apellidos=p_raw.get("apellidos"),
                    cedula=self._limpiar_cedula(p_raw.get("cedula")),
                    edad=p_raw.get("edad"),
                    genero=p_raw.get("genero"),
                    ultima_ubicacion=p_raw.get("ultima_ubicacion"),
                    zona=p_raw.get("zona"),
                    fecha_desaparicion=p_raw.get("fecha_desaparicion"),
                    hora_desaparicion=p_raw.get("hora_desaparicion"),
                    descripcion_fisica=p_raw.get("descripcion_fisica"),
                    ropa_ultima_vez=p_raw.get("ropa_ultima_vez"),
                    senas_particulares=p_raw.get("senas_particulares"),
                    condicion_medica=p_raw.get("condicion_medica"),
                    estatus_actual=p_raw.get("estatus_actual"),
                    es_vulnerable=p_raw.get("es_vulnerable", False),
                    razon_vulnerabilidad=p_raw.get("razon_vulnerabilidad"),
                    contacto_telefono=p_raw.get("contacto_telefono"),
                    contacto_nombre=p_raw.get("contacto_nombre"),
                    tipo_imagen="documento_texto",
                    confianza=float(datos_raw.get("confianza", 0.5)),
                    notas=datos_raw.get("notas"),
                )
                datos.campos_faltantes = self._calcular_faltantes(datos)
                resultados.append(datos)
                
            logger.info(f"Texto analizado: {len(resultados)} personas detectadas.")
            return resultados, datos_raw.get("hospital_nombre_detectado")

        except Exception as e:
            logger.error(f"Error analizando texto con IA: {e}")
            return [], None

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

    def recortar_rostro(self, ruta_imagen: str, caja: list = None) -> Optional[str]:
        """
        Recorta el rostro principal de una imagen usando OpenCV Haar Cascades.
        Si falla, usa el parámetro 'caja' de Gemini como fallback.
        """
        try:
            # Cargar la imagen con OpenCV
            img_cv = cv2.imread(ruta_imagen)
            if img_cv is None:
                logger.error(f"No se pudo cargar la imagen para OpenCV: {ruta_imagen}")
                return None
            
            gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
            img_h, img_w = img_cv.shape[:2]
            
            # Cargar el clasificador pre-entrenado frontal de OpenCV
            face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
            
            # Detectar rostros
            faces = face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(30, 30)
            )
            
            if len(faces) == 0:
                logger.warning(f"OpenCV no detectó rostros en {ruta_imagen}, intentando con caja de Gemini")
                if caja and len(caja) == 4:
                    # Gemini retorna [ymin, xmin, ymax, xmax] en escala 0-1000
                    ymin, xmin, ymax, xmax = caja
                    y = int((ymin / 1000.0) * img_h)
                    x = int((xmin / 1000.0) * img_w)
                    h = int(((ymax - ymin) / 1000.0) * img_h)
                    w = int(((xmax - xmin) / 1000.0) * img_w)
                else:
                    return None
            else:
                # Asumir que el rostro principal es el más grande
                faces = sorted(faces, key=lambda f: f[2]*f[3], reverse=True)
                x, y, w, h = faces[0]
            
            # Añadir padding (30%)
            pad_x = int(w * 0.3)
            pad_y = int(h * 0.3)
            
            left = max(0, x - pad_x)
            top = max(0, y - pad_y)
            right = min(img_w, x + w + pad_x)
            bottom = min(img_h, y + h + pad_y)
            
            # Recortar usando las coordenadas con padding
            rostro_cv = img_cv[top:bottom, left:right]
            
            # Guardar la nueva imagen
            p = Path(ruta_imagen)
            ruta_rostro = p.parent / f"rostro_{p.stem}.jpg"
            cv2.imwrite(str(ruta_rostro), rostro_cv)
            
            return str(ruta_rostro)
            
        except Exception as e:
            logger.error(f"Error recortando rostro con OpenCV: {e}")
            return None

    async def comparar_rostros_gemini(self, ruta_foto_busqueda: str, lista_candidatos: list) -> Optional[dict]:
        """
        Compara una foto de búsqueda con los rostros de una lista de candidatos usando Gemini.
        lista_candidatos es una lista de diccionarios: [{"id": 1, "nombre": "Juan", "foto_rostro_path": "..."}]
        Retorna el diccionario del candidato que coincide y el score de confianza, o None.
        """
        try:
            from PIL import Image
            # Filtrar solo los candidatos que tengan foto de rostro válida localmente
            candidatos_validos = [c for c in lista_candidatos if c.get("foto_rostro_path") and Path(c["foto_rostro_path"]).exists()]
            if not candidatos_validos:
                logger.warning("[IA] No hay candidatos con foto de rostro válida localmente para comparar.")
                return None
            
            # Abrir imagen de búsqueda
            img_busqueda = Image.open(ruta_foto_busqueda)
            img_busqueda = self._optimizar(img_busqueda)
            
            # Cargar imágenes de candidatos (limitar a top 5 por coste de tokens y API)
            candidatos_validos = candidatos_validos[:5]
            
            payload = [img_busqueda]
            
            descripcion_candidatos = []
            for i, cand in enumerate(candidatos_validos):
                img_cand = Image.open(cand["foto_rostro_path"])
                img_cand = self._optimizar(img_cand)
                payload.append(img_cand)
                descripcion_candidatos.append(f"Candidato #{i+1} (ID de Base de Datos: {cand['id']}): {cand['nombre']}")
            
            candidatos_text = "\n".join(descripcion_candidatos)
            
            prompt = f"""
Te proporciono una imagen de búsqueda (la primera imagen) y una lista de fotos de rostro de {len(candidatos_validos)} candidatos registrados:

{candidatos_text}

Analiza minuciosamente los rasgos faciales (forma de ojos, nariz, boca, distancia interpupilar, cejas, marcas, etc.).
Determina si la persona de la imagen de búsqueda coincide visualmente con alguno de los candidatos registrados.

Responde ÚNICAMENTE con JSON válido en este formato:
{{
  "coincide": true/false,
  "candidato_index_coincidente": número de 1 a {len(candidatos_validos)} (o null si no coincide con ninguno),
  "db_id_coincidente": ID de base de datos del candidato que coincide (o null si no coincide con ninguno),
  "score_confianza": número decimal de 0.0 a 1.0 (probabilidad de coincidencia),
  "analisis_comparativo": "Explicación breve de los rasgos que coinciden o difieren"
}}
"""
            payload.append(prompt)
            
            # Generar contenido
            response = await self.model.generate_content_async(payload)
            datos_raw = self._parsear_json(response.text)
            
            if datos_raw.get("coincide") and datos_raw.get("db_id_coincidente") is not None:
                db_id = int(datos_raw.get("db_id_coincidente"))
                cand_match = next((c for c in candidatos_validos if c["id"] == db_id), None)
                if cand_match:
                    return {
                        "candidato": cand_match,
                        "score": float(datos_raw.get("score_confianza", 0.0)),
                        "analisis": datos_raw.get("analisis_comparativo", "")
                    }
            return None
        except Exception as e:
            logger.error(f"[IA] Error en comparación de rostros con Gemini: {e}")
            return None


# Instancia global
procesador_imagenes = ProcesadorImagenes()
