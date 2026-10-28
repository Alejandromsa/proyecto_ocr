# Integración con la webapp

La webapp habla con `stracon-ocr` por HTTP/JSON. La API corre local (`socr servir`, puerto 8000 por defecto),
publica su documentación interactiva en `/docs` (OpenAPI) y trae una consola de prueba en `/`.

```powershell
$env:SOCR_CORS = "http://localhost:5173,https://mi-webapp"   # orígenes permitidos (por defecto *)
.venv\Scripts\socr servir --host 0.0.0.0 --puerto 8000
```

## Endpoints

| Método y ruta | Para qué | Respuesta |
|---|---|---|
| `GET /salud` | Versión, contrato, reglas y motores disponibles | `{estado, version, contrato, motores{…}}` |
| `GET /tipos` | Los 6 tipos de documento con sus campos (para armar formularios) | `[{clave, nombre, campos[]}]` |
| `GET /esquema` | JSON Schema del contrato v1.0 | JSON Schema |
| `POST /documentos` | Procesa **un** archivo y espera el resultado (~2–3 s por página) | `Documento` |
| `POST /trabajos` | Encola **uno o más** archivos y responde al instante (202) | `{trabajos: [{id, nombre, estado}]}` |
| `GET /trabajos/{id}?incluir_documento=true` | Avance: `pendiente` → `procesando` → `listo` o `error` | trabajo (+ `documento`) |
| `POST /expedientes/{rut}/documentos` | Procesa todos los documentos de un postulante y contrasta | `{documentos[], expediente}` |
| `POST /expedientes/{rut}/contraste` | Recalcula el contraste con lo guardado | `Expediente` |
| `GET /expedientes/{rut}` | Último contraste + lista de documentos | `Expediente` + `documentos_detalle` |
| `GET /documentos?expediente=&tipo=` | Lista resumida (id, tipo, señales, severidad máxima) | `[…]` |
| `GET /documentos/{id}` | JSON completo de un documento | `Documento` |
| `GET /documentos/{id}/paginas/{n}/imagen?dpi=110` | PNG de la página con las señales numeradas (rojo/naranjo/amarillo) y los campos (azul) | `image/png` |
| `GET /documentos/{id}/archivo` | Archivo original | binario |
| `GET /buscar?q=` | Texto completo (sin tildes) | `[{id, tipo, fragmento}]` |
| `POST /convocatorias` | Analiza una convocatoria completa de SharePoint (202) o la actualiza de forma incremental | `{id, nombre, estado}` |
| `GET /convocatorias/{id}` | Avance: `leyendo` → `analizando` → `listo` o `error` | `{estado, total, procesados, omitidos, con_error, …}` |
| `GET /convocatorias/{id}/resultados` | Cargos, postulantes, documentos (`DocumentoVista`) y expedientes; parciales mientras analiza | JSON |

Las tres rutas de convocatorias están descritas en [api-convocatorias.md](api-convocatorias.md).

Campos del formulario en `POST /documentos`: `archivo` (obligatorio), `expediente` (RUT), `tipo` (fuerza el tipo),
`motor` (`rapidocr`, `tesseract` o `azure`) y `guardar` (`false` para solo devolver el JSON). Errores: 413 (archivo grande),
415 (formato no soportado), 422 (tipo desconocido).

## Flujo recomendado

1. El postulante o el revisor sube los archivos → `POST /trabajos` con `expediente=<RUT>` (no bloquea la interfaz).
2. La webapp consulta `GET /trabajos/{id}` cada 1–2 s. Al quedar `listo`, el contraste del expediente ya se recalculó.
3. La bandeja muestra `resumen` de cada documento (`GET /documentos?expediente=<RUT>`).
4. La ficha del revisor muestra la imagen con las cajas, la lista de `senales` (mismo número que en la imagen), los
   `campos` y las `validaciones`, y el `Expediente` (discrepancias, faltantes y fuentes a verificar).

```js
const fd = new FormData();
for (const f of archivos) fd.append('archivos', f);
fd.append('expediente', '12794536-5');
const { trabajos } = await (await fetch(`${API}/trabajos`, { method: 'POST', body: fd })).json();

async function esperar(id) {
  for (;;) {
    const t = await (await fetch(`${API}/trabajos/${id}?incluir_documento=true`)).json();
    if (t.estado === 'listo') return t.documento;
    if (t.estado === 'error') throw new Error(t.error);
    await new Promise(r => setTimeout(r, 1500));
  }
}
```

## Qué mirar del JSON

