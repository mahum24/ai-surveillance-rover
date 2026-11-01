"""python -m backend  ->  serve the API + dashboard on every network interface, port 8080."""
import socket

import uvicorn


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    print(f"\n  Dashboard:  http://localhost:8080   (phone on the same Wi-Fi: http://{lan_ip()}:8080)\n")
    uvicorn.run("backend.app:app", host="0.0.0.0", port=8080, log_level="warning")
