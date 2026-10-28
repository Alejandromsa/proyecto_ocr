from __future__ import annotations

import re
import statistics

from rapidfuzz import fuzz

from ..texto import buscar_fecha, normalizar, sin_espacios
from . import tarjeta
from .base import NOMBRE, RUT, CampoSpec, TipoSpec

RE_LINEA_CLASES = re.compile(r"^(?:CLASES?\s+)?([A-F][1-5]?)(?:\s*/\s*[A-F][1-5]?|\s+[A-F][1-5]?)*$")
RE_TOKEN_CLASE = re.compile(r"A[1-5]\*?|[BCDEF]")
NO_NOMBRE = tarjeta.NO_NOMBRE  # compatibilidad

# rótulos de la tarjeta (anverso). Formato antiguo: el valor va a la derecha; formato nuevo (desde 2024 en varias
# municipalidades): el valor va debajo y el reverso trae la tabla CLASE | PRIMER | ACTUAL OTORGAMIENTO | PRÓXIMO CONTROL
ROTULOS = {
    "nombres": ("NOMBRES",),
    "apellidos": ("APELLIDOS",),
    "municipalidad": ("MUNICIPALIDAD",),
    "direccion": ("DIRECCION",),
    "ultimo_control": ("FECHA ULTIMO CONTROL",),
    "control": ("FECHA DE CONTROL",),
    "licencia": ("N DE LICENCIA",),
    "restricciones": ("RESTRICCIONES", "RESTRICCIONES / OBSERVACIONES"),
    "clase": ("CLASE",),
}
# marcas del reverso: sus fechas (primer otorgamiento) no son las del anverso
MARCAS_REVERSO = ("OTORGAM", "TRANSITO", "DIRECTOR", "RECTOR", "FIRMADEL", "RESTRIC")


def _clases_de(texto: str) -> list[str] | None:
    """'B', 'BC', 'B C D', 'A2/A4/C/D', 'CLASE B D', 'A21A4' (la barra leída como 1), 'AS A5 Ley 19.495'."""
    t = normalizar(texto)
    t = re.sub(r"^CLASES?\b", "", t).strip()
    t = re.sub(r"\bLEY\b.*$", "", t).strip()
    if not t:
        return None
    clases, basura = [], 0
    for tok in re.split(r"[\s/|]+", t):
        if not tok:
            continue
        pos, encontrados = 0, []
        while pos < len(tok):
            m = RE_TOKEN_CLASE.match(tok, pos)
            if m:
                encontrados.append(m.group(0).rstrip("*"))
                pos = m.end()
            elif tok[pos] in "1IL" and encontrados:  # barra leída como 1, I o L entre dos clases
                pos += 1
            else:
                break
        if pos == len(tok) and encontrados:
            clases += encontrados
        elif len(tok) <= 2:
            basura += 1
        else:
            return None
    return sorted(set(clases)) if clases and basura <= 1 else None


def _pagina_anverso(doc):
    for p in doc.paginas:
        ns = sin_espacios(normalizar(" ".join(s.texto for s in p.segmentos)))
        if "LICENCIADECONDU" in ns or "LICENCIADECOND" in ns:
            return p
    return next((p for p in doc.paginas if p.segmentos), None)


def _cerca_de_reverso(s, segs) -> bool:
    ns = sin_espacios(s.norm)
    if any(m in ns for m in MARCAS_REVERSO):
        return True
    for o in segs:
        if o is s or not any(m in sin_espacios(o.norm) for m in MARCAS_REVERSO):
            continue
        h = max(o.alto, s.alto)
        if abs((o.bbox[1] + o.bbox[3]) / 2 - (s.bbox[1] + s.bbox[3]) / 2) < 1.6 * h:
            return True
    return False


