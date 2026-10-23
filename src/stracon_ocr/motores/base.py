"""Interfaz común de los motores OCR. Todos devuelven palabras en píxeles de la imagen recibida."""
from __future__ import annotations

from typing import Protocol

from PIL import Image

from ..layout import Pal


class MotorOCR(Protocol):
    nombre: str

    def version(self) -> str: ...

    def leer(self, imagen: Image.Image) -> list[Pal]: ...


def caja_de_poligono(puntos) -> tuple[float, float, float, float]:
    xs = [float(p[0]) for p in puntos]
    ys = [float(p[1]) for p in puntos]
    return (min(xs), min(ys), max(xs), max(ys))
