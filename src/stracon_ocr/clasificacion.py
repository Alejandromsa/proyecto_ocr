"""Clasificación por reglas: títulos y frases típicas de cada tipo (capa 1 del análisis)."""
from __future__ import annotations

from .contrato import Alternativa, Clasificacion
from .texto import normalizar, sin_espacios
from .tipos import TIPOS
from .trabajo import DocTrabajo, PaginaTrabajo

PUNTOS_TITULO = 6.0  # el título exacto está entre los textos más grandes de la primera página
PUNTOS_TITULO_PREFIJO = 4.5  # un texto grande empieza con el título ("CERTIFICADO DE ESTUDIOS PARA FINES LABORALES")
PUNTOS_FRASE_TITULO = 3.0  # el título aparece en cualquier parte
PUNTOS_CLAVE = 1.0
PUNTOS_DECISIVA = 8.0  # frase que identifica el tipo aunque el documento repita el título de otro
PUNTAJE_MINIMO = 2.0  # p. ej. el reverso de una licencia: "Director de tránsito" + "Primer otorgamiento"
# para decir que un archivo trae además OTRO documento: su título y al menos dos frases típicas
PUNTAJE_OTRO_DOCUMENTO = PUNTOS_FRASE_TITULO + 2 * PUNTOS_CLAVE
# tarjetas que el postulante suele fotografiar juntas en la misma página
TARJETAS = {"cedula_identidad", "licencia_conducir"}


def _contiene(frase: str, texto_sin_espacios: str, texto_espaciado: str) -> bool:
    """Las palabras cortas ("Área", "Clase") se buscan como palabra completa para no encontrarlas dentro de otras."""
    n = normalizar(frase)
    return f" {n} " in texto_espaciado if len(n) <= 6 else sin_espacios(n) in texto_sin_espacios


def puntajes(paginas: list[PaginaTrabajo]) -> dict[str, float]:
    """Puntaje de cada tipo sobre un conjunto de páginas (el documento entero o una página)."""
    # todo se compara sin espacios: el OCR y algunos PDF los pierden o los agregan ("LICENCIADE CONDUCTOR")
    texto_espaciado = " " + normalizar("\n".join(s.texto for p in paginas for s in p.segmentos)) + " "
    texto = sin_espacios(texto_espaciado)
    primera = next((p for p in paginas if p.segmentos), None)
    grandes: list[str] = []
    if primera:
        por_alto = sorted(primera.segmentos, key=lambda s: s.alto, reverse=True)[:6]
        grandes = [sin_espacios(s.norm) for s in por_alto]
    salida: dict[str, float] = {}
    for clave, spec in TIPOS.items():
        p = 0.0
        titulos = [sin_espacios(normalizar(t)) for t in spec.titulos]
        if any(t == g for t in titulos for g in grandes):
            p += PUNTOS_TITULO
        elif any(g.startswith(t) for t in titulos for g in grandes):
            p += PUNTOS_TITULO_PREFIJO
        elif any(t in texto for t in titulos):
            p += PUNTOS_FRASE_TITULO
        p += sum(PUNTOS_CLAVE for k in spec.palabras_clave if _contiene(k, texto, texto_espaciado))
        p += sum(PUNTOS_DECISIVA for k in spec.decisivas if sin_espacios(normalizar(k)) in texto)
        salida[clave] = p
    return salida


def clasificar(doc: DocTrabajo) -> Clasificacion:
    orden = sorted(puntajes(doc.paginas).items(), key=lambda kv: kv[1], reverse=True)
    mejor, puntos = orden[0]
    # confianza = margen frente al segundo (0,5 = empate)
    alternativas = [Alternativa(tipo=k, confianza=round(v / (puntos + v), 3)) for k, v in orden[1:3] if v > 0]
    if puntos < PUNTAJE_MINIMO or puntos == orden[1][1]:  # sin evidencia suficiente o empate
        return Clasificacion(tipo="desconocido", confianza=0.0, metodo="reglas", alternativas=alternativas)
    return Clasificacion(tipo=mejor, confianza=round(puntos / (puntos + orden[1][1]), 3), metodo="reglas", alternativas=alternativas)


def otros_documentos(doc: DocTrabajo, tipo: str) -> list[dict]:
    """Otros documentos del MVP dentro del mismo archivo (p. ej. «fotocopia licencia de conducir y cédula de identidad»):
    una página que por sí sola se clasifica como otro tipo con su título, o una cédula y una licencia en la misma página.
    El archivo se clasifica por el documento principal; esto alimenta la observación contenido.varios_documentos."""
    if tipo not in TIPOS:
        return []
    otros: dict[str, dict] = {}
    con_texto = [p for p in doc.paginas if p.segmentos]
    for p in con_texto if len(con_texto) > 1 else []:
        orden = sorted(puntajes([p]).items(), key=lambda kv: kv[1], reverse=True)
        t, v = orden[0]
        if t != tipo and t != "validacion_emisor" and v >= PUNTAJE_OTRO_DOCUMENTO and v > orden[1][1]:
            otros.setdefault(t, {"tipo": t, "paginas": []})["paginas"].append(p.n)
    if tipo in TARJETAS:
        for p in con_texto:
            pts = puntajes([p])
            for t in TARJETAS - {tipo}:
                if pts[t] >= PUNTAJE_OTRO_DOCUMENTO and pts[tipo] >= PUNTAJE_OTRO_DOCUMENTO and p.n not in otros.get(t, {}).get("paginas", []):
                    otros.setdefault(t, {"tipo": t, "paginas": []})["paginas"].append(p.n)
    return list(otros.values())
