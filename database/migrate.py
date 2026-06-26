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
    
    # Adaptar URL de base de datos para asyncpg si es necesario (ya lo hace config.py en settings.database_url)
    url = settings.database_url
    logger.info(f"Conectando a base de datos...")
    
    engine = create_async_engine(url, pool_pre_ping=True)
    
    try:
        async with engine.begin() as conn:
            logger.info("Verificando y alterando tabla 'personas'...")
            # Sentencias SQL para agregar columnas faltantes
            await conn.execute(text("ALTER TABLE personas ADD COLUMN IF NOT EXISTS foto_rostro_local_path VARCHAR(500);"))
            await conn.execute(text("ALTER TABLE personas ADD COLUMN IF NOT EXISTS foto_rostro_url VARCHAR(500);"))
            logger.info("Columnas 'foto_rostro_local_path' y 'foto_rostro_url' añadidas o ya existentes ✓")
            
    except Exception as e:
        logger.error(f"❌ Error durante la migración: {e}")
        raise e
    finally:
        await engine.dispose()
        logger.info("Conexión de migración cerrada.")

if __name__ == "__main__":
    asyncio.run(migrate())
