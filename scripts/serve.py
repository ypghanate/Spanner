"""Run Spanner for the demo: HTTPS on the LAN so the phone can be the camera.

    python scripts/serve.py            # https://<laptop-ip>:8443  (self-signed, accept the warning once on the phone)

Laptop:  https://<ip>:8443/training/viewer/?phone=1
Phone:   https://<ip>:8443/training/viewer/phone.html?s=<session>   (the laptop screen shows this URL)
Plain http://127.0.0.1:8000 still works for the laptop-webcam mode.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CERT_DIR = ROOT / "data" / "certs"


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def ensure_cert(ip: str) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    key_p, crt_p = CERT_DIR / f"spanner-{ip}.key", CERT_DIR / f"spanner-{ip}.crt"
    if key_p.exists() and crt_p.exists():
        return key_p, crt_p
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "spanner.local")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("spanner.local"), x509.DNSName("localhost"),
                                                        x509.IPAddress(ipaddress.ip_address(ip)),
                                                        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    key_p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                        serialization.NoEncryption()))
    crt_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key_p, crt_p


def main() -> None:
    import uvicorn
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ip = lan_ip()
    key_p, crt_p = ensure_cert(ip)
    port = int(os.getenv("SPANNER_HTTPS_PORT", "8443"))
    print(f"\n  laptop:  https://{ip}:{port}/training/viewer/?phone=1\n  phone:   https://{ip}:{port}/training/viewer/phone.html\n"
          "  (self-signed certificate: open the phone URL once, accept the warning, then allow the camera)\n")
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, ssl_keyfile=str(key_p), ssl_certfile=str(crt_p), log_level="warning")


if __name__ == "__main__":
    main()
