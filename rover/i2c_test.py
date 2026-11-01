"""Step-by-step test of the Pi <-> Arduino link. Run on the Pi with the rover's wheels OFF the ground.

    python3 i2c_test.py
"""
import time

from rover_agent import ArduinoLink

link = ArduinoLink()

print("1. Reading status from the Arduino...")
st = link.read_status()
if st is None:
    raise SystemExit("   No answer. Check SDA/SCL/GND wiring, that I2C is enabled, and run: i2cdetect -y 1")
print(f"   OK: distance={st['distance_cm']} cm, gps_fix={st['gps_fix']}, satellites={st['satellites']}")

for cmd, label in (("F", "FORWARD"), ("B", "BACKWARD"), ("L", "SPIN LEFT"), ("R", "SPIN RIGHT")):
    input(f"\n2. Press Enter: wheels should turn {label} for 1 second ")
    link.send(cmd, 160, 10)
    time.sleep(1.3)

input("\n3. Press Enter: the sonar servo sweeps left, centre, right ")
seq = st["scan_seq"]
link.send("C")
for _ in range(30):
    time.sleep(0.1)
    st = link.read_status()
    if st and st["scan_seq"] != seq:
        break
print(f"   scan (cm): {st['scan']}")

print("\n4. Move your hand in front of the sonar (Ctrl+C to stop):")
try:
    while True:
        st = link.read_status()
        if st:
            print(f"   distance: {st['distance_cm']} cm   obstacle: {st['obstacle']}   ", end="\r")
        time.sleep(0.2)
except KeyboardInterrupt:
    link.send("S")
    print("\nDone.")
