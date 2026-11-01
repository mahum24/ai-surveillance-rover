"""Download the two test videos used in the README into tests/videos/.

    python tools/get_test_videos.py

walking.avi - OpenCV's pedestrian sample video (normal walking, for loitering / restricted-zone tests)
fight.avi   - one RWF-2000 fight clip (research use only), streamed out of the dataset archive
              on Hugging Face, so only ~100 MB is read instead of the full 12 GB
"""
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "videos"

WALKING_URL = "https://github.com/opencv/opencv/raw/4.x/samples/data/vtest.avi"
RWF_URL = "https://huggingface.co/datasets/DanJoshua/RWF-2000/resolve/main/RWF-2000.tar.gz"
FIGHT_MEMBER = "RWF-2000/train/Fight/0_DzLlklZa0_3.avi"


def get_walking(dst):
    print(f"Downloading walking video -> {dst}")
    urllib.request.urlretrieve(WALKING_URL, dst)


def get_fight(dst):
    print(f"Streaming fight clip from RWF-2000 (this reads ~100 MB, please wait) -> {dst}")
    with urllib.request.urlopen(RWF_URL) as resp, tarfile.open(fileobj=resp, mode="r|gz") as tar:
        for m in tar:
            if m.name == FIGHT_MEMBER:
                with tar.extractfile(m) as src:
                    dst.write_bytes(src.read())
                return
    raise SystemExit(f"{FIGHT_MEMBER} not found in the archive")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fetch in (("walking.avi", get_walking), ("fight.avi", get_fight)):
        dst = OUT / name
        if dst.exists():
            print(f"{dst} already exists, skipping")
            continue
        fetch(dst)
    print("Done.")


if __name__ == "__main__":
    main()
