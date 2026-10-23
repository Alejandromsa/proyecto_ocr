"""Extracción de campos por tipo: rótulo → valor por posición (zona), luego regex y frases del documento.

Determinista y auditable: cada campo guarda página, caja, confianza, método, regla y evidencia.
Los rótulos se comparan sin tildes ni espacios: hay PDF y lecturas OCR que pegan las palabras
("FECHAEMISIÓN:", "FECHADECONTROL").
"""
from __future__ import annotations

import re
from typing import Any

from .contrato import Campo
from .layout import Segmento, agrupar, redondear
from .texto import RE_RUT, buscar_anio, normalizar, normalizar_fecha, normalizar_rut, rut_valido, sin_espacios, sin_tildes
from .tipos import TIPOS, CampoSpec, TipoSpec
from .trabajo import DocTrabajo, PaginaTrabajo

ESTRICTAS = {"rut", "fecha", "anio", "clases", "numero", "codigo"}
RE_CLASES = re.compile(r"\b(A[1-5]|B|C|D|E|F)\b")
RE_CLASES_ROTULO = re.compile(r"\bCLASES?\s+([A-F][1-5]?(?:\s*[/,]?\s*[A-F][1-5]?\b)*)")


def normalizar_valor(clase: str, texto: str) -> Any:
    texto = (texto or "").strip()
    if clase == "rut":
        return normalizar_rut(texto)
    if clase == "fecha":
        return normalizar_fecha(texto)
    if clase == "anio":
        return buscar_anio(texto)
    if clase == "nombre":
        return re.sub(r"\s+", " ", sin_tildes(texto).upper()).strip() or None
    if clase == "codigo":
        v = re.sub(r"\s+", "", texto.upper())
        return v if re.search(r"\d", v) else None
    if clase == "clases":
        clases = sorted(set(RE_CLASES.findall(texto.upper())))
        return clases or None
    if clase == "numero":
        m = re.search(r"\d+(?:[.,]\d+)?", texto)
        return float(m.group(0).replace(",", ".")) if m else None
    return re.sub(r"\s+", " ", texto) or None


def _etiquetas_conocidas() -> set[str]:
    """Rótulos de todos los tipos, sin espacios."""
    return {sin_espacios(e) for t in TIPOS.values() for c in t.campos for e in c.etiquetas_norm}


def es_rotulo(seg: Segmento, etiquetas: set[str]) -> bool:
    return sin_espacios(seg.norm) in etiquetas


def _yc(s: Segmento) -> float:
    return (s.bbox[1] + s.bbox[3]) / 2


def valor_cercano(rotulo: Segmento, segmentos: list[Segmento], etiquetas: set[str]) -> Segmento | None:
    """Valor a la derecha del rótulo en la misma línea o, si no hay, justo debajo en la misma columna."""
    h = rotulo.alto
    x0, _, x1, y1 = rotulo.bbox
    # un valor no es mucho más alto que su rótulo (descarta letras sueltas de marcas de agua)
    candidatos = [s for s in segmentos if s is not rotulo and s.norm and not es_rotulo(s, etiquetas) and s.alto < 4 * h]
    derecha = [s for s in candidatos
               if abs(_yc(s) - _yc(rotulo)) < 0.6 * max(h, s.alto) and s.bbox[0] >= x1 - 1 and s.bbox[0] - x1 < 15 * h]
    if derecha:
        return min(derecha, key=lambda s: s.bbox[0])
    debajo = []
    for s in candidatos:
        if not (y1 - 0.3 * h <= s.bbox[1] <= y1 + 3.5 * h):
            continue
        cruce = min(s.bbox[2], x1) - max(s.bbox[0], x0)
        if abs(s.bbox[0] - x0) < 3 * h or cruce > 0.5 * min(s.bbox[2] - s.bbox[0], x1 - x0):
            debajo.append(s)
    return min(debajo, key=lambda s: s.bbox[1]) if debajo else None


