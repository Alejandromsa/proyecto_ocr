from .base import NOMBRE, RUT, CampoSpec, TipoSpec

# Un nombre de persona en una frase: empieza con mayúscula y no cruza una coma ni un rótulo de RUT
NOMBRE_PERSONA = r"([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñü'´ ]{4,80}?)"
FECHA_LARGA = r"\d{1,2}\s*de\s*[A-Za-záéíóú]+\s*de\s*\d{4}"
RUT_ROTULO = r"(?:R\s*\.?\s*U\s*\.?\s*[TN]\b|Ru[t1]\b|C[ée]dula)"

TIPO = TipoSpec(
    clave="certificado_estudios_profesionales",
    nombre="Certificado de estudios profesionales",
    titulos=("Certificado de estudios profesionales", "Certificado de título", "Certificado de título profesional", "Certificado de competencias"),
    palabras_clave=("Título", "Universidad", "Instituto profesional", "Centro de formación técnica", "CEIM", "Carrera", "Área", "Vigencia",
                    "Registro de títulos", "Secretario general", "Técnico de nivel superior", "Registro general de títulos",
                    "Perfil ocupacional", "ChileValora"),
    campos=(
        NOMBRE,
        RUT,
        CampoSpec("numero_certificado", ("N° de certificado", "N° certificado", "Folio", "N° de registro"), "codigo", clave=True),
        CampoSpec("institucion", ("Institución", "Institución emisora")),
        CampoSpec("titulo", ("Título", "Título profesional", "Carrera")),
        CampoSpec("area", ("Área", "Especialidad")),
        CampoSpec("fecha_titulacion", ("Fecha de titulación", "Fecha de título"), "fecha"),
        CampoSpec("fecha_emision", ("Fecha de emisión", "Emitido el"), "fecha", clave=True),
        CampoSpec("vigencia", ("Vigencia", "Válido hasta", "Vigente hasta"), "fecha", clave=True),
    ),
    # certificados de título de institutos y universidades: el dato va en una frase. Formatos observados (oct 2026):
    # INACAP ("con fecha … <nombre> Rut: … ha obtenido el Título <título> Número N, del Registro General"), AIEP ("SE
    # CONFIRIÓ A: <NOMBRE> EL TÍTULO DE …, Y FUE APROBADA"), IP Chile ("otorgó a Doña <nombre>, el título de … y fue
    # aprobado"), USACH ("se confirió a don: <nombre> RUT N° …, el título de: … Inscrito"), DUOC ("se otorgó a don(a)
    # <NOMBRE>, RUT Nº … el título nivel profesional de: … La Nota"), ChileValora ("Certifica que <NOMBRE>, RUN …, se ha
    # certificado en el Perfil Ocupacional …")
    patrones=(
        ("nombre", r"confiri[oó]\s*a\s*(?:don|do[ñnf]a|do\w?a)?\s*:?\s*([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ ]+?)\s+(?:el|la)\s+t[ií]tulo"),
        ("nombre", r"con\s*fecha\s*" + FECHA_LARGA + r"\s*,?\s*(?:se\s*confiri[oó]\s*a\s*)?(?:don|do[ñnf]a)?\s*:?\s*" + NOMBRE_PERSONA
                   + r"\s*,?\s*" + RUT_ROTULO),
        ("nombre", r"otorg[oó]\s*a\s*(?:don|do[ñn]a)\s*(?:\(\s*a\s*\))?\s*" + NOMBRE_PERSONA + r"\s*,\s*(?:el\s*t[ií]tulo|" + RUT_ROTULO + ")"),
        ("nombre", r"Certifica\s*que\s*" + NOMBRE_PERSONA + r"\s*,\s*RUN"),
        ("nombre", r"se\s*ha\s*conferido\s*a\s*" + NOMBRE_PERSONA + r"\s+El\s*t[ií]tulo"),
        ("nombre", r"Por\s*cuanto\s*" + NOMBRE_PERSONA + r"\s+Ha\s*cumplido"),
        ("nombre", r"certifica\s*que\s*Don\s*\(\s*[ñn]a\s*\)\s*" + NOMBRE_PERSONA + r"\s+" + RUT_ROTULO),
        ("titulo", r"(?:el|la)\s+t[ií]tulo\s+de\s+(.+?)\s*,\s*y\s+fue"),
        ("titulo", r"t[ií]tulo\s*de\s*(.+?)\s*,?\s*y\s*fue\s*aprobad"),
        ("titulo", r"t[ií]tulo\s*de\s*(.+?)\s*,?\s*(?:siendo\s*)?aprobad[oa]"),
        ("titulo", r"se\s*titul[oó]\s*como\s*(.+?)\s*con\s"),
        ("titulo", r"obtenido\s*el\s*T[ií]tulo\s*(?:Profesional\s*)?(?:de\s*)?(.+?)\s*,?\s*N[uú]mero\s*[\d.]+"),
        ("titulo", r"el\s*t[ií]tulo\s*de\s*:\s*(.+?)\s*Inscrito"),
        ("titulo", r"t[ií]tulo\s*(?:nivel\s*profesional\s*)?de\s*:\s*(.+?)\s*La\s*Nota"),
        ("titulo", r"Perfil\s*Ocupacional\s*(.+?)\s*\."),
        ("fecha_titulacion", r"con\s*fecha\s*(" + FECHA_LARGA + r")"),
        ("fecha_titulacion", r"diploma\s*en\s*[A-Za-zÁÉÍÓÚáéíóú ]+,\s*a\s*(" + FECHA_LARGA + r")"),
        ("fecha_titulacion", r"Registro\s*de\s*T[ií]tulo\s*N\s*[°º]?\s*\d+\s*con\s*fecha\s*(" + FECHA_LARGA + r")"),
        ("numero_certificado", r"\bN\s*[°º]?\s*([A-Z]?\d{3,})\s+del\s+registro\s+de\s+t[ií]tulos"),
        ("numero_certificado", r"N[uú]mero\s*([\d.]{3,})\s*,?\s*del\s*Registro"),
        ("numero_certificado", r"Registro\s*de\s*T[ií]tulo\s*N\s*[°º]?\s*(\d{3,})"),
        ("numero_certificado", r"N\s*[°º]\s*de\s*Rol\s*([\d.]{3,})"),
        ("institucion", r"exigidos\s*por\s*(?:el\s*)?((?:Instituto\s*Profesional|Centro\s*de\s*Formaci[oó]n\s*T[eé]cnica|Universidad)\s*[A-ZÁÉÍÓÚÑ]{2,}"
                        r"(?:\s+(?!Instituto|Universidad|Centro|El\b|La\b|De\b|Del\b|y\b)[A-ZÁÉÍÓÚÑ]{2,})?)"),
        ("institucion", r"\b((?:Instituto\s+Profesional|Centro\s+de\s+Formaci[oó]n\s+T[eé]cnica)\s+[A-ZÁÉÍÓÚÑ]{2,}"
                        r"(?:\s+(?!Instituto|Universidad|Centro|El\b|La\b|De\b|Del\b)[A-ZÁÉÍÓÚÑ]{2,})?)"),
        ("institucion", r"(?#linea)\b(Universidad\s+(?:de\s+|del\s+)?[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+(?:\s+(?:de\s+)?[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+){0,2})"),
        ("institucion", r"(?#linea)\b(Instituto\s*Profesional\s*[A-Z]{2,}(?:\s*UC)?)"),
        ("fecha_emision", r"fecha\s*de\s*emisi[oó]n\s*:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{4}|" + FECHA_LARGA + r")"),
        ("fecha_emision", r"(?:Santiago|Rancagua|Antofagasta|Concepci[oó]n|Valpara[ií]so|Temuco|Calama)\s*(?:\(\s*Chile\s*\))?\s*,\s*(" + FECHA_LARGA + r")"),
    ),
    obligatorios=("nombre",),
)
