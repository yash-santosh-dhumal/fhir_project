FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH=/home/user/app

# Install minimal OS utilities for network healthchecks and certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces requirement: non-root user with UID 1000
RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

WORKDIR $HOME/app

# Install Python requirements with hash verification for reliability & layer caching
COPY --chown=user:user requirements.txt requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --no-deps --ignore-requires-python --require-hashes -r requirements.txt

# Copy all application code, LOINC knowledge bases, and demo files
COPY --chown=user:user . $HOME/app

# Ensure executable permissions on startup script
RUN chmod +x $HOME/app/start.sh

# Switch to non-root user
USER user

# Expose ports (Render sets $PORT, default 7860/10000)
EXPOSE 7860 10000

# Launch both Toolkit backend and Demo Web Portal
CMD ["./start.sh"]