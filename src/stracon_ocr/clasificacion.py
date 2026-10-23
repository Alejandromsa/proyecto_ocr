"""Clasificación por reglas: títulos y frases típicas de cada tipo (capa 1 del análisis)."""
from __future__ import annotations

from .contrato import Alternativa, Clasificacion
from .texto import normalizar, sin_espacios
from .tipos import TIPOS
from .trabajo import DocTrabajo

PUNTOS_TITULO = 6.0  # el título exacto está entre los textos más grandes de la primera página
PUNTOS_TITULO_PREFIJO = 4.5  # un texto grande empieza con el título ("CERTIFICADO DE ESTUDIOS PARA FINES LABORALES")
PUNTOS_FRASE_TITULO = 3.0  # el título aparece en cualquier parte
PUNTOS_CLAVE = 1.0
PUNTAJE_MINIMO = 2.0  # p. ej. el reverso de una licencia: "Director de tránsito" + "Primer otorgamiento"


def _contiene(frase: str, texto_sin_espacios: str, texto_espaciado: str) -> bool:
    """Las palabras cortas ("Área", "Clase") se buscan como palabra completa para no encontrarlas dentro de otras."""
    n = normalizar(frase)
    return f" {n} " in texto_espaciado if len(n) <= 6 else sin_espacios(n) in texto_sin_espacios


def clasificar(doc: DocTrabajo) -> Clasificacion:
    # todo se compara sin espacios: el OCR y algunos PDF los pierden o los agregan ("LICENCIADE CONDUCTOR")
    texto_espaciado = " " + normalizar(doc.texto) + " "
    texto = sin_espacios(texto_espaciado)
    primera = next((p for p in doc.paginas if p.segmentos), None)
    grandes: list[str] = []
    if primera:
        por_alto = sorted(primera.segmentos, key=lambda s: s.alto, reverse=True)[:6]
        grandes = [sin_espacios(s.norm) for s in por_alto]
    puntajes: dict[str, float] = {}
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
        puntajes[clave] = p
    orden = sorted(puntajes.items(), key=lambda kv: kv[1], reverse=True)
    mejor, puntos = orden[0]
    # confianza = margen frente al segundo (0,5 = empate)
    alternativas = [Alternativa(tipo=k, confianza=round(v / (puntos + v), 3)) for k, v in orden[1:3] if v > 0]
    if puntos < PUNTAJE_MINIMO or puntos == orden[1][1]:  # sin evidencia suficiente o empate
        return Clasificacion(tipo="desconocido", confianza=0.0, metodo="reglas", alternativas=alternativas)
    return Clasificacion(tipo=mejor, confianza=round(puntos / (puntos + orden[1][1]), 3), metodo="reglas", alternativas=alternativas)
