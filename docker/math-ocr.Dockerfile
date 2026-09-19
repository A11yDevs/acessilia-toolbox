# RapidLaTeXOCR lightweight math recognition sidecar
# Designed for cropped formula image chips. Ultra-fast CPU inference (< 0.5s), < 150MB RAM.

FROM python:3.11-slim

# System dependencies for OpenCV & image processing
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install lightweight dependencies
RUN pip install --no-cache-dir \
    fastapi>=0.115 \
    uvicorn>=0.30 \
    python-multipart>=0.0.9 \
    Pillow>=10.0 \
    rapid_latex_ocr>=0.1.1 && \
    pip cache purge

# Create server script
RUN cat << 'EOF' > /app/server.py
import io
from fastapi import FastAPI, File, UploadFile, HTTPException
from PIL import Image
from rapid_latex_ocr import LatexOCR

app = FastAPI(title="rapid-latex-ocr", version="0.1.0")
model = None

@app.on_event("startup")
def load_model():
    global model
    model = LatexOCR()

@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}

@app.get("/version")
def version():
    return {
        "version": "0.1.0",
        "rapid_latex_ocr": "0.1.1",
    }

@app.post("/predict")
async def predict(file: UploadFile = File(...)):
    global model
    if model is None:
        model = LatexOCR()
    try:
        contents = await file.read()
        image = Image.open(io.BytesIO(contents)).convert("RGB")
        res, elapse = model(image)
        return {
            "latex": res,
            "elapse": elapse,
            "confidence": 1.0,
            "bbox": [0, 0, image.width, image.height]
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
EOF

EXPOSE 5004

HEALTHCHECK --interval=15s --timeout=5s --retries=10 --start-period=10s \
    CMD curl -fsS http://localhost:5004/health || exit 1

ENTRYPOINT ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5004"]
