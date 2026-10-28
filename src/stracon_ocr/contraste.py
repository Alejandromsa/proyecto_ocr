"""Contraste por expediente: identidad, discrepancias entre documentos, fuentes a verificar y duplicados.

"Fuente no disponible" es un estado de la fuente, nunca una discrepancia.

El expediente es un identificador opaco (p. ej. una carpeta de postulante dentro de una convocatoria): solo se usa como
RUT de referencia si tiene forma de RUT válido (como las carpetas de `datos/dorado/`). Si no, la identidad sale de los
documentos: el RUT más repetido y el nombre mejor respaldado.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from rapidfuzz import fuzz

from .contrato import Discrepancia, Documento, DocumentoRef, Duplicado, Expediente, Fuente, Identidad
from .observaciones import de_expediente
from .texto import normalizar_nombre, normalizar_rut, rut_valido

# documentos cuyo nombre es texto del emisor (PDF nativo): la mejor referencia para el nombre
TIPOS_NOMBRE_EMISOR = ("certificado_antecedentes", "hoja_vida_conductor", "validacion_emisor", "certificado_estudios_medios")
# clases profesionales (Ley 19.495): la tarjeta imprime las vigentes; la hoja de vida registra también las históricas
CLASES_PROFESIONALES = {"A1", "A2", "A3", "A4", "A5"}


# en un nombre leído por OCR de una foto, una similitud entre este valor y el umbral de discrepancia suele ser un error de
# lectura (una o dos letras cambiadas en un nombre o apellido): queda como media y no como alta
SIMILITUD_ALTA_OCR = 0.75


def _leido_por_ocr(d: Documento, campo: str) -> bool:
    c = d.campos.get(campo)
    pagina = next((p for p in d.paginas if c is not None and p.n == c.pagina), None)
    return c is not None and (c.metodo == "ocr" or (pagina is not None and pagina.fuente_texto == "ocr"))


def _valor(d: Documento, campo: str) -> Any:
    c = d.campos.get(campo)
    return c.valor_normalizado if c and c.valor_normalizado not in (None, "", []) else None


def _palabras_iguales(a: str, b: str) -> bool:
    """Misma palabra salvo un error de lectura, o una abreviada/truncada de la otra (la MRZ corta en 30 caracteres:
    "FRANCIS" por "FRANCISCO")."""
    if a == b:
        return True
    corta, larga = sorted((a, b), key=len)
    return (len(corta) >= 3 and larga.startswith(corta)) or (min(len(a), len(b)) >= 4 and fuzz.ratio(a, b) >= 80)


def similitud_nombres(a: str, b: str) -> tuple[float, str | None]:
    """Similitud entre dos nombres y, si son compatibles sin ser iguales, por qué.

    - iguales sin espacios: el OCR o el PDF pegó o separó palabras ("PRUEBASOTO", "A NA") → 1,0
    - todas las palabras del más corto están en el más largo (con 3+ palabras): falta el segundo apellido o un segundo
      nombre, o el orden es otro (APELLIDOS NOMBRES) → 1,0 con motivo "nombre incompleto"
    - si no, rapidfuzz token_sort_ratio (como antes)
    """
    na, nb = normalizar_nombre(a), normalizar_nombre(b)
    if not na or not nb:
        return 0.0, None
    if na.replace(" ", "") == nb.replace(" ", ""):
        return 1.0, None
    pa, pb = na.split(), nb.split()
    corto, largo = sorted((pa, pb), key=len)
    libres = list(largo)
    cubiertas = 0
    for w in corto:
        j = next((i for i, x in enumerate(libres) if _palabras_iguales(w, x)), None)
        if j is not None:
            cubiertas += 1
            libres.pop(j)
    if cubiertas == len(corto) and len(corto) >= 3:
        return 1.0, None if len(corto) == len(largo) else "nombre incompleto en uno de los documentos"
    if cubiertas == len(corto) and len(corto) == 2 and len(largo) >= 3:
        return 0.95, "solo nombre y un apellido en uno de los documentos"
    # mismas palabras con errores de lectura en algunas letras: promedio de la similitud de cada palabra con su mejor
    # pareja (penaliza las palabras que sobran en el más largo)
    libres = list(largo)
    suma = 0.0
    for w in corto:
        j = max(range(len(libres)), key=lambda i: fuzz.ratio(w, libres[i]))
        suma += fuzz.ratio(w, libres.pop(j))
    alineada = suma / len(largo) / 100
    return max(fuzz.token_sort_ratio(na, nb) / 100, alineada), None


def nombre_de_referencia(vigentes: list[Documento], cedula: Documento | None) -> str | None:
    """El nombre mejor respaldado: se agrupan los nombres compatibles entre sí y gana el grupo con más documentos (a
    igualdad, el que incluye texto del emisor en PDF nativo); dentro del grupo, la versión más completa."""
    candidatos = []
    for d in vigentes:
        n = _valor(d, "nombre")
        if d is cedula and not n:
            n = " ".join(filter(None, [_valor(d, "nombres"), _valor(d, "apellidos")])) or None
        if n:
            nativo = d.archivo.origen == "pdf_nativo" and d.clasificacion.tipo in TIPOS_NOMBRE_EMISOR
            candidatos.append((n, nativo))
    if not candidatos:
        return None
    mejor, puntos = None, (-1, -1)
    for n, _ in candidatos:
        grupo = [(m, nat) for m, nat in candidatos if similitud_nombres(n, m)[0] >= 0.97]
        p = (len(grupo), sum(nat for _, nat in grupo))
        if p > puntos:
            nativos = [m for m, nat in grupo if nat] or [m for m, _ in grupo]
            mejor, puntos = max(nativos, key=lambda m: len(normalizar_nombre(m).split())), p
    return mejor


def contrastar(expediente: str, docs: list[Documento], reglas: dict[str, Any], duplicados: list[Duplicado] | None = None) -> Expediente:
    u = reglas["umbrales"]
    por_tipo: dict[str, Documento] = {}
    # por tipo vale el documento con más datos (el anverso antes que el reverso) y, a igualdad, el último (aclaraciones)
    for d in sorted(docs, key=lambda d: (len(d.campos), d.procesado_en)):
        por_tipo[d.clasificacion.tipo] = d
    vigentes = [d for t, d in por_tipo.items() if t != "validacion_emisor"]
    copias = [d for d in docs if d.clasificacion.tipo == "validacion_emisor"]
    exp = Expediente(
        expediente=expediente,
        documentos=[DocumentoRef(id=d.id, tipo=d.clasificacion.tipo, sha256=d.archivo.sha256, nombre=d.archivo.nombre)
                    for d in por_tipo.values()],
        faltantes=[t for t in reglas["tipos_oficiales"] if t not in por_tipo],
        duplicados=duplicados or [],
        procesado_en=datetime.now().astimezone(),
    )
    # faltantes y observaciones de los documentos vigentes (un reverso suelto no se observa si llegó el anverso)
    exp.observaciones = de_expediente(por_tipo, exp.faltantes)

    cedula = por_tipo.get("cedula_identidad")
    rut_carpeta = normalizar_rut(expediente)  # solo si el expediente tiene forma de RUT (carpetas del mockup)
    rut_carpeta = rut_carpeta if rut_valido(rut_carpeta) else None
    ruts = {d.clasificacion.tipo: _valor(d, "rut") for d in vigentes if _valor(d, "rut")}
    validos = Counter(r for r in ruts.values() if rut_valido(r))
    rut_cedula = _valor(cedula, "rut") if cedula else None
    rut_ref = ((rut_cedula if rut_valido(rut_cedula) else None) or rut_carpeta
               or (validos.most_common(1)[0][0] if validos else None))
    nombre_ref = nombre_de_referencia(vigentes, cedula)
    exp.identidad = Identidad(rut=rut_ref, nombre=nombre_ref, fecha_nacimiento=_valor(cedula, "fecha_nacimiento") if cedula else None)

    # RUT: igualdad exacta normalizada, entre documentos y frente a la carpeta (si el expediente es un RUT)
    if rut_carpeta:
        distintos = {t: r for t, r in ruts.items() if r != rut_carpeta}
        if distintos:
            exp.discrepancias.append(Discrepancia(
                campo="rut", documentos=list(distintos), valores=list(distintos.values()), severidad="alta",
                detalle=f"El RUT no coincide con el del expediente ({rut_carpeta})"))
    elif len(set(ruts.values())) > 1:
        exp.discrepancias.append(Discrepancia(campo="rut", documentos=list(ruts), valores=list(ruts.values()), severidad="alta",
                                              detalle="Los documentos traen RUT distintos"))

    # Nombre: compatibilidad (orden, palabras pegadas, apellido omitido) y rapidfuzz token_sort_ratio
    if nombre_ref:
        for d in vigentes:
            n = _valor(d, "nombre")
            if not n:
                continue
            sim, motivo = similitud_nombres(nombre_ref, n)
            if sim < u["nombre_discrepancia"] and _leido_por_ocr(d, "nombre") and sim >= SIMILITUD_ALTA_OCR:
                exp.discrepancias.append(Discrepancia(
                    campo="nombre", documentos=["referencia", d.clasificacion.tipo], valores=[nombre_ref, n], similitud=round(sim, 3),
                    severidad="media", detalle="El nombre no coincide del todo; se leyó por OCR de una foto o escaneo: comparar en la imagen"))
            elif sim < u["nombre_discrepancia"]:
                exp.discrepancias.append(Discrepancia(campo="nombre", documentos=["referencia", d.clasificacion.tipo], valores=[nombre_ref, n],
                                                      similitud=round(sim, 3), severidad="alta", detalle="El nombre no coincide"))
            elif sim < u["nombre_posible_tipeo"]:
                exp.discrepancias.append(Discrepancia(
                    campo="nombre", documentos=["referencia", d.clasificacion.tipo], valores=[nombre_ref, n], similitud=round(sim, 3),
                    severidad="baja" if motivo else "media",
                    detalle=f"Nombre compatible pero no completo ({motivo})" if motivo else "Posible error de tipeo en el nombre"))

    # Fecha de nacimiento: igualdad exacta
    nacs = {d.clasificacion.tipo: _valor(d, "fecha_nacimiento") for d in vigentes if _valor(d, "fecha_nacimiento")}
    if len(set(nacs.values())) > 1:
        exp.discrepancias.append(Discrepancia(campo="fecha_nacimiento", documentos=list(nacs), valores=list(nacs.values()), severidad="alta",
                                              detalle="La fecha de nacimiento no coincide entre documentos"))

    # Clases de licencia: lo que imprime la licencia tiene que estar en la hoja de vida (el registro oficial). La hoja de
    # vida lista además las clases históricas (A2/A3 antiguas junto a A4/A5), así que no se exige igualdad
    lic, hv = por_tipo.get("licencia_conducir"), por_tipo.get("hoja_vida_conductor")
    if lic and hv and _valor(lic, "clases") and _valor(hv, "clases"):
        sobran = set(_valor(lic, "clases")) - set(_valor(hv, "clases"))
        if sobran:
            exp.discrepancias.append(Discrepancia(
                campo="clases", documentos=["licencia_conducir", "hoja_vida_conductor"],
                valores=[_valor(lic, "clases"), _valor(hv, "clases")], severidad="media",
                detalle=f"La licencia trae clase(s) {', '.join(sorted(sobran))} que no figuran en la hoja de vida del conductor"))

    # Fuentes emisoras: sin API pública quedan para el revisor con folio, código y enlace. Si el postulante adjuntó la
    # copia del portal de verificación del Registro Civil con el mismo folio, se compara: código y RUN iguales = coincide
    for tipo, f in reglas.get("verificacion_fuentes", {}).items():
        d = por_tipo.get(tipo)
        if not d:
            continue
        folio = _valor(d, "folio") or _valor(d, "numero_certificado") or _valor(d, "numero_documento")
        codigo = _valor(d, "codigo_verificacion")
        estado = "pendiente_revisor"
        copia = next((c for c in copias if folio and _valor(c, "folio") == folio), None)
        if copia is not None:
            iguales = codigo and _valor(copia, "codigo_verificacion") == codigo and _valor(copia, "rut") == _valor(d, "rut")
            estado = "coincide" if iguales else "no_coincide"
        exp.fuentes.append(Fuente(documento=tipo, fuente=f["fuente"], estado=estado, folio=folio, codigo_verificacion=codigo,
                                  url=f.get("url")))
    return exp
