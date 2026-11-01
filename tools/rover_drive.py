"""Drive the rover once from the laptop - used to calibrate turns and test movement.

    python tools/rover_drive.py right 700          # spin right for 700 ms (is it ~90 degrees?)
    python tools/rover_drive.py forward 1200
    python tools/rover_drive.py scan               # sonar sweep left/centre/right
    python tools/rover_drive.py status

Uses rover.url and rover.token from config.yaml (or --rover / --token).
"""
import argparse
import json
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from rover_ai.rover_client import RoverClient  # noqa: E402

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("action", choices=["forward", "back", "left", "right", "stop", "scan", "status"])
ap.add_argument("ms", nargs="?", type=int, default=500)
ap.add_argument("--speed", type=int)
ap.add_argument("--rover")
ap.add_argument("--token")
args = ap.parse_args()

cfg = yaml.safe_load((ROOT / "config.yaml").read_text())["rover"]
url = args.rover or cfg["url"]
if not url:
    sys.exit("Set rover.url in config.yaml or pass --rover http://<pi-ip>:8000")
rover = RoverClient(url, args.token or cfg["token"])

if args.action == "status":
    result = rover.status()
elif args.action == "scan":
    result = rover.scan()
else:
    result = rover.drive(args.action, args.speed or cfg["speed"], args.ms)
    time.sleep(args.ms / 1000 + 0.3)
if result is None:
    sys.exit(f"No answer from the rover at {url}. Is rover_agent.py running and is the token right?")
print(json.dumps(result, indent=2))