| Necesidad de la pantalla | Dónde está |
|---|---|
| Estado del documento | `resumen.estado` (`senal_alta`, `senal_media`, `observado`, `sin_senales`) y `resumen.estado_texto` (texto del mockup) |
| ¿Requiere revisión? | `resumen.requiere_revision`, `resumen.severidad_max`, `resumen.por_severidad`, `resumen.observaciones` |
| Observaciones (pedir aclaración) | `observaciones[]`: `id`, `motivo`, `detalle`, `accion`, `mensaje_postulante` (texto listo para enviar al postulante) |
| Estado del expediente | `Expediente.estado` (`observado` / `sin_observaciones`) y `Expediente.observaciones[]` (faltantes + observaciones de sus documentos, con `documento` = tipo) |
| Señales del Agente 1 | `senales[]`: `id`, `severidad`, `detalle` (texto listo para mostrar), `campo`, `pagina`, `bbox`, `evidencia` |
| Datos del documento | `campos.<nombre>.valor` (tal como aparece) y `valor_normalizado` (RUT sin puntos, fechas ISO, clases en lista) |
| Reglas que fallan | `validaciones[]` con `resultado = "falla"` |
| Tipo de documento | `clasificacion.tipo` (+ `confianza`; `metodo = "manual"` si se forzó) |
| Contraste entre documentos | `Expediente.discrepancias[]` |
| Verificación en el emisor | `Expediente.fuentes[]` con folio, código y `url`; estado `pendiente_revisor` |
| Metadatos para el revisor | `archivo.metadatos` (productor, fechas, revisiones, EXIF, propiedades del Word) |

## Correspondencia con el mockup

| Mockup (`vd-escenarios.js`) | API |
|---|---|
| Agente 1 · «Señal detectada» `<Documento>: <detalle>` | Un evento por elemento de `senales` con severidad media o alta: `TIPOS[clasificacion.tipo].nombre + ': ' + detalle` |
| Estado del documento: «Señal alta» / «Señal media» / «Sin señales» | `resumen.estado_texto`. Prioridad: Señal alta > Señal media > Observado > Sin señales |
| **Nuevo estado «Observado»** (el documento no se puede validar) | `resumen.estado = "observado"`. El revisor usa «Solicitar aclaración» (documentos pedidos + comentario obligatorio) y puede usar `observaciones[].mensaje_postulante` como comentario. Origen del documento en la aclaración: Agente 1. Al recibir la versión 2 se reprocesa y el estado se recalcula |
| Expediente con documentos faltantes u observados | `Expediente.estado = "observado"` con `Expediente.observaciones[]` (acción `solicitar_documento` para los faltantes) |
| «Evaluación documental · Sin señales de adulteración» | `resumen.estado === "sin_senales"` |
| «(confianza N %)» | No aplica: las señales son deterministas. Mostrar la severidad y la evidencia |
| Agente 2 · «Discrepancia» | `Expediente.discrepancias` (entre documentos). El contraste con el emisor sigue siendo del Agente 2 |
| «Fuente no disponible» | Estado de `Expediente.fuentes[]`; **no** es discrepancia |

Reglas que se mantienen: la decisión siempre es del revisor y la API nunca aprueba ni rechaza. Un «Observado» no es una
adulteración ni un rechazo: indica que falta evidencia (foto ilegible, solo el reverso, imagen de un documento electrónico,
documento vencido o faltante) y que corresponde pedir una aclaración. Un archivo dañado o con contraseña tampoco da error:
vuelve como documento observado (`archivo.ilegible`).

## Producción

- **Cargas simultáneas.** La API procesa los documentos en `SOCR_TRABAJADORES` procesos (2 por defecto). Si llegan
  más a la vez, esperan en cola: ninguno falla ni se rechaza, y la API sigue respondiendo (consultas, lista, imágenes)
  mientras procesa. Medición en un i7-1270P (12 núcleos, 16 hilos), subiendo 12 PDF a la vez:

  | Procesos × hilos OCR | Los 12 en | Documentos/min | Errores | Respuesta de `/salud` |
  |---|---|---|---|---|
  | 1 × 16 | 27,7 s | 26 | 0 | ≤ 1,9 s |
  | 1 × 8 | 28,1 s | 26 | 0 | ≤ 1,9 s |
  | **2 × 4 (defecto)** | **22,3 s** | **32** | 0 | ≤ 1,7 s |
  | 2 × 8 | 22,8 s | 32 | 0 | ≤ 1,7 s |
  | 3 × 4 | 25,4 s | 28 | 0 | ≤ 1,7 s |
  | 4 × 2 | 28,8 s | 25 | 0 | ≤ 1,8 s |
  | 4 × 4 | 35,1 s | 21 | 0 | ≤ 2,2 s |

  Más procesos que 2 no rinden más en un portátil: el OCR ya usa varios núcleos y los procesos compiten. En un servidor
  con más núcleos físicos conviene volver a medir (`SOCR_TRABAJADORES`, `SOCR_HILOS_OCR`). Para más volumen: varias
  instancias detrás de un balanceador, o el adaptador Azure Document Intelligence.
- Para cargas de varios archivos, usar `POST /trabajos` (responde al instante y se consulta el avance) en vez de
  esperar la respuesta de `POST /documentos`.
- La API no tiene autenticación: publicarla solo detrás del backend de la webapp o de un gateway con autenticación.
- La base SQLite y los originales quedan en `datos/`. Para Azure, implementar `Repositorio` sobre DocumentDB o PostgreSQL.
