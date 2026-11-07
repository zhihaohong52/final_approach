# Stage 1: build the React frontend (optional; skipped if frontend/package.json is absent)
FROM node:20-slim AS web
COPY . /src
RUN if [ -f /src/frontend/package.json ]; then \
      cd /src/frontend && if [ -f package-lock.json ]; then npm ci; else npm install; fi && npm run build && cp -r dist /dist; \
    else \
      mkdir -p /dist; \
    fi

# Optional native stage: Rust taxi-loop extension (abi3), copied into the image below. A failed build leaves /out
# empty and the image runs the pure-Python loop (the server logs which loop is active at startup).
FROM rust:1-slim AS native
RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
COPY native/taxi /src/native/taxi
WORKDIR /src/native/taxi
RUN mkdir -p /out && \
    (cargo build --release && cp target/release/lib_taxi_native.so /out/_taxi_native.abi3.so \
     || echo "native taxi loop not built: the image runs the pure-Python loop")

# Stage 2: Python API + built frontend
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY --from=native /out/ /app/backend/wave/
COPY data/ ./data/
COPY eval/ ./eval/
COPY web/ ./web/
COPY --from=web /dist /app/frontend/dist
RUN useradd -m -u 1000 user && chown -R user:user /app
USER user
ENV PORT=7860
ENV ATM_REQUIRE_REAL=1
# 512 MB instance: 8 waves in the per-wave caches (~20-30 MB each at a hub), and 2 glibc malloc arenas (threads)
ENV ATM_WAVE_CACHE=8
ENV MALLOC_ARENA_MAX=2
EXPOSE 7860
CMD ["sh", "-c", "uvicorn backend.server:app --host 0.0.0.0 --port ${PORT:-7860}"]