def _en_linea(seg: Segmento, alias_ns: str, spec: CampoSpec, etiquetas: set[str]) -> tuple[str, Segmento] | None:
    """Rótulo y valor en el mismo segmento: 'RUT: 12.345.678-5', 'FECHADECONTROL 17-04-2031', 'NOMBRES DAVID ANTONIO'."""
    pal = seg.palabras
    for i in range(len(pal)):
        for k in range(i + 1, len(pal)):
            if sin_espacios(normalizar(" ".join(p.t for p in pal[i:k]))) != alias_ns:
                continue
            resto = [p for p in pal[k:] if p.t.strip(":")]
            if not resto:
                return None
            con_dos_puntos = pal[k - 1].t.endswith(":") or pal[k].t.startswith(":")
            texto = " ".join(p.t for p in resto).lstrip(": ")
            valido = spec.clase in ESTRICTAS and normalizar_valor(spec.clase, texto)
            # sin dos puntos, un texto libre solo vale si el rótulo abre la línea y lo que sigue no es otro rótulo
            libre = i == 0 and spec.clase in {"nombre", "texto"} and not es_rotulo(Segmento(resto, seg.pagina), etiquetas)
            if con_dos_puntos or valido or libre:
                return texto, Segmento(resto, seg.pagina)
    return None


def recortar(seg: Segmento) -> Segmento:
    """Corta el valor donde empieza el rótulo siguiente de la misma línea:
    '12.345.678-5 Fecha nacimiento: 3 Marzo 1990' → '12.345.678-5'."""
    pal = seg.palabras
    j = next((i for i, p in enumerate(pal) if i > 0 and p.t.endswith(":")), None)
    if j is None:
        return seg
    inicio = j
    while inicio - 1 > 0 and j - inicio < 2 and pal[inicio - 1].t.rstrip(":").isalpha():
        inicio -= 1
    return Segmento(pal[:inicio], seg.pagina) if inicio > 0 else seg


def ajustar_a_valor(seg: Segmento, valor: str) -> Segmento:
    """Reduce el segmento al tramo contiguo de palabras que forman el valor (para que la caja sea la del valor)."""
    fichas = {t.strip(",.;:") for t in sin_tildes(valor).upper().split()}
    mejor: list = []
    actual: list = []
    for w in seg.palabras:
        if sin_tildes(w.t).upper().strip(",.;:") in fichas:
            actual.append(w)
            mejor = max(mejor, actual, key=len)
        else:
            actual = []
    return Segmento(list(mejor), seg.pagina) if mejor else seg


def _campo(spec: CampoSpec, tipo: TipoSpec, valor_seg: Segmento | None, texto: str, metodo: str, estrategia: str,
           evidencia: str, pagina: PaginaTrabajo | None) -> Campo:
    if valor_seg is not None and valor_seg.texto == texto:  # segmento completo: se recorta el rótulo siguiente
        valor_seg = recortar(valor_seg)
        texto = valor_seg.texto
    if spec.clase == "rut" and (m := RE_RUT.search(texto)):
        texto = m.group(0)
    if valor_seg is not None and valor_seg.texto != texto:
        valor_seg = ajustar_a_valor(valor_seg, texto)
    con_caja = valor_seg is not None and pagina is not None and pagina.fuente_texto != "docx"
    return Campo(
        valor=texto,
        valor_normalizado=normalizar_valor(spec.clase, texto),
        pagina=pagina.n if pagina else None,
        bbox=redondear(valor_seg.bbox) if con_caja else None,
        conf=round(valor_seg.conf, 3) if valor_seg is not None else 1.0,
        metodo=metodo,
        regla_id=f"{tipo.clave}.{spec.nombre}.{estrategia}.v{tipo.version}",
        evidencia=evidencia,
    )


