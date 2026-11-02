# Repair layer for an already-built MinerU CPU image.
#
# Use this when the full CPU image exists but rebuilding it would require more
# temporary disk space than is available. The canonical from-scratch build is
# still docker/mineru-cpu.Dockerfile.

ARG MINERU_BASE_IMAGE=mineru:cpu-base
FROM ${MINERU_BASE_IMAGE}

USER root
RUN pip install --no-cache-dir six && \
    mkdir -p /home/mineru/.cache/modelscope && \
    chown -R mineru:mineru /home/mineru/.cache

USER mineru
