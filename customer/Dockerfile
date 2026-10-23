# The customer's world: MediTrack (legacy EHR + FHIR/SMART layer) and the mock payer, one image, APP_ROLE picks the role.
FROM node:22-slim AS ui
WORKDIR /src/meditrack/ui
COPY meditrack/ui/package*.json ./
RUN npm ci --silent
COPY meditrack/ui ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt && useradd --uid 10001 --create-home app
COPY meditrack meditrack
COPY mock_payer mock_payer
COPY scripts scripts
COPY synthea/prepared synthea/prepared
COPY --from=ui /src/meditrack/ui/dist meditrack/ui/dist
# the data folders are volumes (SMB shares in the hospital); the app runs as a non-root user
RUN mkdir -p /data/documents /data/import_hotfolder && chown -R app /data /app
ENV MEDITRACK_DOCS_DIR=/data/documents MEDITRACK_HOTFOLDER=/data/import_hotfolder
USER 10001
EXPOSE 8001 8003
ENTRYPOINT ["python", "-m", "scripts.serve"]
