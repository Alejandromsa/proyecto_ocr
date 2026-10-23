"""Forense de imágenes (fotos y escaneos): EXIF/XMP de edición, marca de IA y ELA por campo.

ELA da falsos positivos con fotos recomprimidas (WhatsApp): la señal es de severidad baja y solo por zona de campo.
"""
from __future__ import annotations

import io
import re
from typing import Any

import numpy as np
from PIL import ExifTags, Image, ImageChops

from ..contrato import Campo, Senal
from ..trabajo import DocTrabajo


def exif(img: Image.Image) -> dict[str, str]:
    datos = {}
    try:
        ex = img.getexif()
        for k, v in ex.items():
            datos[ExifTags.TAGS.get(k, str(k))] = str(v)[:200]
        for k, v in ex.get_ifd(0x8769).items():
            datos[ExifTags.TAGS.get(k, str(k))] = str(v)[:200]
    except Exception:
        pass
    return {k: v for k, v in datos.items() if k in {"Software", "ProcessingSoftware", "Make", "Model", "DateTime", "DateTimeOriginal", "DateTimeDigitized", "Artist"}}


def ela(img: Image.Image, calidad: int = 90) -> np.ndarray:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=calidad)
    diff = ImageChops.difference(img.convert("RGB"), Image.open(io.BytesIO(buf.getvalue())))
    return np.asarray(diff.convert("L"), dtype=np.float32)


def analizar(doc: DocTrabajo, campos: dict[str, Campo], reglas: dict[str, Any]) -> list[Senal]:
    senales: list[Senal] = []
    original = Image.open(io.BytesIO(doc.datos))
    meta = exif(original)
    doc.pdf_info["exif"] = meta
    software = (meta.get("Software", "") + " " + meta.get("ProcessingSoftware", "")).lower()
    editores = [e for e in reglas["software_imagen_editores"] if e in software]
    if editores:
        senales.append(Senal(id="img.exif_editor", familia="metadatos", severidad="media",
                             detalle=f"La imagen pasó por un editor ({', '.join(editores)})", evidencia=meta))
    if b"trainedAlgorithmicMedia" in doc.datos or b"compositeWithTrainedAlgorithmicMedia" in doc.datos:
        senales.append(Senal(id="img.generada_ia", familia="metadatos", severidad="alta",
                             detalle="Los metadatos declaran que la imagen fue generada o editada con IA (IPTC DigitalSourceType)"))
    xmp = original.info.get("xmp") or b""
    if isinstance(xmp, str):
        xmp = xmp.encode()
    acciones = re.findall(rb"stEvt:action[=>\"]+\s*\"?([a-zA-Z]+)", xmp)
    if acciones:
        senales.append(Senal(id="meta.historial_xmp", familia="metadatos", severidad="media",
                             detalle=f"El XMP de la imagen registra {len(acciones)} acción(es) de edición",
                             evidencia={"acciones": [a.decode() for a in acciones][:20]}))
    if (doc.mime_real or "").endswith("jpeg") and campos:
        senales += ela_campos(doc, campos, reglas["umbrales"]["ela_ratio_zona"])
    return senales


def ela_campos(doc: DocTrabajo, campos: dict[str, Campo], ratio_max: float) -> list[Senal]:
    senales = []
    paginas = {p.n: p for p in doc.paginas}
    mapas: dict[int, np.ndarray] = {}
    for nombre, c in campos.items():
        p = paginas.get(c.pagina or 0)
        if p is None or p.imagen is None or not c.bbox:
            continue
        if p.n not in mapas:
            mapas[p.n] = ela(p.imagen)
        m = mapas[p.n]
        x0, y0, x1, y1 = (int(v) for v in c.bbox)
        zona = m[max(0, y0):y1, max(0, x0):x1]
        texto = [m[int(w.bbox[1]):int(w.bbox[3]), int(w.bbox[0]):int(w.bbox[2])] for w in p.palabras]
        ref = np.median([float(t.mean()) for t in texto if t.size]) if texto else float(m.mean())
        if zona.size and ref > 0 and float(zona.mean()) / ref > ratio_max:
            senales.append(Senal(id="img.ela_zona", familia="visual", severidad="baja", campo=nombre, pagina=p.n, bbox=c.bbox,
                                 detalle=f"La zona del campo {nombre} tiene un nivel de error de compresión {float(zona.mean()) / ref:.1f} veces el del resto del texto",
                                 evidencia={"ela_campo": round(float(zona.mean()), 2), "ela_texto": round(ref, 2)}))
    return senales
