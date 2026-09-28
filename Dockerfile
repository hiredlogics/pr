# The appeal API. Runs anywhere that takes a container: Fly.io, Railway, Render,
# Cloud Run, ECS.
#
# Deliberately a long-lived process rather than a serverless function. This
# service holds case state between requests, carries a ~60MB native PDF
# dependency, and makes vision calls that take 10-15 seconds - all of which are
# unremarkable here and awkward on a function platform.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# WeasyPrint renders the letter PDF through Pango and cairo, which are C
# libraries rather than wheels. Without these, `import weasyprint` fails at the
# point a customer asks for their PDF - so they are installed here even though
# pcn_appeal/pdf.py imports it lazily. fonts-dejavu-core stops the PDF falling
# back to a notdef box for every glyph.
RUN apt-get update && apt-get install --no-install-recommends -y \
      libpango-1.0-0 \
      libpangoft2-1.0-0 \
      libharfbuzz0b \
      libcairo2 \
      libgdk-pixbuf-2.0-0 \
      libffi8 \
      shared-mime-info \
      fonts-dejavu-core \
      curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first: this layer only rebuilds when the requirements change,
# not on every source edit.
COPY requirements-api.txt ./
RUN pip install --no-cache-dir -r requirements-api.txt

COPY pcn_appeal/ ./pcn_appeal/
COPY infra/postgres_schema.sql ./infra/postgres_schema.sql

# Non-root. The service writes nothing to disk; uploads are held in memory and
# evidence lives in blob storage.
RUN useradd --create-home --uid 10001 appeal
USER appeal

ENV PORT=8077
EXPOSE 8077

# Shell form so $PORT is expanded: Railway and Render inject their own.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

CMD exec uvicorn pcn_appeal.api:app --host 0.0.0.0 --port "${PORT}" --proxy-headers
