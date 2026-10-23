"""Segundo motor forense: Tesseract 5 (`spa`). Opcional: se activa si el ejecutable está instalado."""
from __future__ import annotations

import os
import shutil
import threading
from functools import lru_cache
from pathlib import Path

from PIL import Image

from ..layout import Pal

RUTAS_WINDOWS = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
]


@lru_cache(maxsize=4)
def ejecutable(preferido: str | None = None) -> str | None:
    if preferido and Path(preferido).exists():
        return preferido
    encontrado = shutil.which("tesseract")
    if encontrado:
        return encontrado
    return next((str(p) for p in RUTAS_WINDOWS if p.exists()), None)


class MotorTesseract:
    nombre = "tesseract"

    def __init__(self, cmd: str) -> None:
        import pytesseract

        self._pt = pytesseract
        pytesseract.pytesseract.tesseract_cmd = cmd
        langs = set(pytesseract.get_languages(config=""))
        self.lang = "spa" if "spa" in langs else "eng"
        self._lock = threading.Lock()

    def version(self) -> str:
        return str(self._pt.get_tesseract_version())

    def leer(self, imagen: Image.Image) -> list[Pal]:
        with self._lock:
            d = self._pt.image_to_data(imagen, lang=self.lang, config="--psm 3", output_type=self._pt.Output.DICT)
        palabras = []
        for i, t in enumerate(d["text"]):
            conf = float(d["conf"][i])
            if not t.strip() or conf < 0:
                continue
            x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
            palabras.append(Pal(t.strip(), (x, y, x + w, y + h), conf / 100.0, orden=i, motor=self.nombre))
        return palabras
