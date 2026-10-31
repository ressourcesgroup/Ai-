# GODMODE online server: the same HTTP server the plugin uses locally, packaged for a host.
# Build:  docker build -t godmode .
# Run:    docker run -p 8765:8765 -e GODMODE_TOKEN=change-me godmode
# The server refuses to start on 0.0.0.0 without GODMODE_TOKEN.
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch>=2.1" \
 && pip install --no-cache-dir "numpy>=1.24"

COPY pyproject.toml README.md LICENSE ./
COPY godmode ./godmode
COPY data ./data
# The trained checkpoint (instruction-tuned if present, else the pretrained one). Build fails if neither exists.
COPY runs/sft-scripts/best.pt ./runs/sft-scripts/best.pt

ENV GODMODE_HOST=0.0.0.0 \
    PORT=8765 \
    GODMODE_CKPT=/app/runs/sft-scripts/best.pt \
    PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["python", "-m", "godmode.server"]
