from __future__ import annotations

import re

from rapidfuzz import fuzz

from ..texto import normalizar
from . import tarjeta
from .base import NOMBRE, RUT, CampoSpec, TipoSpec

# número de documento de la cédula: 9 caracteres en grupos de 3 ("531.045.217", "B6F.407.047"); no lleva guion (el RUN sí)
RE_NUMERO_DOCUMENTO = re.compile(r"(?<![\w.])([0-9A-Z]{3}\.\d{3}\.\d{3})(?![\w.-])")
ROTULOS = {
    "apellidos": ("APELLIDOS",),
    "nombres": ("NOMBRES",),
    "nacionalidad": ("NACIONALIDAD", "NACIONALIDAD SEXO"),
    "sexo": ("SEXO",),
    "nacimiento": ("FECHA DE NACIMIENTO", "FECHA DE NACIMIENTO NUMERO DOCUMENTO"),
    "numero": ("NUMERO DOCUMENTO",),
    "emision": ("FECHA DE EMISION", "FECHA DE EMISION FECHA DE VENCIMIENTO"),
    "vencimiento": ("FECHA DE VENCIMIENTO",),
    "firma": ("FIRMA DEL TITULAR",),
}


def _pagina_cedula(doc):
    """Página con la cédula (en un PDF con licencia y cédula, la que trae sus rótulos o la MRZ)."""
    mejor, puntos = None, -1
    for p in doc.paginas:
        t = normalizar(" ".join(s.texto for s in p.segmentos))
        n = sum(k in t for k in ("CEDULA", "IDENTIDAD", "APELLIDOS", "NACIONALIDAD", "FIRMA DEL TITULAR", "INCHL", "IDCHL", "NACIO EN"))
        if n > puntos:
            mejor, puntos = p, n
    return mejor


def _bloque(r, segs, lineas):
    if r is None:
        return []
    if r.resto is not None and tarjeta.es_nombre(r.resto):
        return [r.resto]
    return tarjeta.debajo(r, segs, tarjeta.es_nombre, ROTULOS, lineas=lineas)


def _parecidos(a: str, b: str) -> bool:
    a, b = normalizar(a), normalizar(b)
    return bool(a and b) and (fuzz.ratio(a, b) >= 85 or a.startswith(b) or b.startswith(a))


