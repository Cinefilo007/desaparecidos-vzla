# Procfile — Define los procesos para Railway / Heroku

# Proceso principal: Bot + API juntos en un solo dyno
web: uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} & python -m bot.main

# Proceso de scheduler (en Railway crear como servicio separado)
# scheduler: python -m scheduler.main
