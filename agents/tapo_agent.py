"""
Agente para cámaras Tapo (TP-Link).

Requiere:
  - pip install pytapo
  - Para snapshots: pip install opencv-python-headless  (o tener ffmpeg en PATH)

Config en config.json:
  "tapo": {
    "host": "192.168.1.X",
    "password": "contraseña_local_camara",
    "cloud_password": "contraseña_cuenta_tapo"   (opcional, solo para algunas funciones)
  }
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

_RTSP_PATHS   = ["/stream1", "/stream2", "/h264Preview_01_main", "/h264Preview_01_sub"]
_RTSP_PORT    = 554
_RTSP_TIMEOUT = 15
_STREAM_BYTES = 300_000   # ~300 KB de TS → suficiente para al menos un keyframe HD


class TapoAgent(BaseAgent):
    def __init__(
        self,
        host: str,
        password: str,
        cloud_password: str | None = None,
        rtsp_password: str | None = None,
    ) -> None:
        self._host           = host
        self._password       = password
        self._cloud_password = cloud_password
        self._rtsp_password  = rtsp_password or password
        self._cam            = None

    def run(self) -> dict:
        return self.get_status()

    # ------------------------------------------------------------------ #
    # Conexión                                                             #
    # ------------------------------------------------------------------ #

    def _ensure_cam(self) -> None:
        if self._cam is None:
            from pytapo import Tapo
            # El streaming propietario (puerto 8800) usa cloudPassword para auth Digest.
            # En cámaras standalone (C200, C310…) la contraseña de streaming
            # es la misma que la contraseña local del dispositivo.
            stream_pwd = self._cloud_password or self._password
            self._cam = Tapo(
                self._host,
                "admin",
                self._password,
                stream_pwd,
            )

    # ------------------------------------------------------------------ #
    # Estado                                                               #
    # ------------------------------------------------------------------ #

    def get_status(self) -> dict:
        self._ensure_cam()
        raw  = self._cam.getBasicInfo()
        info = (
            raw.get("device_info", {}).get("basic_info", {})
            or raw.get("basic_info", {})
            or {}
        )

        result: dict = {
            "name":    info.get("device_alias") or info.get("alias", "Cámara Tapo"),
            "model":   info.get("device_model") or info.get("model", ""),
            "firmware": info.get("sw_version",  ""),
            "online":  True,
        }

        try:
            priv = self._cam.getPrivacyMode()
            result["privacy_mode"] = (
                priv.get("privacy_mode", {}).get("enabled")
                or priv.get("enabled")
            )
        except Exception:
            result["privacy_mode"] = None

        try:
            mot = self._cam.getMotionDetection()
            result["motion_detection"] = (
                mot.get("motion_detection", {}).get("enabled")
                or mot.get("enabled")
            )
        except Exception:
            result["motion_detection"] = None

        return result

    # ------------------------------------------------------------------ #
    # Snapshot                                                             #
    # ------------------------------------------------------------------ #

    def get_snapshot(self) -> bytes:
        """
        Captura un frame de la cámara. Estrategia en orden:
        1. HTTP snapshot endpoint (algunos modelos modernos)
        2. Protocolo streaming propietario Tapo (puerto 8800)
        3. RTSP + OpenCV
        4. RTSP + ffmpeg
        """
        # 1 ── HTTP snapshot
        try:
            data = _snapshot_http(self._host, self._rtsp_password)
            logger.info("Snapshot via HTTP")
            return data
        except Exception as exc:
            logger.debug("HTTP snapshot falló: %s", exc)

        # 2 ── Protocolo streaming Tapo (puerto 8800)
        self._ensure_cam()
        try:
            data = _snapshot_via_stream(self._cam)
            logger.info("Snapshot via Tapo streaming (puerto 8800)")
            return data
        except Exception as exc:
            logger.debug("Tapo streaming falló: %s", exc)

        # 3 / 4 ── RTSP
        base_url = f"rtsp://admin:{self._rtsp_password}@{self._host}:{_RTSP_PORT}"
        cv2_missing = False
        for path in _RTSP_PATHS:
            rtsp_url = base_url + path
            try:
                data = _snapshot_opencv(rtsp_url)
                logger.info("Snapshot via OpenCV: %s", path)
                return data
            except ImportError:
                cv2_missing = True
                break
            except Exception as exc:
                logger.debug("OpenCV falló para %s: %s", path, exc)

        ffmpeg_missing = False
        for path in _RTSP_PATHS:
            rtsp_url = base_url + path
            try:
                data = _snapshot_ffmpeg(rtsp_url)
                logger.info("Snapshot via ffmpeg: %s", path)
                return data
            except FileNotFoundError:
                ffmpeg_missing = True
                break
            except Exception as exc:
                logger.debug("ffmpeg falló para %s: %s", path, exc)

        if cv2_missing and ffmpeg_missing:
            raise RuntimeError(
                "No se puede capturar snapshot: instala opencv-python-headless "
                "(`pip install opencv-python-headless`) o añade ffmpeg al PATH."
            )
        raise RuntimeError(
            "No se pudo capturar imagen. Todas las vías fallaron (HTTP, streaming "
            "Tapo, RTSP). Comprueba IP y contraseña."
        )

    # ------------------------------------------------------------------ #
    # PTZ                                                                  #
    # ------------------------------------------------------------------ #

    def ptz_move(self, direction: str, steps: int = 5) -> None:
        """
        Mueve la cámara.
        direction: 'left' | 'right' | 'up' | 'down'
        steps: 1-10 (unidades relativas, por defecto 5)
        """
        self._ensure_cam()
        d = direction.lower().strip()
        x = (-steps if d == "left"  else steps if d == "right" else 0)
        y = (-steps if d == "down"  else steps if d == "up"    else 0)
        if x == 0 and y == 0:
            raise ValueError(f"Dirección no reconocida: {direction!r}")
        self._cam.moveMotor(x, y)


# ------------------------------------------------------------------ #
# Helpers de captura                                                   #
# ------------------------------------------------------------------ #

def _snapshot_http(host: str, password: str) -> bytes:
    """Intenta obtener un JPEG vía endpoints HTTP (algunos modelos Tapo)."""
    import urllib.request
    from base64 import b64encode

    auth = b64encode(f"admin:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}

    candidates = [
        f"http://{host}:8800/snapshot.jpg",
        f"http://{host}/snapshot.jpg",
        f"http://{host}/stream/snapshot.jpg",
        f"http://{host}:2020/stream/snapshot.jpg",
        f"http://{host}/onvif/snapshot",
    ]
    for url in candidates:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read()
                if data[:2] == b"\xff\xd8":
                    return data
        except Exception:
            continue
    raise RuntimeError("Ningún endpoint HTTP devolvió imagen JPEG")


async def _snapshot_stream_async(cam) -> bytes:
    """Captura un frame usando el protocolo propietario Tapo (puerto 8800, Digest auth)."""
    import json

    payload = json.dumps({
        "type": "request",
        "seq": 1,
        "params": {
            "preview": {
                "audio": ["default"],
                "channels": [0],
                "resolutions": ["HD"],
            },
            "method": "get",
        },
    })

    ts_data = bytearray()
    media_session = cam.getMediaSession()
    async with media_session:
        async for resp in media_session.transceive(payload, no_data_timeout=8.0):
            if resp.mimetype == "video/mp2t":
                ts_data += resp.plaintext
                if len(ts_data) >= _STREAM_BYTES:
                    break

    if not ts_data:
        raise RuntimeError("Streaming: no se recibieron datos de vídeo")

    return _ts_to_jpeg(bytes(ts_data))


def _ts_to_jpeg(ts_data: bytes) -> bytes:
    """Convierte un buffer MPEG-TS a JPEG (primero intenta ffmpeg, luego cv2 + temp file)."""
    # Intentar ffmpeg por pipe
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "mpegts", "-i", "pipe:0",
                "-frames:v", "1",
                "-q:v", "5",
                "-f", "image2",
                "pipe:1",
            ],
            input=ts_data,
            capture_output=True,
            timeout=15,
        )
        if result.stdout and result.stdout[:2] == b"\xff\xd8":
            return result.stdout
    except FileNotFoundError:
        pass  # ffmpeg no está en PATH

    # Fallback: OpenCV con archivo temporal
    import os
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as fh:
        fh.write(ts_data)
        tmp = fh.name
    try:
        import cv2
        cap = cv2.VideoCapture(tmp)
        for _ in range(3):
            cap.grab()
        ret, frame = cap.retrieve()
        if not ret:
            ret, frame = cap.read()
        cap.release()
        if ret and frame is not None:
            _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return buf.tobytes()
    finally:
        os.unlink(tmp)

    raise RuntimeError("No se pudo extraer frame del stream TS (ni ffmpeg ni cv2)")


def _snapshot_via_stream(cam) -> bytes:
    """Wrapper síncrono de _snapshot_stream_async usando un event loop en hilo propio."""
    holder: dict = {}

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            holder["data"] = loop.run_until_complete(_snapshot_stream_async(cam))
        except Exception as exc:
            holder["error"] = exc
        finally:
            loop.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=30)

    if "error" in holder:
        raise holder["error"]
    if "data" not in holder:
        raise RuntimeError("Streaming snapshot: tiempo de espera agotado (30 s)")
    return holder["data"]


def _snapshot_opencv(rtsp_url: str) -> bytes:
    import cv2

    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    try:
        for _ in range(5):
            cap.grab()
        ret, frame = cap.retrieve()
        if not ret:
            ret, frame = cap.read()
        if not ret or frame is None:
            raise RuntimeError("VideoCapture no devolvió frame")
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes()
    finally:
        cap.release()


def _snapshot_ffmpeg(rtsp_url: str) -> bytes:
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-rtsp_transport", "tcp",
            "-i", rtsp_url,
            "-frames:v", "1",
            "-q:v", "5",
            "-f", "image2",
            "pipe:1",
        ],
        capture_output=True,
        timeout=_RTSP_TIMEOUT,
    )
    if not result.stdout:
        stderr = result.stderr[-300:].decode(errors="replace")
        raise RuntimeError(f"ffmpeg sin salida. stderr: {stderr}")
    return result.stdout
