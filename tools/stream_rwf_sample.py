"""Stream the first N videos from a folder of the RWF-2000 tar.gz on Hugging Face without downloading all 12 GB.

    python tools/stream_rwf_sample.py --prefix RWF-2000/train/Fight/ --n 30 --out data/rwf_sample
"""
import argparse
import tarfile
import urllib.request
from pathlib import Path

URL = "https://huggingface.co/datasets/DanJoshua/RWF-2000/resolve/main/RWF-2000.tar.gz"

ap = argparse.ArgumentParser()
ap.add_argument("--prefix", default="RWF-2000/train/Fight/")
ap.add_argument("--n", type=int, default=30)
ap.add_argument("--out", default="data/rwf_sample")
args = ap.parse_args()

out = Path(args.out) / Path(args.prefix).name
out.mkdir(parents=True, exist_ok=True)
got = 0
with urllib.request.urlopen(URL) as resp, tarfile.open(fileobj=resp, mode="r|gz") as tar:
    for m in tar:
        if not (m.isfile() and m.name.startswith(args.prefix) and m.name.endswith(".avi")):
            continue
        name = Path(m.name).name  # never trust paths inside the archive
        with tar.extractfile(m) as src, open(out / name, "wb") as dst:
            dst.write(src.read())
        got += 1
        print(f"{got}/{args.n} {name}", flush=True)
        if got >= args.n:
            break
