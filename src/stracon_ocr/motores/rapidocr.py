"""Motor principal: RapidOCR (modelos PP-OCR en ONNX Runtime, u OpenVINO si está instalado)."""
from __future__ import annotations

import importlib.metadata as md
import importlib.util
import logging
import math
import threading

import cv2
import numpy as np
from PIL import Image

from ..layout import Pal
from .base import caja_de_poligono

log = logging.getLogger(__name__)


class MotorRapidOCR:
    nombre = "rapidocr"

    def __init__(self, lado_deteccion: int = 1280, hilos: int = -1) -> None:
        self._motor = None
        self._lock = threading.Lock()  # el motor no es seguro entre hilos
        self.backend = "onnxruntime"
        self.lado_deteccion = lado_deteccion
        self.hilos = hilos

    def version(self) -> str:
        return f"{md.version('rapidocr')} ({self.backend})"

    def _cargar(self):
        if self._motor is None:
            from rapidocr import RapidOCR

            # detección sobre la imagen reducida a 1280 px de lado mayor: ~2x más rápido sin perder texto;
            # el reconocimiento recorta de la imagen original
            params = {"Global.log_level": "error", "Det.limit_type": "max", "Det.limit_side_len": self.lado_deteccion}
            if importlib.util.find_spec("openvino") is not None:
                from rapidocr import EngineType

                params.update({
                    "Det.engine_type": EngineType.OPENVINO,
                    "Cls.engine_type": EngineType.OPENVINO,
                    "Rec.engine_type": EngineType.OPENVINO,
                })
                if self.hilos > 0:
                    params["EngineConfig.openvino.inference_num_threads"] = self.hilos
                self.backend = "openvino"
            elif self.hilos > 0:
                params["EngineConfig.onnxruntime.intra_op_num_threads"] = self.hilos
            try:
                self._motor = RapidOCR(params=params)
            except Exception:  # parámetros no soportados por esta versión: valores por defecto
                log.warning("RapidOCR no aceptó %s; se usan los valores por defecto", params)
                self._motor = RapidOCR()
                self.backend = "onnxruntime"
        return self._motor

    def orientacion(self, imagen: Image.Image, palabras: list[Pal], muestras: int = 8) -> tuple[int, dict[int, float]]:
        """Grados antihorarios que hay que girar la página para que el texto quede derecho (0, 90, 180 o 270).

        Se reconocen (sin detección ni clasificador de ángulo) los recortes de las palabras más grandes en cada
        orientación candidata y gana la que lee con más confianza. Cajas más altas que anchas → 90/270; si no, 0/180."""
        cand = sorted((w for w in palabras if len(w.t.strip()) >= 3),
                      key=lambda w: -(w.bbox[2] - w.bbox[0]) * (w.bbox[3] - w.bbox[1]))[:muestras]
        if len(cand) < 3:
            return 0, {}
        verticales = sum((w.bbox[3] - w.bbox[1]) > 1.2 * (w.bbox[2] - w.bbox[0]) for w in cand)
        angulos = (90, 270) if verticales >= 0.6 * len(cand) else (0, 180)
        rgb = imagen.convert("RGB")
        puntajes: dict[int, float] = {}
        with self._lock:
            motor = self._cargar()
            for ang in angulos:
                total = 0.0
                for w in cand:
                    x0, y0, x1, y1 = (int(v) for v in w.bbox)
                    recorte = rgb.crop((max(0, x0 - 2), max(0, y0 - 2), x1 + 2, y1 + 2)).rotate(ang, expand=True)
                    r = motor(cv2.cvtColor(np.asarray(recorte), cv2.COLOR_RGB2BGR), use_det=False, use_cls=False, use_rec=True)
                    total += float(r.scores[0]) if getattr(r, "scores", None) is not None and len(r.scores) else 0.0
                puntajes[ang] = round(total / len(cand), 3)
        mejor = max(puntajes, key=puntajes.get)
        otro = min(puntajes, key=puntajes.get)
        if mejor == 0 or puntajes[mejor] < 0.6 or puntajes[mejor] - puntajes[otro] < 0.15:
            return 0, puntajes
        return mejor, puntajes

    def leer(self, imagen: Image.Image) -> list[Pal]:
        bgr = cv2.cvtColor(np.asarray(imagen.convert("RGB")), cv2.COLOR_RGB2BGR)
        with self._lock:
            # use_det/use_cls/use_rec explícitos: RapidOCR recuerda los de la llamada anterior (p. ej. de orientacion)
            r = self._cargar()(bgr, use_det=True, use_cls=True, use_rec=True, return_word_box=True)
        if r is None or r.txts is None:
            return []
        palabras: list[Pal] = []
        lineas = r.word_results or ()
        for i, (caja, texto, conf) in enumerate(zip(r.boxes, r.txts, r.scores)):
            lw = lineas[i] if i < len(lineas) else ()
            (ax, ay), (bx, by) = caja[0], caja[1]
            girada = abs(math.degrees(math.atan2(by - ay, bx - ax))) > 10  # borde superior de la línea inclinado
            if lw and all(len(w) == 3 for w in lw):
                for t, c, poly in lw:
                    if t.strip():
                        palabras.append(Pal(t.strip(), caja_de_poligono(poly), float(c), motor=self.nombre, girada=girada))
            else:  # sin cajas por palabra: se reparte la línea en proporción al largo
                x0, y0, x1, y1 = caja_de_poligono(caja)
                partes = texto.split()
                total = max(1, sum(len(p) + 1 for p in partes))
                x = x0
                for p in partes:
                    w = (x1 - x0) * (len(p) + 1) / total
                    palabras.append(Pal(p, (x, y0, x + w, y1), float(conf), motor=self.nombre, girada=girada))
                    x += w
        for k, p in enumerate(palabras):
            p.orden = k
        return palabras
