"""Estructura del contenido del PDF: tipografías mezcladas, texto encimado, objetos que tapan texto,
campos rasterizados, imágenes insertadas sin nitidez y texto nativo distinto de lo visible."""
from __future__ import annotations

import re
from collections import Counter
from itertools import groupby
from typing import Any

import cv2
import numpy as np
from rapidfuzz.distance import Levenshtein

from ..contrato import Campo, Senal
from ..extraccion import _etiquetas_conocidas, es_rotulo, valor_cercano
from ..layout import Caja, area, base_fuente, redondear, solape
from ..texto import normalizar, sin_espacios
from ..tipos import TIPOS
from ..trabajo import DocTrabajo, PaginaTrabajo


# frases que delatan que el original era un documento electrónico (normalizadas y sin espacios)
MARCAS_ELECTRONICAS = ("FIRMADOELECTRONICAMENTE", "FIRMAELECTRONICAAVANZADA", "TIMBREELECTRONICO", "VALIDARESTECERTIFICADO",
                       "VERIFIQUEDOCUMENTO", "CODIGODEVERIFICACION")


def campo_en(campos: dict[str, Campo], pagina: int, caja: Caja) -> str | None:
    for nombre, c in campos.items():
        if c.pagina == pagina and c.bbox and solape(tuple(c.bbox), caja) > 0.3:
            return nombre
    return None


def _alfanum(s: str) -> str:
    return "".join(ch for ch in normalizar(s) if ch.isalnum())


def analizar(doc: DocTrabajo, tipo: str, campos: dict[str, Campo], reglas: dict[str, Any]) -> list[Senal]:
    u = reglas["umbrales"]
    senales: list[Senal] = []
    if doc.origen == "pdf_escaneado":
        texto = sin_espacios(normalizar(doc.texto))
        marcas = [m for m in MARCAS_ELECTRONICAS if m in texto]
        if tipo in reglas["tipos_emitidos_como_pdf_nativo"] or marcas:
            # baja: escanear o imprimir a PDF un certificado electrónico es habitual entre los postulantes y no prueba
            # una alteración. Lo que corresponde es pedir el original: eso lo hace la observación
            # contenido.imagen_de_electronico (documento Observado), que se genera con esta señal (calibración oct 2026)
            senales.append(Senal(
                id="pdf.solo_imagen", familia="estructura", severidad="baja",
                detalle="El PDF no tiene texto: es una imagen de un documento que el emisor entrega como PDF electrónico"
                        + (" (el propio documento dice que fue firmado electrónicamente)" if marcas else "")
                        + ". Se pierde la firma y no se puede verificar su integridad: pedir el PDF original",
                evidencia={"marcas_documento_electronico": marcas},
            ))
    for p in doc.paginas:
        if p.fuente_texto != "nativo":
            continue
        senales += fuentes_mezcladas(p, campos)
        senales += texto_superpuesto(p, campos)
        senales += objetos_que_tapan(p, campos)
        senales += campos_rasterizados(p, campos, tipo)
        senales += imagenes_sin_nitidez(p, campos, u["nitidez_min_imagen"])
        senales += nativo_vs_ocr(p, campos, u["cer_campo_nativo_ocr"])
    return senales


def familia_fuente(nombre: str) -> str:
    """'Helvetica-Bold' → 'HELVETICA', 'Arial,BoldItalic' → 'ARIAL', 'TimesNewRomanPS-BoldMT' → 'TIMESNEWROMAN'."""
    base = re.split(r"[-,]", nombre, maxsplit=1)[0].upper()
    return re.sub(r"(PSMT|PS|MT)$", "", base) or base


