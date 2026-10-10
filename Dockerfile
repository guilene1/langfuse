# One image, used by both the ingestion run and the page.
FROM python:3.12.15-slim

WORKDIR /app

# constraints.txt pins every package, including the ones pulled in indirectly
COPY requirements.txt requirements-local.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-local.txt -c constraints.txt

# `streamlit run app/main.py` puts the SCRIPT's folder on sys.path, not the working
# directory, so "from app import ..." fails without this.
ENV PYTHONPATH=/app

# Download the embedding model at build time, so the first question is not slow
ENV FASTEMBED_CACHE_PATH=/opt/models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

COPY app/ ./app/
COPY data/ ./data/

CMD ["streamlit", "run", "app/main.py", "--server.address=0.0.0.0", "--server.port=8501"]
