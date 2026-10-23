"""Reglas de validación por documento: RUT, MRZ, QR frente al OCR, fechas, vigencias y formatos."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from rapidfuzz import fuzz

from .contrato import Campo, Codigo, Validacion
from .texto import diagnostico_rut, normalizar_nombre


def _fecha(c: Campo | None) -> date | None:
    if c is None or not isinstance(c.valor_normalizado, str):
        return None
    try:
        return date.fromisoformat(c.valor_normalizado)
    except ValueError:
        return None


def _solo_alnum(s: Any) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(s or "").upper())


# (dato del código, campo del documento, forma de comparar)
COMPARACIONES = (
    ("run", "rut", "rut"),
    ("numero_documento", "numero_documento", "alnum"),
    ("fecha_nacimiento", "fecha_nacimiento", "fecha"),
    ("folio", "folio", "alnum"),
    ("codigo_verificacion", "codigo_verificacion", "alnum"),
    ("fecha_emision", "fecha_emision", "fecha"),
    ("nombre", "nombre", "nombre"),
)


def _iguales(dato: Any, visible: Any, como: str) -> bool:
    if como == "nombre":
        return fuzz.token_sort_ratio(normalizar_nombre(str(dato)), normalizar_nombre(str(visible))) >= 95
    if como in {"rut", "fecha"}:
        return str(dato) == str(visible)
    return _solo_alnum(dato) == _solo_alnum(visible)


def validar_rut(rut: Campo) -> Validacion:
    diag = diagnostico_rut(rut.valor)
    lectura = {"metodo_lectura": rut.metodo, "confianza_lectura": rut.conf, "pagina": rut.pagina, "evidencia_lectura": rut.evidencia}
    por_ocr = rut.metodo in {"ocr", "llm"} or rut.conf < 1.0
    aviso = " Se leyó por OCR: verificar en la imagen si algún dígito se leyó mal." if por_ocr else ""
    if diag["valido"]:
        return Validacion(regla="rut.modulo11", resultado="ok", campo="rut", evidencia=diag | lectura)
    if diag["motivo"] == "formato":
        return Validacion(regla="rut.modulo11", resultado="falla", campo="rut", evidencia=diag | lectura,
                          detalle=f"No se pudo interpretar '{diag['leido']}' como RUT (se espera 12.345.678-9).{aviso}")
    return Validacion(
        regla="rut.modulo11", resultado="falla", campo="rut", evidencia=diag | lectura,
        detalle=(f"El dígito verificador no corresponde: el documento dice {diag['rut']} y para {diag['cuerpo']} "
                 f"el cálculo módulo 11 da {diag['dv_calculado']} (el documento trae {diag['dv_documento']}).{aviso}"),
    )


def validar(tipo: str, campos: dict[str, Campo], codigos: list[Codigo], reglas: dict[str, Any], hoy: date | None = None) -> list[Validacion]:
    hoy = hoy or date.today()
    u = reglas["umbrales"]
    v: list[Validacion] = []

    rut = campos.get("rut")
    if rut and rut.valor:
        v.append(validar_rut(rut))
    else:
        v.append(Validacion(regla="rut.modulo11", resultado="no_aplica", campo="rut", detalle="No se encontró el RUT"))

    mrz = next((c for c in codigos if c.tipo == "mrz"), None)
    if mrz:
        fallidos = [k for k, ok in mrz.datos.get("controles", {}).items() if not ok]
        v.append(Validacion(regla="mrz.digitos_control", resultado="falla" if fallidos else "ok",
                            detalle=f"Dígitos de control incorrectos: {', '.join(fallidos)}" if fallidos else None))
    elif tipo == "cedula_identidad":
        v.append(Validacion(regla="mrz.digitos_control", resultado="no_aplica", detalle="No se encontró la MRZ (reverso no incluido o ilegible)"))

    # QR, PDF417 y MRZ (datos del emisor) frente a lo que se lee en el documento
    difs, iguales = [], []
    for c in codigos:
        if c.tipo not in {"qr", "pdf417", "mrz"}:
            continue
        for clave_codigo, campo, como in COMPARACIONES:
            dato, f = c.datos.get(clave_codigo), campos.get(campo)
            if not dato or not f or not f.valor:
                continue
            visible = f.valor_normalizado if como in {"rut", "fecha"} else f.valor
            if _iguales(dato, visible, como):
                iguales.append(campo)
            else:
                difs.append({"campo": campo, "codigo": dato, "documento": f.valor, "codigo_tipo": c.tipo})
    if difs or iguales:
        detalle = "; ".join(f"{d['campo']}: el {d['codigo_tipo'].upper()} dice '{d['codigo']}' y el documento '{d['documento']}'" for d in difs)
        v.append(Validacion(regla="qr.coincide_ocr", resultado="falla" if difs else "ok", detalle=detalle or None,
                            evidencia={"coinciden": sorted(set(iguales)), "difieren": difs}))
    elif any(c.tipo in {"qr", "pdf417"} for c in codigos):
        v.append(Validacion(regla="qr.coincide_ocr", resultado="no_aplica", detalle="El código no trae datos comparables (formato no reconocido)"))

    # fechas en orden y no futuras
    nac, emi = _fecha(campos.get("fecha_nacimiento")), _fecha(campos.get("fecha_emision"))
    venc = _fecha(campos.get("fecha_vencimiento")) or _fecha(campos.get("vigencia")) or _fecha(campos.get("fecha_control"))
    problemas = []
    if nac and nac > hoy:
        problemas.append("fecha de nacimiento en el futuro")
    if emi and emi > hoy + timedelta(days=1):
        problemas.append("fecha de emisión en el futuro")
    if nac and emi and nac >= emi:
        problemas.append("emisión anterior al nacimiento")
    if emi and venc and venc < emi:
        problemas.append("vencimiento anterior a la emisión")
    if nac or emi or venc:
        fechas = {"nacimiento": nac and nac.isoformat(), "emision": emi and emi.isoformat(),
                  "vencimiento": venc and venc.isoformat(), "hoy": hoy.isoformat()}
        v.append(Validacion(regla="fechas.logicas", resultado="falla" if problemas else "ok", detalle="; ".join(problemas) or None,
                            evidencia={k: x for k, x in fechas.items() if x}))

    if tipo == "certificado_antecedentes":
        if emi:
            dias = (hoy - emi).days
            maximo = u["dias_vigencia_antecedentes"]
            ok = dias <= maximo
            v.append(Validacion(regla="vigencia.antecedentes", resultado="ok" if ok else "falla", campo="fecha_emision",
                                detalle=f"Emitido el {emi.isoformat()}, hace {dias} días (máximo {maximo})",
                                evidencia={"fecha_emision": emi.isoformat(), "hoy": hoy.isoformat(), "dias": dias, "maximo_dias": maximo}))
        else:
            v.append(Validacion(regla="vigencia.antecedentes", resultado="no_aplica", campo="fecha_emision", detalle="Sin fecha de emisión"))
    for t, campo in (("licencia_conducir", "fecha_vencimiento"), ("hoja_vida_conductor", "fecha_control")):
        if tipo == t:
            f = _fecha(campos.get(campo))
            v.append(Validacion(regla="vigencia.licencia", resultado="no_aplica" if f is None else "ok" if f >= hoy else "falla", campo=campo,
                                detalle=f"Sin {campo.replace('_', ' ')} legible" if f is None else None if f >= hoy
                                else f"Vencida el {f.isoformat()} (hace {(hoy - f).days} días)",
                                evidencia={"fecha": f.isoformat(), "hoy": hoy.isoformat()} if f else {}))
    if tipo == "certificado_estudios_profesionales" and _fecha(campos.get("vigencia")):
        f = _fecha(campos["vigencia"])
        v.append(Validacion(regla="vigencia.certificado", resultado="ok" if f >= hoy else "falla", campo="vigencia",
                            detalle=None if f >= hoy else f"Vencido el {f.isoformat()} (hace {(hoy - f).days} días)",
                            evidencia={"vigencia": f.isoformat(), "hoy": hoy.isoformat()}))

    for clave, patron in reglas.get("formatos", {}).items():
        t, campo = clave.split(".", 1)
        if t != tipo:
            continue
        c = campos.get(campo)
        if not c or not c.valor:
            v.append(Validacion(regla=f"formato.{campo}", resultado="no_aplica", campo=campo, detalle="Campo no encontrado"))
            continue
        valor = c.valor.strip().upper()
        ok = re.match(patron, valor) is not None
        v.append(Validacion(regla=f"formato.{campo}", resultado="ok" if ok else "falla", campo=campo,
                            detalle=None if ok else f"'{c.valor}' no tiene el formato esperado para este emisor ({patron}). "
                                                    "Los formatos de reglas.json son los del mockup: revisar si el emisor real usa otro.",
                            evidencia={"valor": c.valor, "formato_esperado": patron, "metodo_lectura": c.metodo, "confianza_lectura": c.conf}))
    return v
