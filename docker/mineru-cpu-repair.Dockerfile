# Repair layer for an already-built MinerU CPU image.
#
# Use this when the full CPU image exists but rebuilding it would require more
# temporary disk space than is available. The canonical from-scratch build is
# still docker/mineru-cpu.Dockerfile.

ARG MINERU_BASE_IMAGE=mineru:cpu-base
FROM ${MINERU_BASE_IMAGE}

USER root
COPY docker/patch-mineru-warp.py /tmp/patch-mineru-warp.py
# Brings docvortex back inside the range MinerU 4.0.12 supports, so an image
# built before the constraints existed is repaired without a full rebuild (#165).
COPY docker/mineru-constraints.txt /tmp/mineru-constraints.txt
RUN pip install --no-cache-dir -c /tmp/mineru-constraints.txt six docvortex && \
    python3 /tmp/patch-mineru-warp.py && rm /tmp/patch-mineru-warp.py && \
    mkdir -p /home/mineru/.cache/modelscope && \
    chown -R mineru:mineru /home/mineru/.cache

USER mineru
