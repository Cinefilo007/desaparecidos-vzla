# Directivas del Proyecto - Desaparecidos Venezuela 🇻🇪

Este documento contiene las reglas de desarrollo, la estructura visual, pautas de seguridad y la bitácora de cambios para mantener el orden, la seguridad y la modularidad del código.

---

## 🛠️ Reglas Generales de Desarrollo

1. **Modularidad**: El código debe estar siempre organizado en módulos específicos (e.g., `api/`, `bot/`, `scheduler/`, `database/`, `ai/`, `worker/`). Está prohibido el código espagueti.
2. **Asunción del Usuario**: Se asume que el usuario/operador no es experto técnico. Todos los mensajes de error deben ser amigables y los flujos deben guiar al usuario paso a paso.
3. **Control de Cambios**: Cualquier modificación del sistema debe documentarse en la sección de **Bitácora de Cambios** al final de este archivo.

---

## 🎨 Guía de Estilo Visual (Frontend)

Para evitar inconsistencias en el frontend, se establece el siguiente sistema de diseño:

### 1. Paleta de Colores (Inspirada en la bandera de Venezuela y modo oscuro premium)
- **Fondo Principal (`--color-bg`)**: `#0A0A0F` (Negro profundo)
- **Superficies/Cards (`--color-surface`)**: `#131318` (Gris oscuro)
- **Contenedores/Inputs (`--color-surface-2`)**: `#1E1E28` (Gris medio)
- **Bordes/Separadores (`--color-border`)**: `#2A2A38` (Gris sutil)
- **Acento Primario (`--color-primary`)**: `#F5C842` (Amarillo venezolano, para botones principales, llamadas a la acción)
- **Acento Secundario (`--color-blue`)**: `#003DA5` (Azul venezolano, para enlaces y elementos informativos)
- **Alerta Peligro/Sin Contacto (`--color-danger`)**: `#CF142B` (Rojo venezolano, para estados críticos de alerta)
- **Éxito/Localizado (`--color-safe`)**: `#22C55E` (Verde esmeralda, para personas localizadas con éxito)
- **Texto Principal (`--color-text`)**: `#F8F8FF` (Blanco puro)
- **Texto Secundario (`--color-text-muted`)**: `#8B8BA7` (Gris claro)

### 2. Margen y Espaciado
- **Padding de Página**: `16px` (móviles) / `24px` (escritorio).
- **Espaciado base**: Escala de múltiplos de `8px` (`8px`, `16px`, `24px`, `32px`, `48px`).
- **Margen entre tarjetas (Cards)**: `12px` o `16px`.

### 3. Tipografía
- **Fuente**: `Inter` (Google Fonts), sans-serif.
- **Títulos Grandes**: `32px` (`font-weight: 800`).
- **Títulos de Sección**: `24px` (`font-weight: 700`).
- **Títulos de Tarjetas**: `18px` (`font-weight: 600`).
- **Texto Base**: `15px` (`font-weight: 400`).
- **Texto Secundario / Etiquetas**: `13px` / `11px` (`font-weight: 400`).

### 4. Componentes Comunes
- **Botón Primario**: Alto `52px`, radio de borde `12px`, fondo `--color-primary`, texto negro negrita, animación de hover con escala sutil (`scale(1.02)`).
- **Entradas de Texto (Inputs)**: Alto `48px`, fondo `--color-surface-2`, borde `--color-border`, radio `10px`. Foco con borde amarillo `--color-primary` y sombra difuminada.
- **Tarjetas de Persona**: Fondo `--color-surface`, borde `--color-border`, radio `16px`. Foto redonda a la izquierda de `56px`, badge de estado a la derecha.

---

## 🔒 Directivas de Seguridad

1. **Gestión de Credenciales**:
   - **NUNCA** incluir credenciales, tokens (`TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`) ni el archivo `.env` en el repositorio Git.
   - El archivo `.env` debe listarse explícitamente en `.gitignore`.
   - Utilizar siempre variables de entorno cargadas a través de `config.py` con `pydantic-settings`.
2. **Base de Datos**:
   - Evitar inyección de código SQL. Utilizar siempre el ORM (SQLAlchemy) con consultas parametrizadas.
   - El archivo SQLite de desarrollo local (`vzla_bot.db`) debe ignorarse en Git para evitar subir bases de datos de prueba al repositorio público.
3. **Sanitización de Archivos**:
   - Las fotos subidas por los usuarios deben ser validadas en el backend (tipo de archivo JPEG/PNG, tamaño máximo de 5MB) para evitar ataques de ejecución remota de código o denegación de servicio.
4. **Verificación de WebApps**:
   - Validar la firma de los datos de inicialización enviados desde Telegram (`initData`) en el backend para asegurarse de que provienen de Telegram y no de un atacante.

---

## 📊 Bitácora de Cambios

- **2026-06-25**: Creación inicial del proyecto.
  - Diseñado e implementado el motor de búsqueda fonético venezolano (`ai/name_matcher.py`).
  - Diseñado e implementado el bot de Telegram (`bot/main.py`, `bot/handlers/`, `bot/keyboards.py`).
  - Diseñado e implementado el programador adaptativo inteligente (`scheduler/main.py`).
  - Diseñada e implementada la Telegram Mini App en HTML/CSS/JS (`miniapp/index.html`).
  - Diseñado e implementado el backend de la API (`api/main.py`) con FastAPI.
  - Creación del archivo `Procfile` y `railway.toml` para Railway.
  - Creación del archivo de Directivas del Proyecto (`directivas.md`).
- **2026-06-25 (Control de Versiones)**: Configuración e inicialización de Git.
  - Creación del archivo `.gitignore` para proteger credenciales (`.env`) e ignorar base de datos local SQLite (`vzla_bot.db`).
  - Inicialización del repositorio Git local (`git init`) y renombrado de la rama principal a `main`.
  - Realizado primer commit con toda la estructura de la aplicación.
- **2026-06-25 (Despliegue y Optimización)**: Corrección de compilación y unificación de servicios en Railway.
  - Modificado `requirements.txt` para remover dependencias pesadas de IA no utilizadas (tensorflow, torch, deepface, whisper, faiss, playwright) con el fin de evitar fallas de memoria (OOM) y exceso de tiempo de compilación.
  - Modificado `railway.toml` para simplificar la compilación (sin Playwright) y unificar los servicios (API, Bot, Scheduler) dentro de un mismo contenedor.
  - Eliminado el archivo `Procfile` para evitar la creación de servicios duplicados en Railway.
- **2026-06-25 (Corrección de Configuración)**: Migración de `config.py` a Pydantic v2.
  - Actualizado `config.py` para usar `SettingsConfigDict` de `pydantic_settings` en lugar de la clase Config interna antigua (obsoleta en Pydantic v2). Esto soluciona el ValidationError al mapear variables de entorno en mayúsculas (`TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`) y restaura el comportamiento case-insensitive.



