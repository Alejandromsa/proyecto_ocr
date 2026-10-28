# Formatos de los emisores observados en documentos reales

Calibración del 28 oct 2026 con 133 documentos reales de postulantes (`herramientas/calibrar_real.py`). Aquí van solo
**patrones**, nunca valores: los documentos tienen datos personales y no se versionan. Las reglas que validan estos
formatos están en `src/stracon_ocr/reglas.json` (`formatos`, `verificacion_fuentes`).

## Registro Civil (antecedentes, hoja de vida del conductor)

| Elemento | Patrón observado | Uso |
|---|---|---|
| Folio | 12 dígitos, empieza en `500` (`^\d{12}$`) | `formato.folio` |
| Código de verificación | 12 hexadecimales en minúscula (`^[0-9a-f]{12}$`; se compara en mayúsculas) | `formato.codigo_verificacion` |
| Productor del PDF | `iText 1.4.8 (by lowagie.com)`; CreationDate = ModDate | Productor esperado (no está en la lista de editores) |
| Firma | Firma electrónica avanzada + «Timbre electrónico SRCeI»; PDF417 con folio, código, RUN, nombre y fecha de emisión | `qr.coincide_ocr`, `pdf.revisiones_incrementales` (firma = `info`) |
| PDF417 | Cabecera `$%$<tipo> <AAAA-MM-DD> <folio> <código>`; tipo 4 = antecedentes, 20 = hoja de vida. Escapes: `%3` `-`, `%.` `:`, `%,` `,`, `%1`/`%2` paréntesis, `%n` Ñ, `$C`/`$G` salto de línea, `$V` separador, `$` + vocal (mayúscula o minúscula) = vocal con tilde | `codigos.decodificar_srcei` |
| Nombre de archivo al descargar | `ANT_<folio>_<cuerpo del RUT>.pdf`, `ANT_FE_…` (fines especiales), `HVID_<folio>_<cuerpo del RUT>.pdf` | La herramienta de calibración lo usa como verdad para folio y RUT |
| Hoja de vida | Sin «próximo control»: solo trae fechas de otorgamiento por clase (PRIMERA / ULTIMA) y, en licencias de duración restringida, «VALIDA DESDE … HASTA …» | `fecha_control` es opcional |
| Copia de verificación | Mismo formato con la marca «COPIA» (imagen con máscara de color) y «Copia no válida como certificado» | Tipo `validacion_emisor`; confirma la fuente en el contraste |

## Mineduc (enseñanza media, títulos técnicos de nivel medio, certificados anuales)

- PDF nativo generado por `BIRT Report Engine … iText 2.1.7`, texto justificado **sin caracteres de espacio** (las
  palabras se separan por posición: ver `motores/pdf_nativo.TOLERANCIA_X`).
- Frase: «Certifico que según consta en el Registro Nº <n> del año <AAAA>, don(ña) <NOMBRE>, RUN <cuerpo>-<dv>, aprobó…» o
  «…a don(ña) <NOMBRE>, RUN …, quien ha cursado en el año escolar <AAAA>…».
- Código de verificación con forma de UUID (8-4-4-4-12 hexadecimales); QR a `certificados.mineduc.cl`.
- «Certificado de título técnico profesional» = título de técnico de **nivel medio** → `certificado_estudios_medios`.

## Títulos de educación superior

| Emisor | Forma del texto | Código |
|---|---|---|
| INACAP (IP y CFT) | «Certifico que con fecha <fecha> <Nombre> Rut: … ha obtenido el Título <título> Número <n>, del Registro General de Títulos» | 16 hexadecimales; firmas múltiples (sistema y secretaria general) |
| AIEP | «… SE CONFIRIÓ A: <NOMBRE> EL TÍTULO DE <título>, APROBADO / Y FUE APROBADA …» | QR a `titulacion.aiep.cl` |
| IP de Chile | «… otorgó a Doña <nombre>, el título de <título> y fue aprobado …» | Código de validación de 17 dígitos |
| Universidades (p. ej. USACH) | «… se confirió a don: <nombre> RUT N° …, el título de: <título> Inscrito …»; firma electrónica avanzada | Rol numérico |
| DUOC UC | «… se otorgó a don(a) <NOMBRE>, RUT Nº … el título nivel profesional de: <título> La Nota …» | ID de certificado de 10 dígitos; QR a `certificadovalida.duoc.cl` |
| ChileValora (competencias) | «Certifica que <NOMBRE>, RUN …, se ha certificado en el Perfil Ocupacional <perfil>.» El texto trae antes el RUT de la institución y de su representante | — |

Algunos títulos llegan cifrados solo con permisos (Acrobat Sign): se abren sin contraseña.

## Cédula de identidad (formato 2013 en adelante)

- Anverso: APELLIDOS (dos líneas), NOMBRES, NACIONALIDAD / SEXO, FECHA DE NACIMIENTO / NÚMERO DOCUMENTO, FECHA DE
  EMISIÓN / FECHA DE VENCIMIENTO; las dos columnas suelen leerse en una sola línea.
- Número de documento: 9 caracteres en grupos de 3 (`XXX.XXX.XXX`, puede empezar con letras).
- Reverso: «Nació en», «Profesión», QR a `portal.sidiv.registrocivil.cl/docstatus?RUN=…&type=…&serial=…` y MRZ TD1 que
  empieza con `INCHL` (las anteriores, `IDCHL`). La línea 3 de la MRZ trunca los nombres a 30 caracteres.

## Licencia de conducir

- Tarjeta antigua: clase arriba («B», «B/D», «A2/A4/C/D», «A5 Ley 19.495»), N° de licencia = RUT, municipalidad,
  nombres, apellidos, dirección, «FECHA ÚLTIMO CONTROL» y «FECHA DE CONTROL»; nombre y RUT repetidos bajo la foto. El
  valor va a la derecha del rótulo y algo más arriba.
- Tarjeta nueva: valores bajo el rótulo y, en el reverso, la tabla CLASE | PRIMER OTORGAMIENTO | ACTUAL OTORGAMIENTO |
  PRÓXIMO CONTROL; abajo un código `CV` + 20 hexadecimales (sin regla todavía).
- Reverso: «RESTRICCIONES», «DIRECTOR DE TRÁNSITO», «FIRMA DEL CONDUCTOR», «PRIMER OTORGAMIENTO» y su fecha.

## Pendiente

Sin formato verificado (no se valida): código `CV` de la licencia nueva, código del Mineduc (UUID), códigos de cada
institución de educación superior. Para validarlos hay que confirmar el formato con el emisor.
