FROM python:3.11-slim

WORKDIR /app

# Installation de curl pour les tests de sante
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copie et installation des dependances
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie de tout le code source
COPY . .

# Port expose pour FastAPI
EXPOSE 8000

# Commande par defaut
CMD ["python", "webhook.py"]
