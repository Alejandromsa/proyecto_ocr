"""Observaciones: el documento no se puede validar bien y el revisor debe pedir una aclaración al postulante.

No son adulteraciones (eso son las señales). Cada observación trae el motivo, la acción sugerida y un mensaje listo
para el postulante. Fuentes: calidad de imagen (`calidad.py`) y contenido (reglas de este módulo).
"""
from __future__ import annotations

from typing import Any

from . import calidad
from .contrato import Campo, Clasificacion, Documento, Observacion, Senal, Validacion
from .forense.pdf_estructura import MARCAS_ELECTRONICAS
from .texto import normalizar, sin_espacios
from .tipos import TIPOS
from .trabajo import DocTrabajo


def analizar(doc: DocTrabajo, clas: Clasificacion, campos: dict[str, Campo], senales: list[Senal],
             validaciones: list[Validacion], reglas: dict[str, Any]) -> list[Observacion]:
    obs: list[Observacion] = []
    obs += calidad.observaciones(doc, reglas)
    obs += de_contenido(doc, clas, campos, senales, validaciones, reglas)
    # una sola observación por id (la primera es la más específica)
    vistas, unicas = set(), []
    for o in obs:
        if o.id not in vistas:
            vistas.add(o.id)
            unicas.append(o)
    return unicas


NOMBRES_CAMPO = {
    "rut": "RUT", "nombre": "nombre", "numero_documento": "número de documento", "fecha_vencimiento": "fecha de vencimiento",
    "folio": "folio", "codigo_verificacion": "código de verificación", "fecha_emision": "fecha de emisión",
}
# marcas del reverso (normalizadas y sin espacios)
REVERSO_LICENCIA = ("PRIMEROTORGAMIENTO", "RESTRICCIONES", "FIRMADELCONDUCTOR", "DIRECTORDETRANSITO")
TIPOS_ELECTRONICOS = {"certificado_antecedentes", "hoja_vida_conductor", "certificado_estudios_medios",
                      "certificado_estudios_profesionales"}


def _nombre_tipo(tipo: str) -> str:
    spec = TIPOS.get(tipo)
    return spec.nombre if spec else "documento"


