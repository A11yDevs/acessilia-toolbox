from pathlib import Path

path = Path("/usr/local/lib/python3.12/site-packages/mineru/model/ocr/seal_det_warp.py")
source = path.read_text()
old = '{0: "nearest", 1: "linear", 2: "cubic", 3: "area", 4: "lanczos4"}'
new = '{0: "nearest", 1: "linear", 2: "cubic", 3: "linear", 4: "cubic"}'
if source.count(old) != 1:
    raise SystemExit("Unexpected MinerU interpolation mapping; refusing an unverified patch")
path.write_text(source.replace(old, new))
