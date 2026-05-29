"""Diagnóstico rápido de la cámara Tapo. Ejecutar: python tapo_test.py"""
import asyncio
import json
import sys
from pathlib import Path

cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
tapo_cfg = cfg.get("tapo", {})
HOST     = tapo_cfg.get("host", "192.168.1.165")
PASSWORD = tapo_cfg.get("password", "")
CLOUD_PWD = tapo_cfg.get("cloud_password") or PASSWORD  # usa local como fallback

print(f"Host: {HOST}")
print(f"Python: {sys.version}\n")

# 1. Conexión API
print("── 1. Conexión pytapo ──────────────────")
cam = None
try:
    from pytapo import Tapo
    cam = Tapo(HOST, "admin", PASSWORD, CLOUD_PWD)
    info = cam.getBasicInfo()
    basic = info.get("device_info", {}).get("basic_info", info)
    print(f"  OK  modelo={basic.get('device_model')}  alias={basic.get('device_alias')}")
    print(f"  cloudPassword usado para streaming: {CLOUD_PWD!r}")
except Exception as e:
    print(f"  FAIL: {e}")

# 2. Streaming Tapo (puerto 8800)
print("\n── 2. Streaming Tapo puerto 8800 ───────")
if cam is not None:
    async def test_stream():
        import json as _json
        payload = _json.dumps({
            "type": "request", "seq": 1,
            "params": {"preview": {"audio": ["default"], "channels": [0], "resolutions": ["HD"]}, "method": "get"},
        })
        ts_bytes = 0
        session = cam.getMediaSession()
        try:
            async with session:
                async for resp in session.transceive(payload, no_data_timeout=8.0):
                    if resp.mimetype == "video/mp2t":
                        ts_bytes += len(resp.plaintext)
                        if ts_bytes >= 100_000:
                            break
        except Exception as exc:
            return 0, str(exc)
        return ts_bytes, None

    ts_received, err = asyncio.run(test_stream())
    if err:
        print(f"  FAIL: {err}")
    elif ts_received == 0:
        print(f"  FAIL: no se recibieron datos de vídeo")
    else:
        print(f"  OK  recibidos {ts_received:,} bytes de vídeo TS")
else:
    print("  (cam no disponible)")

# 3. OpenCV
print("\n── 3. OpenCV ──────────────────────────")
try:
    import cv2
    print(f"  OK  versión {cv2.__version__}")

    for path in ["/stream1", "/stream2"]:
        url = f"rtsp://admin:{PASSWORD}@{HOST}:554{path}"
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        for _ in range(3): cap.grab()
        ret, frame = cap.retrieve()
        cap.release()
        if ret and frame is not None:
            print(f"  RTSP OK en {path} — frame {frame.shape}")
            break
        else:
            print(f"  RTSP FAIL en {path}")
except ImportError:
    print("  NO INSTALADO → pip install opencv-python-headless")
except Exception as e:
    print(f"  ERROR: {e}")

# 4. HTTP endpoints
print("\n── 4. HTTP snapshot endpoints ─────────")
import urllib.request
from base64 import b64encode
import ssl
auth = b64encode(f"admin:{PASSWORD}".encode()).decode()
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
for url in [
    f"http://{HOST}:8800/snapshot.jpg",
    f"http://{HOST}/snapshot.jpg",
    f"http://{HOST}/stream/snapshot.jpg",
    f"https://{HOST}/stream/snapshot.jpg",
    f"http://{HOST}:2020/stream/snapshot.jpg",
]:
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
        with urllib.request.urlopen(req, timeout=3, context=ctx if url.startswith("https") else None) as r:
            data = r.read(16)
            ok = "JPEG ✓" if data[:2] == b"\xff\xd8" else f"no JPEG (got {data[:4].hex()})"
            print(f"  {url}  →  HTTP {r.status}  {ok}")
    except Exception as e:
        print(f"  {url}  →  {type(e).__name__}: {e}")

print("\nDiagnóstico completado.")