def _tabla(segs):
    """Formato nuevo: filas con 2 o 3 fechas (primer otorgamiento, actual, próximo control) y la clase a la izquierda."""
    encabezado = any(re.search(r"PROXIMO|PR[O0]XIMO|OTORGAMIENTO", s.norm) for s in segs) and any("ACTUAL" in s.norm for s in segs)
    if not encabezado:
        return None
    filas = []
    for s in segs:
        f = tarjeta.fechas_en(s)
        if len(f) >= 2:
            filas.append((s, f))
    if not filas:
        return None
    etiquetas = [s for s in segs if _clases_de(s.texto) and len(s.texto.replace("*", "").strip()) <= 3]
    alto = statistics.median([s.alto for s in etiquetas]) if etiquetas else filas[0][0].alto
    clases, emision, control = set(), [], []
    for s, f in filas:
        yc = (s.bbox[1] + s.bbox[3]) / 2
        izq = [e for e in etiquetas if e.bbox[2] <= s.bbox[0] + alto and abs((e.bbox[1] + e.bbox[3]) / 2 - yc) < 0.7 * alto]
        if izq:
            e = min(izq, key=lambda e: abs((e.bbox[1] + e.bbox[3]) / 2 - yc))
            clases.update(_clases_de(e.texto) or [])
        emision.append((f[1][0], f[1][1], s))
        if len(f) >= 3:
            control.append((f[2][0], f[2][1], s))
    return {"clases": sorted(clases), "emision": max(emision, key=lambda x: x[0]) if emision else None,
            "control": max(control, key=lambda x: x[0]) if control else None}


def _valor_nombre(r, segs, nuevo: bool, lineas: int = 1):
    """Valor de NOMBRES o APELLIDOS: en la misma línea, a la derecha o debajo (según el formato)."""
    if r.resto is not None and tarjeta.es_nombre(r.resto):
        return [r.resto]
    orden = ("debajo", "derecha") if nuevo else ("derecha", "debajo")
    for modo in orden:
        if modo == "derecha":
            v = tarjeta.a_la_derecha(r, segs, tarjeta.es_nombre, ROTULOS)
            if v is not None:
                return [v]
        else:
            v = tarjeta.debajo(r, segs, tarjeta.es_nombre, ROTULOS, lineas=lineas)
            if v:
                return v
    return []


RE_RUT_PALABRA = re.compile(r"\d{1,2}[.,]?\d{3}[.,]?\d{3}\s?-\s?[\dkK]")


def _encima(rotulo, segs, lineas: int):
    """Líneas con forma de nombre inmediatamente encima de un rótulo, en su columna (de abajo hacia arriba)."""
    h = rotulo.alto
    salida, previo = [], rotulo
    for _ in range(lineas):
        cands = [s for s in segs if s is not previo and s not in salida and abs(s.bbox[0] - rotulo.bbox[0]) < 4 * h
                 and (s.bbox[1] + s.bbox[3]) / 2 < (previo.bbox[1] + previo.bbox[3]) / 2 - 0.45 * min(s.alto, previo.alto)
                 and s.bbox[3] >= previo.bbox[1] - 2.2 * h]
        if not cands:
            break
        s = max(cands, key=lambda s: s.bbox[1])
        if not tarjeta.es_nombre(s) or tarjeta.reconocer(s, ROTULOS):
            break
        salida.insert(0, s)
        previo = s
    return salida


def _columna_de_valores(segs):
    """Tarjeta antigua sin rótulos legibles: los valores van en una columna a la derecha de la foto, empezando por el RUT
    (N° de licencia) y siguiendo con municipalidad, nombres y apellidos. Devuelve las líneas con forma de nombre de esa
    columna, en orden."""
    for s in sorted(segs, key=lambda s: s.bbox[1]):
        w = next((p for p in s.palabras if RE_RUT_PALABRA.search(p.t)), None)
        if w is None:
            continue
        x0, h = w.bbox[0], max(s.alto, 1)
        col = [o for o in segs if o.bbox[1] > s.bbox[1] and abs(o.bbox[0] - x0) < 2.5 * h and tarjeta.es_nombre(o)
               and not tarjeta.reconocer(o, ROTULOS) and o.bbox[1] < s.bbox[3] + 8 * h]
        return sorted(col, key=lambda o: o.bbox[1])
    return []


