#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# Escaneo de CVEs de las imágenes de los microservicios con Trivy (Módulo C).
# Los reportes JSON quedan en security/reports/ y cve-exporter los publica como
# métricas (cve_active) para el dashboard "Golden Signals de Seguridad".
#
# Equivalente en nube: Artifact Analysis (GCP) / Amazon Inspector (AWS).
# Uso: ./security/scan_images.sh
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail
export PATH="$HOME/.docker/bin:$PATH"
DIR="$(cd "$(dirname "$0")" && pwd)"
TRIVY="aquasec/trivy:0.56.2"

for svc in service-a service-b data-service; do
  img="otellabs-${svc}:latest"
  echo ">> trivy image $img"
  docker run --rm \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v trivy-cache:/root/.cache/trivy \
    -v "$DIR/reports:/reports" \
    "$TRIVY" image --quiet --scanners vuln --format json --output "/reports/${svc}.json" "$img"
  python3 - "$DIR/reports/${svc}.json" <<'EOF'
import json, sys, collections
rep = json.load(open(sys.argv[1])); c = collections.Counter()
seen = set()
for r in rep.get("Results", []) or []:
    for v in r.get("Vulnerabilities", []) or []:
        k = (v["VulnerabilityID"], v.get("PkgName"))
        if k not in seen:
            seen.add(k); c[v.get("Severity", "UNKNOWN")] += 1
print("   ", {s: c[s] for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW")})
EOF
done
