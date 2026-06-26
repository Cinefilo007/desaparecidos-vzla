"""
bot/main.py — Punto de entrada del bot de Telegram.
Registra todos los handlers y arranca el polling.
"""
import asyncio
from loguru import logger
from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters,
)

from config import settings
from database.crud import init_db, get_estadisticas
from bot.keyboards import kb_menu_principal, kb_abrir_miniapp
from bot.handlers.registro import get_registro_handler
from bot.handlers.busqueda import get_busqueda_handler, get_misc_handlers, mostrar_stats


# ── Comando /start ─────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx):
    stats = await get_estadisticas()
    nombre_usuario = update.effective_user.first_name or "amigo/a"

    await update.message.reply_text(
        f"🇻🇪 *Desaparecidos — Terremoto Venezuela 2026*\n\n"
        f"Hola {nombre_usuario}. Este bot ayuda a reconectar familias "
        f"tras el sismo del 24 de junio.\n\n"
        f"📊 *Estado actual:*\n"
        f"  🔴 Sin contacto: *{stats['buscados']}* personas\n"
        f"  ✅ Localizadas:  *{stats['localizados']}* personas\n\n"
        f"¿Qué deseas hacer?",
        parse_mode="Markdown",
        reply_markup=kb_menu_principal(),
    )


# ── Comando /ayuda ─────────────────────────────────────────────────────

async def cmd_ayuda(update: Update, ctx):
    await update.message.reply_text(
        "📖 *Comandos disponibles:*\n\n"
        "/start — Menú principal\n"
        "/registrar — Reportar a alguien desaparecido\n"
        "/buscar — Buscar a alguien por nombre\n"
        "/stats — Ver estadísticas\n"
        "/ayuda — Ver este mensaje\n\n"
        "💡 *Tip:* Puedes enviar directamente la foto de una ficha "
        "'SE BUSCA' y el bot extrae los datos automáticamente.",
        parse_mode="Markdown",
    )


# ── Menú principal (callback) ──────────────────────────────────────────

async def cb_menu_principal(update: Update, ctx):
    query = update.callback_query
    await query.answer()
    stats = await get_estadisticas()
    await query.message.reply_text(
        f"🏠 *Menú principal*\n\n"
        f"Sin contacto: *{stats['buscados']}* | Localizadas: *{stats['localizados']}*",
        parse_mode="Markdown",
        reply_markup=kb_menu_principal(),
    )


# ── Mini App desde menú ────────────────────────────────────────────────

async def cb_menu_mapa(update: Update, ctx):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "🗺️ Abre el panel web para ver el mapa interactivo:",
        reply_markup=kb_abrir_miniapp(settings.miniapp_url),
    )


# ── Foto enviada fuera de conversación → redirigir a registro ─────────

async def foto_directa(update: Update, ctx):
    await update.message.reply_text(
        "📸 ¿Quieres registrar a alguien con esta foto?\n"
        "Usa /registrar para iniciar el proceso.",
    )


# ── Configurar comandos visibles en Telegram ───────────────────────────

async def configurar_comandos(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start",     "Menú principal"),
        BotCommand("registrar", "Reportar persona desaparecida"),
        BotCommand("buscar",    "Buscar por nombre o cédula"),
        BotCommand("stats",     "Ver estadísticas"),
        BotCommand("ayuda",     "Ayuda y comandos"),
    ])


# ── Función principal ──────────────────────────────────────────────────

async def post_init(app: Application) -> None:
    # Configurar comandos visibles
    await configurar_comandos(app)

def main():
    # Inicializar base de datos de forma sincrónica
    asyncio.run(init_db())
    logger.info("Base de datos inicializada ✓")

    # Construir aplicación
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .build()
    )

    # Registrar handlers en orden (los ConversationHandlers primero)
    app.add_handler(get_registro_handler())
    app.add_handler(get_busqueda_handler())

    # Comandos simples
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("ayuda",     cmd_ayuda))
    app.add_handler(CommandHandler("help",      cmd_ayuda))
    app.add_handler(CommandHandler("stats",     mostrar_stats))

    # Callbacks
    app.add_handler(CallbackQueryHandler(cb_menu_principal, pattern="^menu_principal$"))
    app.add_handler(CallbackQueryHandler(cb_menu_mapa,      pattern="^menu_mapa$"))
    app.add_handler(CallbackQueryHandler(mostrar_stats,     pattern="^menu_stats$"))

    # Handlers de acciones
    for h in get_misc_handlers():
        app.add_handler(h)

    # Foto enviada fuera de contexto
    app.add_handler(MessageHandler(filters.PHOTO, foto_directa))

    logger.info("Bot iniciado correctamente ✓")

    # Arrancar (ejecución sincrónica bloqueante y limpia)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
