"""Raspberry Pi rover agent: camera stream + drive commands + status, over Wi-Fi.

On the Pi:
    python3 rover_agent.py --token MYSECRET

Endpoints (port 8000):
    GET  /               simple control page (video + buttons) for testing in a browser
    GET  /stream.mjpg    live camera (MJPEG) - the laptop's detector reads this
    GET  /snapshot.jpg   one frame
    GET  /status         JSON: distance, obstacle, moving, GPS, last scan
    POST /drive          {"cmd": "forward|back|left|right|stop", "speed": 0-255, "ms": 100-3000}
    POST /scan           sweep the sonar left/centre/right, returns distances in cm
    POST /look           {"angle": 0-180} point the sonar servo

Commands need the token (header "X-Token" or "?token=...").

Testing without a Pi:  python rover/rover_agent.py --fake --camera file:tests/videos/walking.avi
"""
import argparse
import io
import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

I2C_ADDRESS = 0x08
STATUS_LEN = 20
DRIVE_CODES = {"forward": "F", "back": "B", "left": "L", "right": "R", "stop": "S"}


# ============================== Arduino link ==============================
class ArduinoLink:
    """Talks to the Arduino over I2C: 4-byte commands, 20-byte status with checksums."""

    def __init__(self, bus=1):
        from smbus2 import SMBus, i2c_msg
        self.bus = SMBus(bus)
        self.i2c_msg = i2c_msg
        self.lock = threading.Lock()

    def send(self, cmd, a=0, b=0):
        msg = [ord(cmd), a & 0xFF, b & 0xFF]
        chk = 0xA5 ^ msg[0] ^ msg[1] ^ msg[2]
        for _ in range(3):
            try:
                with self.lock:
                    self.bus.i2c_rdwr(self.i2c_msg.write(I2C_ADDRESS, msg + [chk]))
                return True
            except OSError:
                time.sleep(0.02)
        return False

    def read_status(self):
        for _ in range(3):
            try:
                with self.lock:
                    rd = self.i2c_msg.read(I2C_ADDRESS, STATUS_LEN)
                    self.bus.i2c_rdwr(rd)
                s = list(rd)
            except OSError:
                time.sleep(0.02)
                continue
            chk = 0xA5
            for v in s[:-1]:
                chk ^= v
            if s[0] == 0xA5 and chk == s[-1]:
                return _decode_status(s)
            time.sleep(0.02)
        return None


def _decode_status(s):
    def i32(off):
        v = s[off] | s[off + 1] << 8 | s[off + 2] << 16 | s[off + 3] << 24
        return v - (1 << 32) if v & 0x80000000 else v

    def scan_cm(v):
        return None if v == 255 else v * 2

    fix = bool(s[3] & 4)
    return {
        "distance_cm": (s[1] | s[2] << 8) or None,
        "moving": bool(s[3] & 1),
        "obstacle": bool(s[3] & 2),
        "gps_fix": fix,
        "lat": i32(8) / 1e6 if fix else None,
        "lon": i32(12) / 1e6 if fix else None,
        "satellites": s[16],
        "last_cmd": chr(s[4]) if 32 <= s[4] < 127 else "?",
        "scan": {"left": scan_cm(s[5]), "center": scan_cm(s[6]), "right": scan_cm(s[7])},
        "scan_seq": s[17],
    }


class FakeLink:
    """Pretends to be the Arduino, for testing on a laptop."""

    def __init__(self):
        self.state = {"distance_cm": 120, "moving": False, "obstacle": False, "gps_fix": False, "lat": None,
                      "lon": None, "satellites": 0, "last_cmd": "S",
                      "scan": {"left": 150, "center": 120, "right": 90}, "scan_seq": 0}
        self.until = 0
        self.log = []

    def send(self, cmd, a=0, b=0):
        self.log.append((round(time.time(), 2), cmd, a, b))
        print(f"[fake arduino] {cmd} a={a} b={b}", flush=True)
        if cmd in "FBLR":
            self.state.update(moving=True, last_cmd=cmd)
            self.until = time.time() + b / 10
        elif cmd == "S":
            self.state.update(moving=False, last_cmd="S")
        elif cmd == "C":
            self.state["scan_seq"] = (self.state["scan_seq"] + 1) % 256
        return True

    def read_status(self):
        if self.state["moving"] and time.time() > self.until:
            self.state["moving"] = False
        return dict(self.state)


