FROM python:3.13-slim
WORKDIR /app
ENV PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY poc/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app
COPY poc/ /app/poc/
USER 10001
EXPOSE 8501
CMD ["streamlit", "run", "poc/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.baseUrlPath=poc", "--server.headless=true"]