def cambio_de_estilo(utiles: list[tuple[str, str, float]]) -> bool:
    """La palabra cambia de estilo (normal ↔ negrita/cursiva de la misma familia) en tramos de 2+ caracteres y con el
    mismo tamaño: es la composición del emisor ("Nº" en normal seguido del código en negrita, nombre en negrita pegado
    a la coma). Un dígito reemplazado deja un tramo de 1 carácter o de otra familia/tamaño, y eso sí se informa."""
    familias = {familia_fuente(f) for _, f, _ in utiles}
    tams = [t for _, _, t in utiles]
    if len(familias) != 1 or max(tams) > 1.15 * min(tams):
        return False
    tramos = [len(list(g)) for _, g in groupby(f for _, f, _ in utiles)]
    return len(tramos) <= 3 and min(tramos) >= 2


def fuentes_mezcladas(p: PaginaTrabajo, campos: dict[str, Campo]) -> list[Senal]:
    """Una misma palabra con caracteres de otra tipografía o tamaño: típico de un dígito reemplazado."""
    senales = []
    for w in p.palabras:
        utiles = [(ch, base_fuente(f), t) for ch, f, t in zip(w.t, w.fuentes, w.tams) if ch.isalnum()]
        if len(utiles) < 2:
            continue
        fuentes = Counter(f for _, f, _ in utiles)
        tams = [t for _, _, t in utiles]
        distinto_tam = max(tams) > 1.15 * min(tams)
        if len(fuentes) < 2 and not distinto_tam:
            continue
        if cambio_de_estilo(utiles):
            continue
        dominante = fuentes.most_common(1)[0][0]
        mediana = sorted(tams)[len(tams) // 2]
        raros = [{"caracter": ch, "posicion": i, "fuente": f, "tam": t}
                 for i, (ch, f, t) in enumerate(utiles) if f != dominante or abs(t - mediana) > 0.15 * mediana]
        campo = campo_en(campos, p.n, w.bbox)
        senales.append(Senal(
            id="pdf.fuentes_mezcladas_campo", familia="estructura", severidad="alta", campo=campo, pagina=p.n,
            bbox=redondear(w.bbox),
            detalle=f"'{w.t}' mezcla tipografías: " + ", ".join(f"'{r['caracter']}' en {r['fuente']} {r['tam']:g} pt" for r in raros)
                    + f" dentro de {dominante}" + (f" (campo {campo})" if campo else ""),
            evidencia={"palabra": w.t, "fuente_dominante": dominante, "caracteres": raros, "fuentes": dict(fuentes)},
        ))
    return senales


def texto_superpuesto(p: PaginaTrabajo, campos: dict[str, Campo]) -> list[Senal]:
    senales = []
    for s in p.superposiciones:
        visible, debajo = s.texto_visible, s.texto_debajo
        if not _alfanum(visible) or not _alfanum(debajo):
            # una coma o un punto que choca con un dígito es composición del emisor (columnas que se tocan), no un
            # texto reemplazado: para reemplazar un dato hay letras o dígitos en las dos capas
            continue
        campo = campo_en(campos, p.n, s.bbox)
        senales.append(Senal(
            id="pdf.texto_superpuesto", familia="estructura", severidad="alta", campo=campo, pagina=p.n, bbox=redondear(s.bbox),
            detalle=f"Hay texto encimado: '{visible}' sobre '{debajo}'" + (f" (campo {campo})" if campo else ""),
            evidencia={"visible": visible, "debajo": debajo,
                       "fuentes_visible": sorted({base_fuente(w.fuente) for w in s.visible}),
                       "fuentes_debajo": sorted({base_fuente(w.fuente) for w in s.debajo})},
        ))
    return senales


def ocr_en(p: PaginaTrabajo, caja: Caja) -> list:
    """Palabras del OCR con el centro dentro de la caja y altura comparable (descarta marcas de agua grandes)."""
    alto = caja[3] - caja[1]
    return sorted(
        (o for o in p.ocr
         if caja[0] <= (o.bbox[0] + o.bbox[2]) / 2 <= caja[2] and caja[1] <= o.yc <= caja[3] and o.alto < 2.5 * max(alto, 1)),
        key=lambda o: o.bbox[0],
    )


def _visto_por_ocr(p: PaginaTrabajo, palabra) -> bool:
    objetivo = _alfanum(palabra.t)
    if not objetivo:
        return True
    x0, y0, x1, y1 = palabra.bbox
    leido = _alfanum(" ".join(o.t for o in ocr_en(p, (x0 - 3, y0 - 3, x1 + 3, y1 + 3))))
    return objetivo in leido or Levenshtein.normalized_distance(objetivo, leido) < 0.5


MIN_PALABRAS_MARCA_AGUA = 8


def marca_de_agua(debajo: list, ocultas: list) -> bool:
    """Imagen translúcida sobre mucho texto (la marca «COPIA NO VÁLIDA COMO CERTIFICADO» del portal de verificación del
    Registro Civil, un timbre): el texto de abajo se sigue viendo y el OCR solo pierde las palabras que cruzan las letras
    de la marca. Un parche que oculta un dato tapa pocas palabras, o todas las que cubre."""
    lineas = {round(w.yc / max(w.alto, 1)) for w in debajo}
    return len(debajo) >= MIN_PALABRAS_MARCA_AGUA and len(lineas) >= 3 and len(ocultas) < 0.5 * len(debajo)


def objetos_que_tapan(p: PaginaTrabajo, campos: dict[str, Campo]) -> list[Senal]:
    """Rectángulos blancos o imágenes encima de texto nativo que no se ve en el render."""
    senales = []
    pagina_area = p.ancho * p.alto
    tapas = [("rectángulo blanco", r["bbox"]) for r in p.rellenos if r["blanco"]] + [("imagen", im["bbox"]) for im in p.imagenes]
    for nombre, caja in tapas:
        if area(caja) > 0.5 * pagina_area:  # fondo de página
            continue
        debajo = [w for w in p.palabras if solape(w.bbox, caja) > 0.8]
        if not debajo:
            continue
        if p.ocr:
            ocultas = [w for w in debajo if not _visto_por_ocr(p, w)]
            if not ocultas:
                continue
            if marca_de_agua(debajo, ocultas):
                continue
            sev, detalle = "alta", f"Un {nombre} tapa texto que existe en el PDF pero no se ve: '{' '.join(w.t for w in ocultas)}'"
        else:
            ocultas, sev = debajo, "media"
            detalle = f"Un {nombre} está sobre texto del PDF ('{' '.join(w.t for w in debajo)}'); verificar si lo oculta"
        senales.append(Senal(id="pdf.objeto_tapa_texto", familia="estructura", severidad=sev,
                             campo=campo_en(campos, p.n, caja), pagina=p.n, bbox=redondear(caja), detalle=detalle,
                             evidencia={"objeto": nombre, "texto_oculto": " ".join(w.t for w in ocultas)}))
    return senales


def campos_rasterizados(p: PaginaTrabajo, campos: dict[str, Campo], tipo: str) -> list[Senal]:
    """En un PDF nativo, el valor de un campo dibujado como imagen (o ausente) bajo un rótulo con texto."""
    spec = TIPOS.get(tipo)
    campo_de_rotulo = {sin_espacios(e): c.nombre for c in (spec.campos if spec else ()) for e in c.etiquetas_norm}
    senales = []
    ya = set()
    for nombre, c in campos.items():
        if c.pagina == p.n and c.metodo == "ocr":
            img = next((im for im in p.imagenes if c.bbox and solape(im["bbox"], tuple(c.bbox)) > 0.3), None)
            ya.add(nombre)
            senales.append(Senal(
                id="pdf.campo_rasterizado", familia="estructura", severidad="alta", campo=nombre, pagina=p.n, bbox=c.bbox,
                detalle=f"El valor del campo {nombre} no existe como texto en el PDF: está dibujado como imagen"
                        f" (el OCR lee '{c.valor}' con confianza {c.conf:.2f})",
                evidencia={"valor_ocr": c.valor, "conf_ocr": c.conf, "imagen": redondear(img["bbox"]) if img else None},
            ))
    # rótulos con una imagen debajo y sin valor legible
    etiquetas = _etiquetas_conocidas()
    for seg in p.segmentos:
        if not es_rotulo(seg, etiquetas) or valor_cercano(seg, p.segmentos, etiquetas):
            continue
        h = seg.alto
        zona = (seg.bbox[0] - 3 * h, seg.bbox[3], seg.bbox[0] + 30 * h, seg.bbox[3] + 3.5 * h)
        nombre = campo_de_rotulo.get(sin_espacios(seg.norm))
        if nombre in ya:
            continue
        for im in p.imagenes:
            if solape(im["bbox"], zona) > 0.3:
                ya.add(nombre)
                senales.append(Senal(
                    id="pdf.campo_rasterizado", familia="estructura", severidad="alta", campo=nombre, pagina=p.n, bbox=redondear(im["bbox"]),
                    detalle=f"Bajo el rótulo '{seg.texto}' no hay texto: el valor es una imagen" + (f" (campo {nombre})" if nombre else ""),
                    evidencia={"rotulo": seg.texto, "imagen": redondear(im["bbox"])},
                ))
                break
    return senales


def nitidez(img: np.ndarray) -> float:
    gris = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 else img
    return float(cv2.Laplacian(gris, cv2.CV_64F).var())


def imagenes_sin_nitidez(p: PaginaTrabajo, campos: dict[str, Campo], minimo: float) -> list[Senal]:
    """Imagen incrustada borrosa en un PDF vectorial: firma, timbre o campo pegado con otra resolución."""
    if p.imagen is None or not p.imagenes:
        return []
    senales = []
    arr = np.asarray(p.imagen.convert("RGB"))
    f = 1 / p.escala
    for im in p.imagenes:
        x0, y0, x1, y1 = (int(v * f) for v in im["bbox"])
        recorte = arr[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
        if recorte.size == 0 or area(im["bbox"]) > 0.5 * p.ancho * p.alto or min(recorte.shape[:2]) < 12:
            continue
        valor = nitidez(recorte)
        if valor < minimo:
            senales.append(Senal(
                id="pdf.imagen_baja_nitidez", familia="visual", severidad="media", campo=campo_en(campos, p.n, im["bbox"]),
                pagina=p.n, bbox=redondear(im["bbox"]),
                detalle=f"Imagen insertada con baja nitidez (varianza del laplaciano {valor:.0f} < {minimo:g}) dentro de un PDF con texto nativo:"
                        " posible firma, timbre o dato pegado desde otra fuente",
                evidencia={"nitidez": round(valor, 1), "umbral": minimo, "px_fuente": im.get("srcsize")},
            ))
    return senales


def nativo_vs_ocr(p: PaginaTrabajo, campos: dict[str, Campo], umbral: float) -> list[Senal]:
    if not p.ocr:
        return []
    senales = []
    for nombre, c in campos.items():
        if c.pagina != p.n or c.metodo != "zona" or not c.bbox or not c.valor:
            continue
        vistas = ocr_en(p, (c.bbox[0] - 3, c.bbox[1] - 3, c.bbox[2] + 3, c.bbox[3] + 3))
        leido = _alfanum(" ".join(o.t for o in vistas))
        declarado = _alfanum(c.valor)
        # si el OCR no lee nada (marca de agua, sello encima) no hay evidencia: solo cuenta una lectura distinta.
        # Si lo declarado aparece completo en lo leído, el OCR solo tomó texto vecino de la misma línea
        if not declarado or len(leido) < 3 or declarado in leido:
            continue
        cer = Levenshtein.normalized_distance(declarado, leido)
        if cer > umbral:
            senales.append(Senal(
                id="pdf.texto_nativo_distinto_ocr", familia="estructura", severidad="alta", campo=nombre, pagina=p.n, bbox=c.bbox,
                detalle=f"El texto del PDF dice '{c.valor}' pero en la imagen se lee '{' '.join(o.t for o in vistas) or '(nada)'}'",
                evidencia={"declarado": c.valor, "visible": " ".join(o.t for o in vistas), "cer": round(cer, 3)},
            ))
    return senales
