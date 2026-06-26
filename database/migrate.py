"""
database/migrate.py — Script de migración asíncrona en caliente para la base de datos de producción.
Agrega las columnas de rostro a la tabla personas de PostgreSQL.
"""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from config import settings
from loguru import logger

async def migrate():
    logger.info("Iniciando migración de base de datos en caliente...")
    url = settings.database_url
    
    intentos = 5
    intervalo = 3
    
    for intento in range(1, intentos + 1):
        try:
            logger.info(f"Conectando a base de datos (intento {intento}/{intentos})...")
            engine = create_async_engine(url, pool_pre_ping=True)
            async with engine.begin() as conn:
                logger.info("Verificando y alterando tabla 'personas'...")
                # Sentencias SQL para agregar columnas faltantes
                await conn.execute(text("ALTER TABLE personas ADD COLUMN IF NOT EXISTS foto_rostro_local_path VARCHAR(500);"))
                await conn.execute(text("ALTER TABLE personas ADD COLUMN IF NOT EXISTS foto_rostro_url VARCHAR(500);"))
                logger.info("Columnas 'foto_rostro_local_path' y 'foto_rostro_url' añadidas o ya existentes ✓")
            await engine.dispose()
            logger.info("Migración completada con éxito ✓")
            return
        except Exception as e:
            logger.warning(f"Intento {intento} fallido: {e}")
            if intento < intentos:
                logger.info(f"Esperando {intervalo} segundos antes de reintentar...")
                await asyncio.sleep(intervalo)
            else:
                logger.error("❌ Todos los intentos de migración han fallado.")
                raise e

if __name__ == "__main__":
    asyncio.run(migrate())
