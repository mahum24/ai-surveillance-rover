"""Laptop-side client for the Pi rover agent (rover/rover_agent.py)."""
import requests


class RoverClient:
    def __init__(self, url, token, timeout=2.0):
        self.url = url.rstrip("/")
        self.headers = {"X-Token": token or ""}
        self.timeout = timeout
        self.online = False

    @property
    def stream_url(self):
        return f"{self.url}/stream.mjpg"

    def _request(self, method, path, body=None, timeout=None):
        try:
            r = requests.request(method, self.url + path, json=body, headers=self.headers,
                                 timeout=timeout or self.timeout)
            r.raise_for_status()
            self.online = True
            return r.json()
        except (requests.RequestException, ValueError):
            self.online = False
            return None

    def status(self):
        return self._request("GET", "/status")

    def drive(self, cmd, speed=150, ms=500):
        return self._request("POST", "/drive", {"cmd": cmd, "speed": speed, "ms": ms})

    def stop(self):
        return self.drive("stop")

    def scan(self):
        r = self._request("POST", "/scan", timeout=5.0)
        return r.get("scan") if r else None
