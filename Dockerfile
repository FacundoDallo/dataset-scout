# Dataset Scout in a container: same Python, same libraries, on any machine.
#
# Build:   docker build -t dataset-scout .
# Replay a recorded run (no internet needed; snapshots/ comes from an earlier live run):
#   docker run --rm -v "${PWD}/outputs:/app/outputs" -v "${PWD}/snapshots:/app/snapshots" \
#     dataset-scout run config/microglia_aging.yaml --mode replay
# Live run (pass your NCBI e-mail):
#   docker run --rm -e NCBI_EMAIL=you@example.com -v "${PWD}/outputs:/app/outputs" \
#     -v "${PWD}/snapshots:/app/snapshots" dataset-scout run config/microglia_aging.yaml

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first, so this layer is cached when only the code changes.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install .

COPY config ./config

# Run as an unprivileged user.
RUN useradd --create-home scout && chown -R scout /app
USER scout

ENTRYPOINT ["scout"]
CMD ["--help"]
