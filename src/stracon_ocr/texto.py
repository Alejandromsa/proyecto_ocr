"""Normalización de texto, RUT y fechas en español (Chile)."""
from __future__ import annotations

import re
import unicodedata
from datetime import date

MESES = {
    "ene": 1, "enero": 1, "feb": 2, "febrero": 2, "mar": 3, "marzo": 3, "abr": 4, "abril": 4,
    "may": 5, "mayo": 5, "jun": 6, "junio": 6, "jul": 7, "julio": 7, "ago": 8, "agosto": 8,
    "sep": 9, "sept": 9, "set": 9, "septiembre": 9, "setiembre": 9, "oct": 10, "octubre": 10,
    "nov": 11, "noviembre": 11, "dic": 12, "diciembre": 12,
}


def sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalizar(s: str) -> str:
    """Mayúsculas, sin tildes, sin signos de puntuación sueltos y con espacios simples."""
    s = sin_tildes(s).upper().replace("°", " ").replace("º", " ")
    s = re.sub(r"\b(?:NRO|NUM|NUMERO)\b\.?", "N", s)  # N°, Nº, NRO., NÚMERO → N
    s = re.sub(r"[:;,]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def sin_espacios(s: str) -> str:
    """Para comparar rótulos que el PDF o el OCR escriben pegados ("FECHAEMISIÓN", "LICENCIADE CONDUCTOR")."""
    return s.replace(" ", "")


def normalizar_nombre(s: str) -> str:
    s = re.sub(r"[^A-Z ]", " ", sin_tildes(s).upper())
    palabras = [p for p in s.split() if p not in {"DE", "DEL", "LA", "LAS", "LOS", "Y"}]
    return " ".join(palabras)


# --- RUT --------------------------------------------------------------------------------------

# El OCR a veces mete espacios junto a los puntos ("16. 276.101-3"): se toleran
RE_RUT = re.compile(r"(?<![\d.])(\d{1,2}(?:\s?\.\s?)?\d{3}(?:\s?\.\s?)?\d{3})\s?-\s?([\dkK])(?![\dA-Za-z])")


def dv_rut(cuerpo: int) -> str:
    return calculo_dv(cuerpo)["dv"]


def calculo_dv(cuerpo: int) -> dict:
    """Módulo 11 paso a paso: los dígitos de derecha a izquierda por 2, 3, 4, 5, 6, 7, 2, 3…"""
    terminos, m = [], 2
    for d in reversed(str(cuerpo)):
        terminos.append((int(d), m))
        m = 2 if m == 7 else m + 1
    suma = sum(d * p for d, p in terminos)
    r = 11 - suma % 11
    dv = {11: "0", 10: "K"}.get(r, str(r))
    texto = (" + ".join(f"{d}×{p}" for d, p in terminos) + f" = {suma}; {suma} mod 11 = {suma % 11}; 11 − {suma % 11} = {r}"
             + (f" → {dv}" if r in (10, 11) else ""))
    return {"dv": dv, "suma": suma, "calculo": texto}


def normalizar_rut(s: str) -> str | None:
    m = RE_RUT.search(s or "")
    if not m:
        return None
    return f"{int(re.sub(r'[^0-9]', '', m.group(1)))}-{m.group(2).upper()}"


# Los RUT de personas naturales están bajo 50 millones; las empresas e instituciones usan de 50 a 99 millones
# (p. ej. la universidad que certifica una competencia trae su RUT antes que el RUN del postulante)
RUT_MAX_PERSONA = 50_000_000


def es_rut_persona(rut: str | None) -> bool:
    n = normalizar_rut(rut or "")
    return bool(n) and int(n.split("-")[0]) < RUT_MAX_PERSONA


def rut_valido(rut: str | None) -> bool:
    n = normalizar_rut(rut or "")
    if not n:
        return False
    cuerpo, dv = n.split("-")
    return dv_rut(int(cuerpo)) == dv


def diagnostico_rut(texto: str | None) -> dict:
    """Por qué un RUT es o no válido, para mostrarlo al revisor."""
    leido = (texto or "").strip()
    n = normalizar_rut(leido)
    if not n:
        return {"valido": False, "motivo": "formato", "leido": leido}
    cuerpo, dv_doc = n.split("-")
    c = calculo_dv(int(cuerpo))
    return {"valido": c["dv"] == dv_doc, "motivo": None if c["dv"] == dv_doc else "digito_verificador", "leido": leido,
            "rut": formatear_rut(n), "cuerpo": formatear_rut(n).rsplit("-", 1)[0], "dv_documento": dv_doc,
            "dv_calculado": c["dv"], "calculo": c["calculo"]}


def formatear_rut(rut_normalizado: str) -> str:
    cuerpo, dv = rut_normalizado.split("-")
    return f"{int(cuerpo):,}".replace(",", ".") + f"-{dv}"


# --- Fechas -----------------------------------------------------------------------------------

_RE_FECHAS = [
    # 16 oct 2028 · 16 de octubre de 2028 · 16-OCT-2028 · 16deoctubrede2026 (PDF sin espacios) · 17 OCT2022 (cédula leída
    # por OCR: mes y año pegados; el año exige 4 dígitos seguidos de un no dígito, así "20 sep22026" no es fecha)
    (re.compile(r"(?<!\d)(\d{1,2})\s*(?:de\s*|[-/.\s])\s*([a-zA-ZáéíóúÁÉÍÓÚ]{3,10}?)\.?\s*(?:de\s*|[-/.\s])?\s*(\d{4})\b", re.I), "dmy_texto"),
    # 2026-10-21
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "ymd"),
    # 21/10/2026 · 21-10-2026 · 21.10.2026
    (re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b"), "dmy"),
]


def buscar_fecha(s: str) -> tuple[date, str] | None:
    """Primera fecha reconocible en el texto: (fecha, texto que coincidió)."""
    for rx, forma in _RE_FECHAS:
        for m in rx.finditer(s or ""):
            try:
                if forma == "dmy_texto":
                    mes = MESES.get(sin_tildes(m.group(2)).lower())
                    if not mes:
                        continue
                    return date(int(m.group(3)), mes, int(m.group(1))), m.group(0)
                if forma == "ymd":
                    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), m.group(0)
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1))), m.group(0)
            except ValueError:
                continue
    return None


def normalizar_fecha(s: str) -> str | None:
    r = buscar_fecha(s)
    return r[0].isoformat() if r else None


def buscar_anio(s: str) -> str | None:
    m = re.search(r"\b(19[3-9]\d|20[0-4]\d)\b", s or "")
    return m.group(1) if m else None
