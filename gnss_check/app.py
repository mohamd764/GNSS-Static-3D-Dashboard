"""Dash front end for the static 3D GNSS position check."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, dash_table, dcc, html
from pyproj import CRS

from . import sample_data
from .angles import decimal_to_dms, dms_to_decimal
from .computations import horizontal_check, to_float, vertical_check
from .geodesy import (
    CRSInputError,
    GridFactors,
    area_of_use_warning,
    crs_label,
    grid_bearing,
    grid_factors,
    haversine_distance_m,
    resolve_crs,
    true_azimuth_from_grid_bearing,
    utm_zone_from_lonlat,
)
from .report import build_report_html, fmt, html_to_pdf

N_OBSERVATION_ROWS = 20
APPROX_POSITION_TOLERANCE_M = 50_000

OBSERVATION_COLUMNS = [
    ("utc_time", "UTC Time (hh:mm:ss)", "text"),
    ("direction_deg", "Direction Deg", "numeric"),
    ("direction_min", "Direction Min", "numeric"),
    ("direction_sec", "Direction Sec", "numeric"),
    ("obs_distance", "Horizontal Distance (m)", "numeric"),
    ("obs_v_dist", "Vertical Distance (m)", "numeric"),
    ("height", "Target Offset (m)", "numeric"),
    ("dgps_e", "GNSS Easting (m)", "numeric"),
    ("dgps_n", "GNSS Northing (m)", "numeric"),
    ("dgps_h", "GNSS Height (m)", "numeric"),
]

JOB_FIELDS = [
    ("job-number", "Job Number"),
    ("job-description", "Job Description"),
    ("client", "Client"),
    ("party-chief", "Party Chief"),
    ("surveyor", "Surveyor"),
    ("site", "Site / Berth"),
    ("vessel", "Vessel"),
    ("device-name", "GNSS Device"),
]


def empty_observations() -> list[dict]:
    return [{key: None for key, _, _ in OBSERVATION_COLUMNS} for _ in range(N_OBSERVATION_ROWS)]


# ------------------------------------------------------------------ geometry


@dataclass
class Geometry:
    crs: CRS | None = None
    factors: GridFactors | None = None
    bearing: float | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and self.factors is not None and self.bearing is not None

    def report_items(self) -> dict:
        if not self.ok:
            return {"Status": self.error or "Incomplete"}
        f = self.factors
        return {
            "Coordinate Reference System": crs_label(self.crs),
            "Instrument Station Latitude": f"{f.latitude:.8f}°",
            "Instrument Station Longitude": f"{f.longitude:.8f}°",
            "Grid Bearing to Backsight": decimal_to_dms(self.bearing, wrap360=True),
            "True Azimuth to Backsight": decimal_to_dms(
                true_azimuth_from_grid_bearing(self.bearing, f.convergence_deg), wrap360=True
            ),
            "Grid Convergence": decimal_to_dms(f.convergence_deg, signed=True),
            "Point Scale Factor": f"{f.scale_factor:.8f}",
        }


def compute_geometry(mode, zone, hemisphere, epsg, e1, n1, e2, n2, approx_lat=None, approx_lon=None) -> Geometry:
    geom = Geometry()
    try:
        geom.crs = resolve_crs(mode, zone, hemisphere, epsg)
    except CRSInputError as exc:
        geom.error = str(exc)
        return geom

    e1, n1, e2, n2 = (to_float(v) for v in (e1, n1, e2, n2))
    if None in (e1, n1):
        geom.error = "Enter the instrument station easting and northing."
        return geom
    try:
        geom.factors = grid_factors(e1, n1, geom.crs)
    except Exception:
        geom.error = f"Instrument station coordinates are not valid in {crs_label(geom.crs)}."
        return geom

    f = geom.factors
    if warning := area_of_use_warning(f.latitude, f.longitude, geom.crs):
        geom.warnings.append(warning)
    lat0, lon0 = to_float(approx_lat), to_float(approx_lon)
    if lat0 is not None and lon0 is not None:
        dist = haversine_distance_m(lat0, lon0, f.latitude, f.longitude)
        if dist > APPROX_POSITION_TOLERANCE_M:
            geom.warnings.append(
                f"Instrument station is {dist / 1000:,.0f} km from the approximate site position; "
                "check the zone, hemisphere or EPSG code."
            )

    if None in (e2, n2):
        geom.error = "Enter the backsight easting and northing."
        return geom
    try:
        geom.bearing = grid_bearing(e1, n1, e2, n2)
    except ValueError as exc:
        geom.error = str(exc)
    return geom


# ------------------------------------------------------------------ layout helpers


def labeled(label: str, component, width: int = 3, md: int | None = None):
    return dbc.Col([dbc.Label(label, html_for=component.id, className="small fw-semibold mb-1"), component],
                   xs=12, md=md or 6, lg=width, className="mb-3")


def num_input(id_: str, placeholder: str = "", **kwargs):
    return dbc.Input(id=id_, type="number", placeholder=placeholder, **kwargs)


def card(title: str, body, subtitle: str | None = None):
    header = [html.H5(title, className="mb-0")]
    if subtitle:
        header.append(html.Small(subtitle, className="text-muted"))
    return dbc.Card([dbc.CardHeader(header), dbc.CardBody(body)], className="mb-4 shadow-sm")


def results_table(rows: list[dict], decimals: int = 3):
    if not rows:
        return html.Div("No valid observations to display.", className="text-muted")
    columns = list(rows[0].keys())
    return dbc.Table(
        [html.Thead(html.Tr([html.Th(c) for c in columns])),
         html.Tbody([html.Tr([html.Td(fmt(r[c], decimals)) for c in columns]) for r in rows])],
        bordered=True, striped=True, hover=True, size="sm", responsive=True, className="small",
    )


def kv_list(items: dict):
    return html.Ul([html.Li([html.Strong(f"{k}: "), fmt(v)]) for k, v in items.items()], className="mb-0")


def summary_rows(named_summaries: dict) -> list[dict]:
    return [
        {"Quantity": name, "Count": s.count, "Mean (m)": s.mean, "Std Dev (m)": s.std,
         "RMS (m)": s.rms, "Max |value| (m)": s.maximum}
        for name, s in named_summaries.items()
        if s is not None and s.count
    ]


def build_layout():
    job_inputs = [labeled(label, dbc.Input(id=id_, type="text")) for id_, label in JOB_FIELDS]
    job_inputs.append(labeled("Date", dcc.DatePickerSingle(id="date", display_format="YYYY-MM-DD",
                                                           placeholder="Select a date")))
    job_inputs.append(labeled("Vertical Datum", dbc.Input(id="vertical-datum", type="text",
                                                          placeholder="e.g. NAVD88, chart datum")))

    crs_body = [
        dbc.RadioItems(
            id="crs-mode",
            options=[{"label": "WGS 84 / UTM zone + hemisphere", "value": "utm"},
                     {"label": "EPSG code (any metre-based projected CRS)", "value": "epsg"}],
            value="utm", inline=True, className="mb-3",
        ),
        dbc.Row([
            labeled("UTM Zone", dcc.Dropdown(id="utm-zone", options=[{"label": f"Zone {z}", "value": z}
                                                                     for z in range(1, 61)],
                                             placeholder="Select zone")),
            labeled("Hemisphere", dbc.Select(id="hemisphere", options=[{"label": h, "value": h}
                                                                       for h in ("North", "South")],
                                             placeholder="Select hemisphere")),
            labeled("EPSG Code", dbc.Input(id="epsg-code", type="text", placeholder="e.g. 26915, 7855")),
        ]),
        dbc.Row([
            labeled("Approx. Site Latitude (°)", num_input("approx-lat", "e.g. 29.69", min=-90, max=90)),
            labeled("Approx. Site Longitude (°)", num_input("approx-lon", "e.g. -94.9", min=-180, max=180)),
            dbc.Col(dbc.Button("Detect UTM zone from lat/lon", id="detect-zone-button", color="outline-primary",
                               className="w-100"), xs=12, md=6, lg=3, className="mb-3 d-flex align-items-end"),
        ]),
        html.Div(id="crs-feedback"),
    ]

    stations_body = [
        html.H6("Instrument Station", className="text-primary"),
        dbc.Row([
            labeled("Name", dbc.Input(id="instrument-station", type="text")),
            labeled("Easting (m)", num_input("easting")),
            labeled("Northing (m)", num_input("northing")),
            labeled("Height (m)", num_input("station-height")),
            labeled("Instrument Height (m)", num_input("instrument-height")),
        ]),
        html.H6("Backsight Station", className="text-primary"),
        dbc.Row([
            labeled("Name", dbc.Input(id="backsight-station", type="text")),
            labeled("Easting (m)", num_input("backsight-easting")),
            labeled("Northing (m)", num_input("backsight-northing")),
            labeled("Height (m)", num_input("backsight-height")),
        ]),
        html.H6("Backsight Reading (DMS)", className="text-primary"),
        dbc.Row([
            labeled("Degrees", num_input("backsight-observation-degrees", min=0, max=359, step=1), width=2),
            labeled("Minutes", num_input("backsight-observation-minutes", min=0, max=59, step=1), width=2),
            labeled("Seconds", num_input("backsight-observation-seconds", min=0, max=59.99, step=0.01), width=2),
        ]),
    ]

    observations_body = [
        dash_table.DataTable(
            id="gyro-table",
            columns=[{"name": name, "id": key, "type": typ, "editable": True} for key, name, typ in OBSERVATION_COLUMNS],
            data=empty_observations(),
            editable=True,
            style_table={"overflowX": "auto"},
            style_cell={"minWidth": "110px", "whiteSpace": "normal", "textAlign": "right", "fontSize": "14px"},
            style_header={"backgroundColor": "#e9ecef", "fontWeight": "bold", "textAlign": "center"},
        ),
        dbc.Checkbox(
            id="apply-scale-factor",
            label="Reduce horizontal distances to grid (multiply by point scale factor). "
                  "Leave unticked if the instrument already applies the grid scale factor.",
            value=False, className="mt-3",
        ),
    ]

    return dbc.Container([
        dbc.Navbar(
            dbc.Container([
                html.Div([
                    html.Span("Static 3D GNSS Position Check", className="navbar-brand mb-0 h1"),
                    html.Small("Total-station verification of a static GNSS antenna position",
                               className="text-white-50 d-block"),
                ]),
                dbc.Button("Load demo data", id="load-sample-button", color="light", size="sm"),
            ], fluid=True),
            color="dark", dark=True, className="mb-4 rounded-bottom",
        ),
        card("Job Details", dbc.Row(job_inputs)),
        card("Coordinate Reference System", crs_body,
             "An easting/northing does not identify its UTM zone - select it explicitly."),
        card("Stations", stations_body),
        card("Grid Geometry", html.Div(id="geometry-output")),
        card("Observations", observations_body,
             "Total-station direction, distance and vertical distance to the antenna prism, "
             "with the GNSS position logged at the same epoch."),
        dbc.Row([
            dbc.Col(dbc.Button("Compute Horizontal Check", id="submit-button", color="primary", className="w-100"),
                    md=4, className="mb-2"),
            dbc.Col(dbc.Button("Compute Vertical Check", id="vertical-submit-button", color="info",
                               className="w-100"), md=4, className="mb-2"),
            dbc.Col(dbc.Button("Export PDF Report", id="export-pdf-button", color="secondary", className="w-100"),
                    md=4, className="mb-2"),
        ], className="mb-4"),
        dcc.Download(id="download-pdf"),
        dcc.Store(id="horizontal-store"),
        dcc.Store(id="vertical-store"),
        card("Horizontal Check Results", [html.Div(id="results-table"), html.Div(id="statistical-analysis",
                                                                                  className="mt-3")]),
        card("Vertical Check Results", html.Div(id="vertical-results")),
        html.Footer(html.Small("Demo data is synthetic. Computations use PROJ via pyproj.",
                               className="text-muted"), className="text-center mb-4"),
    ], fluid="lg")


# ------------------------------------------------------------------ callbacks

GEOMETRY_STATES = [
    State("crs-mode", "value"), State("utm-zone", "value"), State("hemisphere", "value"),
    State("epsg-code", "value"), State("easting", "value"), State("northing", "value"),
    State("backsight-easting", "value"), State("backsight-northing", "value"),
    State("approx-lat", "value"), State("approx-lon", "value"),
]
JOB_STATES = [State(id_, "value") for id_, _ in JOB_FIELDS] + [State("date", "date"),
                                                                State("vertical-datum", "value")]


def job_details(values) -> dict:
    labels = [label for _, label in JOB_FIELDS] + ["Date", "Vertical Datum"]
    return {label: (value if value not in (None, "") else "N/A") for label, value in zip(labels, values)}


def register_callbacks(app: dash.Dash) -> None:
    @app.callback(
        Output("utm-zone", "value"), Output("hemisphere", "value"), Output("crs-mode", "value"),
        Input("detect-zone-button", "n_clicks"),
        State("approx-lat", "value"), State("approx-lon", "value"),
        prevent_initial_call=True,
    )
    def detect_zone(_, lat, lon):
        lat, lon = to_float(lat), to_float(lon)
        if lat is None or lon is None:
            return dash.no_update, dash.no_update, dash.no_update
        try:
            zone, hemisphere = utm_zone_from_lonlat(lon, lat)
        except CRSInputError:
            return dash.no_update, dash.no_update, dash.no_update
        return zone, hemisphere, "utm"

    @app.callback(
        Output("crs-feedback", "children"), Output("geometry-output", "children"),
        Output("utm-zone", "disabled"), Output("hemisphere", "disabled"), Output("epsg-code", "disabled"),
        [Input(s.component_id, s.component_property) for s in GEOMETRY_STATES],
    )
    def update_geometry(mode, zone, hemisphere, epsg, e1, n1, e2, n2, approx_lat, approx_lon):
        geom = compute_geometry(mode, zone, hemisphere, epsg, e1, n1, e2, n2, approx_lat, approx_lon)
        feedback = []
        if geom.crs is not None:
            feedback.append(dbc.Alert(["Working CRS: ", html.Strong(crs_label(geom.crs))], color="light",
                                      className="py-2 mb-2"))
        for warning in geom.warnings:
            feedback.append(dbc.Alert(warning, color="warning", className="py-2 mb-2"))
        if geom.factors is not None or geom.ok:
            items = geom.report_items() if geom.ok else {
                "Instrument Station Latitude": f"{geom.factors.latitude:.8f}°",
                "Instrument Station Longitude": f"{geom.factors.longitude:.8f}°",
                "Grid Convergence": decimal_to_dms(geom.factors.convergence_deg, signed=True),
                "Point Scale Factor": f"{geom.factors.scale_factor:.8f}",
            }
            items.pop("Coordinate Reference System", None)
            body = [kv_list(items)]
            if geom.error:
                body.append(html.Div(geom.error, className="text-muted mt-2"))
        else:
            body = html.Div(geom.error, className="text-muted")
        utm = mode != "epsg"
        return feedback, body, not utm, not utm, utm

    @app.callback(
        Output("results-table", "children"), Output("statistical-analysis", "children"),
        Output("horizontal-store", "data"),
        Input("submit-button", "n_clicks"),
        GEOMETRY_STATES + [
            State("backsight-observation-degrees", "value"), State("backsight-observation-minutes", "value"),
            State("backsight-observation-seconds", "value"), State("gyro-table", "data"),
            State("apply-scale-factor", "value"),
        ],
        prevent_initial_call=True,
    )
    def submit_horizontal(_, mode, zone, hemisphere, epsg, e1, n1, e2, n2, approx_lat, approx_lon,
                          bs_deg, bs_min, bs_sec, observations, apply_scale):
        geom = compute_geometry(mode, zone, hemisphere, epsg, e1, n1, e2, n2, approx_lat, approx_lon)
        if not geom.ok:
            return dbc.Alert(geom.error, color="danger"), "", None
        backsight_reading = dms_to_decimal(bs_deg, bs_min, bs_sec)
        if backsight_reading is None:
            return dbc.Alert("Backsight reading (DMS) is missing or invalid.", color="danger"), "", None

        scale = geom.factors.scale_factor if apply_scale else 1.0
        result = horizontal_check(float(e1), float(n1), geom.bearing, backsight_reading, observations or [], scale)
        stats = {
            "Observations entered": result.observed_rows,
            "Successful": result.successful_rows,
            "Rejected": result.failed_rows,
            "Distance scale factor applied": f"{scale:.8f}",
        }
        summary = summary_rows({"Linear misclose": result.linear, "dE (calc - GNSS)": result.delta_e,
                                "dN (calc - GNSS)": result.delta_n})
        stats_children = [kv_list(stats), html.Div(results_table(summary), className="mt-3")]
        if result.errors:
            stats_children.append(dbc.Alert(html.Ul([html.Li(e) for e in result.errors]), color="warning",
                                            className="mt-3 mb-0"))
        store = {"rows": result.rows, "stats": stats, "summary": summary}
        return results_table(result.rows), stats_children, store

    @app.callback(
        Output("vertical-results", "children"), Output("vertical-store", "data"),
        Input("vertical-submit-button", "n_clicks"),
        State("station-height", "value"), State("instrument-height", "value"), State("gyro-table", "data"),
        prevent_initial_call=True,
    )
    def submit_vertical(_, station_height, instrument_height, observations):
        station_height, instrument_height = to_float(station_height), to_float(instrument_height)
        if station_height is None or instrument_height is None:
            return dbc.Alert("Enter both the instrument station height and the instrument height.",
                             color="danger"), None
        result = vertical_check(station_height, instrument_height, observations or [])
        summary = summary_rows({"dH (calc - GNSS)": result.misclose})
        body = html.Div([results_table(result.rows), html.Div(results_table(summary), className="mt-3")])
        return body, {"rows": result.rows, "summary": summary}

    @app.callback(
        Output("download-pdf", "data"),
        Input("export-pdf-button", "n_clicks"),
        GEOMETRY_STATES + JOB_STATES + [
            State("instrument-station", "value"), State("station-height", "value"),
            State("instrument-height", "value"), State("backsight-station", "value"),
            State("backsight-height", "value"),
            State("horizontal-store", "data"), State("vertical-store", "data"),
        ],
        prevent_initial_call=True,
    )
    def export_pdf(_, mode, zone, hemisphere, epsg, e1, n1, e2, n2, approx_lat, approx_lon, *rest):
        n_job = len(JOB_STATES)
        job_values, rest = rest[:n_job], rest[n_job:]
        inst_name, inst_h, hi, bs_name, bs_h, horizontal, vertical = rest
        geom = compute_geometry(mode, zone, hemisphere, epsg, e1, n1, e2, n2, approx_lat, approx_lon)
        job = job_details(job_values)
        job.update({
            "Instrument Station": inst_name, "Instrument Easting": e1, "Instrument Northing": n1,
            "Instrument Station Height": inst_h, "Instrument Height": hi,
            "Backsight Station": bs_name, "Backsight Easting": e2, "Backsight Northing": n2,
            "Backsight Height": bs_h,
        })
        horizontal, vertical = horizontal or {}, vertical or {}
        report = {
            "job": job,
            "geodesy": geom.report_items(),
            "horizontal_rows": horizontal.get("rows"),
            "horizontal_stats": horizontal.get("stats"),
            "horizontal_summary": horizontal.get("summary"),
            "vertical_rows": vertical.get("rows"),
            "vertical_summary": vertical.get("summary"),
        }
        pdf = html_to_pdf(build_report_html(report))
        if pdf is None:
            return dash.no_update
        name = str(job.get("Job Number") or "report").replace("/", "-").replace(" ", "_")
        return dcc.send_bytes(pdf, f"gnss_position_check_{name}.pdf")

    demo_outputs = (
        [Output(id_, "value", allow_duplicate=True) if id_ == "crs-mode" else Output(id_, "value")
         for id_ in [f for f, _ in JOB_FIELDS] + ["vertical-datum", "crs-mode", "approx-lat", "approx-lon",
                                                  "instrument-station", "easting", "northing", "station-height",
                                                  "instrument-height", "backsight-station", "backsight-easting",
                                                  "backsight-northing", "backsight-height",
                                                  "backsight-observation-degrees", "backsight-observation-minutes",
                                                  "backsight-observation-seconds"]]
        + [Output("utm-zone", "value", allow_duplicate=True), Output("hemisphere", "value", allow_duplicate=True),
           Output("date", "date"), Output("gyro-table", "data")]
    )

    @app.callback(demo_outputs, Input("load-sample-button", "n_clicks"), prevent_initial_call=True)
    def load_sample(_):
        job = sample_data.JOB_DETAILS
        inst, bs = sample_data.INSTRUMENT, sample_data.BACKSIGHT
        rows = sample_data.gyro_rows()
        rows += [{key: None for key, _, _ in OBSERVATION_COLUMNS} for _ in range(N_OBSERVATION_ROWS - len(rows))]
        return (
            [job[f] for f, _ in JOB_FIELDS]
            + ["NAVD88 (demo)", "utm", 29.69, -94.19,
               inst["name"], inst["easting"], inst["northing"], inst["height"], sample_data.INSTRUMENT_HEIGHT,
               bs["name"], bs["easting"], bs["northing"], bs["height"], *sample_data.BACKSIGHT_READING_DMS]
            + [sample_data.SAMPLE_ZONE, sample_data.SAMPLE_HEMISPHERE, job["date"], rows]
        )


def create_app() -> dash.Dash:
    app = dash.Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP], title="Static 3D GNSS Position Check")
    app.layout = build_layout()
    register_callbacks(app)
    return app


app = create_app()
server = app.server


def main() -> None:
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8050")),
        debug=os.environ.get("DASH_DEBUG", "0") == "1",
    )


if __name__ == "__main__":
    main()
