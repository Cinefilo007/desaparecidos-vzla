"""
bot/handlers/admin.py — Módulo administrativo del bot de Telegram.
Maneja la gestión de fuentes de scraping en caliente y la carga de listas de hospitales con cruces automáticos.
"""
from datetime import datetime
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
    ESPERANDO_NOMBRE_HOSPITAL,
    ESPERANDO_LISTA_HOSPITAL,
    ESPERANDO_FOTO_ENCONTRADOS,
) = range(100, 107)


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
    
    logger.info(f"[Admin] Intento de acceso al panel por chat_id: {chat_id}")
    
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
        "listados de reportes médicos de hospitales o fotos de listas de encontrados."
    )
    
    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Agregar Fuente Scraping", callback_data="admin_add_source")],
        [InlineKeyboardButton("🏥 Cargar Ingresos Hospital",  callback_data="admin_load_hospital")],
        [InlineKeyboardButton("📸 Subir Foto de Encontrados", callback_data="admin_upload_photo")],
        [InlineKeyboardButton("📋 Listar Fuentes Activas",    callback_data="admin_list_sources")],
        [InlineKeyboardButton("❌ Cerrar Panel",              callback_data="admin_close_panel")]
    ])

    if editar and update.callback_query:
        await update.callback_query.message.edit_text(texto, parse_mode="Markdown", reply_markup=teclado)
    else:
        send = update.callback_query.message.reply_text if update.callback_query else update.message.reply_text
        logger.info("[Admin] Enviando menú principal a Telegram...")
        await send(texto, parse_mode="Markdown", reply_markup=teclado)
        logger.info("[Admin] Menú principal enviado correctamente.")


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
        "Envía una *foto* o un *documento* (PDF, Word, Excel, CSV) con el listado de pacientes ingresados, o pega el texto directamente.\n\n"
        "La Inteligencia Artificial extraerá automáticamente el nombre del hospital y los pacientes.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancelar", callback_data="admin_volver")]])
    )
    return ESPERANDO_LISTA_HOSPITAL

