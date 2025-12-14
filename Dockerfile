FROM python:3.11-slim

# Evitar prompts interactivos
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Instalar FFmpeg, Node.js y dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Crear usuario no-root para seguridad
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Directorio de trabajo
WORKDIR /app

# Copiar requirements e instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn bgutil-ytdlp-pot-provider

# Copiar código de la aplicación
COPY backend_sessions.py .
COPY frontend_sessions/ ./frontend_sessions/
COPY cookies/ ./cookies/

# Cambiar propietario al usuario no-root
RUN chown -R appuser:appuser /app

# Cambiar a usuario no-root
USER appuser

# Puerto de la aplicación
EXPOSE 1005

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:1005/api/info || exit 1

# Ejecutar con Gunicorn (producción)
CMD ["gunicorn", "--bind", "0.0.0.0:1005", "--workers", "1", "--threads", "8", "--timeout", "120", "backend_sessions:app"]
