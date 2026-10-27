# Copyright (c) 2026 Alex Wang
# veFaaS 自定义运行时镜像：Python 3.11 + FFmpeg/FFprobe + 应用依赖。
FROM node:22-bookworm-slim AS node-runtime
WORKDIR /opt/video-tools
COPY runtime/package*.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM python:3.11-slim-bookworm
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY --from=node-runtime /opt/video-tools /opt/video-tools

# 系统依赖：
# - ffmpeg 提供 ffmpeg + ffprobe
# - tini 作为轻量 init（回收僵尸进程）
# - Noto CJK/emoji 字体用于云端连环画 PDF 的多语言文本渲染
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        chromium \
        fontconfig \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f \
    && ffmpeg -version \
    && ffprobe -version

WORKDIR /app

# 先装依赖以利用镜像层缓存。
COPY requirements.txt requirements-video-tools.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt -r requirements-video-tools.txt

# 拷贝应用代码（.dockerignore 已排除 .env / .venv / demo 大文件 / tests / docs 等）。
COPY app ./app
COPY static ./static
COPY config.yaml ./
COPY run.sh ./
RUN chmod +x run.sh

# veFaaS native/v1：默认监听端口 8000，可由 _FAAS_RUNTIME_PORT 覆盖。
ENV PORT=8000
ENV VIDEO_SKILLS_ROOT=/app/app/video_skills \
    VIDEO_TOOLS_RUNTIME_DIR=/opt/video-tools \
    VIDEO_OUTPUT_DIR=/tmp/seedance-outputs \
    REMOTION_BROWSER_EXECUTABLE=/usr/bin/chromium \
    OMP_NUM_THREADS=2 \
    MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONUNBUFFERED=1 \
    HYPERFRAMES_SKIP_SKILLS=1 \
    HYPERFRAMES_NO_TELEMETRY=1 \
    DO_NOT_TRACK=1
EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["./run.sh"]
