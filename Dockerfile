# Image de production du backend PGNOC-TI.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dépendances système minimales (psycopg2-binary n'en exige aucune).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code applicatif.
COPY . .

# Gunicorn + WhiteNoise servent l'API et les statiques.
# --timeout 60 : l'export CSV du journal peut être long sur de gros volumes.
EXPOSE 8000

CMD ["gunicorn", "pgnoc.wsgi:application", \
     "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "60"]
