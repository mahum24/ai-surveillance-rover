"""Checks that the Pi decodes the Arduino's 20-byte status exactly as the firmware packs it.

    python tests/test_protocol.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rover"))
from rover_agent import _decode_status  # noqa: E402


def arduino_pack(distance, moving, obstacle, fix, cmd, scan_2cm, lat, lng, sats, seq):
    """Python copy of updateStatus() in firmware/rover_arduino/rover_arduino.ino."""
    s = [0] * 20
    s[0] = 0xA5
    s[1], s[2] = distance & 0xFF, distance >> 8
    s[3] = (1 if moving else 0) | (2 if obstacle else 0) | (4 if fix else 0)
    s[4] = ord(cmd)
    s[5], s[6], s[7] = scan_2cm
    la = int(lat * 1e6) & 0xFFFFFFFF if fix else 0
    lo = int(lng * 1e6) & 0xFFFFFFFF if fix else 0
    for i in range(4):
        s[8 + i] = (la >> (8 * i)) & 0xFF
        s[12 + i] = (lo >> (8 * i)) & 0xFF
    s[16], s[17] = sats, seq
    c = 0xA5
    for v in s[:19]:
        c ^= v
    s[19] = c
    return s


def main():
    cases = [
        (dict(distance=87, moving=True, obstacle=False, fix=True, cmd="F", scan_2cm=(75, 43, 255),
              lat=31.582045, lng=74.329376, sats=7, seq=3),
         dict(distance_cm=87, moving=True, obstacle=False, gps_fix=True, last_cmd="F",
              scan={"left": 150, "center": 86, "right": None}, satellites=7, scan_seq=3)),
        (dict(distance=0, moving=False, obstacle=True, fix=True, cmd="S", scan_2cm=(10, 11, 12),
              lat=-33.868820, lng=-151.209290, sats=5, seq=255),
         dict(distance_cm=None, moving=False, obstacle=True, gps_fix=True, last_cmd="S",
              scan={"left": 20, "center": 22, "right": 24}, satellites=5, scan_seq=255)),
    ]
    ok = True
    for packed, expected in cases:
        got = _decode_status(arduino_pack(**packed))
        for k, v in expected.items():
            if got[k] != v:
                ok = False
                print(f"[FAIL] {k}: expected {v}, got {got[k]}")
        for k, src in (("lat", "lat"), ("lon", "lng")):
            if abs(got[k] - packed[src]) > 1e-6:
                ok = False
                print(f"[FAIL] {k}: expected {packed[src]}, got {got[k]}")
    print("[PASS] Arduino status decodes correctly" if ok else "protocol test FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
