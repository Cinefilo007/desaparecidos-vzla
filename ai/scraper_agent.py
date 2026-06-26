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

    async def ejecutar_busqueda_persona(self, persona) -> dict:
        """Flujo completo para una persona. Retorna estadísticas y posible match."""
        stats = {"sitios": 0, "busquedas": 0, "similitudes": 0}
        
        consultas = await self.generar_consultas(persona)
        todos_resultados = []
        
        for query in consultas:
            try:
                stats["busquedas"] += 1
                resultados = list(self.ddgs.text(query, max_results=3))
                todos_resultados.extend(resultados)
                stats["sitios"] += len(resultados)
            except Exception as e:
                logger.error(f"[ScraperAgent] Error en DDG search para '{query}': {e}")
                
        if not todos_resultados:
            return stats
            
        analisis = await self.analizar_resultados(persona, todos_resultados)
        if analisis.get("match"):
            stats["similitudes"] = 1
            logger.info(f"[ScraperAgent] Match encontrado para #{persona.id} en URL: {analisis.get('url_relevante')}")
            
            # Aquí idealmente crearíamos un Avistamiento si hubo match
            from database.crud import db_session
            from database.models import Avistamiento
            async with db_session() as s:
                nuevo = Avistamiento(
                    persona_id=persona.id,
                    fuente=analisis.get('url_relevante') or 'Búsqueda IA',
                    plataforma='web',
                    descripcion=analisis.get('razon'),
                    url_original=analisis.get('url_relevante'),
                    score_total=0.8, # Alta confianza al ser validado por LLM
                    notificado=False
                )
                s.add(nuevo)
                await s.commit()
                
        return stats

scraper_agent = ScraperAgent()
