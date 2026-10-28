"""Servicio de convocatorias (docs/api-convocatorias.md): flujo falso local, sin red real y con SOCR_TRABAJADORES=0."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stracon_ocr.almacen.resultados import ArchivosJSON, asignar_nombres, sanear_nombre
from stracon_ocr.api import crear_app
from stracon_ocr.config import Config
from stracon_ocr.convocatorias import armar_jerarquia, id_convocatoria, id_expediente, validar_solicitud
from stracon_ocr.flujo import ClienteFlujo, ErrorFlujo, redactar

from .conftest import HOY, mockup
from .flujo_falso import FIRMA, FLUJO_URL, RUTA, SITIO_URL, FlujoFalso

CED, LIC, ANT, CEIM = ("12794536-5/1-cedula-identidad.pdf", "12794536-5/2-licencia-conducir.pdf",
                       "12794536-5/6-certificado-antecedentes.pdf", "12794536-5/4-certificado-ceim.pdf")
CED2 = "16884102-7/1-cedula-identidad.pdf"
CUERPO = {"flujo_url": FLUJO_URL, "sitio_url": SITIO_URL, "ruta": RUTA, "nombre": "PROCESO_202611"}
SECRETOS = [FIRMA, "0123456789abcdef0123456789abcdef", "prod-12.westus", "api-version=2016-06-01"]


def pdf(rel: str) -> bytes:
    return mockup(rel).read_bytes()


@dataclass
class Entorno:
    cliente: TestClient
    fake: FlujoFalso
    cfg: Config
    raiz: Path

    @property
    def servicio(self):
        return self.cliente.app.state.convocatorias

    def lanzar(self, **cambios):
        return self.cliente.post("/convocatorias", json={**CUERPO, **cambios})

    def esperar(self, cid: str, estados=("listo", "error"), timeout: float = 240) -> dict:
        limite = time.time() + timeout
        while time.time() < limite:
            p = self.cliente.get(f"/convocatorias/{cid}").json()
            if p["estado"] in estados:
                return p
            time.sleep(0.1)
        raise AssertionError(f"La convocatoria no llegó a {estados}: {p}")

    def analizar(self, **cambios) -> tuple[str, dict]:
        r = self.lanzar(**cambios)
        assert r.status_code == 202, r.text
        cid = r.json()["id"]
        return cid, self.esperar(cid)

    def resultados(self, cid: str) -> dict:
        r = self.cliente.get(f"/convocatorias/{cid}/resultados")
        assert r.status_code == 200, r.text
        return r.json()

    def documentos(self, cid: str) -> dict[str, dict]:
        """Documentos de los resultados por nombre de archivo."""
        return {d["nombre"]: d for c in self.resultados(cid)["cargos"] for p in c["postulantes"] for d in p["documentos"]}


def montar(cfg: Config, tmp: Path, fake: FlujoFalso | None = None) -> Entorno:
    cfg.resultados = tmp / "resultados"
    fake = fake or FlujoFalso()
    app = crear_app(cfg, transporte_flujo=fake.transporte, hoy=HOY)
    app.state.convocatorias.espera_reintento = 0
    return Entorno(TestClient(app), fake, cfg, cfg.resultados)


@pytest.fixture()
def env(cfg_tmp, tmp_path):
    e = montar(cfg_tmp, tmp_path)
    yield e
    e.servicio.cerrar()
    e.fake.cerrar()


def _tree_completa(fake: FlujoFalso) -> None:
    ced, lic, ant, ceim, ced2 = pdf(CED), pdf(LIC), pdf(ANT), pdf(CEIM), pdf(CED2)
    fake.archivo(["OPERADORES", "A.DUARTE", "01.- Cédula A. DUARTE.pdf"], ced)
    fake.archivo(["OPERADORES", "A.DUARTE", "02.- Licencia de Conducir A. DUARTE.pdf"], lic)
    fake.archivo(["OPERADORES", "A.DUARTE", "Antecedentes", "06.- Antecedentes A. DUARTE.pdf"], ant)  # anidado un nivel más
    fake.archivo(["OPERADORES", "A.DUARTE", "notas.txt"], b"hola")  # formato no soportado
    fake.carpeta("OPERADORES", "SOLO.CARPETA")  # postulante que existe solo como carpeta
    fake.archivo(["OPERADORES", "suelto.pdf"], ced)  # 2 segmentos: se ignora
    fake.archivo(["raiz.pdf"], ced)  # 1 segmento: se ignora
    fake.carpeta("CARGO_VACIO")  # cargo sin postulantes: se ignora
    fake.archivo(["ELECTROMECÁNICOS", "J. Muñoz", "1.- Cédula.pdf"], ced2)
    fake.archivo(["ELECTROMECÁNICOS", "Documentos_Genéricos", "Certificado CEIM.pdf"], ceim)


@pytest.fixture(scope="module")
def completa(tmp_path_factory):
    """Una convocatoria completa analizada una vez; varias pruebas solo leen su resultado."""
    tmp = tmp_path_factory.mktemp("convocatoria")
    cfg = Config()
    cfg.db, cfg.trabajadores = tmp / "prueba.db", 0
    e = montar(cfg, tmp)
    _tree_completa(e.fake)
    cid, progreso = e.analizar()
    yield e, cid, progreso
    e.servicio.cerrar()
    e.fake.cerrar()


# --- validación de la solicitud --------------------------------------------------------------------------


@pytest.mark.parametrize("cambio, campo, texto", [
    ({"flujo_url": "http://prod-12.westus.logic.azure.com/workflows/x?sig=" + FIRMA}, "flujo_url", "HTTPS"),
    ({"flujo_url": "https://ejemplo.com/workflows/x?sig=" + FIRMA}, "flujo_url", "Power Automate"),
    ({"flujo_url": "https://logic.azure.com.malo.com/x?sig=" + FIRMA}, "flujo_url", "Power Automate"),
    ({"flujo_url": "https://usuario:clave@prod.logic.azure.com/x?sig=" + FIRMA}, "flujo_url", "no es válida"),
    ({"flujo_url": ""}, "flujo_url", "obligatorio"),
    ({"flujo_url": 123}, "flujo_url", "texto"),
    ({"sitio_url": "http://straconcom.sharepoint.com/sites/x"}, "sitio_url", "HTTPS"),
    ({"sitio_url": "https://straconcom.ejemplo.com/sites/x"}, "sitio_url", "SharePoint"),
    ({"sitio_url": "https://straconcom.sharepoint.com"}, "sitio_url", "sites"),
    ({"ruta": "/otra/cosa/x/y"}, "ruta", "/sites/"),
    ({"ruta": "/sites/Casodeuso_validacindocumental/../otro/x/y"}, "ruta", "no es válida"),
    ({"ruta": "/sites/Casodeuso_validacindocumental"}, "ruta", "carpeta de la convocatoria"),
    ({"ruta": "/sites/OtroSitio/biblioteca/carpeta"}, "ruta", "no pertenece al sitio"),
    ({"nombre": 5}, "nombre", "texto"),
    ({"nombre": "x" * 201}, "nombre", "200"),
])
def test_validaciones_422(env, cambio, campo, texto):
    r = env.lanzar(**cambio)
    assert r.status_code == 422, r.text
    errores = r.json()["detail"]
    assert any(e["loc"] == ["body", campo] and texto in e["msg"] for e in errores), errores
    assert FIRMA not in r.text  # la URL del flujo no se repite en los errores
    assert env.fake.llamadas == []  # no se llegó a llamar al flujo


def test_validaciones_cuerpo_incompleto_o_invalido(env):
    r = env.cliente.post("/convocatorias", json={"flujo_url": FLUJO_URL})
    assert r.status_code == 422
    campos = {e["loc"][1] for e in r.json()["detail"]}
    assert campos == {"sitio_url", "ruta"} and FIRMA not in r.text
    r = env.cliente.post("/convocatorias", json=[FLUJO_URL])
    assert r.status_code == 422 and FIRMA not in r.text
    r = env.cliente.post("/convocatorias", content=('{"flujo_url": "' + FLUJO_URL + '", ').encode(), headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and FIRMA not in r.text and "JSON" in r.text
    # el resto de la API conserva su comportamiento de validación
    assert env.cliente.get("/documentos", params={"limite": "x"}).status_code == 422


def test_validar_solicitud_acepta_y_normaliza(cfg_tmp):
    sol, errores = validar_solicitud({**CUERPO, "nombre": None, "ruta": RUTA + "/", "sitio_url": SITIO_URL + "/"}, cfg_tmp)
    assert errores == [] and sol.nombre == "PROCESO_202611" and sol.ruta == RUTA and sol.sitio_url == SITIO_URL
    sol, _ = validar_solicitud({**CUERPO, "flujo_url": "https://abc.xx.environment.api.powerplatform.com/powerautomate/x?sig=1"}, cfg_tmp)
    assert sol is not None
    # el host local solo se acepta si se habilita en SOCR_FLUJO_HOSTS (pruebas)
    local = {**CUERPO, "flujo_url": "http://127.0.0.1:9999/x"}
    assert validar_solicitud(local, cfg_tmp)[0] is None
    cfg_tmp.flujo_hosts = [*cfg_tmp.flujo_hosts, "127.0.0.1"]
    assert validar_solicitud(local, cfg_tmp)[0] is not None


def test_id_estable_y_404(env):
    esperado = hashlib.sha1((SITIO_URL.lower() + "|" + RUTA.lower()).encode()).hexdigest()[:12]
    assert id_convocatoria(SITIO_URL, RUTA) == esperado == id_convocatoria(SITIO_URL.upper().replace("HTTPS", "https"), RUTA + "/")
    assert id_convocatoria(SITIO_URL, RUTA + "/otra") != esperado
    r = env.lanzar()
    assert r.status_code == 202 and r.json()["id"] == esperado and r.json()["nombre"] == "PROCESO_202611"
    env.esperar(esperado)
    for ruta in ("/convocatorias/000000000000", "/convocatorias/000000000000/resultados", "/convocatorias/no-existe",
                 "/convocatorias/..%2F..%2Fx/resultados"):
        assert env.cliente.get(ruta).status_code == 404, ruta


# --- jerarquía ---------------------------------------------------------------------------------------------


def _item(ref: str, carpeta: bool = False, **extra) -> dict:
    return {"FileLeafRef": ref.rsplit("/", 1)[-1], "FileRef": ref, "FileDirRef": ref.rsplit("/", 1)[0], "FSObjType": 1 if carpeta else 0,
            "Modified": "2026-10-27T15:10:00Z", "EstadoValidacion": None, **extra}


def test_jerarquia_reglas_del_mockup():
    b = "/sites/S/Lib/CONV"
    crudo = {"ok": True, "rutaInicial": b + "/", "carpetas": [
        _item(f"{b}/OPERADORES", True), _item(f"{b}/OPERADORES/A.DUARTE", True), _item(f"{b}/OPERADORES/A.DUARTE/Sub", True),
        _item(f"{b}/operadores/SOLO", True),  # distinta capitalización: mismo cargo
        _item(f"{b}/OPERADORES/Documentos_Sin_Clasificar", True), _item(f"{b}/VACIO", True), _item("/sites/S/Lib/OTRA/X/Y", True)],
        "archivos": [
        _item(f"{b}/OPERADORES/A.DUARTE/02.pdf"), _item(f"{b}/OPERADORES/A.DUARTE/Sub/Hondo/01.pdf"), _item(f"{b}/OPERADORES/suelto.pdf"),
        _item(f"{b}/raiz.pdf"), _item(f"{b}/OPERADORES/A.DUARTE/Sub", True),  # carpeta en la lista de archivos
        _item("/sites/S/Lib/OTRA/X/Y/z.pdf"),  # fuera de la convocatoria
        _item(f"{b}/MECANICOS/INFERIDO/doc.pdf", Modified=["2026-01-02T00:00:00Z"]),  # postulante sin carpeta listada
        _item(f"{b}/OPERADORES/Documentos_Sin_Clasificar/x.pdf")]}
    r = armar_jerarquia(crudo, b)
    assert [c["nombre"] for c in r["cargos"]] == ["MECANICOS", "OPERADORES"]  # sin VACIO; orden estable
    operadores = r["cargos"][1]["postulantes"]
    assert [p["nombre"] for p in operadores] == ["A.DUARTE", "Documentos_Sin_Clasificar", "SOLO"]
    duarte = operadores[0]
    assert [a["nombre"] for a in duarte["archivos"]] == ["01.pdf", "02.pdf"] and duarte["archivos"][0]["ruta"].endswith("Sub/Hondo/01.pdf")
    assert operadores[2]["archivos"] == []  # solo carpeta
    assert [p["generico"] for p in operadores] == [False, True, False]
    assert duarte["ruta"] == b + "/OPERADORES/A.DUARTE" and duarte["expediente_id"] == hashlib.sha1(duarte["ruta"].encode()).hexdigest()[:10]
    inferido = r["cargos"][0]["postulantes"][0]
    assert inferido["nombre"] == "INFERIDO" and inferido["archivos"][0]["modificado"] == "2026-01-02T00:00:00Z"


@pytest.mark.parametrize("crudo", [None, [], {"ok": True, "carpetas": {}}, {"ok": True, "archivos": [{"FileRef": "/x"}]},
                                   {"ok": True, "archivos": ["texto"]}])
def test_jerarquia_respuesta_malformada(crudo):
    with pytest.raises(ErrorFlujo) as e:
        armar_jerarquia(crudo, "/sites/S/Lib/CONV")
    assert "formato" in str(e.value) or "válid" in str(e.value)


# --- flujo completo -------------------------------------------------------------------------------------------


def test_progreso_final(completa):
    env, cid, p = completa
    assert p["estado"] == "listo" and p["error"] is None and p["terminado_en"] and p["iniciado_en"]
    assert set(p) == {"id", "nombre", "ruta", "estado", "total", "procesados", "omitidos", "con_error", "postulantes", "cargos",
                      "iniciado_en", "terminado_en", "error"}
    # notas.txt (formato no soportado) y los archivos sueltos no cuentan; los postulantes incluyen la carpeta sin archivos
    assert (p["total"], p["procesados"], p["omitidos"], p["con_error"], p["postulantes"], p["cargos"]) == (5, 5, 0, 0, 4, 2)
    assert p["nombre"] == "PROCESO_202611" and p["ruta"] == RUTA
    assert env.fake.contar("listDeepest") == 1 and env.fake.contar("getFile") == 5  # una descarga por PDF
    assert any(FIRMA in r for r in env.fake.recibidos)  # el secreto sí llegó al flujo


def test_resultados_jerarquia_y_clasificacion(completa):
    env, cid, _ = completa
    r = env.resultados(cid)
    assert r["convocatoria"] == {"id": cid, "nombre": "PROCESO_202611", "ruta": RUTA, "estado": "listo"}
    assert [c["nombre"] for c in r["cargos"]] == ["ELECTROMECÁNICOS", "OPERADORES"]
    operadores = r["cargos"][1]["postulantes"]
    assert [p["nombre"] for p in operadores] == ["A.DUARTE", "SOLO.CARPETA"]
    duarte = operadores[0]
    assert duarte["ruta"] == f"{RUTA}/OPERADORES/A.DUARTE" and duarte["generico"] is False
    assert duarte["expediente_id"] == id_expediente(duarte["ruta"]) and len(duarte["expediente_id"]) == 10
    docs = {d["nombre"]: d for d in duarte["documentos"]}
    assert set(docs) == {"01.- Cédula A. DUARTE.pdf", "02.- Licencia de Conducir A. DUARTE.pdf", "06.- Antecedentes A. DUARTE.pdf", "notas.txt"}
    tipos = {n: d["documento"]["clasificacion"]["tipo"] for n, d in docs.items() if d["documento"]}
    assert tipos == {"01.- Cédula A. DUARTE.pdf": "cedula_identidad", "02.- Licencia de Conducir A. DUARTE.pdf": "licencia_conducir",
                     "06.- Antecedentes A. DUARTE.pdf": "certificado_antecedentes"}
    nest = docs["06.- Antecedentes A. DUARTE.pdf"]
    assert nest["estado"] == "listo" and nest["motivo"] is None and nest["ruta"] == f"{RUTA}/OPERADORES/A.DUARTE/Antecedentes/06.- Antecedentes A. DUARTE.pdf"
    assert nest["modificado"] == "2026-10-27T15:10:00Z"
    txt = docs["notas.txt"]
    assert txt["estado"] == "omitido" and "no soportado" in txt["motivo"] and txt["documento"] is None
    # postulante solo con carpeta: aparece, sin documentos; su expediente se calcula igual (todo faltante)
    solo = operadores[1]
    assert solo["documentos"] == [] and solo["expediente"]["faltantes"]
    electro = {p["nombre"]: p for p in r["cargos"][0]["postulantes"]}
    assert electro["Documentos_Genéricos"]["generico"] is True and electro["J. Muñoz"]["generico"] is False
    assert electro["Documentos_Genéricos"]["documentos"][0]["estado"] == "listo"  # se procesa igual


def test_documento_vista_y_json_completo(completa):
    env, cid, _ = completa
    d = env.documentos(cid)["01.- Cédula A. DUARTE.pdf"]["documento"]
    assert d["contrato_version"] == "1.0" and d["archivo"]["nombre"] == "01.- Cédula A. DUARTE.pdf" and d["resumen"]["estado"]
    for clave in ("id", "archivo", "clasificacion", "codigos", "campos", "senales", "observaciones", "validaciones", "resumen", "versiones", "errores"):
        assert clave in d
    pagina = d["paginas"][0]
    assert {"n", "ancho", "alto", "dpi", "rotacion", "fuente_texto", "motor"} <= set(pagina) and pagina["ancho"] > 0 and pagina["alto"] > 0
    assert "bloques" not in pagina and "tablas" not in pagina
    assert d["campos"]["rut"]["valor_normalizado"] == "12794536-5"
    # el JSON completo (con bloques) está en disco y en GET /documentos/{id}
    completo = env.cliente.get(f"/documentos/{d['id']}").json()
    assert completo["paginas"][0]["bloques"]
    carpeta = env.raiz / cid / "OPERADORES" / "A.DUARTE"
    en_disco = json.loads((carpeta / "01.- Cédula A. DUARTE.pdf.json").read_text(encoding="utf-8"))
    assert en_disco["id"] == d["id"] and en_disco["paginas"][0]["bloques"]


def test_expediente_contraste_por_postulante(completa):
    env, cid, _ = completa
    r = env.resultados(cid)
    duarte = r["cargos"][1]["postulantes"][0]
    exp = duarte["expediente"]
    assert exp["expediente"] == duarte["expediente_id"]  # la clave es opaca, no un RUT
    assert exp["identidad"]["rut"] == "12794536-5"  # el RUT real sale de la cédula
    assert {d["tipo"] for d in exp["documentos"]} == {"cedula_identidad", "licencia_conducir", "certificado_antecedentes"}
    assert "hoja_vida_conductor" in exp["faltantes"] and "cedula_identidad" not in exp["faltantes"]
    assert not [x for x in exp["discrepancias"] if x["campo"] == "rut"]
    assert exp["estado"] == "observado" and exp["observaciones"]
    muñoz = {p["nombre"]: p for p in r["cargos"][0]["postulantes"]}["J. Muñoz"]["expediente"]
    assert muñoz["identidad"]["rut"] == "16884102-7" and muñoz["expediente"] != exp["expediente"]
    # y también se guardó en la base (contraste ya disponible en /expedientes)
    assert env.cliente.get(f"/expedientes/{duarte['expediente_id']}").status_code == 200


def test_json_en_disco_estructura(completa):
    env, cid, _ = completa
    raiz = env.raiz / cid
    assert sorted(p.name for p in raiz.iterdir()) == ["ELECTROMECÁNICOS", "OPERADORES", "convocatoria.json"]
    assert sorted(p.name for p in (raiz / "OPERADORES").iterdir()) == ["A.DUARTE", "SOLO.CARPETA"]
    assert sorted(p.name for p in (raiz / "OPERADORES" / "A.DUARTE").iterdir()) == [
        "01.- Cédula A. DUARTE.pdf.json", "02.- Licencia de Conducir A. DUARTE.pdf.json", "06.- Antecedentes A. DUARTE.pdf.json", "_expediente.json"]
    assert (raiz / "OPERADORES" / "SOLO.CARPETA" / "_expediente.json").is_file()
    indice = json.loads((raiz / "convocatoria.json").read_text(encoding="utf-8"))
    assert indice["estado"] == "listo" and "flujo" not in json.dumps(indice).lower().replace("flujo_", "")
    a = indice["arbol"][1]["postulantes"][0]["archivos"][0]
    assert {"ruta", "modificado", "sha256", "documento_id", "estado", "motivo"} <= set(a) and len(a["sha256"]) == 64
    assert not list(raiz.rglob("*.tmp"))  # escritura atómica: no queda ningún temporal


# --- nombres saneados y colisiones -------------------------------------------------------------------------


def test_sanear_nombres():
    assert sanear_nombre("Licencia: A. Pavéz?.pdf") == "Licencia_ A. Pavéz_.pdf"
    assert sanear_nombre('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert sanear_nombre("informe final. . ") == "informe final"  # sin puntos ni espacios finales
    assert sanear_nombre("...") == "_" and sanear_nombre("") == "_"
    assert sanear_nombre("CON") == "_CON" and sanear_nombre("nul.txt") == "_nul.txt" and sanear_nombre("COM1") == "_COM1"
    assert sanear_nombre("Ñandú Peña") == "Ñandú Peña"  # acentos y espacios internos se conservan
    largo = sanear_nombre("x" * 300)
    assert len(largo) <= 80 and sanear_nombre("x" * 300 + "y") != largo  # el hash distingue los largos
    assert sanear_nombre(largo) == largo  # idempotente


def test_asignar_nombres_colisiones():
    r = asignar_nombres({"k1": "a?.pdf", "k2": "a_.pdf", "k3": "A_.PDF", "k4": "_expediente"}, extension=".json", reservados=["_expediente.json"])
    assert r["k1"] == "a_.pdf.json"  # el primero (orden estable) conserva el nombre
    assert r["k2"].startswith("a_.pdf-") and r["k2"].endswith(".json") and len(r["k2"]) == len("a_.pdf-123456.json")
    assert r["k3"] != r["k2"] and r["k3"].lower() != r["k1"].lower()  # Windows no distingue mayúsculas
    assert r["k4"].startswith("_expediente-")  # no pisa el archivo reservado
    assert len({v.lower() for v in r.values()}) == 4
    # lo ya asignado no cambia aunque aparezca algo nuevo
    mas = asignar_nombres({**{"k1": "a?.pdf", "k2": "a_.pdf"}, "k0": "a!.pdf"}, previos={"k1": r["k1"], "k2": r["k2"]}, extension=".json")
    assert mas["k1"] == r["k1"] and mas["k2"] == r["k2"] and mas["k0"] == "a!.pdf.json"


def test_nombres_raros_en_disco(env):
    lic, ced = pdf(LIC), pdf(CED)
    cargo, post = "Operación: Mantención", "Ñ. Peña?"
    env.fake.archivo([cargo, post, "Licencia: A. Pavéz?.pdf"], lic)
    env.fake.archivo([cargo, post, "Licencia_ A. Pavéz_.pdf"], lic)  # colisiona al sanear
    env.fake.archivo([cargo, post, "CON.pdf"], ced)  # nombre reservado de Windows
    cid, p = env.analizar()
    assert p["estado"] == "listo" and p["total"] == 3 and p["procesados"] == 3
    r = env.resultados(cid)
    assert r["cargos"][0]["nombre"] == cargo and r["cargos"][0]["postulantes"][0]["nombre"] == post  # los nombres originales se informan tal cual
    carpeta = env.raiz / cid / "Operación_ Mantención" / "Ñ. Peña_"
    nombres = sorted(f.name for f in carpeta.iterdir())
    assert len(nombres) == 4 and "_expediente.json" in nombres and "_CON.pdf.json" in nombres
    licencias = [n for n in nombres if n.startswith("Licencia_ A. Pavéz_.pdf")]
    assert len(licencias) == 2 and "Licencia_ A. Pavéz_.pdf.json" in licencias  # una con el nombre limpio, la otra con sufijo de hash
    assert len({n.lower() for n in nombres}) == 4
    assert all(d["estado"] == "listo" and d["documento"] for d in env.documentos(cid).values())
    # segunda pasada: los nombres no cambian
    env.lanzar()
    env.esperar(cid)
    assert sorted(f.name for f in carpeta.iterdir()) == nombres


def test_almacen_archivos_json(tmp_path):
    a = ArchivosJSON(tmp_path / "res")
    assert a.leer(("x", "no.json")) is None and not a.existe(("x", "no.json"))
    a.escribir(("c1", "Cargo", "ñ.json"), {"a": "ñandú", "n": [1, 2]})
    assert a.leer(("c1", "Cargo", "ñ.json")) == {"a": "ñandú", "n": [1, 2]}
    assert "ñandú" in (tmp_path / "res" / "c1" / "Cargo" / "ñ.json").read_text(encoding="utf-8")  # UTF-8 legible
    for malo in (("..", "x.json"), ("c1", "..", "..", "x.json"), ("c1/otro", "x.json"), ("c1\\otro", "x.json"), ("c:", "x.json"), ("", "x"), ()):
        with pytest.raises(ValueError):
            a.escribir(malo, {})
    assert not (tmp_path / "x.json").exists()
    # lecturas concurrentes con escrituras: nunca se ve un archivo a medias
    errores = []

    def escribir():
        for i in range(60):
            a.escribir(("c1", "p.json"), {"i": i, "relleno": "z" * 50000})

    def leer():
        for _ in range(120):
            try:
                d = a.leer(("c1", "p.json"))
                assert d is None or len(d["relleno"]) == 50000
            except Exception as e:  # noqa: BLE001
                errores.append(e)

    hilos = [threading.Thread(target=f) for f in (escribir, escribir, leer, leer)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert errores == [] and not list((tmp_path / "res" / "c1").glob("*.tmp"))


# --- actualización incremental ---------------------------------------------------------------------------


def test_actualizacion_incremental(env):
    f = env.fake
    ref_a = f.archivo(["OPERADORES", "X.PEREZ", "01.- Cédula.pdf"], pdf(CED))
    ref_b = f.archivo(["OPERADORES", "X.PEREZ", "02.- Licencia.pdf"], pdf(LIC))
    cid, p = env.analizar()
    assert p["procesados"] == 2
    ids = {n: d["documento"]["id"] for n, d in env.documentos(cid).items()}
    carpeta = env.raiz / cid / "OPERADORES" / "X.PEREZ"
    mtimes = {x.name: x.stat().st_mtime_ns for x in carpeta.iterdir() if x.name != "_expediente.json"}

    # 2.ª llamada sin cambios: no descarga ni reprocesa nada
    r = env.lanzar()
    assert r.status_code == 202 and r.json()["id"] == cid
    p = env.esperar(cid)
    assert (f.contar("getFile", ref_a), f.contar("getFile", ref_b), f.contar("listDeepest")) == (1, 1, 2)
    assert {n: d["documento"]["id"] for n, d in env.documentos(cid).items()} == ids
    assert {x.name: x.stat().st_mtime_ns for x in carpeta.iterdir() if x.name != "_expediente.json"} == mtimes
    assert p["estado"] == "listo" and p["total"] == 2 and p["procesados"] == 2

    # A cambia de contenido y de Modified; B solo de Modified (mismo contenido); C es nuevo
    f.archivo(["OPERADORES", "X.PEREZ", "01.- Cédula.pdf"], pdf(ANT), modificado="2026-10-28T09:00:00Z")
    f.archivo(["OPERADORES", "X.PEREZ", "02.- Licencia.pdf"], pdf(LIC), modificado="2026-10-28T09:30:00Z")
    ref_c = f.archivo(["OPERADORES", "X.PEREZ", "03.- Nuevo.pdf"], pdf(CEIM))
    env.lanzar()
    p = env.esperar(cid)
    assert (f.contar("getFile", ref_a), f.contar("getFile", ref_b), f.contar("getFile", ref_c)) == (2, 2, 1)
    docs = env.documentos(cid)
    assert docs["01.- Cédula.pdf"]["documento"]["id"] != ids["01.- Cédula.pdf"]  # reprocesado
    assert docs["01.- Cédula.pdf"]["documento"]["clasificacion"]["tipo"] == "certificado_antecedentes"
    assert docs["01.- Cédula.pdf"]["modificado"] == "2026-10-28T09:00:00Z"
    assert docs["02.- Licencia.pdf"]["documento"]["id"] == ids["02.- Licencia.pdf"]  # mismo contenido: se reutiliza el resultado
    assert docs["02.- Licencia.pdf"]["modificado"] == "2026-10-28T09:30:00Z"
    assert docs["03.- Nuevo.pdf"]["estado"] == "listo" and p["total"] == 3 and p["procesados"] == 3
    exp = env.resultados(cid)["cargos"][0]["postulantes"][0]["expediente"]
    assert {d["tipo"] for d in exp["documentos"]} == {"certificado_antecedentes", "licencia_conducir", "certificado_estudios_profesionales"}

    # B desaparece: queda «retirado», su JSON no se borra y deja de contarse
    f.quitar(ref_b)
    env.lanzar()
    p = env.esperar(cid)
    docs = env.documentos(cid)
    assert docs["02.- Licencia.pdf"]["estado"] == "retirado" and "ya no está" in docs["02.- Licencia.pdf"]["motivo"]
    assert docs["02.- Licencia.pdf"]["documento"]["id"] == ids["02.- Licencia.pdf"]
    assert (carpeta / "02.- Licencia.pdf.json").is_file()
    assert (p["total"], p["procesados"]) == (2, 2)
    exp = env.resultados(cid)["cargos"][0]["postulantes"][0]["expediente"]
    assert "licencia_conducir" not in {d["tipo"] for d in exp["documentos"]}
    assert f.contar("getFile", ref_c) == 1


def test_postulante_retirado_se_conserva(env):
    f = env.fake
    f.archivo(["OPERADORES", "X.PEREZ", "01.- Cédula.pdf"], pdf(CED))
    ref = f.archivo(["MECANICOS", "Y.RIOS", "01.- Cédula.pdf"], pdf(CED2))
    cid, p = env.analizar()
    assert (p["cargos"], p["postulantes"]) == (2, 2)
    f.quitar(ref)
    f.carpetas -= {c for c in f.carpetas if "/MECANICOS" in c}
    env.lanzar()
    p = env.esperar(cid)
    assert (p["cargos"], p["postulantes"], p["total"]) == (1, 1, 1)
    r = env.resultados(cid)
    mec = next(c for c in r["cargos"] if c["nombre"] == "MECANICOS")["postulantes"][0]
    assert mec["documentos"][0]["estado"] == "retirado" and mec["documentos"][0]["documento"]
    assert (env.raiz / cid / "MECANICOS" / "Y.RIOS" / "01.- Cédula.pdf.json").is_file()


# --- tamaño, formatos y errores por archivo ----------------------------------------------------------------


def test_archivo_mayor_que_el_limite_se_omite(env):
    f, cfg = env.fake, env.cfg
    cfg.max_mb = 1
    grande = b"%PDF-1.4\n" + b"0" * 1_300_000
    ref_ok = f.archivo(["OPERADORES", "X.PEREZ", "01.- Cédula.pdf"], pdf(CED))
    ref_g1 = f.archivo(["OPERADORES", "X.PEREZ", "grande declarado.pdf"], grande)
    ref_g2 = f.archivo(["OPERADORES", "X.PEREZ", "grande sin tamaño.pdf"], grande, declarar_tamano=False)
    cid, p = env.analizar()
    assert p["estado"] == "listo" and (p["total"], p["procesados"], p["omitidos"], p["con_error"]) == (3, 1, 2, 0)
    docs = env.documentos(cid)
    for n in ("grande declarado.pdf", "grande sin tamaño.pdf"):
        assert docs[n]["estado"] == "omitido" and "tamaño máximo" in docs[n]["motivo"] and "1 MB" in docs[n]["motivo"] and docs[n]["documento"] is None
    assert docs["01.- Cédula.pdf"]["estado"] == "listo"
    assert not (env.raiz / cid / "OPERADORES" / "X.PEREZ" / "grande declarado.pdf.json").exists()
    # sin cambios: lo omitido no se vuelve a descargar mientras el límite siga igual
    env.lanzar()
    env.esperar(cid)
    assert f.contar("getFile", ref_g1) == 1 and f.contar("getFile", ref_g2) == 1 and f.contar("getFile", ref_ok) == 1
    # si se sube SOCR_MAX_MB se reintenta (aunque el archivo no haya cambiado)
    cfg.max_mb = 5
    env.lanzar()
    p = env.esperar(cid)
    assert f.contar("getFile", ref_g1) == 2 and p["omitidos"] == 0 and p["estado"] == "listo"
    assert env.documentos(cid)["grande declarado.pdf"]["estado"] == "listo"


def test_un_archivo_que_falla_no_detiene_la_convocatoria(env, monkeypatch):
    f = env.fake
    f.archivo(["OPERADORES", "X.PEREZ", "01.- Cédula.pdf"], pdf(CED))
    ref_403 = f.archivo(["OPERADORES", "X.PEREZ", "02.- sin permiso.pdf"], pdf(LIC))
    ref_b64 = f.archivo(["OPERADORES", "X.PEREZ", "03.- base64 roto.pdf"], pdf(LIC))
    ref_vacio = f.archivo(["OPERADORES", "X.PEREZ", "04.- sin contenido.pdf"], pdf(LIC))
    f.archivo(["OPERADORES", "X.PEREZ", "05.- explota.pdf"], pdf(LIC))
    f.archivo(["OPERADORES", "X.PEREZ", "06.- falso.pdf"], b"hola mundo, esto no es un pdf")
    f.archivo(["OPERADORES", "X.PEREZ", "07.- Antecedentes.pdf"], pdf(ANT))
    f.fallos[("getFile", ref_403)] = "ok_false"
    f.fallos[("getFile", ref_b64)] = "base64"
    f.fallos[("getFile", ref_vacio)] = "sin_contenido"
    proc = env.cliente.app.state.procesador
    original = proc.procesar

    def procesar(*a, **k):
        if k.get("nombre") == "05.- explota.pdf":
            raise RuntimeError("falla simulada en la etapa de ingesta con datos de " + FIRMA)
        return original(*a, **k)

    monkeypatch.setattr(proc, "procesar", procesar)
    cid, p = env.analizar()
    assert p["estado"] == "listo" and p["error"] is None  # la convocatoria termina igual
    docs = env.documentos(cid)
    assert docs["01.- Cédula.pdf"]["estado"] == "listo" and docs["07.- Antecedentes.pdf"]["estado"] == "listo"
    assert docs["02.- sin permiso.pdf"]["estado"] == "error" and "no permite" in docs["02.- sin permiso.pdf"]["motivo"]
    assert docs["03.- base64 roto.pdf"]["estado"] == "error" and "no es válido" in docs["03.- base64 roto.pdf"]["motivo"]
    assert docs["04.- sin contenido.pdf"]["estado"] == "error" and "no entregó" in docs["04.- sin contenido.pdf"]["motivo"]
    assert docs["05.- explota.pdf"]["estado"] == "error" and "RuntimeError" in docs["05.- explota.pdf"]["motivo"]
    assert docs["06.- falso.pdf"]["estado"] in {"omitido", "listo"}  # contenido que no es PDF: omitido (formato) o documento observado
    errores = sum(d["estado"] == "error" for d in docs.values())
    assert errores == 4 and p["con_error"] == 4 and p["total"] == 7
    assert all(d["documento"] is None for d in docs.values() if d["estado"] == "error")
    # un archivo con error se reintenta en la siguiente actualización (los listos no)
    f.fallos.clear()
    monkeypatch.setattr(proc, "procesar", original)
    env.lanzar()
    p = env.esperar(cid)
    assert p["con_error"] == 0 and env.documentos(cid)["02.- sin permiso.pdf"]["estado"] == "listo"
    assert f.contar("getFile", f.base + "/OPERADORES/X.PEREZ/01.- Cédula.pdf") == 1


# --- errores del flujo ------------------------------------------------------------------------------------


def test_error_del_flujo_deja_estado_error_sin_secretos(env):
    f = env.fake
    f.archivo(["OPERADORES", "X.PEREZ", "01.- Cédula.pdf"], pdf(CED))
    f.fallos[("listDeepest", None)] = "ok_false"
    cid, p = env.analizar()
    assert p["estado"] == "error" and "No se pudo leer la convocatoria" in p["error"] and "no permite" in p["error"]
    assert p["terminado_en"] and not any(s in json.dumps(p) for s in SECRETOS)
    f.fallos[("listDeepest", None)] = "http500"
    env.lanzar()
    p = env.esperar(cid)
    assert p["estado"] == "error" and "HTTP 500" in p["error"]
    # error de red: el texto original de la excepción trae la URL y no debe llegar al mensaje; se reintenta una vez
    f.fallos.clear()
    f.red_caida, antes = 2, len(f.llamadas)
    env.lanzar()
    p = env.esperar(cid)
    assert p["estado"] == "error" and "No se pudo contactar el flujo" in p["error"] and not any(s in json.dumps(p) for s in SECRETOS)
    assert len(f.llamadas) == antes and f.red_caida == 0  # las dos tentativas fallaron en el transporte
    # con un solo fallo de red, el reintento lo resuelve
    f.red_caida = 1
    env.lanzar()
    p = env.esperar(cid)
    assert p["estado"] == "listo" and p["error"] is None and p["procesados"] == 1


def test_error_de_red_en_un_archivo_se_reintenta_una_vez(env):
    f = env.fake
    f.archivo(["OPERADORES", "X.PEREZ", "01.- Cédula.pdf"], pdf(CED))
    f.archivo(["OPERADORES", "X.PEREZ", "02.- Licencia.pdf"], pdf(LIC))
    transporte_real = f.transporte
    fallos = {"n": 0}

    def inestable(url, cuerpo, timeout):
        if b'"getFile"' in cuerpo and b"02.-" in cuerpo and fallos["n"] < 1:
            fallos["n"] += 1
            raise ConnectionResetError("conexión cerrada por " + url)
        return transporte_real(url, cuerpo, timeout)

    env.servicio.transporte = inestable
    cid, p = env.analizar()
    assert p["estado"] == "listo" and p["procesados"] == 2 and p["con_error"] == 0 and fallos["n"] == 1


def test_cliente_flujo_unitario():
    def con(respuesta, llamadas=None):
        def transporte(url, cuerpo, timeout):
            if llamadas is not None:
                llamadas.append(json.loads(cuerpo))
            if isinstance(respuesta, Exception):
                raise respuesta
            return respuesta
        return ClienteFlujo(FLUJO_URL, SITIO_URL, transporte=transporte, espera_reintento=0)

    for carga, texto in [((200, b"no es json"), "formato"), ((200, b'{"ok": false, "error": {"code": "not_found"}}'), "no encontró"),
                         ((404, b'{"ok": false, "error": {"code": "inventado"}}'), "inventado"), ((400, b""), "HTTP 400"),
                         ((200, b'{"ok": true}'), None)]:
        c = con(carga)
        if texto is None:
            assert c.listar("/sites/x/y/z") == {"ok": True}
            continue
        with pytest.raises(ErrorFlujo) as e:
            c.listar("/sites/x/y/z")
        assert texto in str(e.value) and not any(s in str(e.value) for s in SECRETOS)
    llamadas: list = []
    with pytest.raises(ErrorFlujo) as e:
        con((503, b""), llamadas).listar("/sites/x/y/z")
    assert len(llamadas) == 2 and "503" in str(e.value)  # 503 se reintenta una vez
    llamadas.clear()
    with pytest.raises(ErrorFlujo) as e:
        con(TimeoutError(FLUJO_URL), llamadas).descargar("/sites/x/y/a.pdf", 1024)
    assert len(llamadas) == 2 and "tardó demasiado" in str(e.value) and e.value.__cause__ is None and FIRMA not in repr(e.value)
    assert llamadas[0] == {"action": "getFile", "siteUrl": SITIO_URL, "path": "/sites/x/y/a.pdf"}
    # el reintento no oculta un error de contenido: no se reintenta
    llamadas.clear()
    with pytest.raises(ErrorFlujo):
        con((200, json.dumps({"ok": True, "contentBase64": ""}).encode()), llamadas).descargar("/sites/x/y/a.pdf", 1024)
    assert len(llamadas) == 1
    assert FIRMA not in repr(con((200, b"{}"))) and "ClienteFlujo" in repr(con((200, b"{}")))
    assert redactar(f"fallo en {FLUJO_URL} (sig={FIRMA})", FLUJO_URL) == "fallo en [flujo] (sig=[flujo])"
    assert redactar(f"fallo en {FLUJO_URL.split('?')[0]}?sig={FIRMA}", FLUJO_URL).count(FIRMA) == 0


# --- concurrencia, parciales y reanudación -----------------------------------------------------------------


def test_ya_analizando_no_lanza_otra_y_resultados_parciales(env):
    f = env.fake
    ref_a = f.archivo(["OPERADORES", "X.PEREZ", "01.- Cédula.pdf"], pdf(CED))
    ref_b = f.archivo(["OPERADORES", "X.PEREZ", "02.- Licencia.pdf"], pdf(LIC))
    libre = f.bloquear(ref_b)
    try:
        r1 = env.lanzar()
        cid = r1.json()["id"]
        limite = time.time() + 120
        while time.time() < limite and f.contar("getFile", ref_b) == 0:
            time.sleep(0.05)
        assert f.contar("getFile", ref_b) == 1  # la segunda descarga está en curso
        r2 = env.lanzar()
        assert r2.status_code == 202 and r2.json()["id"] == cid and r2.json()["estado"] == "analizando"
        assert f.contar("listDeepest") == 1  # no se lanzó otro análisis
        p = env.cliente.get(f"/convocatorias/{cid}").json()
        assert p["estado"] == "analizando" and p["total"] == 2 and p["terminado_en"] is None
        docs = env.documentos(cid)  # parciales: lo ya procesado se ve, lo demás figura como pendiente
        assert docs["01.- Cédula.pdf"]["estado"] == "listo" and docs["01.- Cédula.pdf"]["documento"]
        assert docs["02.- Licencia.pdf"]["estado"] == "pendiente" and docs["02.- Licencia.pdf"]["documento"] is None
        assert env.resultados(cid)["cargos"][0]["postulantes"][0]["expediente"] is None  # aún sin contraste
        assert env.resultados(cid)["convocatoria"]["estado"] == "analizando"
    finally:
        libre.set()
    p = env.esperar(cid)
    assert p["estado"] == "listo" and p["procesados"] == 2 and f.contar("getFile", ref_a) == 1
    assert env.resultados(cid)["cargos"][0]["postulantes"][0]["expediente"]["identidad"]["rut"] == "12794536-5"


def test_reanudacion_tras_reinicio(cfg_tmp, tmp_path):
    fake = FlujoFalso()
    e1 = montar(cfg_tmp, tmp_path, fake)
    refs = [fake.archivo(["OPERADORES", "X.PEREZ", n], pdf(rel)) for n, rel in
            [("01.- Cédula.pdf", CED), ("02.- Licencia.pdf", LIC), ("03.- Antecedentes.pdf", ANT)]]
    libre = fake.bloquear(refs[1])
    cid = e1.lanzar().json()["id"]
    limite = time.time() + 120
    while time.time() < limite and fake.contar("getFile", refs[1]) == 0:
        time.sleep(0.05)
    e1.servicio._detener.set()  # el servicio se apaga con la 2.ª descarga en curso
    libre.set()
    e1.servicio.esperar(cid)
    en_disco = json.loads((e1.raiz / cid / "convocatoria.json").read_text(encoding="utf-8"))
    estados = [a["estado"] for a in en_disco["arbol"][0]["postulantes"][0]["archivos"]]
    assert en_disco["estado"] == "analizando" and estados == ["listo", "listo", "pendiente"]  # a mitad, con el estado en disco
    assert not (e1.raiz / cid / "OPERADORES" / "X.PEREZ" / "_expediente.json").exists()

    # reinicio: otra instancia del servicio sobre los mismos archivos
    e2 = montar(cfg_tmp, tmp_path, fake)
    p = e2.cliente.get(f"/convocatorias/{cid}").json()
    assert p["estado"] == "error" and "interrumpió" in p["error"] and p["procesados"] == 2
    parcial = e2.documentos(cid)
    assert parcial["01.- Cédula.pdf"]["estado"] == "listo" and parcial["03.- Antecedentes.pdf"]["estado"] == "pendiente"
    descargas = [fake.contar("getFile", r) for r in refs]
    assert e2.lanzar().status_code == 202
    p = e2.esperar(cid)
    assert p["estado"] == "listo" and (p["total"], p["procesados"]) == (3, 3)
    assert [fake.contar("getFile", r) - d for r, d in zip(refs, descargas)] == [0, 0, 1]  # solo se baja lo que faltaba
    assert e2.resultados(cid)["cargos"][0]["postulantes"][0]["expediente"]["identidad"]["rut"] == "12794536-5"
    e2.servicio.cerrar()
    fake.cerrar()


# --- el secreto no aparece en ninguna salida ---------------------------------------------------------------


def test_la_url_del_flujo_no_aparece_en_ninguna_salida(env, caplog):
    caplog.set_level(logging.DEBUG)
    f = env.fake
    f.archivo(["OPERADORES", "X.PEREZ", "01.- Cédula.pdf"], pdf(CED))
    f.archivo(["OPERADORES", "X.PEREZ", "02.- Licencia.pdf"], pdf(LIC))
    f.fallos[("getFile", f.base + "/OPERADORES/X.PEREZ/02.- Licencia.pdf")] = "ok_false"
    salidas: list[str] = []

    def anotar(r):
        salidas.append(f"{r.status_code} {r.text}")
        return r

    # entradas inválidas que traen la URL secreta
    for cuerpo in ({"flujo_url": FLUJO_URL.replace("https", "http")}, {"flujo_url": FLUJO_URL, "sitio_url": 5},
                   {"flujo_url": FLUJO_URL, "ruta": "/mal"}, {"flujo_url": FLUJO_URL}, [FLUJO_URL]):
        anotar(env.cliente.post("/convocatorias", json=cuerpo))
    anotar(env.cliente.post("/convocatorias", content=('{"flujo_url": "' + FLUJO_URL + '"').encode(), headers={"Content-Type": "application/json"}))
    r = anotar(env.lanzar())
    cid = r.json()["id"]
    anotar(env.cliente.get(f"/convocatorias/{cid}"))
    p = env.esperar(cid)
    assert p["estado"] == "listo" and p["con_error"] == 1
    anotar(env.cliente.get(f"/convocatorias/{cid}"))
    anotar(env.cliente.get(f"/convocatorias/{cid}/resultados"))
    # y un error del flujo y uno de red (con la URL dentro del texto de la excepción)
    f.fallos[("listDeepest", None)] = "ok_false"
    env.lanzar()
    anotar(env.cliente.get(f"/convocatorias/{cid}"))
    env.esperar(cid)
    f.fallos.clear()
    f.red_caida = 2
    env.lanzar()
    env.esperar(cid)
    anotar(env.cliente.get(f"/convocatorias/{cid}"))
    anotar(env.cliente.get(f"/convocatorias/{cid}/resultados"))

    assert any(FIRMA in x for x in f.recibidos)  # la prueba no es vacía: el flujo sí recibió la URL
    todo = "\n".join(salidas) + "\n" + caplog.text
    for raiz, _, archivos in os.walk(env.raiz.parent):  # JSON de resultados, base SQLite, archivos guardados
        todo += "".join(Path(raiz, a).read_bytes().decode("latin-1") for a in archivos)
    for secreto in SECRETOS:
        assert secreto not in todo, f"Apareció el secreto {secreto!r}"
    assert "sig=" not in ("\n".join(salidas) + caplog.text)
