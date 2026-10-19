#!/usr/bin/env python3
"""
cve-exporter — Expone como métricas Prometheus los CVEs activos encontrados por
Trivy en las imágenes del sistema (Golden Signals de Seguridad, Módulo C).

Lee los reportes JSON de `security/scan_images.sh` (trivy image --format json) y publica:
  cve_active{image, severity}            CVEs abiertos por imagen y severidad
  cve_fixable{image, severity}           de ellos, cuántos tienen versión corregida
  cve_scan_timestamp_seconds{image}      cuándo se escaneó

Equivalente en nube: Artifact Analysis (GCP) / Amazon Inspector → Security Command
Center / Security Hub (ver infra/terraform/*/security.tf).
"""
import glob
import json
import os
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPORTS = os.getenv("TRIVY_REPORTS", "/reports")


def collect():
    lines = ["# TYPE cve_active gauge", "# TYPE cve_fixable gauge", "# TYPE cve_scan_timestamp_seconds gauge"]
    for path in sorted(glob.glob(f"{REPORTS}/*.json")):
        try:
            rep = json.load(open(path))
        except ValueError:
            continue
        image = rep.get("ArtifactName", os.path.basename(path))
        active, fixable, seen = Counter(), Counter(), set()
        for res in rep.get("Results", []) or []:
            for v in res.get("Vulnerabilities", []) or []:
                key = (v["VulnerabilityID"], v.get("PkgName"))
                if key in seen:
                    continue
                seen.add(key)
                sev = v.get("Severity", "UNKNOWN")
                active[sev] += 1
                if v.get("FixedVersion"):
                    fixable[sev] += 1
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"):
            lines.append(f'cve_active{{image="{image}",severity="{sev}"}} {active[sev]}')
            lines.append(f'cve_fixable{{image="{image}",severity="{sev}"}} {fixable[sev]}')
        lines.append(f'cve_scan_timestamp_seconds{{image="{image}"}} {os.path.getmtime(path):.0f}')
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = collect().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print("cve-exporter escuchando en :9200", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 9200), Handler).serve_forever()
