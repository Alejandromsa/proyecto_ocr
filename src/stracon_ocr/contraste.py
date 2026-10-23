"""Contraste por expediente: identidad, discrepancias entre documentos, fuentes a verificar y duplicados.

"Fuente no disponible" es un estado de la fuente, nunca una discrepancia.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from rapidfuzz import fuzz

from .contrato import Discrepancia, Documento, DocumentoRef, Duplicado, Expediente, Fuente, Identidad
from .observaciones import de_expediente
from .texto import normalizar_nombre, normalizar_rut, rut_valido


def _valor(d: Documento, campo: str) -> Any:
    c = d.campos.get(campo)
    return c.valor_normalizado if c and c.valor_normalizado not in (None, "", []) else None


def contrastar(expediente: str, docs: list[Documento], reglas: dict[str, Any], duplicados: list[Duplicado] | None = None) -> Expediente:
    u = reglas["umbrales"]
    por_tipo: dict[str, Documento] = {}
    # por tipo vale el documento con más datos (el anverso antes que el reverso) y, a igualdad, el último (aclaraciones)
    for d in sorted(docs, key=lambda d: (len(d.campos), d.procesado_en)):
        por_tipo[d.clasificacion.tipo] = d
    vigentes = list(por_tipo.values())
    exp = Expediente(
        expediente=expediente,
        documentos=[DocumentoRef(id=d.id, tipo=d.clasificacion.tipo, sha256=d.archivo.sha256, nombre=d.archivo.nombre) for d in vigentes],
        faltantes=[t for t in reglas["tipos_oficiales"] if t not in por_tipo],
        duplicados=duplicados or [],
        procesado_en=datetime.now().astimezone(),
    )
    # faltantes y observaciones de los documentos vigentes (un reverso suelto no se observa si llegó el anverso)
    exp.observaciones = de_expediente(por_tipo, exp.faltantes)

    cedula = por_tipo.get("cedula_identidad")
    rut_carpeta = normalizar_rut(expediente)
    rut_ref = (_valor(cedula, "rut") if cedula else None) or (rut_carpeta if rut_valido(rut_carpeta) else None)
    nombre_ref = None
    if cedula:
        nombre_ref = _valor(cedula, "nombre") or " ".join(filter(None, [_valor(cedula, "nombres"), _valor(cedula, "apellidos")])) or None
    if not nombre_ref:
        nombre_ref = next((_valor(d, "nombre") for d in vigentes if _valor(d, "nombre")), None)
    exp.identidad = Identidad(rut=rut_ref, nombre=nombre_ref, fecha_nacimiento=_valor(cedula, "fecha_nacimiento") if cedula else None)

    # RUT: igualdad exacta normalizada, entre documentos y frente a la carpeta
    ruts = {d.clasificacion.tipo: _valor(d, "rut") for d in vigentes if _valor(d, "rut")}
    if rut_carpeta and rut_valido(rut_carpeta):
        distintos = {t: r for t, r in ruts.items() if r != rut_carpeta}
        if distintos:
            exp.discrepancias.append(Discrepancia(
                campo="rut", documentos=list(distintos), valores=list(distintos.values()), severidad="alta",
                detalle=f"El RUT no coincide con el del expediente ({rut_carpeta})"))
    elif len(set(ruts.values())) > 1:
        exp.discrepancias.append(Discrepancia(campo="rut", documentos=list(ruts), valores=list(ruts.values()), severidad="alta",
                                              detalle="Los documentos traen RUT distintos"))

    # Nombre: rapidfuzz token_sort_ratio sobre nombres normalizados
    if nombre_ref:
        ref = normalizar_nombre(nombre_ref)
        for d in vigentes:
            n = _valor(d, "nombre")
            if not n or d is cedula:
                continue
            sim = fuzz.token_sort_ratio(ref, normalizar_nombre(n)) / 100
            if sim < u["nombre_discrepancia"]:
                exp.discrepancias.append(Discrepancia(campo="nombre", documentos=["referencia", d.clasificacion.tipo], valores=[nombre_ref, n],
                                                      similitud=round(sim, 3), severidad="alta", detalle="El nombre no coincide"))
            elif sim < u["nombre_posible_tipeo"]:
                exp.discrepancias.append(Discrepancia(campo="nombre", documentos=["referencia", d.clasificacion.tipo], valores=[nombre_ref, n],
                                                      similitud=round(sim, 3), severidad="media", detalle="Posible error de tipeo en el nombre"))

    # Fecha de nacimiento: igualdad exacta
    nacs = {d.clasificacion.tipo: _valor(d, "fecha_nacimiento") for d in vigentes if _valor(d, "fecha_nacimiento")}
    if len(set(nacs.values())) > 1:
        exp.discrepancias.append(Discrepancia(campo="fecha_nacimiento", documentos=list(nacs), valores=list(nacs.values()), severidad="alta",
                                              detalle="La fecha de nacimiento no coincide entre documentos"))

    # Clases de licencia: licencia frente a hoja de vida
    lic, hv = por_tipo.get("licencia_conducir"), por_tipo.get("hoja_vida_conductor")
    if lic and hv and _valor(lic, "clases") and _valor(hv, "clases") and set(_valor(lic, "clases")) != set(_valor(hv, "clases")):
        exp.discrepancias.append(Discrepancia(campo="clases", documentos=["licencia_conducir", "hoja_vida_conductor"],
                                              valores=[_valor(lic, "clases"), _valor(hv, "clases")], severidad="media",
                                              detalle="La clase de la licencia no coincide con la hoja de vida del conductor"))

    # Fuentes emisoras: sin API pública, quedan para el revisor con folio, código y enlace
    for tipo, f in reglas.get("verificacion_fuentes", {}).items():
        d = por_tipo.get(tipo)
        if not d:
            continue
        exp.fuentes.append(Fuente(documento=tipo, fuente=f["fuente"], estado="pendiente_revisor",
                                  folio=_valor(d, "folio") or _valor(d, "numero_certificado") or _valor(d, "numero_documento"),
                                  codigo_verificacion=_valor(d, "codigo_verificacion"), url=f.get("url")))
    return exp
