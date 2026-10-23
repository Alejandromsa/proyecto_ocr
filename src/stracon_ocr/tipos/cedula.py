from .base import NOMBRE, RUT, CampoSpec, TipoSpec

TIPO = TipoSpec(
    clave="cedula_identidad",
    nombre="Cédula de identidad",
    titulos=("Cédula de identidad", "Cédula de identidad para extranjeros"),
    palabras_clave=("República de Chile", "Registro Civil", "N° de documento", "Fecha de nacimiento", "Nacionalidad", "IDCHL", "Sexo"),
    campos=(
        NOMBRE,
        RUT,
        CampoSpec("nombres", ("Nombres",), "nombre"),
        CampoSpec("apellidos", ("Apellidos",), "nombre"),
        CampoSpec("numero_documento", ("N° de documento", "Número de documento", "N° documento", "Documento"), "codigo", clave=True),
        CampoSpec("fecha_nacimiento", ("Fecha de nacimiento",), "fecha", clave=True),
        CampoSpec("fecha_emision", ("Fecha de emisión",), "fecha"),
        CampoSpec("fecha_vencimiento", ("Fecha de vencimiento", "Vencimiento"), "fecha", clave=True),
        CampoSpec("nacionalidad", ("Nacionalidad",)),
        CampoSpec("sexo", ("Sexo",)),
    ),
    obligatorios=("rut", "numero_documento"),
)
