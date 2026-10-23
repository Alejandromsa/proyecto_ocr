from __future__ import annotations

import re

from rapidfuzz import fuzz

from ..texto import buscar_fecha, normalizar
from .base import NOMBRE, RUT, CampoSpec, TipoSpec

RE_LINEA_CLASES = re.compile(r"^(?:CLASES?\s+)?([A-F][1-5]?)(?:\s*/\s*[A-F][1-5]?|\s+[A-F][1-5]?)*$")
# palabras de la tarjeta que no son parte del nombre
NO_NOMBRE = {"LICENCIA", "CONDUCTOR", "REPUBLICA", "CHILE", "DIRECCION", "MUNICIPALIDAD", "NOMBRES", "APELLIDOS", "FECHA", "CONTROL",
             "ULTIMO", "CLASE", "RESTRICCIONES", "DIRECTOR", "TRANSITO", "FIRMA", "PRIMER", "OTORGAMIENTO", "LEY", "DE", "DEL"}


def _runs_alfabeticos(texto: str) -> list[str]:
    """Tramos de palabras en mayúsculas que no son rótulos: candidatos a nombres y apellidos."""
    runs, actual = [], []
    for w in normalizar(texto).split():
        if w.isalpha() and len(w) > 2 and w not in NO_NOMBRE:
            actual.append(w)
        else:
            if actual:
                runs.append(" ".join(actual))
            actual = []
    if actual:
        runs.append(" ".join(actual))
    return runs


def completar(doc, campos: dict, crear) -> dict:
    """Tarjeta de licencia: los rótulos son tenues y el OCR los lee mal, así que se usa la estructura de la tarjeta."""
    nuevos: dict = {}
    segs = [s for p in doc.paginas for s in p.segmentos]
    # clases: la línea "B/C/D" o "A2/A4/C/D" bajo el título
    if "clases" not in campos:
        for i, s in enumerate(segs):
            m = RE_LINEA_CLASES.match(s.norm)
            if m and ("/" in s.texto or s.norm.startswith("CLASE") or i < 8):
                nuevos["clases"] = crear("clases", s.texto, s.texto, s)
                break
    # fechas del anverso (sin la de primer otorgamiento del reverso): la menor es el último control y la mayor la próxima
    fechas = []
    for s in segs:
        if "OTORGAMIENTO" in s.norm.replace(" ", ""):
            continue
        r = buscar_fecha(s.texto)
        if r:
            fechas.append((r[0], r[1], s))
    if len(fechas) >= 2:
        fechas.sort(key=lambda f: f[0])
        emi, venc = campos.get("fecha_emision"), campos.get("fecha_vencimiento")
        # los rótulos de la tarjeta son tenues: si falta una fecha o no quedan en orden, manda la estructura
        if not emi or not venc or (emi.valor_normalizado or "") >= (venc.valor_normalizado or ""):
            nuevos["fecha_emision"] = crear("fecha_emision", fechas[0][1], fechas[0][2].texto, fechas[0][2])
            nuevos["fecha_vencimiento"] = crear("fecha_vencimiento", fechas[-1][1], fechas[-1][2].texto, fechas[-1][2])
    # nombre: la tarjeta lo repite (bajo la foto y en los campos); nombres y apellidos son los tramos que aparecen dos veces
    if "nombre" not in campos:
        tramos = [(r, s) for s in segs for r in _runs_alfabeticos(s.texto)]

        def coincide(a: str, b: str) -> bool:
            # "QUINTRECURA" y "QUINTRECURA CONSTANZO" son el mismo; "UARAYA TELLO" y "ARAYA TELLO" también (OCR)
            return a.startswith(b + " ") or b.startswith(a + " ") or fuzz.ratio(a, b) >= 85

        repetidos: list[tuple[str, object]] = []  # (versión más larga, segmento de la primera aparición)
        for i, (r, s) in enumerate(tramos):
            if any(coincide(r, x) for x, _ in repetidos):
                repetidos = [(max(x, r, key=len) if coincide(r, x) else x, y) for x, y in repetidos]
            elif any(coincide(r, o) for j, (o, _) in enumerate(tramos) if j != i):
                repetidos.append((r, s))
        repetidos = [(x, y) for x, y in repetidos if len(x) >= 4]
        if len(repetidos) >= 2:
            (n1, s1), (n2, _) = repetidos[0], repetidos[1]
            nuevos["nombre"] = crear("nombre", f"{n1} {n2}", "nombre repetido en la tarjeta", s1)
    return nuevos


TIPO = TipoSpec(
    clave="licencia_conducir",
    nombre="Licencia de conducir",
    titulos=("Licencia de conductor", "Licencia de conducir"),
    palabras_clave=("Dirección de Tránsito", "Director de tránsito", "Municipalidad", "Clase", "Restricciones", "Fecha de control",
                    "Fecha último control", "Primer otorgamiento", "Firma del conductor"),
    campos=(
        NOMBRE,
        RUT,
        CampoSpec("clases", ("Clase", "Clases"), "clases", clave=True),
        CampoSpec("folio", ("Folio",), "codigo", clave=True),
        CampoSpec("fecha_emision", ("Fecha de emisión", "Fecha último control"), "fecha"),
        CampoSpec("fecha_vencimiento", ("Vencimiento", "Fecha de vencimiento", "Próximo control", "Fecha de control"), "fecha", clave=True),
        CampoSpec("municipalidad", ("Municipalidad",)),
        CampoSpec("direccion", ("Dirección",)),
        CampoSpec("restricciones", ("Restricciones",)),
    ),
    completar=completar,
    obligatorios=("rut", "fecha_vencimiento"),
)
