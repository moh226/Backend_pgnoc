# Image de production du backend PGNOC-TI.
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dépendances système minimales (psycopg2-binary n'en exige aucune).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Étape 2 : image finale sans outils de build ──
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copier les dépendances installées depuis l'étape builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Créer un utilisateur non-root
RUN groupadd -r pgnoc && useradd -r -g pgnoc -d /app -s /sbin/nologin pgnoc

# Code applicatif
COPY . .

# Collectstatic au build time (CompressedManifestStaticFilesStorage l'exige).
# PHASE_BUILD lève uniquement le garde-fou MinIO (les variables de prod
# ne sont pas injectées au build ; collectstatic ne touche que les
# statiques servis par whitenoise, jamais le stockage des justificatifs).
RUN DJANGO_SECRET_KEY=build-placeholder \
    DJANGO_COLLECTSTATIC_BUILD=1 \
    python manage.py collectstatic --noinput

# Donner la propriété à l'utilisateur non-root
RUN chown -R pgnoc:pgnoc /app

USER pgnoc

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz/')" || exit 1

CMD ["gunicorn", "pgnoc.wsgi:application", \
     "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "60", \
     "--access-logfile", "-", "--error-logfile", "-"]
