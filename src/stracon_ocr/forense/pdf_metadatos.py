"""Integridad y metadatos del PDF: revisiones incrementales, productor, fechas, Info vs XMP, firmas y capas."""
from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pikepdf

from ..contrato import Senal

RE_FECHA_PDF = re.compile(r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+\-])?(\d{2})?'?(\d{2})?")


def fecha_pdf(v: Any) -> datetime | None:
    m = RE_FECHA_PDF.match(str(v or ""))
    if not m:
        return None
    a, mes, d, h, mi, s, signo, th, tm = m.groups()
    try:
        tz = timezone.utc
        if signo in {"+", "-"} and th:
            delta = timedelta(hours=int(th), minutes=int(tm or 0))
            tz = timezone(delta if signo == "+" else -delta)
        return datetime(int(a), int(mes or 1), int(d or 1), int(h or 0), int(mi or 0), int(s or 0), tzinfo=tz)
    except ValueError:
        return None


def fecha_xmp(v: Any) -> datetime | None:
    try:
        f = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return f if f.tzinfo else f.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _s(v: Any) -> str | None:
    return str(v) if v is not None else None


def revisiones(datos: bytes) -> dict[str, Any]:
    eofs = [m.start() for m in re.finditer(rb"%%EOF", datos)]
    linealizado = b"/Linearized" in datos[:2048]
    # un PDF linealizado trae legítimamente dos %%EOF
    extra = len(eofs) - (2 if linealizado and len(eofs) >= 2 else 1)
    return {"eof": len(eofs), "linealizado": linealizado, "revisiones_extra": max(0, extra), "offsets_eof": eofs}


def paginas_cambiadas(datos: bytes, offsets_eof: list[int]) -> list[int]:
    """Compara el texto de la primera revisión con el de la última (render de la versión previa)."""
    import pdfplumber

    previo = datos[: offsets_eof[0] + 5]
    cambiadas = []
    try:
        with pdfplumber.open(io.BytesIO(previo)) as a, pdfplumber.open(io.BytesIO(datos)) as b:
            for i, pb in enumerate(b.pages):
                ta = a.pages[i].extract_text() if i < len(a.pages) else None
                if ta != pb.extract_text():
                    cambiadas.append(i + 1)
    except Exception:
        return []
    return cambiadas


def tipo_de_actualizacion(parte: bytes) -> str:
    """'firma' si la revisión agrega una firma, 'vacia' si no agrega objetos, 'contenido' en otro caso."""
    if b"/ByteRange" in parte:
        return "firma"
    if not re.search(rb"\d+\s+\d+\s+obj\b", parte):
        return "vacia"
    return "contenido"


def firma_cubre_todo(datos: bytes) -> bool:
    """El /ByteRange de la última firma llega hasta el final del archivo (no se agregó nada después)."""
    rangos = re.findall(rb"/ByteRange\s*\[\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\]", datos)
    if not rangos:
        return False
    _, _, c, d = (int(x) for x in rangos[-1])
    return abs(c + d - len(datos)) <= 2  # tolera el salto de línea final


def senal_revisiones(doc, rev: dict, firmas_ok: bool, tipo: str, reglas: dict[str, Any]) -> Senal | None:
    """Explica las revisiones incrementales: firma electrónica del emisor, actualización vacía o contenido nuevo."""
    datos = doc.datos
    cortes = [o + 5 for o in rev["offsets_eof"]]
    partes = [datos[a:b] for a, b in zip(cortes, cortes[1:])][-rev["revisiones_extra"]:] if rev["revisiones_extra"] else []
    clases = [tipo_de_actualizacion(p) for p in partes]
    rev["actualizaciones"] = clases
    if not clases:
        return None
    evidencia = {"eof": rev["eof"], "linealizado": rev["linealizado"], "actualizaciones": clases}
    ultima_firma = max((i for i, c in enumerate(clases) if c == "firma"), default=None)
    contenido_tras_firma = ultima_firma is not None and "contenido" in clases[ultima_firma + 1:]
    if "contenido" not in clases or (ultima_firma is not None and not contenido_tras_firma and firma_cubre_todo(datos)):
        explicacion = []
        if "firma" in clases:
            explicacion.append("la firma electrónica del emisor" + (" (válida)" if firmas_ok else " (no se pudo verificar la firma)"))
        if "vacia" in clases:
            explicacion.append("una actualización sin contenido nuevo")
        return Senal(id="pdf.revisiones_incrementales", familia="integridad", severidad="info", evidencia=evidencia,
                     detalle=f"El PDF tiene {rev['revisiones_extra'] + 1} revisiones; las posteriores solo agregan " + " y ".join(explicacion))
    cambiadas = paginas_cambiadas(datos, rev["offsets_eof"])
    evidencia["paginas_cambiadas"] = cambiadas
    if tipo in reglas.get("tipos_documento_fisico", []):
        # PDF armado por el postulante (escaneo o foto de un documento físico): no prueba que se alteró el documento
        severidad, detalle = "baja", (f"El PDF tiene {rev['revisiones_extra'] + 1} revisiones: el archivo se volvió a guardar "
                                      "(es un escaneo hecho por el postulante, no un PDF del emisor)")
    else:
        severidad = "alta"
        detalle = f"El PDF tiene {rev['revisiones_extra'] + 1} revisiones: se modificó después de generarse"
        if contenido_tras_firma:
            detalle += ", después de la firma electrónica"
    if cambiadas:
        detalle += f"; cambia el texto de la(s) página(s) {', '.join(map(str, cambiadas))}"
    return Senal(id="pdf.revisiones_incrementales", familia="integridad", severidad=severidad, detalle=detalle, evidencia=evidencia)


