"""
ai/scraper_agent.py - Scraper inteligente guiado por Gemini
"""

import json
from loguru import logger
import google.generativeai as genai
from duckduckgo_search import DDGS
from config import get_settings

settings = get_settings()
genai.configure(api_key=settings.gemini_api_key)

class ScraperAgent:
    def __init__(self):
        self.model = genai.GenerativeModel(settings.gemini_model)
        self.ddgs = DDGS()

    async def generar_consultas(self, persona) -> list[str]:
        """Usa Gemini para generar consultas de búsqueda inteligentes."""
        prompt = f"""
Eres un experto investigador OSINT. Necesito que generes exactamente 3 consultas de búsqueda web óptimas para encontrar información sobre la siguiente persona desaparecida en Venezuela tras un desastre.
Datos de la persona:
Nombre: {persona.nombre_completo()}
Cédula: {persona.cedula or 'No disponible'}
Edad: {persona.edad or 'No disponible'}
Última ubicación: {persona.ultima_ubicacion or 'No disponible'}

Devuelve SOLO un JSON válido con un array llamado "queries" que contenga strings. 
Ejemplo: {{"queries": ["'Juan Perez' desaparecido 'Caracas'", "Juan Perez cedula 12345678"]}}
"""
        try:
            response = await self.model.generate_content_async(prompt)
            texto = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(texto)
            return data.get("queries", [])
        except Exception as e:
            logger.error(f"[ScraperAgent] Error generando consultas para #{persona.id}: {e}")
            return [f"\"{persona.nombre_completo()}\" desaparecido", f"\"{persona.nombre_completo()}\" venezuela"]

    async def analizar_resultados(self, persona, resultados_web: list[dict]) -> dict:
        """Pasa los resultados a Gemini para ver si alguno es un match."""
        if not resultados_web:
            return {"match": False}

        resultados_texto = "\n\n".join([f"URL: {r['href']}\nTítulo: {r['title']}\nTexto: {r['body']}" for r in resultados_web])
        
        prompt = f"""
Revisa los siguientes resultados de búsqueda web sobre una persona desaparecida.
Persona: {persona.nombre_completo()} (Edad: {persona.edad}, Ubicación: {persona.ultima_ubicacion})

Resultados Web:
{resultados_texto}

¿Hay información en estos resultados que confirme que la persona fue encontrada (viva o fallecida) o que dé pistas claras?
Devuelve SOLO un JSON válido con el siguiente formato:
{{
    "match": true/false,
    "razon": "Explicación breve de por qué",
    "url_relevante": "La URL del resultado si hubo match, o null"
}}
"""
        try:
            response = await self.model.generate_content_async(prompt)
            texto = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(texto)
            return data
        except Exception as e:
            logger.error(f"[ScraperAgent] Error analizando resultados para #{persona.id}: {e}")
            return {"match": False}

    async def buscar_en_hospitales(self, persona) -> Optional[dict]:
        """Busca si el desaparecido coincide con algún ingreso hospitalario registrado."""
        from database.crud import db_session
        from database.models import IngresoHospital
        from sqlalchemy import select, or_, and_
        
        try:
            async with db_session() as s:
                # 1. Buscar por cédula si está disponible
                if persona.cedula:
                    ced_limpia = "".join(filter(str.isdigit, str(persona.cedula)))
                    if ced_limpia:
                        result = await s.execute(
                            select(IngresoHospital).where(IngresoHospital.cedula.like(f"%{ced_limpia}%"))
                        )
                        hosp = result.scalars().first()
                        if hosp:
                            return {
                                "hospital": hosp.hospital_nombre,
                                "detalles": hosp.detalles_ingreso or "Ingresado",
                                "fecha": hosp.fecha_ingreso or "Desconocida"
                            }
                
                # 2. Buscar por coincidencia aproximada de palabras clave del nombre
                nombre_comp = persona.nombre_completo()
                partes_nombre = [p for p in nombre_comp.split() if len(p) > 2]
                
                query_parts = []
                for p in partes_nombre:
                    query_parts.append(IngresoHospital.nombre_completo.ilike(f"%{p}%"))
                
                if query_parts:
                    result = await s.execute(
                        select(IngresoHospital).where(or_(*query_parts))
                    )
                    candidatos = result.scalars().all()
                    
                    nombre_des_lower = nombre_comp.lower()
                    palabras_des = set(nombre_des_lower.split())
                    
                    for hosp in candidatos:
                        nombre_hosp_lower = hosp.nombre_completo.lower()
                        palabras_hosp = set(nombre_hosp_lower.split())
                        comunes = palabras_des.intersection(palabras_hosp)
                        
                        # Si coinciden al menos 2 palabras (o 1 si es nombre único)
                        if len(comunes) >= 2 or (len(palabras_des) == 1 and len(comunes) >= 1):
                            return {
                                "hospital": hosp.hospital_nombre,
                                "detalles": hosp.detalles_ingreso or "Ingresado",
                                "fecha": hosp.fecha_ingreso or "Desconocida"
                            }
        except Exception as e:
            logger.error(f"[ScraperAgent] Error buscando en hospitales: {e}")
        return None

    async def ejecutar_busqueda_persona(self, persona) -> dict:
        """Flujo completo para una persona. Retorna estadísticas y posible match."""
        stats = {"sitios": 0, "busquedas": 0, "similitudes": 0}
        
        logger.info(f"[ScraperAgent] Iniciando búsqueda para #{persona.id} - {persona.nombre_completo()}")
        
        # 1. Búsqueda cruzada en hospitales de la base de datos
        logger.info(f"[ScraperAgent] Buscando coincidencias en registros de hospitales para #{persona.id}...")
        hosp_match = await self.buscar_en_hospitales(persona)
        if hosp_match:
            stats["similitudes"] += 1
            stats["sitios"] += 1
            stats["busquedas"] += 1
            
            from database.crud import db_session
            from database.models import Avistamiento
            from sqlalchemy import select, and_
            
            try:
                async with db_session() as s:
                    # Evitar registrar avistamiento duplicado
                    fuente_hosp = f"Hospital: {hosp_match['hospital']}"
                    existente = await s.execute(
                        select(Avistamiento).where(
                            and_(
                                Avistamiento.persona_id == persona.id,
                                Avistamiento.fuente == fuente_hosp
                            )
                        )
                    )
                    if not existente.scalars().first():
                        nuevo = Avistamiento(
                            persona_id=persona.id,
                            fuente=fuente_hosp,
                            plataforma='hospital',
                            descripcion=f"COINCIDENCIA EN HOSPITAL: Ingreso registrado en '{hosp_match['hospital']}'. Detalles médicos: {hosp_match['detalles']}. Fecha de ingreso registrada: {hosp_match['fecha']}.",
                            url_original=None,
                            score_total=0.95,
                            notificado=False
                        )
                        s.add(nuevo)
                        await s.commit()
                        logger.info(f"[ScraperAgent] ¡Match en hospitales registrado para #{persona.id}!")
            except Exception as e:
                logger.error(f"[ScraperAgent] Error registrando avistamiento de hospital: {e}")

        # 2. Búsqueda asistida por Gemini Google Grounding (bypasseando DuckDuckGo rate-limited)
        try:
            from google.generativeai import protos
            tool = protos.Tool()
            tool._pb.google_search.SetInParent()
            
            model_name = settings.gemini_model
            if not model_name.startswith("models/"):
                model_name = f"models/{model_name}"
                
            model_grounding = genai.GenerativeModel(model_name, tools=[tool])
            
            prompt = f"""
Busca en la web información reciente sobre la desaparición de {persona.nombre_completo()} en Venezuela.
Datos clave:
- Nombre completo: {persona.nombre_completo()}
- Cédula: {persona.cedula or 'No disponible'}
- Edad: {persona.edad or 'No disponible'}
- Última ubicación: {persona.ultima_ubicacion or 'No disponible'}

Determina si hay noticias, reportes oficiales o publicaciones que indiquen que fue localizada (viva o fallecida) o que aporten pistas concretas de su paradero actual.
Devuelve tu análisis estrictamente en formato JSON con la siguiente estructura:
{{
    "encontrado": true/false,
    "razon": "Un breve resumen del hallazgo o estado de la búsqueda",
    "url_relevante": "La URL específica del artículo, tweet o post donde se reporta esto (o null si no hay novedades)",
    "sitios_revisados": 4,
    "busquedas_realizadas": 2
}}
"""
            stats["busquedas"] += 2
            logger.info(f"[ScraperAgent] Buscando en Google usando Gemini Grounding para '{persona.nombre_completo()}'...")
            
            response = await model_grounding.generate_content_async(prompt)
            texto_json = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(texto_json)
            
            sitios = data.get("sitios_revisados", 3)
            stats["sitios"] += sitios
            
            if data.get("encontrado") and data.get("url_relevante"):
                stats["similitudes"] += 1
                
                from database.crud import db_session
                from database.models import Avistamiento
                from sqlalchemy import select, and_
                
                async with db_session() as s:
                    # Evitar duplicados
                    url_match = data.get("url_relevante")
                    existente = await s.execute(
                        select(Avistamiento).where(
                            and_(
                                Avistamiento.persona_id == persona.id,
                                Avistamiento.url_original == url_match
                            )
                        )
                    )
                    if not existente.scalars().first():
                        nuevo = Avistamiento(
                            persona_id=persona.id,
                            fuente='Búsqueda Agéntica (Google)',
                            plataforma='web',
                            descripcion=data.get("razon", "Match detectado en web."),
                            url_original=url_match,
                            score_total=0.85,
                            notificado=False
                        )
                        s.add(nuevo)
                        await s.commit()
                        logger.info(f"[ScraperAgent] ¡Match en web registrado para #{persona.id}! URL: {url_match}")
                        
        except Exception as e:
            logger.error(f"[ScraperAgent] Falló la búsqueda web asistida por Gemini Grounding: {e}")
            
        return stats

    async def clasificar_y_extraer_texto(self, texto_crudo: str, fuente: str = "Desconocida") -> dict:
        """
        Analiza un texto crudo de redes sociales o noticias.
        Clasifica y extrae entidades compatibles con PFIF.
        """
        prompt = f"""
        Eres un agente clasificador para un sistema de búsqueda de personas desaparecidas (basado en Google PFIF).
        Analiza este texto extraído de la fuente: {fuente}
        Texto: "{texto_crudo}"
        
        PASO 1: Clasifica el texto en UNA de estas categorías:
        - "Reporte de Desaparecido"
        - "Confirmación de Estado" (localizado, herido, fallecido, etc.)
        - "Noticia General" (habla de un sismo, estadísticas, política, sin mencionar personas específicas o de interés directo)
        - "Spam/Información Falsa"
        
        PASO 2: Si es Reporte o Confirmación, extrae los datos de la(s) persona(s).
        
        Devuelve SOLO un JSON válido:
        {{
            "categoria": "...",
            "es_relevante": true/false,
            "personas": [
                {{
                    "nombre_completo": "...",
                    "cedula": "...",
                    "edad": 0,
                    "estado_actual": "...",
                    "ultima_ubicacion": "...",
                    "detalles": "..."
                }}
            ]
        }}
        """
        try:
            response = await self.model.generate_content_async(prompt)
            t = response.text.replace("```json", "").replace("```", "").strip()
            return json.loads(t)
        except Exception as e:
            logger.error(f"[ScraperAgent] Error clasificando texto: {e}")
            return {"categoria": "Error", "es_relevante": False, "personas": []}

scraper_agent = ScraperAgent()
