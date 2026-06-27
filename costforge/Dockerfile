# Builds OneByJorah/CostForge directly from GitHub.
# No upstream Dockerfile exists yet, so this clones + installs + serves it.

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN git clone --depth 1 https://github.com/OneByJorah/CostForge.git .

# requirements.txt may or may not exist upstream — fall back to known deps
RUN if [ -f backend/requirements.txt ]; then \
        pip install --no-cache-dir -r backend/requirements.txt ; \
    else \
        pip install --no-cache-dir fastapi uvicorn[standard] httpx pydantic ; \
    fi

EXPOSE 8000
WORKDIR /app
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