def _nombre_repetido(segs):
    """La tarjeta antigua repite nombres y apellidos bajo la foto: son los tramos que aparecen dos veces."""
    tramos = [(r, s) for s in segs for r in _runs_alfabeticos(s.texto)]

    def coincide(a: str, b: str) -> bool:
        return a.startswith(b + " ") or b.startswith(a + " ") or fuzz.ratio(a, b) >= 85

    repetidos: list[tuple[str, object]] = []
    for i, (r, s) in enumerate(tramos):
        if any(coincide(r, x) for x, _ in repetidos):
            repetidos = [(max(x, r, key=len) if coincide(r, x) else x, y) for x, y in repetidos]
        elif any(coincide(r, o) for j, (o, _) in enumerate(tramos) if j != i):
            repetidos.append((r, s))
    repetidos = [(x, y) for x, y in repetidos if len(x) >= 4]
    if len(repetidos) >= 2:
        return f"{repetidos[0][0]} {repetidos[1][0]}", repetidos[0][1]
    return None


def _runs_alfabeticos(texto: str) -> list[str]:
    """Tramos de palabras en mayúsculas que no son rótulos: candidatos a nombres y apellidos."""
    runs, actual = [], []
    for w in normalizar(texto).split():
        if w.isalpha() and len(w) > 2 and w not in tarjeta.NO_NOMBRE:
            actual.append(w)
        else:
            if actual:
                runs.append(" ".join(actual))
            actual = []
    if actual:
        runs.append(" ".join(actual))
    return runs


