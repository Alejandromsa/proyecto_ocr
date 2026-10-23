"""Señales a partir del OCR: confianza anómala en un campo y discrepancia entre motores."""
from __future__ import annotations

import statistics
from typing import Any

from ..contrato import Campo, Senal
from ..layout import solape
from ..texto import normalizar
from ..tipos import TIPOS
from ..trabajo import DocTrabajo


def _alfanum(s: str) -> str:
    return "".join(ch for ch in normalizar(s) if ch.isalnum())


def analizar(doc: DocTrabajo, tipo: str, campos: dict[str, Campo], reglas: dict[str, Any]) -> list[Senal]:
    u = reglas["umbrales"]
    spec = TIPOS.get(tipo)
    claves = {c.nombre for c in spec.campos if c.clave} if spec else set()
    senales: list[Senal] = []
    paginas = {p.n: p for p in doc.paginas}
    for nombre, c in campos.items():
        p = paginas.get(c.pagina or 0)
        if p is None or not c.bbox:
            continue
        leido_por_ocr = p.fuente_texto == "ocr" or c.metodo == "ocr"
        if leido_por_ocr and len(p.palabras if p.fuente_texto == "ocr" else p.ocr) >= 8:
            base = p.palabras if p.fuente_texto == "ocr" else p.ocr
            confs = [w.conf for w in base]
            media, desv = statistics.mean(confs), statistics.pstdev(confs) or 0.01
            del_campo = [w for w in base if solape(w.bbox, tuple(c.bbox)) > 0.5]
            if del_campo:
                peor = min(del_campo, key=lambda w: w.conf)
                z = (media - peor.conf) / desv
                if z > u["zscore_confianza"] and peor.conf < u["confianza_min_campo"]:
                    senales.append(Senal(
                        id="ocr.confianza_anomala", familia="ocr", severidad="media", campo=nombre, pagina=p.n, bbox=c.bbox,
                        detalle=f"El OCR lee '{peor.t}' del campo {nombre} con confianza {peor.conf:.2f}, muy por debajo del resto de la página ({media:.2f})",
                        evidencia={"palabra": peor.t, "conf": round(peor.conf, 3), "media_pagina": round(media, 3), "z": round(z, 2)},
                    ))
        if nombre in claves and p.ocr2:
            base = p.palabras if p.fuente_texto == "ocr" else p.ocr
            a = _alfanum(" ".join(w.t for w in sorted(base, key=lambda w: w.bbox[0]) if solape(w.bbox, tuple(c.bbox)) > 0.5))
            b = _alfanum(" ".join(w.t for w in sorted(p.ocr2, key=lambda w: w.bbox[0]) if solape(w.bbox, tuple(c.bbox)) > 0.5))
            if a and b and a != b:
                senales.append(Senal(
                    id="ocr.motores_discrepan", familia="ocr", severidad="baja", campo=nombre, pagina=p.n, bbox=c.bbox,
                    detalle=f"Los motores OCR leen distinto el campo {nombre}: '{a}' frente a '{b}'",
                    evidencia={"principal": a, "segundo": b},
                ))
    return senales
