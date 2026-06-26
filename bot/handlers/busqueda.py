"""
bot/handlers/busqueda.py — Búsqueda de personas y envío de resultados.
"""
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler

from database.crud import buscar_por_nombre, listar_personas, get_estadisticas, actualizar_estado
from database.models import EstadoPersona
from ai.name_matcher import buscar_por_nombre as fuzzy_buscar
from bot.keyboards import kb_acciones_persona, kb_menu_principal
from config import settings

ESPERANDO_NOMBRE_BUSQUEDA = 10


# ── Búsqueda por nombre ────────────────────────────────────────────────

async def iniciar_busqueda(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    send = query.message.reply_text if query else update.message.reply_text
    if query:
        await query.answer()

    await send(
        "🔍 *Búsqueda de personas*\n\n"
        "Escribe el *nombre*, *apellido*, o *número de cédula* de la persona que buscas.\n"
        "También puedes escribir parte del nombre.",
        parse_mode="Markdown",
    )
    return ESPERANDO_NOMBRE_BUSQUEDA


async def ejecutar_busqueda(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    texto = update.message.text.strip()
    await update.message.reply_text(f"🔍 Buscando *{texto}*...", parse_mode="Markdown")

    # Búsqueda en BD
    personas_bd = await buscar_por_nombre(texto, limit=100)

    if not personas_bd:
        await update.message.reply_text(
            f"❌ No encontré a nadie con el nombre *{texto}*.\n\n"
            "• Intenta con menos palabras o solo el apellido\n"
            "• Verifica la ortografía\n"
            "• La persona puede no estar registrada aún",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 Registrarla ahora", callback_data="menu_registrar")],
                [InlineKeyboardButton("🏠 Menú principal",   callback_data="menu_principal")],
            ]),
        )
        return ConversationHandler.END

    # Convertir a dicts para fuzzy matching
    personas_dicts = [
        {"id": p.id, "nombre": p.nombre, "apellidos": p.apellidos or "",
         "edad": p.edad, "zona": p.zona, "estado": p.estado,
         "cedula": p.cedula}
        for p in personas_bd
    ]

    # Aplicar fuzzy matching con fonética venezolana
    resultados = fuzzy_buscar(texto, personas_dicts, umbral=0.65, top_k=5)

    if not resultados:
        resultados = [(p, 0.70) for p in personas_dicts[:5]]

    await update.message.reply_text(
        f"✅ Encontré *{len(resultados)} resultado(s)* para '{texto}':",
        parse_mode="Markdown",
    )

    for persona_dict, score in resultados:
        persona = next((p for p in personas_bd if p.id == persona_dict["id"]), None)
        if not persona:
            continue

        estado_emoji = {"buscado": "🔴", "localizado": "✅", "posible": "🟡", "fallecido": "⚫"}.get(
            persona.estado, "⚪"
        )
        vulnerable = " ⚠️ VULNERABLE" if persona.es_vulnerable else ""
        confianza  = f"Coincidencia: {score:.0%}" if score < 0.99 else ""

        caption = (
            f"{estado_emoji} *{persona.nombre_completo()}*{vulnerable}\n"
            f"🎂 {persona.edad or '?'} años  |  📍 {persona.ultima_ubicacion or 'No especificado'}\n"
        )
        if persona.cedula:
            caption += f"🪪 V-{persona.cedula}\n"
        if confianza:
            caption += f"🎯 {confianza}\n"

        if persona.foto_local_path:
            try:
                await update.message.reply_photo(
                    photo=open(persona.foto_local_path, "rb"),
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=kb_acciones_persona(persona.id),
                )
                continue
            except Exception:
                pass

        await update.message.reply_text(
            caption,
            parse_mode="Markdown",
            reply_markup=kb_acciones_persona(persona.id),
        )

    return ConversationHandler.END


# ── Estadísticas ───────────────────────────────────────────────────────

async def mostrar_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    stats = await get_estadisticas()
    texto = (
        f"📊 *Estadísticas en vivo*\n\n"
        f"👥 Total registradas:   *{stats['total']}*\n"
        f"🔴 Sin contacto:        *{stats['buscados']}*\n"
        f"✅ Localizadas:         *{stats['localizados']}*\n"
        f"⚠️ Vulnerables activos: *{stats['vulnerables']}*\n"
    )
    if query:
        await query.message.reply_text(texto, parse_mode="Markdown")
    else:
        await update.message.reply_text(texto, parse_mode="Markdown")


# ── Marcar como localizado ─────────────────────────────────────────────

async def marcar_localizado(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    persona_id = int(query.data.split("_")[1])
    ok = await actualizar_estado(persona_id, EstadoPersona.LOCALIZADO)

    if ok:
        await query.message.reply_text(
            f"✅ *¡Persona #{persona_id:04d} marcada como LOCALIZADA!*\n\n"
            "Gracias por actualizar el estado. Esta información ayuda a liberar "
            "recursos para quienes aún siguen siendo buscados. 🙏",
            parse_mode="Markdown",
        )
    else:
        await query.message.reply_text("⚠️ No se pudo actualizar. Intenta de nuevo.")


# ── Handlers exportables ───────────────────────────────────────────────

def get_busqueda_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("buscar", iniciar_busqueda),
            MessageHandler(filters.Regex("^🔍 Buscar Persona$"), iniciar_busqueda),
            CallbackQueryHandler(iniciar_busqueda, pattern="^menu_buscar$"),
        ],
        states={
            ESPERANDO_NOMBRE_BUSQUEDA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ejecutar_busqueda),
            ],
        },
        fallbacks=[],
        conversation_timeout=120,
        name="busqueda",
    )


def get_misc_handlers():
    """Handlers sueltos (stats, localizar, etc.)."""
    return [
        CallbackQueryHandler(mostrar_stats,     pattern="^menu_stats$"),
        CallbackQueryHandler(marcar_localizado, pattern=r"^localizado_\d+$"),
    ]