def completar(doc, campos: dict, crear) -> dict:
    """Tarjeta de licencia: los rótulos son tenues y el OCR los lee mal, así que se usa la estructura de la tarjeta.

    Calibración con 22 licencias reales (oct 2026): dos formatos (antiguo: clase arriba, valores a la derecha de rótulos
    tenues y nombre repetido bajo la foto; nuevo: valores bajo el rótulo y tabla de clases con primer otorgamiento,
    otorgamiento actual y próximo control). Muchas fotos traen anverso y reverso en la misma página.
    """
    nuevos: dict = {}
    pagina = _pagina_anverso(doc)
    if pagina is None:
        return nuevos
    segs = pagina.segmentos
    tabla = _tabla(segs)
    rots = {}
    for r in tarjeta.rotulos(segs, ROTULOS):
        rots.setdefault(r.nombre, r)

    # clases
    clases_actual = campos.get("clases")
    if tabla and tabla["clases"]:
        nuevos["clases"] = crear("clases", ", ".join(tabla["clases"]), "tabla de clases con fechas", None)
    elif not clases_actual or not clases_actual.valor_normalizado:
        tope = min((s.bbox[1] for s in segs if re.search(r"\d{1,2}\.?\d{3}\.?\d{3}\s?-\s?[\dkK]", s.texto)), default=None)
        lineas = [s for s in segs if (tope is None or s.bbox[1] <= tope + s.alto) and _clases_de(s.texto)
                  and not _cerca_de_reverso(s, segs)]
        if not lineas:  # tarjetas sin RUT legible arriba: la línea de clases con barra ("B/D")
            lineas = [s for i, s in enumerate(segs) if _clases_de(s.texto) and ("/" in s.texto or s.norm.startswith("CLASE"))]
        encontradas = sorted({c for s in lineas for c in (_clases_de(s.texto) or [])})
        if encontradas:
            nuevos["clases"] = crear("clases", ", ".join(encontradas), " | ".join(s.texto for s in lineas), lineas[0])

    # fechas: último control (emisión) y próximo control (vencimiento)
    emi, venc = campos.get("fecha_emision"), campos.get("fecha_vencimiento")
    if tabla and tabla["control"]:
        fe, fv = tabla["emision"], tabla["control"]
        nuevos["fecha_emision"] = crear("fecha_emision", fe[1], f"otorgamiento actual: {fe[2].texto}", fe[2])
        nuevos["fecha_vencimiento"] = crear("fecha_vencimiento", fv[1], f"próximo control: {fv[2].texto}", fv[2])
    else:
        fechas = []
        for s in segs:
            if _cerca_de_reverso(s, segs):
                continue
            for f, texto in tarjeta.fechas_en(s):
                fechas.append((f, texto, s))
        distintas = sorted({f[0] for f in fechas})
        if len(distintas) >= 2:
            primera = next(f for f in fechas if f[0] == distintas[0])
            ultima = next(f for f in fechas if f[0] == distintas[-1])
            if not emi or not venc or (emi.valor_normalizado or "") >= (venc.valor_normalizado or "") \
                    or emi.valor_normalizado not in {d.isoformat() for d in distintas} \
                    or venc.valor_normalizado != distintas[-1].isoformat():
                nuevos["fecha_emision"] = crear("fecha_emision", primera[1], f"fecha menor del anverso: {primera[2].texto}", primera[2])
                nuevos["fecha_vencimiento"] = crear("fecha_vencimiento", ultima[1], f"fecha mayor del anverso: {ultima[2].texto}", ultima[2])
        elif len(distintas) == 1 and not venc:
            f = fechas[0]
            r = rots.get("control")
            if r is not None and abs(r.seg.bbox[1] - f[2].bbox[1]) < 3 * r.seg.alto:
                nuevos["fecha_vencimiento"] = crear("fecha_vencimiento", f[1], f"junto a {r.seg.texto}", f[2])

    # nombre: NOMBRES + APELLIDOS por rótulo; si no, los tramos repetidos bajo la foto
    nuevo_formato = tabla is not None
    nombres = _valor_nombre(rots["nombres"], segs, nuevo_formato) if "nombres" in rots else []
    apellidos = _valor_nombre(rots["apellidos"], segs, nuevo_formato, lineas=2) if "apellidos" in rots else []
    texto_n = tarjeta.limpiar_nombre(" ".join(s.texto for s in nombres))
    texto_a = tarjeta.limpiar_nombre(" ".join(s.texto for s in apellidos))
    if texto_n and not texto_a and nuevo_formato and "nombres" in rots:
        # formato nuevo: los apellidos (1 o 2 líneas) van justo encima del rótulo NOMBRES, aunque su rótulo no se lea
        arriba = _encima(rots["nombres"].seg, segs, 2)
        texto_a = tarjeta.limpiar_nombre(" ".join(s.texto for s in arriba))
        apellidos = arriba
    generico = campos.get("nombre")
    generico_ok = generico is not None and tarjeta.es_nombre(generico.valor or "")
    if texto_n and texto_a and normalizar(texto_n) != normalizar(texto_a):
        nuevos["nombre"] = crear("nombre", f"{texto_n} {texto_a}", "rótulos NOMBRES y APELLIDOS", nombres[0])
    elif not generico_ok:
        # sin rótulos legibles: la tarjeta antigua repite nombres y apellidos bajo la foto; si tampoco, la posición en
        # la columna de valores (municipalidad, nombres, apellidos bajo el RUT). No reemplazan un nombre leído junto a
        # su rótulo
        rep = _nombre_repetido(segs)
        col = _columna_de_valores(segs) if not nuevo_formato else []
        if rep:
            nuevos["nombre"] = crear("nombre", rep[0], "nombre repetido en la tarjeta", rep[1])
        elif len(col) >= 3:
            nuevos["nombre"] = crear("nombre", f"{tarjeta.limpiar_nombre(col[1].texto)} {tarjeta.limpiar_nombre(col[2].texto)}",
                                     "posición bajo el RUT (municipalidad, nombres, apellidos)", col[1])
            if "municipalidad" not in campos and "municipalidad" not in rots:
                nuevos["municipalidad"] = crear("municipalidad", tarjeta.limpiar_nombre(col[0].texto), "posición bajo el RUT", col[0])
        elif generico is not None:
            nuevos["nombre"] = None  # lo leído por rótulo genérico no es un nombre

    # municipalidad
    r = rots.get("municipalidad")
    if r is not None and "municipalidad" not in campos:
        if r.resto is not None and tarjeta.es_nombre(r.resto):
            v = [r.resto]
        else:
            v = tarjeta.debajo(r, segs, tarjeta.es_nombre, ROTULOS) if nuevo_formato else []
            v = v or ([x] if (x := tarjeta.a_la_derecha(r, segs, tarjeta.es_nombre, ROTULOS)) else [])
        if v:
            nuevos["municipalidad"] = crear("municipalidad", tarjeta.limpiar_nombre(v[0].texto), r.seg.texto, v[0])
    return nuevos


TIPO = TipoSpec(
    clave="licencia_conducir",
    nombre="Licencia de conducir",
    titulos=("Licencia de conductor", "Licencia de conducir"),
    palabras_clave=("Dirección de Tránsito", "Director de tránsito", "Municipalidad", "Clase", "Restricciones", "Fecha de control",
                    "Fecha último control", "Primer otorgamiento", "Firma del conductor", "Próximo control", "Ley 19.495"),
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