def completar(doc, campos: dict, crear) -> dict:
    """Cédula (formato 2013 en adelante): rótulos tenues con el valor debajo, dos columnas que el OCR suele juntar en una
    línea ("03 JUN 1969 531.045.217") y la MRZ del reverso con dígitos de control.

    - número de documento: el patrón de 9 caracteres en grupos de 3 que se ve impreso; si no se lee, el de la MRZ cuando
      su dígito de control cuadra.
    - fechas: nacimiento < emisión < vencimiento, así que se ordenan las fechas del anverso; la MRZ las confirma.
    - nombre: NOMBRES + APELLIDOS (dos líneas); la MRZ completa los apellidos cuando el OCR pierde una línea.
    Calibración con 7 cédulas reales (oct 2026): antes no se extraía el número de documento en ninguna.
    """
    from ..codigos import buscar_mrz

    nuevos: dict = {}
    pagina = _pagina_cedula(doc)
    if pagina is None:
        return nuevos
    segs = pagina.segmentos
    mrz = buscar_mrz(doc)
    md = mrz.datos if mrz else {}
    ctl = md.get("controles", {})
    rots = {}
    for r in tarjeta.rotulos(segs, ROTULOS):
        rots.setdefault(r.nombre, r)

    # número de documento
    actual = campos.get("numero_documento")
    if not actual or not actual.valor_normalizado or not RE_NUMERO_DOCUMENTO.search(actual.valor or ""):
        for s in segs:
            m = RE_NUMERO_DOCUMENTO.search(s.texto.upper())
            if m:
                nuevos["numero_documento"] = crear("numero_documento", m.group(1), s.texto, s)
                break
        else:
            if md.get("numero_documento") and ctl.get("numero_documento"):
                n = md["numero_documento"]
                nuevos["numero_documento"] = crear("numero_documento", f"{n[:3]}.{n[3:6]}.{n[6:9]}", "MRZ (dígito de control correcto)",
                                                   None, metodo="mrz")
            elif actual is not None:
                nuevos["numero_documento"] = None

    # fechas del anverso (sin la línea "Nació en")
    fechas = []
    for s in segs:
        if "NACIO" in s.norm:
            continue
        for f, texto in tarjeta.fechas_en(s):
            fechas.append((f, texto, s))
    distintas = sorted({f[0] for f in fechas})
    nac_mrz = md.get("fecha_nacimiento") if ctl.get("fecha_nacimiento") else None
    venc_mrz = md.get("fecha_vencimiento") if ctl.get("fecha_vencimiento") else None

    def primera(fecha):
        return next(f for f in fechas if f[0] == fecha)

    if len(distintas) >= 2:
        nac, venc = primera(distintas[0]), primera(distintas[-1])
        nuevos["fecha_nacimiento"] = crear("fecha_nacimiento", nac[1], f"fecha menor del anverso: {nac[2].texto}", nac[2])
        nuevos["fecha_vencimiento"] = crear("fecha_vencimiento", venc[1], f"fecha mayor del anverso: {venc[2].texto}", venc[2])
        if len(distintas) >= 3:
            emi = primera(distintas[-2])
            nuevos["fecha_emision"] = crear("fecha_emision", emi[1], f"fecha intermedia del anverso: {emi[2].texto}", emi[2])
    else:
        if nac_mrz and "fecha_nacimiento" not in campos:
            nuevos["fecha_nacimiento"] = crear("fecha_nacimiento", nac_mrz, "MRZ (dígito de control correcto)", None, metodo="mrz")
        if venc_mrz and "fecha_vencimiento" not in campos:
            nuevos["fecha_vencimiento"] = crear("fecha_vencimiento", venc_mrz, "MRZ (dígito de control correcto)", None, metodo="mrz")
    for k in ("fecha_emision",):
        c = campos.get(k)
        if k not in nuevos and c is not None and len(distintas) >= 2 and c.valor_normalizado in (distintas[0].isoformat(), distintas[-1].isoformat()):
            nuevos[k] = None  # el rótulo genérico tomó la fecha de nacimiento o de vencimiento

    # nombres y apellidos
    nombres = _bloque(rots.get("nombres"), segs, 1)
    apellidos = _bloque(rots.get("apellidos"), segs, 2)
    texto_n = tarjeta.limpiar_nombre(" ".join(s.texto for s in nombres))
    texto_a = tarjeta.limpiar_nombre(" ".join(s.texto for s in apellidos))
    ap_mrz, no_mrz = md.get("apellidos") or "", md.get("nombres") or ""
    if ap_mrz and (not texto_a or (len(ap_mrz.split()) > len(texto_a.split())
                                   and all(any(_parecidos(w, m) for m in ap_mrz.split()) for w in texto_a.split()))):
        texto_a = ap_mrz  # el OCR perdió una de las dos líneas de apellidos (la MRZ no trae tildes ni Ñ)
    if no_mrz and not texto_n:
        texto_n = no_mrz
    if texto_n and texto_a and not _parecidos(texto_n, texto_a):
        ref = nombres[0] if nombres else (apellidos[0] if apellidos else None)
        nuevos["nombres"] = crear("nombres", texto_n, "rótulo NOMBRES" if nombres else "MRZ", nombres[0] if nombres else None)
        nuevos["apellidos"] = crear("apellidos", texto_a, "rótulo APELLIDOS" if apellidos else "MRZ", apellidos[0] if apellidos else None)
        nuevos["nombre"] = crear("nombre", f"{texto_n} {texto_a}", "nombres + apellidos", ref)

    # nacionalidad y sexo ("CHILENA M" en una línea)
    r = rots.get("nacionalidad")
    v = _bloque(r, segs, 1) if r else []
    if v:
        partes = normalizar(v[0].texto).split()
        if partes and partes[0].isalpha() and len(partes[0]) > 3:
            nuevos["nacionalidad"] = crear("nacionalidad", partes[0], r.seg.texto, v[0])
        if len(partes) > 1 and partes[-1] in {"M", "F"}:
            nuevos["sexo"] = crear("sexo", partes[-1], r.seg.texto, v[0])
    if "nacionalidad" not in nuevos and md.get("nacionalidad"):
        nuevos["nacionalidad"] = crear("nacionalidad", md["nacionalidad"], "MRZ", None, metodo="mrz")
    if "sexo" not in nuevos and md.get("sexo") in {"M", "F"}:
        nuevos["sexo"] = crear("sexo", md["sexo"], "MRZ", None, metodo="mrz")
    return nuevos


TIPO = TipoSpec(
    clave="cedula_identidad",
    nombre="Cédula de identidad",
    titulos=("Cédula de identidad", "Cédula de identidad para extranjeros"),
    palabras_clave=("República de Chile", "Registro Civil", "N° de documento", "Fecha de nacimiento", "Nacionalidad", "IDCHL", "Sexo",
                    "INCHL", "Nació en", "Profesión", "Firma del titular", "Número documento"),
    campos=(
        NOMBRE,
        RUT,
        CampoSpec("nombres", ("Nombres",), "nombre"),
        CampoSpec("apellidos", ("Apellidos",), "nombre"),
        CampoSpec("numero_documento", ("N° de documento", "Número de documento", "N° documento", "Documento"), "codigo", clave=True),
        CampoSpec("fecha_nacimiento", ("Fecha de nacimiento",), "fecha", clave=True),
        CampoSpec("fecha_emision", ("Fecha de emisión",), "fecha"),
        CampoSpec("fecha_vencimiento", ("Fecha de vencimiento", "Vencimiento"), "fecha", clave=True),
        CampoSpec("nacionalidad", ("Nacionalidad",)),
        CampoSpec("sexo", ("Sexo",)),
    ),
    completar=completar,
    obligatorios=("rut", "numero_documento"),
)
