"""
bot/keyboards.py — Teclados y botones reutilizables del bot de Telegram.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo


# ── Menú Principal ─────────────────────────────────────────────────────

def kb_menu_principal() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Reportar a alguien", callback_data="menu_registrar")],
        [InlineKeyboardButton("🔍 Buscar por nombre",  callback_data="menu_buscar")],
        [InlineKeyboardButton("🗺️ Ver el mapa",        callback_data="menu_mapa"),
         InlineKeyboardButton("📊 Estadísticas",       callback_data="menu_stats")],
        [InlineKeyboardButton("🤝 Ser voluntario",     callback_data="menu_voluntario")],
    ])


# ── Confirmación de datos extraídos de imagen ─────────────────────────

def kb_confirmar_datos() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Todo correcto — REGISTRAR", callback_data="registro_confirmar")],
        [InlineKeyboardButton("✏️ Corregir un dato",         callback_data="registro_corregir")],
        [InlineKeyboardButton("❌ Cancelar",                  callback_data="cancelar")],
    ])


# ── Cancelar acción actual ─────────────────────────────────────────────

def kb_cancelar(texto: str = "❌ Cancelar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(texto, callback_data="cancelar")]])


# ── Acciones sobre una persona encontrada ─────────────────────────────

def kb_acciones_persona(persona_id: int, ya_suscrito: bool = False) -> InlineKeyboardMarkup:
    alertas_btn = (
        InlineKeyboardButton("🔕 Desactivar Alertas", callback_data=f"alerta_desub_{persona_id}")
        if ya_suscrito else
        InlineKeyboardButton("🔔 Recibir Alertas", callback_data=f"alerta_sub_{persona_id}")
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Marcar como LOCALIZADO", callback_data=f"localizado_{persona_id}")],
        [InlineKeyboardButton("📣 Tengo información",      callback_data=f"info_{persona_id}")],
        [alertas_btn, InlineKeyboardButton("🔗 Compartir", callback_data=f"compartir_{persona_id}")],
    ])


# ── Confirmación de match de IA ────────────────────────────────────────

def kb_confirmar_match(persona_id: int, avistamiento_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sí, es él/ella",   callback_data=f"match_confirm_{persona_id}_{avistamiento_id}")],
        [InlineKeyboardButton("❌ No es esa persona", callback_data=f"match_deny_{persona_id}_{avistamiento_id}")],
    ])


# ── Búsqueda colectiva ────────────────────────────────────────────────

def kb_busqueda_colectiva(persona_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁️ Sí, lo/la he visto",    callback_data=f"vi_{persona_id}")],
        [InlineKeyboardButton("❌ No lo/la he visto",      callback_data=f"novi_{persona_id}")],
    ])


# ── Abrir Mini App ────────────────────────────────────────────────────

def kb_abrir_miniapp(miniapp_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🌐 Abrir Panel de Búsqueda",
            web_app=WebAppInfo(url=miniapp_url)
        )],
    ])


# ── Saltar campo opcional ─────────────────────────────────────────────

def kb_saltar_campo() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭️ No sé / Saltar", callback_data="campo_saltar")],
    ])


# ── Menú Persistente de Botones ────────────────────────────────────────

def kb_menu_persistente(miniapp_url: str, es_admin: bool = False) -> ReplyKeyboardMarkup:
    botones = [
        [KeyboardButton("📝 Registrar Persona"), KeyboardButton("🔍 Buscar Persona")],
        [KeyboardButton("🌐 Abrir Panel Web", web_app=WebAppInfo(url=miniapp_url)), KeyboardButton("📊 Estadísticas")]
    ]
    if es_admin:
        botones.append([KeyboardButton("⚙️ Panel Administrar")])
    return ReplyKeyboardMarkup(
        botones,
        resize_keyboard=True,
        is_persistent=True
    )

