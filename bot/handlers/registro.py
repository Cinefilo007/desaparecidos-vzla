"""
bot/handlers/registro.py — Flujo conversacional de registro de personas.
Maneja: foto con datos, foto sola, texto, y preguntas adaptativas.
"""
import os
import uuid
from pathlib import Path
from loguru import logger

from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters,
)

from ai.image_processor import procesador_imagenes, DatosExtraidos
from database.crud import crear_persona, buscar_posible_duplicado, suscribir_a_persona
from database.models import EstadoPersona, Prioridad, FuenteRegistro
from bot.keyboards import kb_confirmar_datos, kb_saltar_campo, kb_acciones_persona, kb_cancelar
from config import settings

# ── Estados de la conversación ─────────────────────────────────────────
(
    ESPERANDO_FOTO_O_INICIO,
    CONFIRMANDO_DATOS,
    COMPLETANDO_CAMPO,
    CORRIGIENDO_CAMPO,
) = range(4)

FOTOS_DIR = Path("fotos_temp")
FOTOS_DIR.mkdir(exist_ok=True)


# ── Inicio del flujo de registro ───────────────────────────────────────

async def iniciar_registro(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Punto de entrada: /registrar o botón del menú."""
    query = update.callback_query
    if query:
        await query.answer()
        send = query.message.reply_text
    else:
        send = update.message.reply_text

    await send(
        "📝 *Registro de persona desaparecida*\n\n"
        "Puedes:\n"
        "• 📸 *Enviar una foto* de la ficha 'SE BUSCA' — extraigo los datos automáticamente\n"
        "• 📸 *Enviar una foto* de la persona — te haré unas preguntas cortas\n"
        "• ✍️ *Escribir el nombre* directamente\n\n"
        "¿Cómo prefieres continuar?",
        parse_mode="Markdown",
        reply_markup=kb_cancelar("❌ Cancelar registro"),
    )
    return ESPERANDO_FOTO_O_INICIO


# ── Recibe foto ────────────────────────────────────────────────────────

async def recibir_foto(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Descarga la foto y la analiza con Gemini Vision."""
    msg = update.message
    await msg.reply_text("⏳ Analizando la imagen con IA... (esto tarda 1-2 segundos)")

    # Descargar la foto en máxima resolución
    foto = msg.photo[-1]
    file = await ctx.bot.get_file(foto.file_id)
    ruta = FOTOS_DIR / f"{uuid.uuid4()}.jpg"
    await file.download_to_drive(str(ruta))

    # Extraer datos con Gemini Vision (ahora retorna una lista)
    lista_datos = await procesador_imagenes.extraer_datos(str(ruta))

    if not lista_datos:
        # Fallback si falla por completo
        from ai.image_processor import DatosExtraidos
        lista_datos = [DatosExtraidos()]
        lista_datos[0].campos_faltantes = procesador_imagenes._calcular_faltantes(lista_datos[0])

    ctx.user_data["lista_datos"] = lista_datos
    ctx.user_data["foto_path"] = str(ruta)
    ctx.user_data["chat_id"] = str(msg.chat_id)

    # Iniciar flujo con la primera persona
    return await _iniciar_registro_persona(update, ctx)


async def _iniciar_registro_persona(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia el registro de la persona actual en la cola."""
    lista_datos = ctx.user_data.get("lista_datos", [])
    if not lista_datos:
        return ConversationHandler.END

    datos = lista_datos[0]
    ctx.user_data["datos"] = datos
    
    total = len(lista_datos)
    total_original = ctx.user_data.get("total_personas", total)
    if "total_personas" not in ctx.user_data:
        ctx.user_data["total_personas"] = total
        
    actual = total_original - total + 1

    msg = update.message if update.message else update.callback_query.message

    if total_original > 1:
        await msg.reply_text(f"👥 *Registrando persona {actual} de {total_original}*", parse_mode="Markdown")

    # Si no se extrajo nada útil, pedir nombre directamente
    if not datos.nombre_completo() and not datos.tiene_cara_visible:
        await msg.reply_text(
            "📸 Guardé la foto pero no pude leer texto para esta persona.\n"
            "Empecemos con lo básico:\n\n"
            "¿Cuál es el *nombre completo* de la persona que buscas?",
            parse_mode="Markdown",
        )
        ctx.user_data["campo_esperando"] = "nombre"
        return COMPLETANDO_CAMPO

    # Mostrar datos extraídos para confirmación
    resumen = procesador_imagenes.generar_resumen_telegram(datos)
    await msg.reply_text(
        resumen,
        parse_mode="Markdown",
        reply_markup=kb_confirmar_datos(),
    )
    return CONFIRMANDO_DATOS


# ── Recibe nombre por texto ────────────────────────────────────────────

async def recibir_texto_inicial(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Si el usuario escribe un nombre en lugar de enviar foto."""
    nombre = update.message.text.strip()

    datos = DatosExtraidos()
    datos.nombre = nombre.split()[0] if nombre else None
    datos.apellidos = " ".join(nombre.split()[1:]) if len(nombre.split()) > 1 else None
    datos.campos_faltantes = procesador_imagenes._calcular_faltantes(datos)

    ctx.user_data["datos"]    = datos
    ctx.user_data["chat_id"]  = str(update.message.chat_id)
    ctx.user_data["foto_path"] = None

    # Ir directamente a completar los campos faltantes
    return await _preguntar_siguiente_campo(update, ctx)


# ── Confirmación de datos ──────────────────────────────────────────────

async def confirmar_datos(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Usuario confirmó que los datos son correctos."""
    query = update.callback_query
    await query.answer()

    datos: DatosExtraidos = ctx.user_data.get("datos")
    if not datos:
        await query.message.reply_text("⚠️ Sesión expirada. Usa /registrar para comenzar de nuevo.")
        return ConversationHandler.END

    # ¿Quedan campos obligatorios sin completar?
    obligatorios_faltantes = [f for f in datos.campos_faltantes if f["obligatorio"]]
    if obligatorios_faltantes:
        ctx.user_data["campo_idx"] = 0
        return await _preguntar_siguiente_campo(update, ctx, desde_query=True)

    # Todos los datos necesarios → registrar
    return await _registrar_y_finalizar(update, ctx)


async def corregir_dato(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Usuario quiere corregir un dato."""
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "✏️ ¿Qué dato quieres corregir? Escribe el nombre del campo "
        "(por ejemplo: *nombre*, *edad*, *teléfono*, *ubicación*, *ropa*)",
        parse_mode="Markdown",
    )
    return CORRIGIENDO_CAMPO

async def seleccionar_campo_corregir(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """El usuario escribió el nombre del campo a corregir."""
    campo_texto = update.message.text.strip().lower()
    
    mapeo = {
        "nombre": "nombre",
        "apellido": "apellidos",
        "apellidos": "apellidos",
        "edad": "edad",
        "ubicacion": "ultima_ubicacion",
        "ubicación": "ultima_ubicacion",
        "zona": "zona",
        "descripcion": "descripcion_fisica",
        "descripción": "descripcion_fisica",
        "ropa": "descripcion_fisica",
        "fisico": "descripcion_fisica",
        "físico": "descripcion_fisica",
        "condicion": "condicion_medica",
        "condición": "condicion_medica",
    }
    
    campo_real = mapeo.get(campo_texto)
    if not campo_real:
        for k, v in mapeo.items():
            if k in campo_texto:
                campo_real = v
                break

    if not campo_real:
        await update.message.reply_text("❌ No reconocí ese campo. Intenta con: nombre, edad, ropa, ubicación...")
        return CORRIGIENDO_CAMPO
        
    ctx.user_data["campo_esperando"] = campo_real
    await update.message.reply_text(f"📝 Escribe el *nuevo valor* para la {campo_texto}:", parse_mode="Markdown")
    return COMPLETANDO_CAMPO


# ── Completar campos faltantes ─────────────────────────────────────────

async def _preguntar_siguiente_campo(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    desde_query: bool = False,
) -> int:
    datos: DatosExtraidos = ctx.user_data.get("datos", DatosExtraidos())
    faltantes = [f for f in datos.campos_faltantes if not getattr(datos, f["campo"], None)]

    if not faltantes:
        return await _registrar_y_finalizar(update, ctx, desde_query=desde_query)

    prox = faltantes[0]
    ctx.user_data["campo_esperando"] = prox["campo"]

    teclado = kb_saltar_campo() if not prox["obligatorio"] else None
    texto   = f"📝 {prox['pregunta']}"

    if desde_query:
        await update.callback_query.message.reply_text(texto, parse_mode="Markdown", reply_markup=teclado)
    else:
        await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=teclado)

    return COMPLETANDO_CAMPO


async def recibir_respuesta_campo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Recibe la respuesta a una pregunta de un campo faltante."""
    datos: DatosExtraidos = ctx.user_data.get("datos")
    campo    = ctx.user_data.get("campo_esperando")
    respuesta = update.message.text.strip()

    if not campo or not datos:
        return ConversationHandler.END

    # Ignorar respuestas vacías o "no sé"
    if respuesta.lower() not in ("no sé", "no se", "no", "n/a", "-", ""):
        # Conversión de tipos
        if campo == "edad":
            try:
                setattr(datos, campo, int("".join(filter(str.isdigit, respuesta))))
            except (ValueError, TypeError):
                pass
        else:
            setattr(datos, campo, respuesta)

        # Marcar campo como completado
        datos.campos_faltantes = [f for f in datos.campos_faltantes if f["campo"] != campo]

    return await _preguntar_siguiente_campo(update, ctx)


async def saltar_campo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Saltea un campo opcional."""
    query = update.callback_query
    await query.answer("Campo omitido ✓")
    campo = ctx.user_data.get("campo_esperando")
    datos: DatosExtraidos = ctx.user_data.get("datos")
    if datos and campo:
        datos.campos_faltantes = [f for f in datos.campos_faltantes if f["campo"] != campo]
    return await _preguntar_siguiente_campo(update, ctx, desde_query=True)


# ── Registro final ─────────────────────────────────────────────────────

async def _registrar_y_finalizar(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    desde_query: bool = False,
) -> int:
    datos: DatosExtraidos = ctx.user_data.get("datos")
    chat_id = ctx.user_data.get("chat_id", "")
    
    # Determinar método de envío robusto (evita caídas si update.message o update.callback_query son None)
    if update.callback_query and update.callback_query.message:
        send = update.callback_query.message.reply_text
    elif update.message:
        send = update.message.reply_text
    else:
        chat_id_val = update.effective_chat.id if update.effective_chat else chat_id
        async def send_fallback(text, **kwargs):
            return await ctx.bot.send_message(chat_id=chat_id_val, text=text, **kwargs)
        send = send_fallback

    if not datos or not datos.nombre:
        await send("⚠️ No hay suficientes datos para registrar. Usa /registrar para comenzar de nuevo.")
        return ConversationHandler.END

    # Verificar duplicados
    duplicado = await buscar_posible_duplicado(
        nombre=datos.nombre_completo(),
        edad=datos.edad,
        zona=datos.zona,
    )
    if duplicado:
        await send(
            f"⚠️ *Posible duplicado detectado*\n\n"
            f"Encontré un registro similar: *{duplicado.nombre_completo()}* (ID #{duplicado.id})\n"
            f"¿Es la misma persona? Si no, continúa y se creará un registro nuevo.",
            parse_mode="Markdown",
            reply_markup=kb_acciones_persona(duplicado.id),
        )
        return ConversationHandler.END

    # Determinar prioridad
    prioridad = Prioridad.CRITICA if datos.es_vulnerable else Prioridad.MEDIA

    # Recortar y guardar rostro si es visible
    foto_path = ctx.user_data.get("foto_path")
    foto_rostro_path = None
    if foto_path and datos.tiene_cara_visible and datos.caja_delimitadora_rostro:
        foto_rostro_path = procesador_imagenes.recortar_rostro(foto_path, datos.caja_delimitadora_rostro)

    # Mover fotos a uploads/ para su persistencia
    import shutil
    UPLOADS_DIR = Path("uploads")
    UPLOADS_DIR.mkdir(exist_ok=True)
    
    if foto_path and os.path.exists(foto_path):
        nuevo_path = UPLOADS_DIR / os.path.basename(foto_path)
        shutil.move(foto_path, str(nuevo_path))
        foto_path = str(nuevo_path)
        
    if foto_rostro_path and os.path.exists(foto_rostro_path):
        nuevo_rostro_path = UPLOADS_DIR / os.path.basename(foto_rostro_path)
        shutil.move(foto_rostro_path, str(nuevo_rostro_path))
        foto_rostro_path = str(nuevo_rostro_path)

    # Crear en base de datos
    persona_dict = datos.to_persona_dict()
    persona_dict.update({
        "prioridad":        prioridad,
        "fuente_registro":  FuenteRegistro.TELEGRAM,
        "contacto_chat_id": chat_id,
        "foto_local_path":  foto_path,
        "foto_rostro_local_path": foto_rostro_path,
    })

    persona = await crear_persona(persona_dict)
    
    # Suscribir automáticamente al creador de la alerta para notificaciones futuras del scraper
    if chat_id:
        await suscribir_a_persona(persona.id, chat_id)
        
    import html
    nombre  = html.escape(persona.nombre_completo())

    emoji_prioridad = "🚨" if datos.es_vulnerable else "✅"
    aviso_vulnerable = (
        f"\n\n⚠️ <b>PRIORIDAD MÁXIMA</b> por: {html.escape(datos.razon_vulnerabilidad)}"
        if datos.es_vulnerable and datos.razon_vulnerabilidad else ""
    )

    await send(
        f"{emoji_prioridad} <b>¡{nombre} fue registrado/a exitosamente!</b>\n\n"
        f"🔔 Te notificaremos <b>inmediatamente</b> si encontramos coincidencias.\n"
        f"📊 ID de seguimiento: <code>#VZ-{persona.id:04d}</code>"
        f"{aviso_vulnerable}",
        parse_mode="HTML",
        reply_markup=kb_abrir_miniapp_reg(persona.id),
    )

    logger.info(f"Persona registrada: #{persona.id} — {nombre} (prioridad={prioridad})")

    # Verificar si hay más personas en la lista
    lista_datos = ctx.user_data.get("lista_datos", [])
    if lista_datos:
        lista_datos.pop(0)  # Eliminar la persona que acabamos de registrar
        
    if lista_datos:
        # Hay más personas en la lista, registrar a la siguiente
        await send("🔄 Procesando a la siguiente persona detectada en la foto...")
        return await _iniciar_registro_persona(update, ctx)
    else:
        # Limpiar sesión solo si ya registramos a todos
        ctx.user_data.clear()
        return ConversationHandler.END


def kb_abrir_miniapp_reg(persona_id: int):
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🌐 Ver en el panel web",
            web_app=WebAppInfo(url=f"{settings.miniapp_url}/?persona={persona_id}")
        )],
        [InlineKeyboardButton("🔍 Buscar otra persona", callback_data="menu_buscar")],
    ])


