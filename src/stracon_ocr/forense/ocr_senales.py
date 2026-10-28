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
        # solo campos clave: en fotos y escaneos los logos, timbres y textos secundarios (dirección, institución) se leen
        # con confianza baja de forma rutinaria y no son lo que un falsificador cambia (calibración oct 2026)
        if leido_por_ocr and nombre in claves and len(p.palabras if p.fuente_texto == "ocr" else p.ocr) >= 8:
            base = p.palabras if p.fuente_texto == "ocr" else p.ocr
            confs = [w.conf for w in base]
            media, desv = statistics.mean(confs), statistics.pstdev(confs) or 0.01
            valor = _alfanum(c.valor or "")
            # solo las palabras que forman el valor (la caja puede abarcar la fila entera, p. ej. en la tabla de clases
            # de la licencia, y una fecha vecina leída con ruido no dice nada del campo)
            # y solo las que tienen dígitos: lo que se adultera en una foto son dígitos (RUT, fechas, folios); los meses
            # abreviados ("oct") y los apellidos poco comunes se leen con confianza baja de forma rutinaria
            del_campo = [w for w in base if solape(w.bbox, tuple(c.bbox)) > 0.5 and _alfanum(w.t) and _alfanum(w.t) in valor
                         and any(ch.isdigit() for ch in w.t)]
            if del_campo:
                peor = min(del_campo, key=lambda w: w.conf)
                z = (media - peor.conf) / desv
                if z > u["zscore_confianza"] and peor.conf < u["confianza_min_campo"]:
                    # en cédulas y licencias fotografiadas los dígitos van sobre el fondo de seguridad (guilloches,
                    # hologramas) y se leen con confianza 0,5–0,6 de forma rutinaria: ahí es contexto (baja) y lo que
                    # verifica el valor es la MRZ (calibración oct 2026)
                    sev = "baja" if tipo in reglas.get("tipos_documento_fisico", []) else "media"
                    senales.append(Senal(
                        id="ocr.confianza_anomala", familia="ocr", severidad=sev, campo=nombre, pagina=p.n, bbox=c.bbox,
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
