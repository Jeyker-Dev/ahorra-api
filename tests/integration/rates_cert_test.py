import ssl
from datetime import UTC, datetime
from importlib.resources import files

from app.rates.sources import build_bcv_ssl_context, load_bcv_intermediate_pem


def test_bundled_sectigo_certificate_exists_parses_and_expires_after_2030() -> None:
    resource = files("app.rates").joinpath("certs/sectigo_ov_r36.pem")
    assert resource.is_file()

    pem = load_bcv_intermediate_pem()
    assert "BEGIN CERTIFICATE" in pem

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cadata=pem)
    certs = ctx.get_ca_certs()
    assert len(certs) == 1

    not_after_raw = certs[0]["notAfter"]
    assert isinstance(not_after_raw, str)
    not_after = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after_raw), tz=UTC)
    assert not_after > datetime(2030, 1, 1, tzinfo=UTC)

    combined_ctx = build_bcv_ssl_context()
    assert isinstance(combined_ctx, ssl.SSLContext)