def analizar(doc, reglas: dict[str, Any], tipo: str = "desconocido") -> list[Senal]:
    u = reglas["umbrales"]
    senales: list[Senal] = []
    datos = doc.datos
    fisico = tipo in reglas.get("tipos_documento_fisico", [])
    rev = revisiones(datos)

    try:
        pdf = pikepdf.open(io.BytesIO(datos))
    except pikepdf.PasswordError:
        senales.append(Senal(id="pdf.cifrado_o_restringido", familia="integridad", severidad="media",
                             detalle="El PDF está protegido con contraseña: no se puede analizar por completo"))
        return senales
    with pdf:
        if pdf.is_encrypted:
            senales.append(Senal(id="pdf.cifrado_o_restringido", familia="integridad", severidad="media",
                                 detalle="El PDF está cifrado (con permisos restringidos); el análisis puede ser parcial",
                                 evidencia={"permisos": str(pdf.allow)}))
        info = {k.lstrip("/"): _s(v) for k, v in (pdf.docinfo or {}).items()}
        xmp: dict[str, Any] = {}
        xmp_raw = b""
        try:
            if "/Metadata" in pdf.Root:
                xmp_raw = pdf.Root.Metadata.read_bytes()
            with pdf.open_metadata() as m:
                for k in ("xmp:CreatorTool", "pdf:Producer", "xmp:CreateDate", "xmp:ModifyDate", "xmpMM:DocumentID", "xmpMM:InstanceID"):
                    if m.get(k):
                        xmp[k] = str(m.get(k))
        except Exception:
            pass
        doc.pdf_info["info"] = info
        doc.pdf_info["xmp"] = xmp

        # productor / creador fuera de lo esperado
        herramientas = " | ".join(filter(None, [info.get("Producer"), info.get("Creator"), xmp.get("xmp:CreatorTool"), xmp.get("pdf:Producer")]))
        editores = [e for e in reglas["productores_editores"] if e in herramientas.lower()]
        if editores:
            ev = {"producer": info.get("Producer"), "creator": info.get("Creator"), "xmp_creator_tool": xmp.get("xmp:CreatorTool")}
            if fisico:  # el postulante armó el PDF con fotos o escaneos de su tarjeta: es lo esperable
                senales.append(Senal(id="meta.productor_no_esperado", familia="metadatos", severidad="info", evidencia=ev,
                                     detalle=f"El PDF lo armó el postulante con {', '.join(editores)} (normal en un documento físico escaneado)"))
            else:
                sev = "alta" if tipo in reglas["tipos_emitidos_como_pdf_nativo"] else "media"
                senales.append(Senal(id="meta.productor_no_esperado", familia="metadatos", severidad=sev, evidencia=ev,
                                     detalle=f"El PDF fue generado o guardado con un editor ({', '.join(editores)}), no con el sistema del emisor"))

        # fechas
        creado, modificado = fecha_pdf(info.get("CreationDate")), fecha_pdf(info.get("ModDate"))
        ahora = datetime.now(timezone.utc)
        motivos = []
        if creado and modificado and (modificado - creado).total_seconds() > u["segundos_mod_creacion"]:
            motivos.append(f"modificado {modificado - creado} después de creado")
        for nombre, f in (("CreationDate", creado), ("ModDate", modificado)):
            if f and f > ahora + timedelta(days=1):
                motivos.append(f"{nombre} en el futuro")
        if motivos:
            senales.append(Senal(id="meta.fechas_incoherentes", familia="metadatos", severidad="media",
                                 detalle="Fechas del PDF incoherentes: " + "; ".join(motivos),
                                 evidencia={"CreationDate": info.get("CreationDate"), "ModDate": info.get("ModDate")}))

        # Info frente a XMP
        if xmp:
            difs = {}
            for k_info, k_xmp, es_fecha in (("CreationDate", "xmp:CreateDate", True), ("ModDate", "xmp:ModifyDate", True), ("Producer", "pdf:Producer", False)):
                a, b = info.get(k_info), xmp.get(k_xmp)
                if not a or not b:
                    continue
                if es_fecha:
                    fa, fb = fecha_pdf(a), fecha_xmp(b)
                    if fa and fb and abs((fa - fb).total_seconds()) > u["segundos_mod_creacion"]:
                        difs[k_info] = [a, b]
                elif a.strip() != b.strip():
                    difs[k_info] = [a, b]
            if difs:
                senales.append(Senal(id="meta.info_xmp_distintos", familia="metadatos", severidad="media",
                                     detalle="Los metadatos Info y XMP no coinciden (metadatos reescritos a medias)", evidencia=difs))

        # historial XMP
        acciones = re.findall(rb"stEvt:action[=>\"]+\s*\"?([a-zA-Z]+)", xmp_raw)
        agentes = sorted({a.decode(errors="replace") for a in re.findall(rb"stEvt:softwareAgent[=>\"]+\s*\"?([^\"<]+)", xmp_raw)})
        if acciones:
            senales.append(Senal(id="meta.historial_xmp", familia="metadatos", severidad="media",
                                 detalle=f"El XMP registra {len(acciones)} acción(es) de edición",
                                 evidencia={"acciones": [a.decode() for a in acciones][:20], "software": agentes}))

        # capas ocultas
        try:
            ocp = pdf.Root.get("/OCProperties")
            apagadas = list(ocp.D.get("/OFF", [])) if ocp is not None and "/D" in ocp else []
            if apagadas:
                senales.append(Senal(id="pdf.capas_ocultas", familia="estructura", severidad="media",
                                     detalle=f"El PDF tiene {len(apagadas)} capa(s) de contenido ocultas",
                                     evidencia={"capas": [str(c.get("/Name", "")) for c in apagadas]}))
        except Exception:
            pass

        tiene_firmas = "/AcroForm" in pdf.Root and any(
            f.get("/FT") == "/Sig" for f in pdf.Root.AcroForm.get("/Fields", [])
        )
    senales_firma = firmas(datos, doc) if tiene_firmas else []
    senales += senales_firma
    firmas_ok = bool(doc.pdf_info.get("firmas")) and all(f["intacta"] and f["valida"] for f in doc.pdf_info["firmas"])
    s = senal_revisiones(doc, rev, firmas_ok and not senales_firma, tipo, reglas)
    if s:
        senales.append(s)
    doc.pdf_info["revisiones"] = {k: v for k, v in rev.items() if k != "offsets_eof"}
    return senales


