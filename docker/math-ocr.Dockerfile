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
    requests \
    pyyaml \
    rapid_latex_ocr>=0.1.1 && \
    pip cache purge

# Create server script
RUN cat << 'EOF' > /app/server.py
import io
from fastapi import FastAPI, File, UploadFile, HTTPException
import numpy as np
from PIL import Image
from rapid_latex_ocr import LaTeXOCR

# Monkey patch RapidLaTeXOCR 1D argmax bug on certain image dimensions
_orig_loop = LaTeXOCR.loop_image_resizer

def _patched_loop(self, img):
    pillow_img = Image.fromarray(img)
    pad_img = self.pre_pro.pad(pillow_img)
    input_image = self.pre_pro.minmax_size(pad_img).convert("RGB")
    r, w, h = 1, input_image.size[0], input_image.size[1]
    for _ in range(10):
        h = int(h * r)
        final_img, pad_img = self.pre_process(input_image, r, w, h)
        resizer_res = self.image_resizer([final_img.astype(np.float32)])[0]
        argmax_idx = int(np.squeeze(np.argmax(resizer_res, axis=-1)))
        w = (argmax_idx + 1) * 32
        if w == pad_img.size[0]:
            break
        r = w / pad_img.size[0]
    return final_img

LaTeXOCR.loop_image_resizer = _patched_loop

app = FastAPI(title="rapid-latex-ocr", version="0.1.0")
model = None

@app.on_event("startup")
def load_model():
    global model
    model = LaTeXOCR()

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
        model = LaTeXOCR()
    try:
        contents = await file.read()
        raw_img = Image.open(io.BytesIO(contents))
        
        # Robust preprocessing: compose transparency onto white background
        if raw_img.mode in ("RGBA", "LA") or (raw_img.mode == "P" and "transparency" in raw_img.info):
            raw_rgba = raw_img.convert("RGBA")
            bg = Image.new("RGBA", raw_rgba.size, (255, 255, 255, 255))
            alpha_comp = Image.alpha_composite(bg, raw_rgba).convert("RGB")
        else:
            alpha_comp = raw_img.convert("RGB")
            
        # Add 20px padding (respiro) to prevent glyphs abutting image borders
        padded = Image.new("RGB", (alpha_comp.width + 40, alpha_comp.height + 40), (255, 255, 255))
        padded.paste(alpha_comp, (20, 20))
        
        # Pass numpy array directly to model
        img_np = np.array(padded)
        res, elapse = model(img_np)
        return {
            "latex": res,
            "elapse": elapse,
            "confidence": 1.0,
            "bbox": [0, 0, raw_img.width, raw_img.height]
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
EOF

EXPOSE 5004

HEALTHCHECK --interval=15s --timeout=5s --retries=10 --start-period=10s \
    CMD curl -fsS http://localhost:5004/health || exit 1

ENTRYPOINT ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5004"]