def _ubicar(doc: DocTrabajo, valor: str) -> tuple[Segmento | None, PaginaTrabajo | None]:
    """Segmento donde aparece un valor encontrado en el texto corrido (para dar página y caja)."""
    clave = sin_espacios(normalizar(valor))[:14]
    for p in doc.paginas:
        for s in p.segmentos:
            if clave and clave in sin_espacios(s.norm):
                return s, p
    return None, None


def _aceptable(spec: CampoSpec, texto: str) -> bool:
    """Un valor de clase estricta tiene que poder interpretarse (un RUT que no es RUT es otro dato)."""
    return spec.clase not in {"rut", "fecha", "anio", "clases"} or normalizar_valor(spec.clase, texto) is not None


def por_rotulo(doc: DocTrabajo, spec: CampoSpec, tipo: TipoSpec, etiquetas: set[str]) -> Campo | None:
    """Rótulo que ocupa su propio segmento, con el valor a la derecha o debajo."""
    for alias in spec.etiquetas_norm:
        alias_ns = sin_espacios(alias)
        for p in doc.paginas:
            for seg in p.segmentos:
                if sin_espacios(seg.norm) != alias_ns:
                    continue
                v = valor_cercano(seg, p.segmentos, etiquetas)
                if v and _aceptable(spec, v.texto):
                    return _campo(spec, tipo, v, v.texto, "zona", "rotulo", f"{seg.texto} → {v.texto}", p)
                if v is None and p.fuente_texto == "nativo" and p.ocr:
                    # el valor no está como texto nativo: se busca en el OCR del render (p. ej., campo rasterizado)
                    v = valor_cercano(seg, agrupar(p.ocr, p.n), etiquetas)
                    if v and _aceptable(spec, v.texto):
                        return _campo(spec, tipo, v, v.texto, "ocr", "rotulo_ocr", f"{seg.texto} → {v.texto} (OCR)", p)
    return None


def por_rotulo_en_linea(doc: DocTrabajo, spec: CampoSpec, tipo: TipoSpec, etiquetas: set[str]) -> Campo | None:
    """Rótulo y valor en la misma línea; los rótulos más largos primero ("Establecimiento educacional" antes que "Establecimiento")."""
    for alias in sorted(spec.etiquetas_norm, key=len, reverse=True):
        alias_ns = sin_espacios(alias)
        for p in doc.paginas:
            for seg in p.segmentos:
                if alias_ns in sin_espacios(seg.norm) and sin_espacios(seg.norm) != alias_ns:
                    r = _en_linea(seg, alias_ns, spec, etiquetas)
                    if r and _aceptable(spec, r[0]):
                        return _campo(spec, tipo, r[1], r[0], "zona", "en_linea", seg.texto, p)
    return None


def por_regex(doc: DocTrabajo, spec: CampoSpec, tipo: TipoSpec) -> Campo | None:
    if spec.clase == "clases":
        # hoja de vida: varias líneas "PRIMERA Clase : B"; se juntan todas
        encontradas, primera = [], None
        for p in doc.paginas:
            for s in p.segmentos + [Segmento(a.palabras + b.palabras, p.n) for a, b in zip(p.segmentos, p.segmentos[1:])]:
                for m in RE_CLASES_ROTULO.finditer(s.norm):
                    encontradas.append(m.group(1))
                    primera = primera or (s, p)
        if encontradas:
            texto = ", ".join(sorted(set(normalizar_valor("clases", " ".join(encontradas)) or [])))
            return _campo(spec, tipo, primera[0], texto, "regex", "clases", f"{len(encontradas)} línea(s) 'Clase'", primera[1])
        return None
    for p in doc.paginas:
        for seg in p.segmentos:
            if spec.clase == "rut":
                m = RE_RUT.search(seg.texto)
                if m and rut_valido(m.group(0)):
                    return _campo(spec, tipo, seg, m.group(0), "regex", "rut", seg.texto, p)
            elif spec.clase in ESTRICTAS - {"codigo"}:
                seg_ns = sin_espacios(seg.norm)
                for alias in spec.etiquetas_norm:
                    alias_ns = sin_espacios(alias)
                    if len(alias_ns) > 4 and alias_ns in seg_ns:
                        resto = seg_ns.split(alias_ns, 1)[1]
                        if resto and normalizar_valor(spec.clase, resto):
                            return _campo(spec, tipo, seg, resto, "regex", "frase", seg.texto, p)
    if tipo.clave == "certificado_antecedentes" and spec.nombre == "resultado":
        texto = normalizar(doc.texto)
        for frase, valor in (("NO REGISTRA ANTECEDENTES", "Sin antecedentes"), ("SIN ANTECEDENTES", "Sin antecedentes"),
                             ("REGISTRA ANTECEDENTES", "Con antecedentes"), ("CON ANOTACIONES", "Con antecedentes")):
            if frase in texto:
                seg = next(s for p in doc.paginas for s in p.segmentos if frase in s.norm)
                pag = next(p for p in doc.paginas if seg in p.segmentos)
                return _campo(spec, tipo, seg, valor, "regex", "resultado", seg.texto, pag)
    return None


