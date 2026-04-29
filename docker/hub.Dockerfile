FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (separate layer for better cache)
COPY hub/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy hub source code
COPY hub/ .

# The React build is copied here by the deploy script (frontend/dist → hub/static)
# If the static directory is absent, the hub serves API only (fine for dev).

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
