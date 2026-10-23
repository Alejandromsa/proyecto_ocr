"""Registro de motores OCR. Se crean una vez por proceso (cargar modelos es caro)."""
from __future__ import annotations

import threading

from ..config import Config
from .base import MotorOCR
from .tesseract import ejecutable as ejecutable_tesseract

_cache: dict[str, MotorOCR] = {}
_lock = threading.Lock()


class MotorNoDisponible(RuntimeError):
    pass


def obtener(nombre: str, cfg: Config) -> MotorOCR:
    with _lock:
        if nombre not in _cache:
            _cache[nombre] = _crear(nombre, cfg)
        return _cache[nombre]


def _crear(nombre: str, cfg: Config) -> MotorOCR:
    if nombre == "rapidocr":
        from .rapidocr import MotorRapidOCR

        return MotorRapidOCR(hilos=cfg.hilos_ocr)
    if nombre == "tesseract":
        cmd = ejecutable_tesseract(cfg.tesseract_cmd)
        if not cmd:
            raise MotorNoDisponible("Tesseract no está instalado (instalador UB Mannheim + spa.traineddata, o SOCR_TESSERACT)")
        from .tesseract import MotorTesseract

        return MotorTesseract(cmd)
    if nombre == "azure":
        if not (cfg.azure_endpoint and cfg.azure_key):
            raise MotorNoDisponible("Faltan SOCR_AZURE_ENDPOINT y SOCR_AZURE_KEY")
        try:
            from .azure_di import MotorAzureDI
        except ImportError as e:
            raise MotorNoDisponible("Instalar el extra: uv sync --extra azure") from e
        return MotorAzureDI(cfg.azure_endpoint, cfg.azure_key)
    raise MotorNoDisponible(f"Motor desconocido: {nombre}")


def segundo_motor(cfg: Config, principal: str) -> MotorOCR | None:
    """Tesseract como segundo motor forense, si está instalado y no es ya el principal."""
    if not cfg.segundo_motor or principal == "tesseract" or not ejecutable_tesseract(cfg.tesseract_cmd):
        return None
    try:
        return obtener("tesseract", cfg)
    except Exception:
        return None


def disponibles(cfg: Config) -> dict[str, bool]:
    import importlib.util

    return {
        "rapidocr": importlib.util.find_spec("rapidocr") is not None,
        "tesseract": bool(ejecutable_tesseract(cfg.tesseract_cmd)) and importlib.util.find_spec("pytesseract") is not None,
        "azure": bool(cfg.azure_endpoint and cfg.azure_key) and importlib.util.find_spec("azure.ai.documentintelligence") is not None,
    }