# ── Cancelar ───────────────────────────────────────────────────────────

async def cancelar(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        await query.message.reply_text("❌ Registro cancelado. Usa /start para volver al menú.")
    else:
        await update.message.reply_text("❌ Operación cancelada. Usa /start para volver al menú.")
    ctx.user_data.clear()
    return ConversationHandler.END


# ── ConversationHandler exportable ────────────────────────────────────

def get_registro_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("registrar", iniciar_registro),
            MessageHandler(filters.Regex("^📝 Registrar Persona$"), iniciar_registro),
            CallbackQueryHandler(iniciar_registro, pattern="^menu_registrar$"),
        ],
        states={
            ESPERANDO_FOTO_O_INICIO: [
                MessageHandler(filters.PHOTO, recibir_foto),
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_texto_inicial),
            ],
            CONFIRMANDO_DATOS: [
                CallbackQueryHandler(confirmar_datos, pattern="^registro_confirmar$"),
                CallbackQueryHandler(corregir_dato,  pattern="^registro_corregir$"),
            ],
            COMPLETANDO_CAMPO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_respuesta_campo),
                CallbackQueryHandler(saltar_campo, pattern="^campo_saltar$"),
            ],
            CORRIGIENDO_CAMPO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, seleccionar_campo_corregir),
            ],
        },
        fallbacks=[
            CommandHandler("cancelar", cancelar),
            CallbackQueryHandler(cancelar, pattern="^cancelar$"),
            MessageHandler(filters.Regex("^📝 Registrar Persona$"), iniciar_registro)
        ],
        conversation_timeout=600,  # 10 minutos
        name="registro",
        persistent=False,
    )
