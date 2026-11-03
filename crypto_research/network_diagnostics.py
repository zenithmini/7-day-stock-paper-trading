"""Bounded public-data connectivity checks using the same Python as the app."""
import os
import platform
import ssl
import sys
from urllib.request import getproxies

from .market_data import MARKET_HOSTS, MarketDataError, probe_market_host


def diagnose_network():
    paths = ssl.get_default_verify_paths()
    proxy_configured = any(value for scheme, value in getproxies().items() if scheme in {"http", "https", "all"})
    results, stop = [], False
    for host in MARKET_HOSTS:
        if stop:
            results.append({"host": host, "status": "skipped", "reason": "A rate limit or access restriction stopped further probes."})
            continue
        try:
            results.append(probe_market_host(host))
        except MarketDataError as error:
            results.append({"host": host, "status": "failed", "error_kind": error.kind,
                            "http_status": error.http_status, "message": str(error)})
            if error.kind == "rate_limit" or error.http_status in (403, 451):
                stop = True
    return {
        "schema_version": 1, "python_version": platform.python_version(), "python_executable": sys.executable,
        "platform": platform.system(), "tls_verification": "enabled", "environment_proxies_used": False,
        "system_or_environment_proxy_configured": bool(proxy_configured),
        "ca_file_present": bool(paths.cafile and os.path.isfile(paths.cafile)),
        "ca_directory_present": bool(paths.capath and os.path.isdir(paths.capath)),
        "hosts": results, "any_host_verified": any(row["status"] == "ok" for row in results),
    }


def render_diagnostics(value):
    lines = ["Crypto research network diagnostics (no account or API keys)",
             "Python: " + value["python_version"] + " | " + value["python_executable"],
             "TLS verification: enabled | environment proxies: disabled",
             "System/environment proxy configured (values omitted): " + str(value["system_or_environment_proxy_configured"]),
             "CA file present: " + str(value["ca_file_present"]) + " | CA directory present: " + str(value["ca_directory_present"])]
    for row in value["hosts"]:
        lines.append(row["host"] + ": " + row["status"])
        if row["status"] == "ok":
            lines.append("  Exchange time and one closed BTCUSDT kline verified.")
        elif row["status"] == "failed":
            lines.append("  " + row["message"])
        else:
            lines.append("  " + row["reason"])
    if value["any_host_verified"]:
        lines.append("A successful probe does not download history or change the selected host.")
        for row in value["hosts"]:
            if row["status"] == "ok":
                lines.append("To select this host explicitly, add: --market-host " + row["host"])
    lines.append("Certificate errors require checking Python CA certificates; do not disable HTTPS verification.")
    return "\n".join(lines)
