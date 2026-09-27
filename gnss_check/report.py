"""HTML/PDF report generation."""

from __future__ import annotations

import io
from html import escape

from xhtml2pdf import pisa

_STYLE = """
body { font-family: Helvetica, Arial, sans-serif; font-size: 9pt; }
h1 { font-size: 16pt; text-align: center; margin-bottom: 4pt; }
h2 { font-size: 12pt; margin-top: 12pt; border-bottom: 1px solid #888; }
table { width: 100%; border-collapse: collapse; }
th, td { border: 1px solid #bbb; padding: 3pt; text-align: left; }
th { background-color: #eeeeee; }
.note { color: #555; font-size: 8pt; }
"""


def fmt(value, decimals: int = 3) -> str:
    if value is None or value == "":
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{decimals}f}"
    return str(value)


def _kv_table(items: dict) -> str:
    rows = "".join(f"<tr><th>{escape(str(k))}</th><td>{escape(fmt(v))}</td></tr>" for k, v in items.items())
    return f"<table>{rows}</table>"


def _rows_table(rows: list[dict]) -> str:
    if not rows:
        return "<p>No results.</p>"
    columns = list(rows[0].keys())
    head = "".join(f"<th>{escape(c)}</th>" for c in columns)
    body = "".join("<tr>" + "".join(f"<td>{escape(fmt(r[c]))}</td>" for c in columns) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def build_report_html(report: dict) -> str:
    """``report`` keys: job, geodesy, horizontal_rows, horizontal_stats, vertical_rows, vertical_stats."""
    sections = [
        "<h1>Static 3D GNSS Position Check</h1>",
        "<h2>Job Details</h2>",
        _kv_table(report.get("job", {})),
        "<h2>Coordinate Reference System &amp; Grid Factors</h2>",
        _kv_table(report.get("geodesy", {})),
        "<p class='note'>Grid convergence sign: positive when grid north is east of true north "
        "(grid bearing = true azimuth - convergence).</p>",
    ]
    if report.get("horizontal_rows"):
        sections += [
            "<h2>Horizontal Check</h2>",
            _rows_table(report["horizontal_rows"]),
            _kv_table(report.get("horizontal_stats", {})),
        ]
    if report.get("vertical_rows"):
        sections += [
            "<h2>Vertical Check</h2>",
            _rows_table(report["vertical_rows"]),
            _kv_table(report.get("vertical_stats", {})),
        ]
    return (
        "<html><head><meta charset='utf-8'><title>GNSS Position Check Report</title>"
        f"<style>{_STYLE}</style></head><body>{''.join(sections)}</body></html>"
    )


def html_to_pdf(html_content: str) -> bytes | None:
    buffer = io.BytesIO()
    status = pisa.CreatePDF(html_content, dest=buffer, encoding="utf-8")
    if status.err:
        return None
    return buffer.getvalue()