# ============================== camera ==============================
class Camera:
    """Keeps the latest JPEG frame; sources: picamera2, an OpenCV device, or a video file (testing)."""

    def __init__(self, source, width, height, fps, quality=80):
        self.frame = None
        self.cond = threading.Condition()
        self.width, self.height, self.fps, self.quality = width, height, fps, quality
        if source == "auto":
            try:
                import picamera2  # noqa: F401
                source = "picamera2"
            except ImportError:
                source = "opencv:0"
        self.source = source
        target = self._run_picamera2 if source == "picamera2" else self._run_opencv
        threading.Thread(target=target, daemon=True).start()

    def _publish(self, jpeg):
        with self.cond:
            self.frame = jpeg
            self.cond.notify_all()

    def wait_frame(self, last=None, timeout=2.0):
        with self.cond:
            self.cond.wait_for(lambda: self.frame is not None and self.frame is not last, timeout)
            return self.frame

    def _run_picamera2(self):
        from picamera2 import Picamera2
        from picamera2.encoders import MJPEGEncoder
        from picamera2.outputs import FileOutput

        cam = self

        class Output(io.BufferedIOBase):
            def write(self, buf):
                cam._publish(bytes(buf))

        picam = Picamera2()
        picam.configure(picam.create_video_configuration(
            main={"size": (self.width, self.height)}, controls={"FrameRate": self.fps}))
        picam.start_recording(MJPEGEncoder(), FileOutput(Output()))

    def _run_opencv(self):
        import cv2
        loop_file = self.source.startswith("file:")
        src = self.source[5:] if loop_file else int(self.source.split(":")[1])
        cap = cv2.VideoCapture(src)
        if not loop_file:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        delay = 1.0 / (cap.get(cv2.CAP_PROP_FPS) or self.fps) if loop_file else 0
        while True:
            ok, img = cap.read()
            if not ok:
                if loop_file:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                time.sleep(0.5)
                continue
            ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
            if ok:
                self._publish(jpg.tobytes())
            if delay:
                time.sleep(delay)


# ============================== rover state ==============================
class Rover:
    def __init__(self, link, poll_s=0.2):
        self.link = link
        self.status = {}
        self.last_ok = 0.0
        threading.Thread(target=self._poll, args=(poll_s,), daemon=True).start()

    def _poll(self, poll_s):
        while True:
            st = self.link.read_status()
            if st is not None:
                self.status, self.last_ok = st, time.time()
            time.sleep(poll_s)

    def drive(self, cmd, speed=160, ms=500):
        code = DRIVE_CODES[cmd]
        if code == "S":
            return self.link.send("S")
        ms = max(100, min(int(ms), 3000))
        return self.link.send(code, max(0, min(int(speed), 255)), round(ms / 100))

    def scan(self, timeout=3.0):
        seq = self.status.get("scan_seq")
        self.link.send("C")
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(0.1)
            if self.status.get("scan_seq") != seq:
                break
        return self.status.get("scan")

    def look(self, angle):
        return self.link.send("V", max(0, min(int(angle), 180)))

    def snapshot(self):
        return dict(self.status, arduino_ok=time.time() - self.last_ok < 2.0)


# ============================== HTTP ==============================
PAGE = """<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1">
<title>Rover</title><style>body{font-family:sans-serif;margin:12px;background:#111;color:#eee}
img{width:100%;max-width:640px;border-radius:8px}button{font-size:20px;padding:14px 18px;margin:4px;border-radius:8px}
pre{background:#222;padding:8px;border-radius:8px}</style></head><body>
<h3>Rover control</h3><img src="/stream.mjpg"><div>
<button onclick="d('forward')">&#9650;</button><button onclick="d('left')">&#9664;</button>
<button onclick="d('stop')">&#9632;</button><button onclick="d('right')">&#9654;</button>
<button onclick="d('back')">&#9660;</button><button onclick="scan()">Scan</button></div><pre id=s></pre>
<script>const t=new URLSearchParams(location.search).get('token')||'';
function d(c){fetch('/drive?token='+t,{method:'POST',body:JSON.stringify({cmd:c,ms:500})})}
function scan(){fetch('/scan?token='+t,{method:'POST'})}
setInterval(async()=>{s.textContent=JSON.stringify(await (await fetch('/status')).json(),null,1)},700)</script>
</body></html>"""


def make_handler(rover, camera, token):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def _json(self, obj, code=HTTPStatus.OK):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self):
            q = parse_qs(urlparse(self.path).query)
            given = self.headers.get("X-Token") or (q.get("token") or [""])[0]
            return not token or given == token

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return {}

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                body = PAGE.encode()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/status":
                self._json(rover.snapshot())
            elif path == "/snapshot.jpg":
                jpg = camera.wait_frame()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpg or b"")))
                self.end_headers()
                self.wfile.write(jpg or b"")
            elif path == "/stream.mjpg":
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                last = None
                try:
                    while True:
                        jpg = camera.wait_frame(last)
                        if jpg is None or jpg is last:
                            continue
                        last = jpg
                        self.wfile.write(b"--FRAME\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            path = urlparse(self.path).path
            if not self._authorised():
                return self._json({"error": "bad token"}, HTTPStatus.UNAUTHORIZED)
            body = self._body()
            if path == "/drive":
                cmd = body.get("cmd", "stop")
                if cmd not in DRIVE_CODES:
                    return self._json({"error": f"cmd must be one of {list(DRIVE_CODES)}"}, HTTPStatus.BAD_REQUEST)
                ok = rover.drive(cmd, body.get("speed", 160), body.get("ms", 500))
                self._json({"ok": ok})
            elif path == "/scan":
                self._json({"scan": rover.scan()})
            elif path == "/look":
                self._json({"ok": rover.look(body.get("angle", 90))})
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--token", default="", help="shared secret the laptop must send with commands")
    ap.add_argument("--camera", default="auto", help="auto | picamera2 | opencv:0 | file:video.mp4")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--fake", action="store_true", help="no Arduino: simulate it (for testing)")
    args = ap.parse_args()

    link = FakeLink() if args.fake else ArduinoLink()
    rover = Rover(link)
    camera = Camera(args.camera, args.width, args.height, args.fps)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(rover, camera, args.token))
    print(f"Rover agent on port {args.port} (camera: {camera.source}, arduino: {'FAKE' if args.fake else 'I2C'})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
