from __future__ import annotations

from fastapi.testclient import TestClient

from stracon_ocr.api import crear_app

from .conftest import mockup


def test_flujo_api(cfg_tmp):
    cliente = TestClient(crear_app(cfg_tmp))
    s = cliente.get("/salud").json()
    assert s["estado"] == "ok" and s["motores"]["rapidocr"]
    assert len(cliente.get("/tipos").json()) == 6
    assert "Consola de prueba".lower() in cliente.get("/").text.lower()

    ruta = mockup("12794536-5/2-licencia-conducir.pdf")
    r = cliente.post("/documentos", files={"archivo": (ruta.name, ruta.read_bytes(), "application/pdf")}, data={"expediente": "12794536-5"})
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["clasificacion"]["tipo"] == "licencia_conducir"
    assert doc["resumen"]["requiere_revision"] is True
    assert any(s["id"] == "pdf.fuentes_mezcladas_campo" for s in doc["senales"])

    assert cliente.get(f"/documentos/{doc['id']}").json()["id"] == doc["id"]
    img = cliente.get(f"/documentos/{doc['id']}/paginas/1/imagen")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert cliente.get(f"/documentos/{doc['id']}/paginas/9/imagen").status_code == 404
    assert cliente.get(f"/documentos/{doc['id']}/archivo").content[:5] == b"%PDF-"
    assert cliente.get("/documentos", params={"expediente": "12794536-5"}).json()[0]["id"] == doc["id"]
    assert cliente.get("/buscar", params={"q": "transito"}).json()  # FTS sin tildes

    nuevo = cliente.post(f"/documentos/{doc['id']}/reprocesar")
    assert nuevo.status_code == 200 and nuevo.json()["id"] != doc["id"]
    assert cliente.get(f"/documentos/{doc['id']}").status_code == 404  # el resultado anterior se reemplaza
    assert cliente.post("/documentos/no-existe/reprocesar").status_code == 404

    exp = cliente.post("/expedientes/12794536-5/contraste").json()
    assert exp["identidad"]["rut"] == "12794536-5"
    assert cliente.get("/expedientes/12794536-5").status_code == 200

    malo = cliente.post("/documentos", files={"archivo": ("x.txt", b"hola mundo", "text/plain")})
    assert malo.status_code == 415


def test_cargas_simultaneas(cfg_tmp):
    """Con procesos de trabajo reales: 6 documentos a la vez, ninguno se pierde ni falla."""
    from concurrent.futures import ThreadPoolExecutor

    cfg_tmp.trabajadores = 2
    archivos = [mockup(f"{rut}/{n}") for rut in ("12794536-5", "16884102-7") for n in
                ("1-cedula-identidad.pdf", "2-licencia-conducir.pdf", "6-certificado-antecedentes.pdf")]
    with TestClient(crear_app(cfg_tmp)) as cliente:
        assert cliente.get("/salud").json()["trabajadores"] == 2

        def subir(ruta):
            return cliente.post("/documentos", files={"archivo": (ruta.name, ruta.read_bytes(), "application/pdf")},
                                data={"expediente": ruta.parent.name})

        with ThreadPoolExecutor(6) as hilos:
            respuestas = list(hilos.map(subir, archivos))
        assert [r.status_code for r in respuestas] == [200] * 6, [r.text[:200] for r in respuestas]
        assert {r.json()["clasificacion"]["tipo"] for r in respuestas} == {"cedula_identidad", "licencia_conducir", "certificado_antecedentes"}
        assert len(cliente.get("/documentos").json()) == 6


def test_trabajo_asincrono(cfg_tmp):
    import time

    cliente = TestClient(crear_app(cfg_tmp))
    ruta = mockup("12794536-5/6-certificado-antecedentes.pdf")
    r = cliente.post("/trabajos", files=[("archivos", (ruta.name, ruta.read_bytes(), "application/pdf"))], data={"expediente": "12794536-5"})
    assert r.status_code == 202
    tid = r.json()["trabajos"][0]["id"]
    for _ in range(120):
        t = cliente.get(f"/trabajos/{tid}", params={"incluir_documento": True}).json()
        if t["estado"] in {"listo", "error"}:
            break
        time.sleep(0.5)
    assert t["estado"] == "listo", t
    assert t["documento"]["clasificacion"]["tipo"] == "certificado_antecedentes"
