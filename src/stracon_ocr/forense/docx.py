"""Forense de DOCX: formato editable, cambios rastreados, rsid de los campos y metadatos de edición."""
from __future__ import annotations

import io
import zipfile
from collections import Counter
from typing import Any

from lxml import etree

from ..contrato import Campo, Senal
from ..trabajo import DocTrabajo

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {
    "w": W,
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
}


def _texto(z: zipfile.ZipFile, nombre: str) -> etree._Element | None:
    try:
        return etree.fromstring(z.read(nombre))
    except KeyError:
        return None


def metadatos(z: zipfile.ZipFile) -> dict[str, Any]:
    m: dict[str, Any] = {}
    core = _texto(z, "docProps/core.xml")
    if core is not None:
        for k, xp in (("creador", "dc:creator"), ("ultimo_editor", "cp:lastModifiedBy"), ("revision", "cp:revision"),
                      ("creado", "dcterms:created"), ("modificado", "dcterms:modified")):
            v = core.findtext(xp, namespaces=NS)
            if v:
                m[k] = v
    app = _texto(z, "docProps/app.xml")
    if app is not None:
        for k, xp in (("aplicacion", "ep:Application"), ("plantilla", "ep:Template"), ("minutos_edicion", "ep:TotalTime"), ("empresa", "ep:Company")):
            v = app.findtext(xp, namespaces=NS)
            if v:
                m[k] = v
    return m


def analizar(doc: DocTrabajo, tipo: str, campos: dict[str, Campo], reglas: dict[str, Any]) -> list[Senal]:
    senales: list[Senal] = []
    with zipfile.ZipFile(io.BytesIO(doc.docx_datos)) as z:
        meta = metadatos(z)
        doc.pdf_info["docx"] = meta
        if tipo in reglas["tipos_oficiales"]:
            senales.append(Senal(id="docx.certificado_en_word", familia="docx", severidad="alta",
                                 detalle="Un documento oficial llegó en un formato editable (Word); los emisores entregan PDF o el documento físico",
                                 evidencia={"origen": doc.origen, "aplicacion": meta.get("aplicacion")}))
        cuerpo = _texto(z, "word/document.xml")
        if cuerpo is None:
            return senales
        ins = cuerpo.findall(".//w:ins", NS)
        dele = cuerpo.findall(".//w:del", NS)
        comentarios = _texto(z, "word/comments.xml")
        n_com = len(comentarios.findall(".//w:comment", NS)) if comentarios is not None else 0
        if ins or dele or n_com:
            senales.append(Senal(id="docx.cambios_rastreados", familia="docx", severidad="alta",
                                 detalle=f"El Word tiene historial de cambios: {len(ins)} inserción(es), {len(dele)} eliminación(es), {n_com} comentario(s)",
                                 evidencia={"inserciones": [("".join(e.itertext()))[:80] for e in ins][:10],
                                            "eliminaciones": [("".join(e.itertext()))[:80] for e in dele][:10],
                                            "autores": sorted({e.get(f"{{{W}}}author", "") for e in ins + dele})}))
        senales += rsid_campos(cuerpo, campos)
        motivos = []
        if meta.get("ultimo_editor") and meta.get("creador") and meta["ultimo_editor"] != meta["creador"]:
            motivos.append(f"editado por '{meta['ultimo_editor']}' (creado por '{meta['creador']}')")
        if meta.get("revision", "1").isdigit() and int(meta["revision"]) > 3:
            motivos.append(f"revisión {meta['revision']}")
        if meta.get("minutos_edicion", "0").isdigit() and int(meta["minutos_edicion"]) > 0:
            motivos.append(f"{meta['minutos_edicion']} min de edición")
        if motivos:
            senales.append(Senal(id="docx.metadatos_edicion", familia="docx", severidad="baja",
                                 detalle="Rastro de edición en el Word: " + "; ".join(motivos), evidencia=meta))
    return senales


def rsid_campos(cuerpo: etree._Element, campos: dict[str, Campo]) -> list[Senal]:
    """Un campo clave escrito en otra sesión de edición (rsid poco frecuente) que el resto del documento."""
    runs = []
    for r in cuerpo.iter(f"{{{W}}}r"):
        t = "".join(x.text or "" for x in r.findall("w:t", NS))
        rsid = r.get(f"{{{W}}}rsidR") or r.getparent().get(f"{{{W}}}rsidR")
        if t.strip() and rsid:
            runs.append((t.strip(), rsid))
    if len(runs) < 5:
        return []
    frecuencia = Counter(rsid for _, rsid in runs)
    dominante = frecuencia.most_common(1)[0][0]
    senales = []
    for nombre, c in campos.items():
        if not c.valor or len(c.valor) < 3:
            continue
        rsids = {rsid for t, rsid in runs if len(t) >= 2 and (t in c.valor or c.valor in t)}
        raros = [r for r in rsids if r != dominante and frecuencia[r] / len(runs) < 0.1]
        if raros:
            senales.append(Senal(id="docx.rsid_campo_distinto", familia="docx", severidad="media", campo=nombre,
                                 detalle=f"El campo {nombre} ('{c.valor}') se escribió en otra sesión de edición que el resto del documento",
                                 evidencia={"rsid_campo": raros, "rsid_dominante": dominante}))
    return senales