def por_patron(doc: DocTrabajo, spec: CampoSpec, tipo: TipoSpec) -> Campo | None:
    """Certificados en texto corrido: 'a don(ña) NOMBRE, RUN …', 'el título de … y fue aprobado'."""
    texto = " ".join(s.texto for p in doc.paginas for s in p.segmentos)
    for campo, patron in tipo.patrones:
        if campo != spec.nombre:
            continue
        m = re.search(patron, texto, re.I)
        if m and m.group(1).strip(" ,.:"):
            valor = _sin_repeticion(re.sub(r"\s+", " ", m.group(1).strip(" ,.:")))
            seg, pag = _ubicar(doc, valor)
            return _campo(spec, tipo, seg if seg and seg.texto != valor else None, valor, "regex", "frase", m.group(0)[:160], pag)
    return None


def _sin_repeticion(valor: str) -> str:
    """'ANA PÉREZ ANA PÉREZ' → 'ANA PÉREZ' (escaneos con el mismo texto dos veces en la línea)."""
    w = valor.split()
    mitad = len(w) // 2
    return " ".join(w[:mitad]) if len(w) % 2 == 0 and mitad and w[:mitad] == w[mitad:] else valor


def extraer(doc: DocTrabajo, tipo: str) -> dict[str, Campo]:
    spec = TIPOS.get(tipo)
    if not spec:
        return {}
    etiquetas = _etiquetas_conocidas()
    campos: dict[str, Campo] = {}
    for c in spec.campos:
        campo = (por_rotulo(doc, c, spec, etiquetas) or por_patron(doc, c, spec) or por_rotulo_en_linea(doc, c, spec, etiquetas)
                 or por_regex(doc, c, spec))
        if campo:
            campos[c.nombre] = campo
    if spec.completar:
        def crear(nombre: str, valor: str, evidencia: str, seg: Segmento | None = None) -> Campo:
            c = spec.campo(nombre) or CampoSpec(nombre, ())
            pag = next((p for p in doc.paginas if seg is not None and seg in p.segmentos), None)
            return _campo(c, spec, seg, valor, "regex", "estructura", evidencia, pag)

        campos.update(spec.completar(doc, campos, crear))
    # nombre completo a partir de nombres y apellidos
    if "nombre" not in campos and "nombres" in campos and "apellidos" in campos:
        n, a = campos["nombres"], campos["apellidos"]
        texto = f"{n.valor} {a.valor}"
        campos["nombre"] = Campo(valor=texto, valor_normalizado=normalizar_valor("nombre", texto), pagina=n.pagina, conf=min(n.conf, a.conf),
                                 metodo=n.metodo, regla_id=f"{tipo}.nombre.nombres_apellidos.v{spec.version}", evidencia="nombres + apellidos")
    return campos