def de_contenido(doc: DocTrabajo, clas: Clasificacion, campos: dict[str, Campo], senales: list[Senal],
                 validaciones: list[Validacion], reglas: dict[str, Any]) -> list[Observacion]:
    """Tipo no reconocido, datos obligatorios faltantes, solo un lado del documento, imagen de un documento electrónico,
    documento vencido."""
    obs: list[Observacion] = []
    tipo = clas.tipo
    spec = TIPOS.get(tipo)
    texto = sin_espacios(normalizar(doc.texto))
    ids_senales = {s.id for s in senales}

    if tipo == "desconocido":
        if doc.paginas:
            obs.append(Observacion(
                id="contenido.tipo_no_reconocido", motivo="no_reconocido", accion="solicitar_reenvio",
                detalle="No se reconoció qué documento es" + ("" if texto else ": no se pudo leer texto en el archivo"),
                mensaje_postulante=f"No pudimos identificar el documento «{doc.nombre}». Por favor verifique que corresponda al "
                                   "documento solicitado y envíelo nuevamente completo y legible.",
                evidencia={"alternativas": [a.model_dump() for a in clas.alternativas], "caracteres_leidos": len(texto)},
            ))
        return obs

    nombre = _nombre_tipo(tipo)
    faltan = [c for c in (spec.obligatorios if spec else ()) if c not in campos]

    # solo el reverso: licencia o cédula sin RUT con las marcas del reverso
    marcas_reverso = [m for m in REVERSO_LICENCIA if m in texto] if tipo == "licencia_conducir" else []
    mrz = "IDCHL" in texto or "<<" in doc.texto  # banda MRZ del reverso de la cédula
    reverso = "rut" not in campos and (len(marcas_reverso) >= 1 if tipo == "licencia_conducir"
                                       else tipo == "cedula_identidad" and mrz)
    if reverso:
        obs.append(Observacion(
            id="contenido.solo_reverso", motivo="incompleto", accion="solicitar_reenvio",
            detalle=f"Se recibió solo el reverso de la {nombre.lower()}: falta el anverso (frente), que trae el RUT y las fechas",
            mensaje_postulante=f"Recibimos solo el reverso de su {nombre.lower()}. Por favor envíe también el frente (anverso), "
                               "con el RUT y las fechas legibles.",
            evidencia={"marcas_reverso": marcas_reverso, "mrz": mrz},
        ))
    elif faltan:
        legibles = ", ".join(NOMBRES_CAMPO.get(c, c.replace("_", " ")) for c in faltan)
        obs.append(Observacion(
            id="contenido.campos_faltantes", motivo="incompleto", accion="solicitar_reenvio",
            detalle=f"No se pudo leer en el documento: {legibles}. Sin estos datos no se puede validar",
            mensaje_postulante=f"No pudimos leer {legibles} en su {nombre.lower()}. Por favor envíe nuevamente el documento "
                               "completo, sin cortes y con buena luz.",
            evidencia={"faltan": faltan, "encontrados": sorted(campos)},
        ))

    # imagen o escaneo de un documento que el emisor entrega como PDF electrónico
    marcas = [m for m in MARCAS_ELECTRONICAS if m in texto]
    if "pdf.solo_imagen" in ids_senales or (doc.origen == "imagen" and tipo in TIPOS_ELECTRONICOS and marcas):
        obs.append(Observacion(
            id="contenido.imagen_de_electronico", motivo="no_verificable", accion="solicitar_original",
            detalle=f"Se recibió una imagen o un escaneo de un documento electrónico ({nombre}): sin el PDF original no se "
                    "puede verificar la firma electrónica ni la integridad del archivo",
            mensaje_postulante=f"Necesitamos el PDF original de su {nombre.lower()}, tal como lo descarga del sitio del emisor "
                               "(no una foto, un escaneo ni una impresión escaneada).",
            evidencia={"origen": doc.origen, "marcas_documento_electronico": marcas},
        ))

    # documento vencido
    for v in validaciones:
        if v.resultado == "falla" and v.regla.startswith("vigencia."):
            if v.regla == "vigencia.antecedentes":
                maximo = v.evidencia.get("maximo_dias", reglas["umbrales"]["dias_vigencia_antecedentes"])
                mensaje = (f"Su certificado de antecedentes fue emitido hace más de {maximo} días. Por favor envíe un certificado "
                           f"emitido hace menos de {maximo} días (se obtiene gratis en www.registrocivil.cl).")
            elif v.regla == "vigencia.licencia" and tipo == "hoja_vida_conductor":
                mensaje = ("Su hoja de vida del conductor indica una fecha de control vencida. Por favor envíe una hoja de vida "
                           "reciente y su licencia vigente.")
            elif v.regla == "vigencia.licencia":
                mensaje = "Su licencia de conducir figura vencida. Por favor envíe su licencia vigente (frente y reverso)."
            else:
                mensaje = f"Su {nombre.lower()} figura vencido. Por favor envíe un certificado vigente."
            obs.append(Observacion(
                id="contenido.vencido", motivo="vigencia", accion="solicitar_vigente", campo=v.campo,
                detalle=v.detalle or f"{nombre} vencido", mensaje_postulante=mensaje, evidencia=v.evidencia,
            ))
            break
    return obs


def de_expediente(por_tipo: dict[str, Documento], faltantes: list[str]) -> list[Observacion]:
    """Observaciones del expediente: documentos que faltan y las observaciones de los documentos vigentes."""
    obs = [Observacion(
        id="expediente.documento_faltante", motivo="faltante", accion="solicitar_documento", documento=t,
        detalle=f"Falta el documento: {_nombre_tipo(t)}",
        mensaje_postulante=f"Falta su {_nombre_tipo(t).lower()}. Por favor envíelo para completar su expediente.",
    ) for t in faltantes]
    for tipo, d in por_tipo.items():
        for o in d.observaciones:
            obs.append(o.model_copy(update={"documento": tipo}))
    return obs


def archivo_ilegible(nombre: str, error: str) -> Observacion:
    """El archivo no se pudo abrir (dañado, protegido con contraseña, vacío)."""
    return Observacion(
        id="archivo.ilegible", motivo="archivo", accion="solicitar_reenvio",
        detalle=f"No se pudo abrir el archivo {nombre}: {error}",
        mensaje_postulante=f"No pudimos abrir el archivo «{nombre}». Por favor envíelo nuevamente en PDF o como foto (JPG), "
                           "sin contraseña.",
        evidencia={"error": error},
    )
