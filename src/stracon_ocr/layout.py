"""Palabras con geometría y su agrupación en segmentos (frases contiguas en una línea).

Un segmento es la unidad que usan la extracción por etiqueta y las señales tipográficas: las columnas
de un formulario quedan en segmentos distintos aunque estén en la misma línea.
"""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field

from .texto import normalizar

Caja = tuple[float, float, float, float]


def base_fuente(nombre: str | None) -> str:
    """'ABCDEF+Arial-BoldMT' → 'Arial-BoldMT'."""
    if not nombre:
        return ""
    return nombre.split("+", 1)[1] if "+" in nombre[:8] else nombre


@dataclass
class Pal:
    t: str
    bbox: Caja
    conf: float = 1.0
    fuentes: tuple[str, ...] = ()  # fuente de cada carácter (PDF nativo)
    tams: tuple[float, ...] = ()  # tamaño de cada carácter (PDF nativo)
    color: tuple | None = None
    orden: int = 0  # orden de dibujo en el content stream
    motor: str = ""
    girada: bool = False  # texto inclinado (marcas de agua): no participa en la extracción

    @property
    def fuente(self) -> str | None:
        return Counter(self.fuentes).most_common(1)[0][0] if self.fuentes else None

    @property
    def tam(self) -> float | None:
        return round(statistics.median(self.tams), 2) if self.tams else None

    @property
    def alto(self) -> float:
        return self.bbox[3] - self.bbox[1]

    @property
    def yc(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


@dataclass
class Segmento:
    palabras: list[Pal]
    pagina: int
    norm: str = field(init=False)

    def __post_init__(self) -> None:
        self.norm = normalizar(self.texto)

    @property
    def texto(self) -> str:
        return " ".join(p.t for p in self.palabras)

    @property
    def bbox(self) -> Caja:
        return union([p.bbox for p in self.palabras])

    @property
    def conf(self) -> float:
        return min(p.conf for p in self.palabras)

    @property
    def alto(self) -> float:
        return statistics.median(p.alto for p in self.palabras)


def union(cajas: list[Caja]) -> Caja:
    return (min(c[0] for c in cajas), min(c[1] for c in cajas), max(c[2] for c in cajas), max(c[3] for c in cajas))


def area(c: Caja) -> float:
    return max(0.0, c[2] - c[0]) * max(0.0, c[3] - c[1])


def interseccion(a: Caja, b: Caja) -> float:
    return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def solape(a: Caja, b: Caja) -> float:
    """Intersección sobre el área de la caja menor (0 a 1)."""
    m = min(area(a), area(b))
    return interseccion(a, b) / m if m > 0 else 0.0


def escalar(c: Caja, f: float) -> Caja:
    return (c[0] * f, c[1] * f, c[2] * f, c[3] * f)


def redondear(c: Caja | None) -> list[float] | None:
    return [round(v, 1) for v in c] if c else None


@dataclass
class Superposicion:
    """Dos capas de texto distinto dibujadas en el mismo lugar."""

    visible: list[Pal]  # caracteres que quedan a la vista
    debajo: list[Pal]  # caracteres de la otra capa

    @property
    def bbox(self) -> Caja:
        return union([p.bbox for p in self.visible + self.debajo])

    @staticmethod
    def _unir(chars: list[Pal]) -> str:
        texto, previo = "", None
        for c in sorted(chars, key=lambda c: c.bbox[0]):
            if previo is not None and c.bbox[0] - previo.bbox[2] > 0.2 * max(c.alto, 1):
                texto += " "
            texto += c.t
            previo = c
        return texto

    @property
    def texto_visible(self) -> str:
        return self._unir(self.visible)

    @property
    def texto_debajo(self) -> str:
        return self._unir(self.debajo)


def _misma_capa(a: Pal, b: Pal) -> bool:
    return base_fuente(a.fuente) == base_fuente(b.fuente) and abs((a.tam or 0) - (b.tam or 0)) <= 0.5


def depurar_superpuestas(chars: list[Pal], umbral: float = 0.5, umbral_otra_fuente: float = 0.25) -> tuple[list[Pal], list[Superposicion]]:
    """Depura caracteres encimados antes de armar palabras.

    - Mismo carácter en el mismo lugar con la misma fuente (sombra, negrita simulada): se descarta sin más.
    - Mismo carácter con otra fuente o tamaño: se descarta y queda como rastro de otra capa.
    - Carácter distinto encimado: se queda el de la fuente dominante de la página y el otro es evidencia.
    Entre fuentes distintas basta un solape menor (la capa de abajo suele estar corrida unos puntos).
    Solo se informan las zonas donde hay al menos un carácter distinto (texto reemplazado).
    """
    peso = Counter(base_fuente(c.fuente) for c in chars)
    vivos: dict[int, Pal] = {}
    cubetas: dict[int, list[int]] = {}
    pares: list[tuple[Pal, Pal, bool]] = []  # (visible, debajo, mismo_texto)
    for c in sorted(chars, key=lambda c: c.orden):
        cub = int(c.yc // 4)
        cercanos = [vivos[i] for b in (cub - 1, cub, cub + 1) for i in cubetas.get(b, []) if i in vivos]
        k = max(cercanos, key=lambda k: solape(c.bbox, k.bbox), default=None)
        s = solape(c.bbox, k.bbox) if k is not None else 0.0
        comparable = k is not None and c.t.isalnum() and k.t.isalnum() and 0.6 < (c.alto / max(k.alto, 0.1)) < 1.6
        if s < umbral_otra_fuente or s < umbral and (_misma_capa(c, k) or not comparable):
            vivos[id(c)] = c
            cubetas.setdefault(cub, []).append(id(c))
            continue
        if c.t == k.t:
            if base_fuente(c.fuente) != base_fuente(k.fuente) or abs((c.tam or 0) - (k.tam or 0)) > 0.5:
                pares.append((k, c, True))
            continue
        if peso[base_fuente(c.fuente)] > peso[base_fuente(k.fuente)]:
            del vivos[id(k)]
            vivos[id(c)] = c
            cubetas.setdefault(cub, []).append(id(c))
            pares.append((c, k, False))
        else:
            pares.append((k, c, False))
    grupos: list[tuple[list[tuple[Pal, Pal, bool]], Caja]] = []
    for par in sorted(pares, key=lambda p: (p[0].yc, p[0].bbox[0])):
        v = par[0]
        for g in grupos:
            caja = g[1]
            if abs((caja[1] + caja[3]) / 2 - v.yc) < 0.5 * v.alto and v.bbox[0] - caja[2] < 3 * v.alto:
                g[0].append(par)
                grupos[grupos.index(g)] = (g[0], union([caja, v.bbox]))
                break
        else:
            grupos.append(([par], v.bbox))
    superposiciones = []
    for ps, caja in grupos:
        if all(igual for _, _, igual in ps):
            continue
        debajo = [p[1] for p in ps]
        zona = union([caja] + [d.bbox for d in debajo])
        # lo visible es todo lo que quedó en esa zona de la línea, no solo los caracteres emparejados
        visible = [v for v in vivos.values() if v.bbox[0] < zona[2] and v.bbox[2] > zona[0] and abs(v.yc - (zona[1] + zona[3]) / 2) < 0.5 * v.alto]
        superposiciones.append(Superposicion(visible=visible or [p[0] for p in ps], debajo=debajo))
    return sorted(vivos.values(), key=lambda c: c.orden), superposiciones


def agrupar(palabras: list[Pal], pagina: int) -> list[Segmento]:
    """Agrupa palabras en líneas y corta cada línea en segmentos donde hay un hueco grande."""
    if not palabras:
        return []
    lineas: list[list[Pal]] = []
    for p in sorted(palabras, key=lambda p: p.yc):
        for ln in lineas:
            ref = ln[-1]
            if abs(p.yc - statistics.mean(x.yc for x in ln)) < 0.5 * min(p.alto, ref.alto):
                ln.append(p)
                break
        else:
            lineas.append([p])
    segmentos: list[Segmento] = []
    for ln in lineas:
        ln.sort(key=lambda p: p.bbox[0])
        actual = [ln[0]]
        for p in ln[1:]:
            hueco = p.bbox[0] - actual[-1].bbox[2]
            if hueco > 2.5 * min(p.alto, actual[-1].alto):
                segmentos.append(Segmento(actual, pagina))
                actual = [p]
            else:
                actual.append(p)
        segmentos.append(Segmento(actual, pagina))
    segmentos.sort(key=lambda s: (round(s.bbox[1] / 4), s.bbox[0]))
    return segmentos
