"""Calidad de imagen de las páginas leídas por OCR (fotos y escaneos): métricas y observaciones de calidad.

Las métricas se calculan sobre la imagen reducida a un lado mayor fijo para que sean comparables entre fotos de
distinta resolución. Un documento auténtico y legible no debe quedar observado: solo se observa cuando la calidad
impide validar (poco texto legible, confianza baja) o cuando la imagen está claramente degradada.
"""
from __future__ import annotations

import statistics
from typing import Any

import cv2
import numpy as np
from PIL import Image

from .contrato import Observacion
from .layout import Pal
from .trabajo import DocTrabajo, PaginaTrabajo

LADO_REFERENCIA = 1600  # px: lado mayor al que se reduce la imagen para medir
CONF_BAJA = 0.80  # una palabra con menos confianza que esto se considera dudosa

MENSAJE_REENVIO = ("Por favor envíe nuevamente el documento: una foto nítida, completa, con buena luz, sin reflejos y "
                   "tomada de frente (o el PDF original, si lo tiene).")


def puntaje_lectura(palabras: list[Pal]) -> float:
    """Cuánto texto legible hay: suma de confianzas de palabras de 2+ caracteres con confianza razonable.
    Sirve para comparar lecturas de la misma imagen en distintas orientaciones."""
    return round(sum(w.conf for w in palabras if len(w.t.strip()) >= 2 and w.conf >= 0.6), 2)


def _gris_reducida(img: Image.Image) -> np.ndarray:
    gris = np.asarray(img.convert("L"))
    f = LADO_REFERENCIA / max(gris.shape)
    if f < 1:
        gris = cv2.resize(gris, (int(gris.shape[1] * f), int(gris.shape[0] * f)), interpolation=cv2.INTER_AREA)
    return gris


def medir(p: PaginaTrabajo) -> dict[str, Any]:
    """Métricas de la página: nitidez, brillo, contraste, resolución, palabras y confianza OCR, rotación aplicada."""
    m: dict[str, Any] = {}
    if p.imagen is not None:
        gris = _gris_reducida(p.imagen)
        m["nitidez"] = round(float(cv2.Laplacian(gris, cv2.CV_64F).var()), 1)
        m["brillo"] = round(float(gris.mean()), 1)
        m["contraste"] = round(float(gris.std()), 1)
        m["lado_px"] = int(max(p.imagen.size))
        if p.dpi:
            m["dpi"] = int(p.dpi)
        elif p.escala:
            m["dpi"] = int(round(72 / p.escala))  # render de un PDF escaneado
    palabras = [w for w in p.ocr if w.t.strip()]
    m["palabras"] = len(palabras)
    m["confianza_media"] = round(statistics.mean(w.conf for w in palabras), 3) if palabras else 0.0
    m["fraccion_conf_baja"] = round(sum(w.conf < CONF_BAJA for w in palabras) / len(palabras), 3) if palabras else 1.0
    m["puntaje_lectura"] = puntaje_lectura(palabras)
    m["rotacion_aplicada"] = int(p.rotacion or 0)
    return m


def _obs(id_: str, detalle: str, pagina: int, evidencia: dict[str, Any]) -> Observacion:
    return Observacion(id=id_, motivo="calidad", accion="solicitar_reenvio", detalle=detalle, mensaje_postulante=MENSAJE_REENVIO,
                       pagina=pagina, evidencia=evidencia)


def observaciones(doc: DocTrabajo, reglas: dict[str, Any]) -> list[Observacion]:
    """Observaciones de calidad (ilegible, borrosa, oscura, sobreexpuesta, baja resolución), una por tipo y documento.

    Una página con texto bien leído (suficientes palabras y confianza alta) no se observa por métricas de imagen:
    la foto puede verse oscura o poco nítida y aun así permitir validar."""
    u = reglas.get("calidad", {})
    obs: list[Observacion] = []
    vistos: set[str] = set()

    def agregar(o: Observacion) -> None:
        if o.id not in vistos:
            vistos.add(o.id)
            obs.append(o)

    for p in doc.paginas:
        c = p.calidad
        if p.fuente_texto != "ocr" or not c:
            continue
        palabras, conf = c.get("palabras", 0), c.get("confianza_media", 0.0)
        legible = palabras >= u.get("palabras_legible", 12) and conf >= u.get("confianza_legible", 0.85) \
            and c.get("fraccion_conf_baja", 1.0) <= u.get("fraccion_conf_baja_max", 0.35)
        if palabras < u.get("palabras_min", 6) or c.get("puntaje_lectura", 0) < u.get("puntaje_min", 5) \
                or conf < u.get("confianza_min", 0.70):
            agregar(_obs("calidad.ilegible", f"Página {p.n}: casi no se puede leer texto ({palabras} palabras, confianza media "
                         f"{conf:.2f}); no es posible validar el documento", p.n, c))
            continue
        extrema = c.get("brillo", 128) < u.get("brillo_extremo_min", 35) or c.get("contraste", 99) < u.get("contraste_extremo_min", 10)
        if legible and not extrema:
            continue
        motivo = []
        if "nitidez" in c and c["nitidez"] < u.get("nitidez_min", 60):
            agregar(_obs("calidad.borrosa", f"Página {p.n}: imagen borrosa (nitidez {c['nitidez']:.0f}); el texto se lee con "
                         f"confianza media {conf:.2f}", p.n, c))
            motivo.append("borrosa")
        if "brillo" in c and (c["brillo"] < u.get("brillo_min", 60) or extrema and c["brillo"] < 128):
            agregar(_obs("calidad.oscura", f"Página {p.n}: imagen demasiado oscura (brillo {c['brillo']:.0f} de 255, contraste "
                         f"{c.get('contraste', 0):.0f})", p.n, c))
            motivo.append("oscura")
        elif "brillo" in c and (c["brillo"] > u.get("brillo_max", 245) and c.get("contraste", 99) < u.get("contraste_min", 25)
                                or extrema and c["brillo"] >= 128):
            agregar(_obs("calidad.sobreexpuesta", f"Página {p.n}: imagen sobreexpuesta o lavada (brillo {c['brillo']:.0f}, "
                         f"contraste {c.get('contraste', 0):.0f})", p.n, c))
            motivo.append("sobreexpuesta")
        if c.get("lado_px", 9999) < u.get("lado_min_px", 900):
            agregar(_obs("calidad.baja_resolucion", f"Página {p.n}: resolución insuficiente ({c['lado_px']} px de lado mayor)", p.n, c))
            motivo.append("baja_resolucion")
        if not motivo:
            # no es legible pero ninguna métrica de imagen lo explica (reflejos, recorte, letra pequeña)
            agregar(_obs("calidad.lectura_dudosa", f"Página {p.n}: el texto se lee con poca seguridad ({palabras} palabras, "
                         f"confianza media {conf:.2f}, {c.get('fraccion_conf_baja', 0):.0%} dudosas)", p.n, c))
    return obs
