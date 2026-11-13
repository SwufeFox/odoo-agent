FROM python:3.12-slim-bookworm
ARG ODOO_REF=7d8d985d0b4e5bf758b2a493e0da7f3ddce1383c
RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential libpq-dev libldap2-dev libsasl2-dev libssl-dev \
    libxml2-dev libxslt1-dev libjpeg62-turbo-dev zlib1g-dev libfreetype6-dev \
    libmagic1 fonts-dejavu-core ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN git init /opt/odoo && git -C /opt/odoo remote add origin https://github.com/odoo/odoo.git \
    && git -C /opt/odoo fetch --depth 1 origin ${ODOO_REF} \
    && git -C /opt/odoo checkout --detach FETCH_HEAD \
    && pip install --no-cache-dir -r /opt/odoo/requirements.txt
RUN useradd --create-home --uid 1000 odoo && mkdir -p /var/lib/odoo /workspace && chown -R odoo:odoo /var/lib/odoo /workspace
WORKDIR /workspace
COPY --chown=odoo:odoo addons /workspace/addons
COPY --chown=odoo:odoo scripts /workspace/scripts
USER odoo
EXPOSE 8069
ENTRYPOINT ["python", "/workspace/scripts/container_entrypoint.py"]
CMD ["run"]
