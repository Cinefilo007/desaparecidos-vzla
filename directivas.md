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
- **2026-06-25 (Corrección de Tolerancia a Fallos)**: Robustez ante variables de entorno faltantes.
  - Modificado `config.py` para asignar valores por defecto vacíos (`""`) a `telegram_bot_token` y `gemini_api_key`, evitando que la app crasheé de inmediato con un `ValidationError`.
  - Agregado log de depuración seguro para enumerar las claves de entorno de bot y base de datos detectadas en el contenedor de Railway.
  - Implementadas alertas de error explícitas en los logs para advertir si las variables críticas están vacías o ausentes.
- **2026-06-25 (Soporte PostgreSQL en Producción)**: Adaptación de base de datos para Railway.
  - Agregado el driver asíncrono `asyncpg` a `requirements.txt` para habilitar conexiones asíncronas con PostgreSQL.
  - Implementado un validador en `config.py` (`validate_db_url`) que traduce automáticamente el formato estándar `postgresql://` inyectado por Railway al protocolo asíncrono `postgresql+asyncpg://` requerido por SQLAlchemy.
- **2026-06-25 (Corrección del Bucle de Eventos del Bot)**: Solución de colisión en asyncio.
  - Modificado `bot/main.py` para reescribir `main()` como una función sincrónica y llamar a `app.run_polling()` de forma bloqueante.
  - Implementado `post_init` para gestionar asíncronamente la configuración de comandos de Telegram (`configurar_comandos`), solucionando la excepción `RuntimeError: This event loop is already running`.
- **2026-06-25 (Corrección de Event Loop en MainThread)**: Bucle de eventos explícito para el bot.
  - Modificado `bot/main.py` para crear e instalar un bucle de eventos nuevo (`asyncio.new_event_loop()`, `asyncio.set_event_loop()`) antes de instanciar la aplicación. Esto corrige el error `RuntimeError: There is no current event loop in thread 'MainThread'` debido a que el bucle de eventos temporal utilizado para inicializar la base de datos se destruía antes de iniciar el polling.
- **2026-06-25 (Menú de Botones, Gemini 2.5 y Solución Asyncio)**: Nuevas características y robustez.
  - Implementado un menú de botones persistente (`ReplyKeyboardMarkup` con `is_persistent=True` y `resize_keyboard=True`) en `bot/keyboards.py`, permitiendo abrir la Mini App web y acceder a comandos de forma directa.
  - Modificado `bot/handlers/registro.py` y `bot/handlers/busqueda.py` para admitir disparadores de texto basados en expresiones regulares (`filters.Regex`) desde el menú de botones persistentes.
  - Cambiada la versión del modelo de IA a `gemini-2.5-flash` en `config.py`.
  - Reescrito el arranque del bot de Telegram en `bot/main.py` de forma completamente asíncrona dentro del mismo loop de eventos principal (`asyncio.run(main())`), solucionando permanentemente la excepción `RuntimeError: Event loop is closed` de SQLAlchemy/asyncpg al interactuar con Postgres.
- **2026-06-25 (Corrección de URL de la Mini App)**: Sanitización de protocolo para botones WebApp.
  - Agregado el validador `validate_miniapp_url` en `config.py` para forzar e inyectar el protocolo seguro `https://` en la URL de la Mini App si el usuario la configura sin protocolo en Railway, evitando la caída `BadRequest: Keyboard button web app url is invalid`.
- **2026-06-25 (Corrección del Método de Envío del Bot)**: Solución de AttributeError al registrar personas.
  - Modificado `bot/handlers/registro.py` para autodetectar de forma segura si la interacción proviene de un botón (`callback_query`) o de un mensaje de texto.
  - Implementado un fallback asíncrono robusto utilizando `context.bot.send_message` si tanto `message` como `callback_query` son nulos, previniendo caídas imprevistas (`AttributeError`) al finalizar y registrar el flujo de una persona desaparecida.
- **2026-06-25 (Scraping Dinámico, Notificaciones y Reconocimiento Facial)**: Nuevas capacidades de búsqueda avanzada y alertas.
  - Implementado el script del Worker asíncrono en `worker/main.py` para procesar tareas de la cola de Redis de forma eficiente mediante BLPOP.
  - Creadas las tablas `SuscripcionAlerta`, `FuenteScraping` e `IngresoHospital` en `database/models.py`.
  - Añadido el soporte de fuentes dinámicas de base de datos en `scheduler/main.py`, permitiendo registrar URLs y perfiles de scraping en caliente.
  - Agregadas las capacidades de recorte de rostros con Pillow y comparación facial comparativa con Gemini 2.5 Flash en `ai/image_processor.py`.
  - Integrado el flujo de suscripción en el Bot de Telegram (`bot/handlers/busqueda.py`) con los botones dinámicos en `bot/keyboards.py`.
  - Mejorada la lógica de sugerencia de registro para búsquedas sin resultados, guiando de inmediato al usuario al flujo de registro de desaparecidos.
- **2026-06-25 (Solución de Base de Datos y Panel de Control de Admin)**: Reactivación de producción y gestión interactiva.
  - Implementado el script de migración en caliente en `database/migrate.py` con hasta 5 reintentos y esperas de 3 segundos ante retrasos en la conexión con la base de datos de Railway.
  - Modificado el orden de precedencia y agrupamiento en `railway.toml` para ejecutar sincrónicamente la migración de base de datos antes de levantar los servicios unificados de fondo en Railway, garantizando que el bot y la API arranquen con la estructura de tablas corregida.
  - Creado el panel administrativo en `bot/handlers/admin.py` protegido por `ADMIN_CHAT_ID` con menús inline interactivos para añadir fuentes de scraping y cargar listas de ingresos hospitalarios de texto.
  - Implementada la notificación automática por Telegram al familiar si un ingreso hospitalario coincide fonéticamente con una persona en estado de búsqueda.
  - Actualizado `bot/keyboards.py` para inyectar dinámicamente el botón "⚙️ Panel Administrar" en el teclado persistente del chat solo si el usuario es administrador.
  - Expuestos los endpoints `GET/POST /api/fuentes` y `GET/POST /api/hospitales/ingresos` in `api/main.py` para administración programática.
- **2026-06-25 (Solución Definitiva de Base de Datos e Integración de Migraciones)**:
  - Modificado `database/crud.py` para integrar la lógica de migración en caliente (`ALTER TABLE personas ADD COLUMN IF NOT EXISTS ...`) directamente en `init_db()`. Esto garantiza que los campos `foto_rostro_local_path` y `foto_rostro_url` se verifiquen y creen automáticamente en PostgreSQL de producción al iniciar cualquier servicio (bot, API, worker), previniendo de raíz el error `UndefinedColumnError` y eliminando fallas de arranque que inducen a colisiones de polling de Telegram en despliegues concurrentes de Railway.

