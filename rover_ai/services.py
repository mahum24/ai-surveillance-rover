"""Network helpers used by run.py:

- LatestFrameReader: reads a network camera stream in the background and keeps only the newest
  frame, so a slow detector never falls behind the live picture.
- LiveView: serves the annotated detection view as MJPEG (for the dashboard / phone).
- BackendLink: sends alerts and status to the FastAPI backend and picks up commands from it.
"""
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import requests


class LatestFrameReader:
    def __init__(self, url):
        self.url = url
        self.frame = None
        self.seq = 0
        self.cond = threading.Condition()
        self.alive = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while self.alive:
            cap = cv2.VideoCapture(self.url)
            if not cap.isOpened():
                print(f"[stream] cannot open {self.url}, retrying...")
                time.sleep(2.0)
                continue
            while self.alive:
                ok, frame = cap.read()
                if not ok:
                    print("[stream] lost connection, reconnecting...")
                    break
                with self.cond:
                    self.frame, self.seq = frame, self.seq + 1
                    self.cond.notify_all()
            cap.release()
            time.sleep(1.0)

    def read(self, last_seq, timeout=5.0):
        """Waits for a frame newer than last_seq. Returns (frame, seq) or (None, last_seq)."""
        with self.cond:
            self.cond.wait_for(lambda: self.seq != last_seq, timeout)
            if self.seq == last_seq:
                return None, last_seq
            return self.frame, self.seq


class LiveView:
    def __init__(self, port, quality=70):
        self.quality = quality
        self.jpeg = None
        self.cond = threading.Condition()
        view = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                if self.path.startswith("/snapshot.jpg"):
                    jpg = view.wait()
                    self.send_header("Content-Type", "image/jpeg")
                    self.end_headers()
                    self.wfile.write(jpg or b"")
                    return
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
                self.end_headers()
                last = None
                try:
                    while True:
                        jpg = view.wait(last)
                        if jpg is None or jpg is last:
                            continue
                        last = jpg
                        self.wfile.write(b"--FRAME\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()

    def publish(self, frame):
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        if ok:
            with self.cond:
                self.jpeg = jpg.tobytes()
                self.cond.notify_all()

    def wait(self, last=None, timeout=2.0):
        with self.cond:
            self.cond.wait_for(lambda: self.jpeg is not None and self.jpeg is not last, timeout)
            return self.jpeg


class BackendLink:
    """Fire-and-forget calls to the backend; detection never waits on the network."""

    def __init__(self, url, device_token):
        self.url = url.rstrip("/")
        self.headers = {"X-Device-Token": device_token}
        self.outbox = queue.Queue(maxsize=200)
        self.commands = queue.Queue()
        self.status = {}
        self.online = False
        threading.Thread(target=self._sender, daemon=True).start()
        threading.Thread(target=self._status_loop, daemon=True).start()

    def post_alert(self, record):
        try:
            self.outbox.put_nowait(record)
        except queue.Full:
            pass

    def _call(self, method, path, body=None):
        try:
            r = requests.request(method, self.url + path, json=body, headers=self.headers, timeout=3)
            r.raise_for_status()
            self.online = True
            return r.json()
        except (requests.RequestException, ValueError):
            self.online = False
            return None

    def _sender(self):
        while True:
            record = self.outbox.get()
            while self._call("POST", "/api/device/alerts", record) is None:
                time.sleep(3.0)       # backend down: keep the alert and retry

    def _status_loop(self):
        while True:
            self._call("POST", "/api/device/status", self.status)
            for cmd in self._call("GET", "/api/device/commands") or []:
                self.commands.put(cmd)
            time.sleep(1.0)
