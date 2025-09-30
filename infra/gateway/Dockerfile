FROM python:3.11-slim

# Evita pyc e buffers em logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Instalar dependências do sistema (mínimo)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala libs Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o app
COPY app.py .

# Porta exposta pelo Flask gateway
EXPOSE 8080

# Variáveis de ambiente default (podem ser sobrescritas no compose)
ENV PORT=8080 \
    UPSTREAM_TIMEOUT=5.0 \
    RETRY_COUNT=1 \
    RATE_LIMIT_RPM=120 \
    CORS_ALLOW_ORIGIN="*"

# Suba com o servidor do Flask (para dev). Em produção, prefira Gunicorn
CMD ["python", "app.py"]

