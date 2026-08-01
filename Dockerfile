FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

# Dependances de base. Stanza et CLTK sont volontairement absents de
# l'image par defaut : ils pesent plusieurs gigaoctets avec PyTorch.
# Voir Dockerfile.nlp pour l'image complete.
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
      "fastapi>=0.115" "uvicorn[standard]>=0.30" "sqlalchemy>=2.0" \
      "jinja2>=3.1" "python-multipart>=0.0.9"

COPY app ./app
COPY data ./data
COPY scripts ./scripts

ENV DATA_DIR=/srv/var
RUN mkdir -p /srv/var

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
