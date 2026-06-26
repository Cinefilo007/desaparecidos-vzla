"""
bot/handlers/busqueda.py — Búsqueda de personas y envío de resultados.
"""
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler

from database.crud import (
    buscar_por_nombre, listar_personas, get_estadisticas, actualizar_estado,
    es_usuario_suscrito, suscribir_a_persona, desuscribir_de_persona, get_persona
)
from database.models import EstadoPersona
from ai.name_matcher import buscar_por_nombre as fuzzy_buscar
from bot.keyboards import kb_acciones_persona, kb_menu_principal
from config import settings
from ai.image_processor import procesador_imagenes

ESPERANDO_NOMBRE_BUSQUEDA = 10


# ── Búsqueda por nombre ────────────────────────────────────────────────

async def iniciar_busqueda(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    send = query.message.reply_text if query else update.message.reply_text
    if query:
        await query.answer()

    await send(
        "🔍 *Búsqueda de personas*\n\n"
        "Puedes:\n"
        "• ✍️ Escribir el *nombre*, *apellido* o *cédula* de la persona que buscas.\n"
        "• 📸 Enviar una *foto clara* de la persona para buscarla por reconocimiento facial.",
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
            f"❌ *No encontré coincidencias para '{texto}' en nuestra base de datos.*\n\n"
            "¿Deseas registrar a esta persona ahora para iniciar el scraping en internet y guardarla en la base de datos?",
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

    chat_id = str(update.message.chat_id)

    for persona_dict, score in resultados:
        persona = next((p for p in personas_bd if p.id == persona_dict["id"]), None)
        if not persona:
            continue

        ya_suscrito = await es_usuario_suscrito(persona.id, chat_id)
        reply_markup = kb_acciones_persona(persona.id, ya_suscrito)

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
                    reply_markup=reply_markup,
                )
                continue
            except Exception:
                pass

        await update.message.reply_text(
            caption,
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )

    return ConversationHandler.END


# ── Búsqueda por reconocimiento facial (rostro) ───────────────────────

async def ejecutar_busqueda_por_rostro(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    await msg.reply_text("⏳ Procesando imagen para búsqueda por reconocimiento facial con IA...")

    # Descargar foto
    foto = msg.photo[-1]
    file = await ctx.bot.get_file(foto.file_id)
    import uuid
    from pathlib import Path
    
    # Asegurar directorio
    Path("fotos_temp").mkdir(exist_ok=True)
    ruta = Path("fotos_temp") / f"search_{uuid.uuid4()}.jpg"
    await file.download_to_drive(str(ruta))

    # Obtener todas las personas en estado BUSCADO
    personas_activas = await listar_personas(estado=EstadoPersona.BUSCADO, limit=100)
    candidatos = []
    for p in personas_activas:
        if p.foto_rostro_local_path and Path(p.foto_rostro_local_path).exists():
            candidatos.append({
                "id": p.id,
                "nombre": p.nombre_completo(),
                "foto_rostro_path": p.foto_rostro_local_path
            })
            
    if not candidatos:
        # Borrar archivo temporal
        try:
            ruta.unlink(missing_ok=True)
        except Exception:
            pass
            
        await msg.reply_text(
            "⚠️ *No hay personas registradas con foto de rostro en el sistema.* En esta fase no es posible hacer comparación visual.\n\n"
            "¿Deseas registrar a la persona que buscas para activar la búsqueda con nuestro scraper de internet?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 Registrar Persona", callback_data="menu_registrar")],
                [InlineKeyboardButton("🏠 Menú principal", callback_data="menu_principal")]
            ])
        )
        return ConversationHandler.END

    # Comparar con Gemini
    res = await procesador_imagenes.comparar_rostros_gemini(str(ruta), candidatos)
    
    # Borrar archivo temporal de búsqueda
    try:
        ruta.unlink(missing_ok=True)
    except Exception:
        pass

    if res and res.get("candidato"):
        candidato = res["candidato"]
        score = res["score"]
        analisis = res["analisis"]
        
        # Obtener datos de la persona
        persona = await get_persona(candidato["id"])
        
        if persona:
            chat_id = str(msg.chat_id)
            ya_suscrito = await es_usuario_suscrito(persona.id, chat_id)
            reply_markup = kb_acciones_persona(persona.id, ya_suscrito)
            
            estado_emoji = {"buscado": "🔴", "localizado": "✅", "posible": "🟡", "fallecido": "⚫"}.get(
                persona.estado, "⚪"
            )
            
            caption = (
                f"🚨 *¡COINCIDENCIA VISUAL DETECTADA!* 🚨\n\n"
                f"{estado_emoji} *Nombre:* {persona.nombre_completo()}\n"
                f"🎂 *Edad:* {persona.edad or '?'} años  |  📍 *Zona:* {persona.zona or 'No especificada'}\n"
                f"🎯 *Certeza del Reconocimiento:* {score:.0%}\n\n"
                f"📝 *Análisis Visual de la IA:*\n_{analisis}_\n"
            )
            
            if persona.foto_local_path:
                try:
                    await msg.reply_photo(
                        photo=open(persona.foto_local_path, "rb"),
                        caption=caption,
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
                    return ConversationHandler.END
                except Exception:
                    pass

            await msg.reply_text(
                caption,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
            return ConversationHandler.END

    await msg.reply_text(
        "❌ *No encontramos coincidencias visuales en nuestra base de datos con esa fotografía.*\n\n"
        "¿Deseas registrar a esta persona ahora para iniciar el scraping en internet y guardarla en la base de datos?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Registrarla ahora", callback_data="menu_registrar")],
            [InlineKeyboardButton("🏠 Menú principal", callback_data="menu_principal")]
        ])
    )
    return ConversationHandler.END


