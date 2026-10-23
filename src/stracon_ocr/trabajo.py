"""Modelo interno de trabajo (no es el contrato): lo que cada etapa del pipeline lee y completa."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from .layout import Pal, Segmento, Superposicion


@dataclass
class PaginaTrabajo:
    n: int
    ancho: float  # unidades de la página: puntos PDF, o píxeles en imágenes
    alto: float
    dpi: int | None  # None = puntos PDF
    rotacion: int = 0
    imagen: Image.Image | None = None  # render (PDF) o la imagen original
    escala: float = 1.0  # unidades de página por píxel de `imagen`
    nativas: list[Pal] = field(default_factory=list)  # texto nativo del PDF o del DOCX
    rotadas: list[Pal] = field(default_factory=list)  # texto girado (marcas de agua)
    imagenes: list[dict[str, Any]] = field(default_factory=list)  # imágenes incrustadas (PDF)
    rellenos: list[dict[str, Any]] = field(default_factory=list)  # rectángulos rellenos (PDF)
    ocr: list[Pal] = field(default_factory=list)  # motor principal, en unidades de página
    ocr2: list[Pal] = field(default_factory=list)  # segundo motor, en unidades de página
    superposiciones: list[Superposicion] = field(default_factory=list)
    palabras: list[Pal] = field(default_factory=list)  # las que valen para extraer
    segmentos: list[Segmento] = field(default_factory=list)
    fuente_texto: str = "ocr"
    motor: str = ""
    similitud_ocr: float | None = None
    calidad: dict[str, Any] = field(default_factory=dict)  # métricas de calidad (calidad.medir)


@dataclass
class DocTrabajo:
    ruta: Path
    nombre: str
    datos: bytes
    sha256: str
    mime_real: str | None
    mime_declarado: str | None
    origen: str
    paginas: list[PaginaTrabajo] = field(default_factory=list)
    pdf_info: dict[str, Any] = field(default_factory=dict)  # metadatos y estructura del PDF
    docx_datos: bytes | None = None  # paquete .docx (también si vino de .doc)
    errores: list[tuple[str, str]] = field(default_factory=list)
    tiempos: dict[str, int] = field(default_factory=dict)  # ms por etapa

    @property
    def texto(self) -> str:
        return "\n".join(s.texto for p in self.paginas for s in p.segmentos)

    def segmentos(self) -> list[Segmento]:
        return [s for p in self.paginas for s in p.segmentos]