def firmas(datos: bytes, doc) -> list[Senal]:
    """Validación PAdES con pyHanko (extra opcional `firmas`). Sin raíces de confianza configuradas, la
    confianza en el certificado se informa pero no se trata como invalidez."""
    try:
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import validate_pdf_signature
        # sin las raíces chilenas (Acepta, E-Cert) pyHanko registra cada intento fallido de cadena: se silencia
        for nombre in ("pyhanko", "pyhanko_certvalidator"):
            logging.getLogger(nombre).setLevel(logging.CRITICAL)
    except ImportError:
        return [Senal(id="pdf.firma_invalida", familia="integridad", severidad="info",
                      detalle="El PDF tiene firmas pero pyHanko no está instalado (uv sync --extra firmas)")]
    senales: list[Senal] = []
    resumen = []
    try:
        r = PdfFileReader(io.BytesIO(datos))
        for firma in r.embedded_signatures:
            st = validate_pdf_signature(firma)
            cobertura = str(getattr(st, "coverage", ""))
            modificacion = str(getattr(st, "modification_level", ""))
            item = {"campo": firma.field_name, "intacta": st.intact, "valida": st.valid, "confiable": st.trusted,
                    "cobertura": cobertura, "modificacion": modificacion, "firmante": str(getattr(st, "signing_cert", {}).subject.human_friendly) if getattr(st, "signing_cert", None) else None}
            resumen.append(item)
            if not (st.intact and st.valid):
                senales.append(Senal(id="pdf.firma_invalida", familia="integridad", severidad="alta",
                                     detalle=f"La firma '{firma.field_name}' no es válida (documento alterado después de firmar o firma corrupta)", evidencia=item))
            elif "ENTIRE_FILE" not in cobertura:
                senales.append(Senal(id="pdf.cambios_post_firma", familia="integridad", severidad="alta",
                                     detalle=f"Hay contenido agregado después de la firma '{firma.field_name}'", evidencia=item))
    except Exception as e:  # firma ilegible
        senales.append(Senal(id="pdf.firma_invalida", familia="integridad", severidad="media",
                             detalle=f"No se pudo validar la firma: {e}"))
    doc.pdf_info["firmas"] = resumen
    return senales
