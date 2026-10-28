"""Apoyo para tarjetas físicas fotografiadas o escaneadas (cédula, licencia de conducir).

En las tarjetas los rótulos van impresos en letra pequeña y tenue, y el OCR los lee a medias ("APEELIDOS", "OMBRES",
"MUNICIRRIDAD", "FECHADECONTIOL"). Aquí se reconocen por similitud y se busca su valor por posición: en la misma línea,
a la derecha (en la licencia antigua el valor va a la derecha y algo más arriba que el rótulo) o debajo (cédula y
licencia nueva). Nada de esto depende de datos de una persona: solo de la disposición de la tarjeta.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from ..layout import Segmento
from ..texto import buscar_fecha, normalizar, sin_espacios

# palabras impresas en las tarjetas que nunca son parte de un nombre
NO_NOMBRE = {"LICENCIA", "CONDUCTOR", "REPUBLICA", "CHILE", "DIRECCION", "MUNICIPALIDAD", "NOMBRES", "APELLIDOS", "FECHA",
             "CONTROL", "ULTIMO", "CLASE", "RESTRICCIONES", "DIRECTOR", "TRANSITO", "FIRMA", "PRIMER", "OTORGAMIENTO", "LEY",
             "DE", "DEL", "ESCANEADO", "CAMSCANNER", "CON", "DUPLICADO", "CEDULA", "IDENTIDAD", "NACIONALIDAD", "SEXO",
             "NACIMIENTO", "DOCUMENTO", "EMISION", "VENCIMIENTO", "TITULAR", "SERVICIO", "REGISTRO", "CIVIL",
             "IDENTIFICACION", "RUN", "PROFESION", "NACIO", "INFORMADA", "CHILENA", "CHILENO", "OBSERVACIONES", "ACTUAL",
             "PROXIMO", "CASA", "MONEDA", "LENTES", "RENOVAR", "ANTICIPACION", "DIAS"}


@dataclass
class Rotulo:
    nombre: str  # clave del catálogo
    seg: Segmento
    resto: Segmento | None  # palabras del mismo segmento después del rótulo (valor en la misma línea)
    similitud: float


def _palabras_norm(seg: Segmento) -> list[str]:
    return [sin_espacios(normalizar(p.t)) for p in seg.palabras]


def reconocer(seg: Segmento, catalogo: dict[str, tuple[str, ...]], umbral: float = 76) -> Rotulo | None:
    """¿El segmento empieza con un rótulo del catálogo? Se prueban las primeras 1–4 palabras juntas contra cada forma
    del rótulo; gana la más parecida. Las lecturas parciales cuentan si son un trozo largo del rótulo ("APELL",
    "OMBRES")."""
    pal = _palabras_norm(seg)
    mejor: tuple[float, str, int] | None = None
    for k in range(1, min(4, len(pal)) + 1):
        texto = "".join(pal[:k])
        if len(texto) < 3:
            continue
        for nombre, formas in catalogo.items():
            for forma in formas:
                f = sin_espacios(normalizar(forma))
                sim = fuzz.ratio(texto, f)
                if len(texto) >= 4 and len(texto) >= 0.55 * len(f) and (f.startswith(texto) or f.endswith(texto)):
                    sim = max(sim, 90.0)
                if sim >= umbral and (mejor is None or sim > mejor[0] + 0.5 or (abs(sim - mejor[0]) <= 0.5 and k > mejor[2])):
                    mejor = (sim, nombre, k)
    if mejor is None:
        return None
    sim, nombre, k = mejor
    resto = Segmento(seg.palabras[k:], seg.pagina) if len(seg.palabras) > k else None
    return Rotulo(nombre, seg, resto, sim)


def rotulos(segs: list[Segmento], catalogo: dict[str, tuple[str, ...]]) -> list[Rotulo]:
    return [r for s in segs if (r := reconocer(s, catalogo))]


def es_rotulo(seg: Segmento, catalogo: dict[str, tuple[str, ...]]) -> bool:
    r = reconocer(seg, catalogo)
    return r is not None and r.resto is None


def _yc(s: Segmento) -> float:
    return (s.bbox[1] + s.bbox[3]) / 2


def a_la_derecha(r: Rotulo, segs: list[Segmento], acepta, catalogo) -> Segmento | None:
    """Valor a la derecha del rótulo, en su línea o hasta ~2 líneas más arriba (licencia antigua)."""
    h = r.seg.alto
    x0, y0, x1, y1 = r.seg.bbox
    cands = [s for s in segs if s is not r.seg and s.bbox[0] >= x1 - 0.5 * h and s.bbox[0] - x1 < 25 * h
             and y0 - 2.4 * h <= _yc(s) <= y1 + 0.4 * h and acepta(s) and not es_rotulo(s, catalogo)]
    return min(cands, key=lambda s: (abs(_yc(s) - (y0 + y1) / 2), s.bbox[0] - x1)) if cands else None


def debajo(r: Rotulo, segs: list[Segmento], acepta, catalogo, lineas: int = 1) -> list[Segmento]:
    """Valor bajo el rótulo, en la misma columna (cédula y licencia nueva); hasta `lineas` líneas seguidas."""
    h = r.seg.alto
    x0 = r.seg.bbox[0]
    salida: list[Segmento] = []
    previo = r.seg
    for _ in range(lineas):
        # por el centro: en la cédula el valor (letra grande) empieza antes de que termine el rótulo (letra pequeña)
        cands = [s for s in segs if s is not r.seg and s not in salida and _yc(s) > _yc(previo) + 0.45 * min(previo.alto, s.alto)
                 and s.bbox[1] <= previo.bbox[3] + 2.2 * h and abs(s.bbox[0] - x0) < 4 * h]
        if not cands:
            break
        s = min(cands, key=_yc)
        if es_rotulo(s, catalogo) or reconocer(s, catalogo) or not acepta(s):
            break
        salida.append(s)
        previo = s
    return salida


def es_nombre(s: Segmento | str) -> bool:
    """Texto que puede ser parte de un nombre: palabras alfabéticas que no son rótulos de la tarjeta."""
    texto = s.texto if isinstance(s, Segmento) else s
    palabras = [w for w in normalizar(texto).replace("-", " ").split() if w]
    utiles = [w for w in palabras if w.isalpha() and len(w) >= 2 and w not in NO_NOMBRE]
    return bool(utiles) and len(utiles) >= 0.6 * len(palabras) and sum(len(w) for w in utiles) >= 3


def limpiar_nombre(texto: str) -> str:
    """Quita lo que no es nombre (rótulos, números, signos) y deja las palabras en el orden leído."""
    palabras = [w.strip(".,;:-_'\"") for w in texto.split()]
    return " ".join(w for w in palabras if w and normalizar(w).isalpha() and normalizar(w) not in NO_NOMBRE and len(w) >= 2)


def fechas_en(seg: Segmento) -> list[tuple]:
    """Fechas del segmento (con separadores leídos mal por el OCR: '26:01/2030', '.01.07/2025', '03.11/2023')."""
    texto = re.sub(r"(?<=\d)[:;,](?=\d)", "/", seg.texto)
    salida = []
    for m in re.finditer(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}|\d{1,2}\s*[A-Za-zÁÉÍÓÚ]{3,10}\.?\s*\d{4}", texto):
        f = buscar_fecha(m.group(0).replace(".", "/") if re.match(r"\d{1,2}\.\d{1,2}[/.]", m.group(0)) else m.group(0))
        if f:
            salida.append((f[0], m.group(0)))
    return salida