# ── Suscripción / Desuscripción de Alertas ─────────────────────────────

async def suscribir_alerta_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    chat_id = str(query.message.chat_id)
    persona_id = int(query.data.split("_")[2])
    
    await suscribir_a_persona(persona_id, chat_id)
    
    persona = await get_persona(persona_id)
    nombre = persona.nombre_completo() if persona else "la persona"
    
    await query.message.reply_text(
        f"🔔 *Alertas activadas para {nombre}*.\n"
        f"Te notificaremos de inmediato si detectamos información o avistamientos en internet. 📱",
        parse_mode="Markdown"
    )
    
    # Actualizar botones del mensaje de búsqueda
    try:
        await query.edit_message_reply_markup(reply_markup=kb_acciones_persona(persona_id, ya_suscrito=True))
    except Exception:
        pass


async def desuscribir_alerta_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    chat_id = str(query.message.chat_id)
    persona_id = int(query.data.split("_")[2])
    
    await desuscribir_de_persona(persona_id, chat_id)
    
    persona = await get_persona(persona_id)
    nombre = persona.nombre_completo() if persona else "la persona"
    
    await query.message.reply_text(
        f"🔕 *Alertas desactivadas para {nombre}*.\n"
        f"Ya no recibirás notificaciones automáticas de avistamientos.",
        parse_mode="Markdown"
    )
    
    # Actualizar botones del mensaje de búsqueda
    try:
        await query.edit_message_reply_markup(reply_markup=kb_acciones_persona(persona_id, ya_suscrito=False))
    except Exception:
        pass


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
                MessageHandler(filters.PHOTO, ejecutar_busqueda_por_rostro),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^🔍 Buscar Persona$"), iniciar_busqueda)
        ],
        conversation_timeout=120,
        name="busqueda",
    )


def get_misc_handlers():
    """Handlers sueltos (stats, localizar, etc.)."""
    return [
        CallbackQueryHandler(mostrar_stats,     pattern="^menu_stats$"),
        CallbackQueryHandler(marcar_localizado, pattern=r"^localizado_\d+$"),
        CallbackQueryHandler(suscribir_alerta_handler, pattern=r"^alerta_sub_\d+$"),
        CallbackQueryHandler(desuscribir_alerta_handler, pattern=r"^alerta_desub_\d+$"),
    ]
