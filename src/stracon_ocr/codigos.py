"""QR y PDF417 (zxing-cpp) y MRZ TD1 de la cédula (dígitos de control ICAO 9303)."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import zxingcpp

from .contrato import Codigo
from .layout import redondear
from .texto import normalizar_rut, rut_valido
from .trabajo import DocTrabajo


def leer_codigos(doc: DocTrabajo) -> list[Codigo]:
    codigos: list[Codigo] = []
    for p in doc.paginas:
        if p.imagen is None:
            continue
        for b in zxingcpp.read_barcodes(p.imagen):
            pos = b.position
            xs = [pt.x for pt in (pos.top_left, pos.top_right, pos.bottom_right, pos.bottom_left)]
            ys = [pt.y for pt in (pos.top_left, pos.top_right, pos.bottom_right, pos.bottom_left)]
            caja = (min(xs) * p.escala, min(ys) * p.escala, max(xs) * p.escala, max(ys) * p.escala)
            formato = str(b.format).split(".")[-1].lower()
            tipo = "qr" if "qr" in formato else "pdf417" if "pdf417" in formato else "otro"
            datos, valido = interpretar_codigo(b.text)
            codigos.append(Codigo(tipo=tipo, contenido=b.text, datos=datos | {"formato": formato}, pagina=p.n, bbox=redondear(caja), valido=valido))
    mrz = buscar_mrz(doc)
    if mrz:
        codigos.append(mrz)
    return codigos


ACENTOS_SRCEI = {"A": "Á", "E": "É", "I": "Í", "O": "Ó", "U": "Ú", "N": "Ñ"}


def decodificar_srcei(texto: str) -> dict | None:
    """PDF417 de los certificados del Registro Civil (antecedentes, hoja de vida).

    Usa escapes propios: '%3' = '-', '%.' = ':', '$C'/'$G' = salto de línea y '$' + vocal = vocal con tilde.
    Cabecera: '$%$<tipo> <fecha emisión> <folio> <código de verificación>'.
    """
    if not texto.startswith("$%$"):
        return None
    t = texto.replace("%3", "-").replace("%.", ":")
    t = re.sub(r"\$[CG]", "\n", t[3:])
    t = re.sub(r"\$([AEIOUN])", lambda m: ACENTOS_SRCEI[m.group(1)], t)
    cab = re.match(r"\s*(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{6,15})\s+([0-9A-Fa-f]{8,16})", t)
    if not cab:
        return None
    datos = {"emisor": "Registro Civil", "tipo_certificado": cab.group(1), "fecha_emision": cab.group(2),
             "folio": cab.group(3), "codigo_verificacion": cab.group(4)}
    for clave, patron in (("nombre", r"NOMBRE\s*:\s*([^\n]+)"), ("run", r"R\.U\.N\.\s*:\s*([\d.]+-[\dkK])"),
                          ("fecha_nacimiento_texto", r"Fecha nacimiento\s*:\s*([^\n]+)")):
        m = re.search(patron, t)
        if m:
            datos[clave] = m.group(1).strip()
    if "run" in datos:
        datos["run"] = normalizar_rut(datos["run"]) or datos["run"]
    datos["texto"] = t.strip()[:2000]
    return datos


def interpretar_codigo(texto: str) -> tuple[dict, bool | None]:
    """QR de la cédula chilena (URL con RUN, tipo y número de serie) y PDF417 del Registro Civil.
    Otros formatos: no reconocidos (None), lo que no es adulteración."""
    srcei = decodificar_srcei(texto)
    if srcei:
        return srcei, rut_valido(srcei["run"]) if "run" in srcei else None
    datos: dict = {}
    if texto.lower().startswith("http"):
        u = urlparse(texto)
        q = {k.lower(): v[0] for k, v in parse_qs(u.query).items()}
        datos["url"] = texto
        datos["dominio"] = u.netloc
        if "run" in q:
            datos["run"] = normalizar_rut(q["run"]) or q["run"]
        if "serial" in q:
            datos["numero_documento"] = q["serial"]
        if "type" in q:
            datos["tipo_documento"] = q["type"]
        if "run" in datos:
            return datos, rut_valido(datos["run"])
        return datos, None
    rut = normalizar_rut(texto)
    if rut:
        datos["run"] = rut
        return datos, rut_valido(rut)
    return datos, None


# --- MRZ -----------------------------------------------------------------------------------------

_VALORES = {**{str(i): i for i in range(10)}, **{chr(65 + i): 10 + i for i in range(26)}, "<": 0}
RE_LINEA_MRZ = re.compile(r"^[A-Z0-9<]{28,32}$")


def digito_icao(s: str) -> str:
    pesos = (7, 3, 1)
    return str(sum(_VALORES.get(c, 0) * pesos[i % 3] for i, c in enumerate(s)) % 10)


def _limpiar(linea: str) -> str:
    return re.sub(r"\s+", "", linea.upper()).replace("«", "<").replace("‹", "<")


def buscar_mrz(doc: DocTrabajo) -> Codigo | None:
    for p in doc.paginas:
        lineas = [(_limpiar(s.texto), s) for s in p.segmentos]
        for i in range(len(lineas) - 2):
            trio = [lineas[i + k][0] for k in range(3)]
            if all(RE_LINEA_MRZ.match(l) for l in trio) and "<" in "".join(trio):
                return interpretar_td1([l.ljust(30, "<")[:30] for l in trio], p.n)
    return None


def interpretar_td1(l: list[str], pagina: int | None = None) -> Codigo:
    l1, l2, l3 = l
    controles = {
        "numero_documento": digito_icao(l1[5:14]) == l1[14],
        "fecha_nacimiento": digito_icao(l2[0:6]) == l2[6],
        "fecha_vencimiento": digito_icao(l2[8:14]) == l2[14],
        "compuesto": digito_icao(l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29]) == l2[29],
    }
    apellidos, _, nombres = l3.partition("<<")
    datos = {
        "tipo_documento": l1[0:2].replace("<", ""),
        "pais": l1[2:5],
        "numero_documento": l1[5:14].replace("<", ""),
        "opcional_1": l1[15:30].replace("<", " ").strip(),
        "fecha_nacimiento": _fecha_mrz(l2[0:6], futuro=False),
        "sexo": l2[7].replace("<", ""),
        "fecha_vencimiento": _fecha_mrz(l2[8:14], futuro=True),
        "nacionalidad": l2[15:18],
        "apellidos": apellidos.replace("<", " ").strip(),
        "nombres": nombres.replace("<", " ").strip(),
        "controles": controles,
    }
    run = re.search(r"(\d{7,8})<?([\dK])", l1[15:30])
    if run:
        datos["run"] = f"{int(run.group(1))}-{run.group(2)}"
    return Codigo(tipo="mrz", contenido="\n".join(l), datos=datos, pagina=pagina, valido=all(controles.values()))


def _fecha_mrz(s: str, futuro: bool) -> str | None:
    if not s.isdigit():
        return None
    yy, mm, dd = int(s[:2]), s[2:4], s[4:6]
    siglo = 2000 if (futuro or yy <= 30) else 1900
    return f"{siglo + yy}-{mm}-{dd}"
