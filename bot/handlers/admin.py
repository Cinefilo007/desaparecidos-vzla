"""
bot/handlers/admin.py — Módulo administrativo del bot de Telegram.
Maneja la gestión de fuentes de scraping en caliente y la carga de listas de hospitales con cruces automáticos.
"""
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters
)
from loguru import logger
from config import settings
from database.crud import (
    crear_fuente_scraping, listar_fuentes_scraping, desactivar_fuente_scraping,
    registrar_ingreso_hospital, get_persona, suscribir_a_persona
)
from bot.keyboards import kb_cancelar

# ── Estados del Flujo de Admin ─────────────────────────────────────────
(
    MENU_ADMIN,
    ESPERANDO_TIPO_FUENTE,
    ESPERANDO_URL_FUENTE,
    ESPERANDO_NOMBRE_FUENTE,
    ESPERANDO_LISTA_HOSPITAL,
) = range(100, 105)


def es_administrador(chat_id: str) -> bool:
    """Verifica si el chat_id corresponde al administrador del sistema."""
    if not settings.admin_chat_id:
        return False
    # Comparar limpiando posibles espacios
    return str(chat_id).strip() == str(settings.admin_chat_id).strip()


# ── Entrada ────────────────────────────────────────────────────────────

async def iniciar_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Punto de entrada: /admin o botón del teclado persistente."""
    chat_id = str(update.effective_chat.id)
    
    if not es_administrador(chat_id):
        logger.warning(f"Intento de acceso no autorizado al panel administrativo de chat_id={chat_id}")
        await update.effective_message.reply_text(
            "🚫 *Acceso no autorizado.*\n\n"
            "Este menú y sus funciones están estrictamente reservados para los coordinadores del proyecto.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    await enviar_menu_principal(update, ctx)
    return MENU_ADMIN


async def enviar_menu_principal(update: Update, ctx: ContextTypes.DEFAULT_TYPE, editar: bool = False):
    texto = (
        "⚙️ *Panel de Control Administrativo*\n\n"
        "Desde aquí puedes gestionar las fuentes de scraping en internet e ingresar "
        "listados de reportes médicos de hospitales."
    )
    
    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Agregar Fuente Scraping", callback_data="admin_add_source")],
        [InlineKeyboardButton("🏥 Cargar Ingresos Hospital",  callback_data="admin_load_hospital")],
        [InlineKeyboardButton("📋 Listar Fuentes Activas",    callback_data="admin_list_sources")],
        [InlineKeyboardButton("❌ Cerrar Panel",              callback_data="admin_close_panel")]
    ])

    if editar and update.callback_query:
        await update.callback_query.message.edit_text(texto, parse_mode="Markdown", reply_markup=teclado)
    else:
        send = update.callback_query.message.reply_text if update.callback_query else update.message.reply_text
        await send(texto, parse_mode="Markdown", reply_markup=teclado)


# ── Agregar Fuente de Scraping ──────────────────────────────────────────

async def cb_agregar_fuente(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    texto = (
        "💻 *Agregar Nueva Fuente de Scraping*\n\n"
        "Selecciona el tipo de plataforma de la fuente:"
    )
    
    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Sitio Web/RSS", callback_data="src_tipo_web"),
            InlineKeyboardButton("📢 Canal Telegram", callback_data="src_tipo_telegram_channel")
        ],
        [
            InlineKeyboardButton("🐦 Perfil de X (Twitter)", callback_data="src_tipo_twitter_profile"),
            InlineKeyboardButton("#️⃣ Hashtag (Búsqueda general)", callback_data="src_tipo_hashtag")
        ],
        [InlineKeyboardButton("🔙 Atrás", callback_data="admin_volver")]
    ])
    
    await query.message.edit_text(texto, parse_mode="Markdown", reply_markup=teclado)
    return ESPERANDO_TIPO_FUENTE


async def cb_tipo_fuente_seleccionada(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    tipo_mapeo = {
        "src_tipo_web": "web",
        "src_tipo_telegram_channel": "telegram_channel",
        "src_tipo_twitter_profile": "twitter_profile",
        "src_tipo_hashtag": "hashtag"
    }
    
    tipo = tipo_mapeo.get(query.data, "web")
    ctx.user_data["admin_new_source_tipo"] = tipo
    
    ejemplos = {
        "web": "Escribe la URL del portal (ej: `https://eldiario.com/sucesos/`):",
        "telegram_channel": "Escribe el canal de Telegram con el @ (ej: `@ProteccionCivilVzla`):",
        "twitter_profile": "Escribe el perfil de X (Twitter) sin el @ (ej: `PeriodistaVzla`):",
        "hashtag": "Escribe el hashtag a rastrear incluyendo el # (ej: `#DesaparecidosVzla`):"
    }
    
    await query.message.edit_text(
        f"🔗 *Paso 1: Dirección de la fuente*\n\n"
        f"{ejemplos[tipo]}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Atrás", callback_data="admin_volver")]])
    )
    return ESPERANDO_URL_FUENTE


async def recibir_url_fuente(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip()
    tipo = ctx.user_data.get("admin_new_source_tipo", "web")
    
    # Validaciones básicas
    if tipo == "telegram_channel" and not url.startswith("@"):
        url = "@" + url
    elif tipo == "hashtag" and not url.startswith("#"):
        url = "#" + url
        
    ctx.user_data["admin_new_source_url"] = url
    
    await update.message.reply_text(
        f"✅ ¡URL guardada!\n\n"
        f"Ahora escribe un *nombre descriptivo* para esta fuente (ej: 'El Diario - Sucesos').",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancelar", callback_data="admin_volver")]])
    )
    return ESPERANDO_NOMBRE_FUENTE


async def recibir_nombre_fuente(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    nombre = update.message.text.strip()
    tipo = ctx.user_data.get("admin_new_source_tipo", "web")
    url = ctx.user_data.get("admin_new_source_url")
    
    try:
        # Guardar en BD
        fuente = await crear_fuente_scraping(nombre=nombre, url=url, tipo=tipo)
        await update.message.reply_text(
            f"✅ *¡Fuente agregada con éxito!*\n\n"
            f"• *Nombre:* {fuente.nombre}\n"
            f"• *Dirección:* `{fuente.url}`\n"
            f"• *Tipo:* `{fuente.tipo}`\n\n"
            f"El planificador y el scraper comenzarán a sondear esta fuente de forma automática.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error creando fuente de scraping: {e}")
        await update.message.reply_text(f"⚠️ *Error al agregar la fuente:* {e}", parse_mode="Markdown")
        
    # Limpiar
    ctx.user_data.pop("admin_new_source_tipo", None)
    ctx.user_data.pop("admin_new_source_url", None)
    
    # Volver al menú
    await enviar_menu_principal(update, ctx)
    return MENU_ADMIN


# ── Listar Fuentes de Scraping ─────────────────────────────────────────

async def cb_listar_fuentes(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    fuentes = await listar_fuentes_scraping(solo_activas=True)
    
    if not fuentes:
        texto = "📋 *Fuentes de Scraping*\n\n_No hay fuentes dinámicas configuradas aún en la base de datos._"
    else:
        lineas = []
        for f in fuentes:
            lineas.append(f"• *{f.nombre}* — `{f.url}` (`{f.tipo}`)")
        texto = "📋 *Fuentes de Scraping Activas:*\n\n" + "\n".join(lineas)
        
    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Volver", callback_data="admin_volver")]
    ])
    
    await query.message.edit_text(texto, parse_mode="Markdown", reply_markup=teclado)
    return MENU_ADMIN


# ── Cargar Ingresos de Hospitales ──────────────────────────────────────

async def cb_cargar_hospital(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    await query.message.edit_text(
        "🏥 *Carga Masiva de Hospital*\n\n"
        "Pega aquí la lista de personas ingresadas.\n\n"
        "*Formato sugerido (una por línea):*\n"
        "`Hospital Domingo Luciani`\n"
        "`Juan Perez, 30 años`\n"
        "`Maria Gomez, posible fractura`\n\n"
        "El sistema intentará cruzar estos datos con la base de datos de desaparecidos.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancelar", callback_data="admin_volver")]])
    )
    return ESPERANDO_LISTA_HOSPITAL


async def recibir_lista_hospital(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    texto = update.message.text.strip()
    lineas = [l.strip() for l in texto.split("\n") if l.strip()]
    
    await update.message.reply_text(f"⏳ Procesando {len(lineas)} registros de hospital y ejecutando cruces fonéticos...")
    
    exitosos = 0
    coincidencias = 0
    
    for linea in lineas:
        partes = [p.strip() for p in linea.split("-")]
        if len(partes) < 3:
            # Reintentar con comas o formato libre básico
            partes = [p.strip() for p in linea.split(",")]
            
        if len(partes) < 3:
            continue
            
        nombre = partes[0]
        try:
            edad = int("".join(filter(str.isdigit, partes[1])))
        except Exception:
            edad = None
            
        hospital = partes[2]
        detalles = partes[3] if len(partes) > 3 else "Ingreso reportado"
        
        datos_ingreso = {
            "nombre_completo": nombre,
            "edad": edad,
            "hospital_nombre": hospital,
            "detalles_ingreso": detalles,
            "fecha_ingreso": datetime.now().strftime("%d/%m/%Y")
        }
        
        try:
            ingreso = await registrar_ingreso_hospital(datos_ingreso)
            exitosos += 1
            
            # Si hubo coincidencia fonética y vinculación automática
            if ingreso.persona_id_vinculada:
                coincidencias += 1
                persona = await get_persona(ingreso.persona_id_vinculada)
                if persona:
                    # Avisar al familiar inmediatamente
                    if persona.contacto_chat_id:
                        msg_familiar = (
                            f"🚨 *¡NOTIFICACIÓN URGENTE DE LOCALIZACIÓN!* 🚨\n\n"
                            f"El sistema ha detectado una coincidencia en un hospital para tu familiar:\n"
                            f"👤 *Nombre:* {persona.nombre_completo()}\n"
                            f"🏥 *Ubicación:* {ingreso.hospital_nombre}\n"
                            f"📋 *Reporte Médico:* {ingreso.detalles_ingreso}\n"
                            f"📅 *Fecha:* {ingreso.fecha_ingreso}\n\n"
                            f"Por favor, acércate o ponte en contacto con este hospital para verificar. ¡Esperamos que todo esté bien! 🙏"
                        )
                        try:
                            await ctx.bot.send_message(chat_id=persona.contacto_chat_id, text=msg_familiar, parse_mode="Markdown")
                            logger.info(f"Notificación de hospital enviada con éxito al familiar chat_id={persona.contacto_chat_id}")
                        except Exception as err_fam:
                            logger.error(f"No se pudo notificar al familiar chat_id={persona.contacto_chat_id}: {err_fam}")
        except Exception as e:
            logger.error(f"Error procesando línea de hospital: {e}")
            
    await update.message.reply_text(
        f"📊 *Resultados de la Carga de Hospitales:*\n\n"
        f"• Registros procesados con éxito: *{exitosos}/{len(lineas)}*\n"
        f"• Coincidencias/Alertas automáticas enviadas: *{coincidencias}*",
        parse_mode="Markdown"
    )
    
    # Volver al menú
    await enviar_menu_principal(update, ctx)
    return MENU_ADMIN


# ── Cancelación y Retorno ──────────────────────────────────────────────

async def cb_volver_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await enviar_menu_principal(update, ctx, editar=True)
    return MENU_ADMIN


async def cb_cerrar_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    return ConversationHandler.END


async def cancelar_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        await query.message.reply_text("❌ Operación administrativa cancelada.")
    else:
        await update.message.reply_text("❌ Operación administrativa cancelada.")
    
    # Limpiar user_data administrativa
    ctx.user_data.pop("admin_new_source_tipo", None)
    ctx.user_data.pop("admin_new_source_url", None)
    
    return ConversationHandler.END


# ── ConversationHandler exportable ────────────────────────────────────

def get_admin_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("admin", iniciar_admin),
            MessageHandler(filters.Regex("^⚙️ Panel Administrar$"), iniciar_admin),
        ],
        states={
            MENU_ADMIN: [
                CallbackQueryHandler(cb_agregar_fuente,      pattern="^admin_add_source$"),
                CallbackQueryHandler(cb_cargar_hospital,     pattern="^admin_load_hospital$"),
                CallbackQueryHandler(cb_listar_fuentes,      pattern="^admin_list_sources$"),
                CallbackQueryHandler(cb_cerrar_panel,        pattern="^admin_close_panel$"),
            ],
            ESPERANDO_TIPO_FUENTE: [
                CallbackQueryHandler(cb_tipo_fuente_seleccionada, pattern="^src_tipo_"),
                CallbackQueryHandler(cb_volver_menu,              pattern="^admin_volver$"),
            ],
            ESPERANDO_URL_FUENTE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_url_fuente),
                CallbackQueryHandler(cb_volver_menu,              pattern="^admin_volver$"),
                CallbackQueryHandler(cancelar_admin,             pattern="^cancelar$"),
            ],
            ESPERANDO_NOMBRE_FUENTE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_nombre_fuente),
                CallbackQueryHandler(cb_volver_menu,              pattern="^admin_volver$"),
                CallbackQueryHandler(cancelar_admin,             pattern="^cancelar$"),
            ],
            ESPERANDO_LISTA_HOSPITAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_lista_hospital),
                CallbackQueryHandler(cb_volver_menu,              pattern="^admin_volver$"),
                CallbackQueryHandler(cancelar_admin,             pattern="^cancelar$"),
            ]
        },
        fallbacks=[
            CommandHandler("cancelar", cancelar_admin),
            CallbackQueryHandler(cancelar_admin, pattern="^cancelar$")
        ],
        conversation_timeout=300,
        name="admin",
        persistent=False
    )