async def recibir_nombre_hospital(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    nombre_hospital = update.message.text.strip()
    ctx.user_data["hospital_nombre"] = nombre_hospital
    
    pendientes = ctx.user_data.get("ingresos_pendientes")
    if not pendientes:
        # Fallback si no hay pendientes, volvemos a pedir foto
        await update.message.reply_text(
            f"🏥 Hospital fijado: *{nombre_hospital}*\n\n"
            "Ahora envía la foto o documento de los pacientes.", parse_mode="Markdown"
        )
        return ESPERANDO_LISTA_HOSPITAL
        
    ingresos_a_procesar = []
    for d in pendientes:
        if d.nombre:
            ingresos_a_procesar.append({
                "nombre_completo": d.nombre_completo(),
                "edad": d.edad,
                "hospital_nombre": nombre_hospital,
                "detalles_ingreso": d.condicion_medica or "Ingreso clínico",
                "fecha_ingreso": datetime.now().strftime("%d/%m/%Y")
            })
            
    if ingresos_a_procesar:
        from database.crud import registrar_ingreso_hospital
        for ing in ingresos_a_procesar:
            await registrar_ingreso_hospital(ing)
        await update.message.reply_text(f"✅ ¡Se registraron {len(ingresos_a_procesar)} pacientes en {nombre_hospital}!")
    else:
        await update.message.reply_text("❌ No se encontraron datos válidos para procesar.")
        
    ctx.user_data.pop("ingresos_pendientes", None)
    ctx.user_data.pop("hospital_nombre", None)
    await enviar_menu_principal(update, ctx)
    return MENU_ADMIN


async def recibir_lista_hospital(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    
    # Lista para almacenar los ingresos extraídos de foto o texto
    ingresos_a_procesar = []
    
    import uuid
    from pathlib import Path
    from ai.image_processor import procesador_imagenes
    
    if msg.photo or msg.document:
        await msg.reply_text("⏳ Analizando archivo con IA... (esto puede tardar unos segundos)")
        FOTOS_DIR = Path("fotos_temp")
        FOTOS_DIR.mkdir(exist_ok=True)
        
        lista_datos = []
        hosp_ai = None
        
        if msg.photo:
            foto = msg.photo[-1]
            file = await ctx.bot.get_file(foto.file_id)
            ruta = FOTOS_DIR / f"hosp_{uuid.uuid4()}.jpg"
            await file.download_to_drive(str(ruta))
            lista_datos, hosp_ai = await procesador_imagenes.extraer_datos(str(ruta))
        else:
            doc = msg.document
            file = await ctx.bot.get_file(doc.file_id)
            ext = Path(doc.file_name).suffix.lower() if doc.file_name else ""
            ruta = FOTOS_DIR / f"hosp_{uuid.uuid4()}{ext}"
            await file.download_to_drive(str(ruta))
            
            from ai.document_reader import extraer_texto_de_documento
            texto_doc = extraer_texto_de_documento(str(ruta))
            if texto_doc:
                lista_datos, hosp_ai = await procesador_imagenes.extraer_datos_de_texto(texto_doc)
            else:
                lista_datos = []
        
        if not lista_datos:
            await msg.reply_text("❌ No pude extraer datos del archivo.")
            await enviar_menu_principal(update, ctx)
            return MENU_ADMIN
            
        # Si la IA no detectó un hospital en la imagen/documento y no tenemos uno guardado
        hospital_global = hosp_ai or ctx.user_data.get("hospital_nombre") or msg.caption
        if not hospital_global:
            # Guardar temporalmente y pedir nombre
            ctx.user_data["ingresos_pendientes"] = lista_datos
            await msg.reply_text(
                "⚠️ La Inteligencia Artificial logró extraer a los pacientes, pero **no encontró el nombre del hospital** en el archivo.\n\n"
                "Por favor, escribe a continuación el nombre del hospital o refugio al que pertenecen:",
                parse_mode="Markdown"
            )
            return ESPERANDO_NOMBRE_HOSPITAL
            
        for d in lista_datos:
            if d.nombre:
                ingresos_a_procesar.append({
                    "nombre_completo": d.nombre_completo(),
                    "edad": d.edad,
                    "hospital_nombre": hospital_global,
                    "detalles_ingreso": d.condicion_medica or "Ingreso clínico",
                    "fecha_ingreso": datetime.now().strftime("%d/%m/%Y")
                })
    else:
        # Texto
        texto = msg.text.strip()
        
        if "drive.google.com" in texto:
            await msg.reply_text("📥 *Enlace de Google Drive detectado.*\n\n✅ El Agente IA ha iniciado la extracción y filtrado en segundo plano inmediatamente. Los pacientes se consolidarán en la vista de *Hospitales* en unos instantes.", parse_mode="Markdown")
            
            # Simulamos el encolado inicial
            try:
                import redis.asyncio as aioredis
                import json
                from config import settings
                redis_conn = await aioredis.from_url(settings.redis_url)
                await redis_conn.lpush("queue:p3", json.dumps({"tipo": "sincronizar_gdrive", "datos": {"url": texto}}))
                await redis_conn.close()
            except Exception as e:
                logger.error(f"Error encolando tarea gdrive: {e}")
            
            await enviar_menu_principal(update, ctx)
            return MENU_ADMIN
            
        lineas = [l.strip() for l in texto.split("\n") if l.strip()]
        for linea in lineas:
            partes = [p.strip() for p in linea.split(",")]
            if len(partes) < 3:
                partes = [p.strip() for p in linea.split("-")]
            if len(partes) < 3:
                continue
            
            try:
                edad = int("".join(filter(str.isdigit, partes[1])))
            except:
                edad = None
                
            ingresos_a_procesar.append({
                "nombre_completo": partes[0],
                "edad": edad,
                "hospital_nombre": partes[2],
                "detalles_ingreso": partes[3] if len(partes) > 3 else "Ingreso reportado",
                "fecha_ingreso": datetime.now().strftime("%d/%m/%Y")
            })

    if not ingresos_a_procesar:
        await msg.reply_text("⚠️ No pude detectar ingresos válidos. Intenta nuevamente.")
        return ESPERANDO_LISTA_HOSPITAL
        
    await msg.reply_text(f"⏳ Procesando {len(ingresos_a_procesar)} registros de hospital y ejecutando cruces...")
    
    exitosos = 0
    coincidencias = 0
    
    for datos_ingreso in ingresos_a_procesar:
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
        f"• Registros procesados con éxito: *{exitosos}/{len(ingresos_a_procesar)}*\n"
        f"• Coincidencias/Alertas automáticas enviadas: *{coincidencias}*",
        parse_mode="Markdown"
    )
    
    # Volver al menú
    await enviar_menu_principal(update, ctx)
    return MENU_ADMIN


# ── Subir Foto de Encontrados ──────────────────────────────────────────

async def cb_subir_foto_encontrados(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    texto = (
        "📸 *Subir Foto de Listado de Encontrados*\n\n"
        "Envía una foto de una lista de personas encontradas (ej. la pizarra de un refugio). "
        "Asegúrate de incluir la *ubicación del hallazgo en la descripción* de la foto.\n\n"
        "La IA extraerá todos los nombres, los marcará como LOCALIZADOS y notificará a sus familiares automáticamente."
    )
    teclado = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Atrás", callback_data="admin_volver")]])
    await query.message.edit_text(texto, parse_mode="Markdown", reply_markup=teclado)
    return ESPERANDO_FOTO_ENCONTRADOS


async def recibir_foto_encontrados(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    if not msg.photo:
        await msg.reply_text("⚠️ Por favor envía una imagen. Intenta de nuevo o cancela con /cancelar.")
        return ESPERANDO_FOTO_ENCONTRADOS

    ubicacion = msg.caption or "Ubicación reportada por administrador (sin detalles)"
    await msg.reply_text("⏳ Analizando el listado con IA... (esto puede tardar unos segundos)")

    # Descargar foto
    import uuid
    from pathlib import Path
    foto = msg.photo[-1]
    file = await ctx.bot.get_file(foto.file_id)
    FOTOS_DIR = Path("fotos_temp")
    FOTOS_DIR.mkdir(exist_ok=True)
    ruta = FOTOS_DIR / f"listado_{uuid.uuid4()}.jpg"
    await file.download_to_drive(str(ruta))

    # Analizar con IA
    from ai.image_processor import procesador_imagenes
    lista_datos = await procesador_imagenes.extraer_datos(str(ruta))

    if not lista_datos or (len(lista_datos) == 1 and not lista_datos[0].nombre):
        await msg.reply_text("❌ No pude extraer nombres de esta imagen. Asegúrate de que el texto sea legible.")
        await enviar_menu_principal(update, ctx)
        return MENU_ADMIN

    # Registrar
    from database.crud import crear_persona
    from database.models import EstadoPersona, Prioridad, FuenteRegistro

    registrados = 0
    for d in lista_datos:
        if d.nombre:
            persona_dict = d.to_persona_dict()
            persona_dict.update({
                "estado": EstadoPersona.LOCALIZADO,
                "ultima_ubicacion": ubicacion,
                "fuente_registro": FuenteRegistro.TELEGRAM,
                "prioridad": Prioridad.BAJA,
            })
            await crear_persona(persona_dict)
            registrados += 1

    await msg.reply_text(
        f"✅ *Listado procesado con éxito*\n\n"
        f"Se han registrado *{registrados}* personas como LOCALIZADAS en:\n_{ubicacion}_\n\n"
        f"Las alertas a los familiares suscritos se enviarán en segundo plano.",
        parse_mode="Markdown"
    )
    
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
                CallbackQueryHandler(cb_subir_foto_encontrados, pattern="^admin_upload_photo$"),
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
            ESPERANDO_NOMBRE_HOSPITAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_nombre_hospital),
                CallbackQueryHandler(cb_volver_menu,              pattern="^admin_volver$"),
                CallbackQueryHandler(cancelar_admin,             pattern="^cancelar$"),
            ],
            ESPERANDO_LISTA_HOSPITAL: [
                MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, recibir_lista_hospital),
                CallbackQueryHandler(cb_volver_menu,              pattern="^admin_volver$"),
                CallbackQueryHandler(cancelar_admin,             pattern="^cancelar$"),
            ],
            ESPERANDO_FOTO_ENCONTRADOS: [
                MessageHandler(filters.PHOTO, recibir_foto_encontrados),
                CallbackQueryHandler(cb_volver_menu,              pattern="^admin_volver$"),
                CallbackQueryHandler(cancelar_admin,             pattern="^cancelar$"),
            ]
        },
        fallbacks=[
            CommandHandler("cancelar", cancelar_admin),
            CallbackQueryHandler(cancelar_admin, pattern="^cancelar$"),
            MessageHandler(filters.Regex("^⚙️ Panel Administrar$"), iniciar_admin)
        ],
        conversation_timeout=300,
        name="admin",
        persistent=False
    )
