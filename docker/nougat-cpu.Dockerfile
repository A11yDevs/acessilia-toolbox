# Nougat visual document transcription image (CPU inference).
#
# Provides an HTTP API serving Facebook Research's Nougat model
# for scientific PDF transcription (LaTeX math, tables, sections).
#
# Build:
#   docker build -t nougat:latest -f docker/nougat-cpu.Dockerfile .
#
# Run:
#   docker run -p 5004:5004 \
#     -v nougat-torch-models:/root/.cache/torch/hub \
#     -v nougat-models:/root/.cache/nougat \
#     nougat:latest

FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        libgl1 \
        libglib2.0-0 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir \
        "albumentations<1.4.0" \
        "transformers<4.38" \
        "pypdfium2<5.0" \
        "nougat-ocr>=0.1.17" \
        fastapi \
        "uvicorn[standard]" \
        python-multipart && \
    python -m nltk.downloader words && \
    pip cache purge

RUN mkdir -p /root/.cache/torch/hub /root/.cache/nougat
VOLUME /root/.cache/torch/hub
VOLUME /root/.cache/nougat

# FastAPI server running real Nougat model inference
COPY <<'EOF' /app/server.py
import importlib.metadata
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Any

import torch
from fastapi import FastAPI, File, HTTPException, Response, UploadFile, status
from nougat import NougatModel
from nougat.postprocessing import markdown_compatible
from nougat.utils.checkpoint import get_checkpoint
from nougat.utils.dataset import LazyDataset
from torch.utils.data import DataLoader

LOG = logging.getLogger("nougat_serve")
logging.basicConfig(level=logging.INFO)

state: dict[str, Any] = {
    "model": None,
    "model_loaded": False,
    "checkpoint": None,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    checkpoint_tag = get_checkpoint()
    state["checkpoint"] = str(checkpoint_tag)
    LOG.info("Loading Nougat model checkpoint: %s", checkpoint_tag)
    try:
        model = NougatModel.from_pretrained(checkpoint_tag)
        model = model.eval()
        if torch.cuda.is_available():
            model = model.to("cuda")
        state["model"] = model
        state["model_loaded"] = True
        LOG.info("Nougat model loaded successfully.")
    except Exception as exc:
        LOG.error("Failed to load Nougat model checkpoint: %s", exc)
        state["model_loaded"] = False
    yield
    state.clear()


app = FastAPI(title="Nougat Serve", lifespan=lifespan)


@app.get("/health")
def health(response: Response):
    is_ready = bool(state.get("model_loaded"))
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "degraded",
            "healthy": False,
            "detail": "Model is not loaded or initialization failed",
        }
    return {
        "status": "ok",
        "healthy": True,
        "checkpoint": state.get("checkpoint"),
    }


@app.get("/version")
def version():
    try:
        pkg_version = importlib.metadata.version("nougat-ocr")
    except Exception:
        pkg_version = "0.1.17"
    return {
        "version": pkg_version,
        "service": "nougat-serve",
        "torch_version": torch.__version__,
    }


@app.post("/predict")
async def predict(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")

    model = state.get("model")
    if model is None or not state.get("model_loaded"):
        raise HTTPException(
            status_code=503, detail="Nougat model is not ready or failed to load"
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(content)
            temp_path = Path(tmp.name)

        prepare_fn = partial(model.encoder.prepare_input, random_padding=False)
        dataset = LazyDataset(temp_path, prepare=prepare_fn)
        dataloader = DataLoader(
            dataset,
            batch_size=1,
            shuffle=False,
            collate_fn=LazyDataset.ignore_none_collate,
        )

        pages_out: list[dict[str, Any]] = []
        page_idx = 1

        for sample, is_last_page in dataloader:
            if sample is None:
                continue
            model_output = model.inference(image_tensors=sample)
            for prediction in model_output.get("predictions", []):
                formatted_text = markdown_compatible(prediction)
                pages_out.append({
                    "page_number": page_idx,
                    "text": formatted_text,
                })
                page_idx += 1

        full_text = "\n\n".join(p["text"] for p in pages_out)
        return {
            "num_pages": len(pages_out),
            "pages": pages_out,
            "text": full_text,
        }
    except Exception as exc:
        LOG.exception("Nougat inference failed: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Inference execution failed: {exc}"
        ) from exc
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
EOF

EXPOSE 5004

HEALTHCHECK --interval=20s --timeout=5s --retries=15 --start-period=60s \
    CMD curl -fsS http://localhost:5004/health || exit 1

ENTRYPOINT ["uvicorn", "server:app", "--app-dir", "/app", "--host", "0.0.0.0", "--port", "5004"]
