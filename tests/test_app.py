import json

import pytest

from gnss_check import sample_data
from gnss_check.app import app, compute_geometry
from gnss_check.report import build_report_html, html_to_pdf


@pytest.fixture()
def client():
    return app.server.test_client()


def test_index_and_layout_served(client):
    assert client.get("/").status_code == 200
    layout = client.get("/_dash-layout")
    assert layout.status_code == 200
    text = layout.get_data(as_text=True)
    for component_id in ("utm-zone", "hemisphere", "epsg-code", "gyro-table", "geometry-output"):
        assert f'"{component_id}"' in text


def _update(client, output, inputs, state=None, changed=None):
    payload = {
        "output": output,
        "outputs": [{"id": o.split(".")[0], "property": o.split(".")[1]} for o in output.strip(".").split("...")],
        "inputs": inputs,
        "state": state or [],
        "changedPropIds": changed or [f"{inputs[0]['id']}.{inputs[0]['property']}"],
    }
    response = client.post("/_dash-update-component", json=payload)
    assert response.status_code == 200, response.get_data(as_text=True)
    return json.loads(response.get_data(as_text=True))["response"]


def test_geometry_callback_end_to_end(client):
    inst, bs = sample_data.INSTRUMENT, sample_data.BACKSIGHT
    values = {
        "crs-mode": "utm", "utm-zone": 15, "hemisphere": "North", "epsg-code": None,
        "easting": inst["easting"], "northing": inst["northing"],
        "backsight-easting": bs["easting"], "backsight-northing": bs["northing"],
        "approx-lat": None, "approx-lon": None,
    }
    inputs = [{"id": k, "property": "value", "value": v} for k, v in values.items()]
    output = ("..crs-feedback.children...geometry-output.children...utm-zone.disabled"
              "...hemisphere.disabled...epsg-code.disabled..")
    response = _update(client, output, inputs)
    rendered = json.dumps(response["geometry-output"]["children"])
    assert "-000\\u00b0 35' 19.67\\\"" in rendered  # grid convergence at the demo station
    assert "0.99976317" in rendered


def test_horizontal_callback_with_demo_session(client):
    inst, bs = sample_data.INSTRUMENT, sample_data.BACKSIGHT
    geometry = [
        ("crs-mode", "utm"), ("utm-zone", 15), ("hemisphere", "North"), ("epsg-code", None),
        ("easting", inst["easting"]), ("northing", inst["northing"]),
        ("backsight-easting", bs["easting"]), ("backsight-northing", bs["northing"]),
        ("approx-lat", 29.69), ("approx-lon", -94.19),
    ]
    extra = [
        ("backsight-observation-degrees", 0), ("backsight-observation-minutes", 0),
        ("backsight-observation-seconds", 0.0),
    ]
    state = [{"id": k, "property": "value", "value": v} for k, v in geometry + extra]
    state.append({"id": "gyro-table", "property": "data", "value": sample_data.gyro_rows()})
    state.append({"id": "apply-scale-factor", "property": "value", "value": True})
    output = "..results-table.children...statistical-analysis.children...horizontal-store.data.."
    response = _update(client, output, [{"id": "submit-button", "property": "n_clicks", "value": 1}], state)

    store = response["horizontal-store"]["data"]
    assert store["stats"]["Successful"] == 12 and store["stats"]["Rejected"] == 0
    linear = next(r for r in store["summary"] if r["Quantity"] == "Linear misclose")
    assert linear["Mean (m)"] < 0.04


def test_compute_geometry_epsg_equivalent_to_utm():
    inst, bs = sample_data.INSTRUMENT, sample_data.BACKSIGHT
    args = (inst["easting"], inst["northing"], bs["easting"], bs["northing"])
    utm = compute_geometry("utm", 15, "North", None, *args)
    epsg = compute_geometry("epsg", None, None, "32615", *args)
    assert utm.ok and epsg.ok
    assert utm.factors == epsg.factors
    assert utm.bearing == pytest.approx(36.8698976, abs=1e-6)


def test_pdf_report_is_generated_and_escapes_input():
    html = build_report_html({
        "job": {"Client": "<script>alert(1)</script>"},
        "geodesy": {"Grid Convergence": "+000° 00' 01.00\""},
        "horizontal_rows": [{"UTC Time": "01:00:00", "Linear Misclose (m)": 0.012345}],
        "horizontal_stats": {"Linear misclose mean (m)": "0.012"},
    })
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "0.012" in html
    pdf = html_to_pdf(html)
    assert pdf is not None and pdf.startswith(b"%PDF")
