"""
Tests for FastAPI routes.
Uses FastAPI TestClient — no real HTTP server or network calls needed.

Run with:
    pytest tests/test_api.py -v
"""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

# Patch where functions are *used*, not where they're defined.
SCAN_CSP_TARGET = "backend.routers.csp.run_csp_scan"
SCAN_CC_TARGET  = "backend.routers.covered_calls.run_cc_scan"

# ─── Shared fixtures ──────────────────────────────────────────────────────────

MOCK_RESULT = {
    "ticker":            "AMD",
    "price":             127.45,
    "strike":            110.0,
    "premium":           1.75,
    "DTE":               35,
    "cushion_pct":       13.71,
    "roi_pct":           1.59,
    "annual_yield_pct":  16.60,
    "collateral":        11000.0,
    "credit":            175.0,
    "expiration":        "2025-05-16",
}

VALID_PAYLOAD = {
    "tickers":           ["AMD", "PLTR"],
    "dte_min":           30,
    "dte_max":           45,
    "max_results":       5,
    "collateral_budget": 10000,
    "safety":            "balanced",
}


# ─── GET /health ──────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_returns_200(self):
        assert client.get("/health").status_code == 200

    def test_status_ok(self):
        assert client.get("/health").json()["status"] == "ok"

    def test_version_present(self):
        assert "version" in client.get("/health").json()


# ─── POST /scan/csp — success ─────────────────────────────────────────────────

class TestScanCspSuccess:

    @patch(SCAN_CSP_TARGET, return_value=[MOCK_RESULT])
    def test_returns_200(self, _):
        assert client.post("/scan/csp", json=VALID_PAYLOAD).status_code == 200

    @patch(SCAN_CSP_TARGET, return_value=[MOCK_RESULT])
    def test_response_has_required_keys(self, _):
        body = client.post("/scan/csp", json=VALID_PAYLOAD).json()
        for key in ("results", "count", "elapsed_ms", "tickers_scanned", "params"):
            assert key in body

    @patch(SCAN_CSP_TARGET, return_value=[MOCK_RESULT])
    def test_count_matches_results_length(self, _):
        body = client.post("/scan/csp", json=VALID_PAYLOAD).json()
        assert body["count"] == len(body["results"])

    @patch(SCAN_CSP_TARGET, return_value=[MOCK_RESULT])
    def test_tickers_scanned_echoed(self, _):
        body = client.post("/scan/csp", json=VALID_PAYLOAD).json()
        assert set(body["tickers_scanned"]) == set(VALID_PAYLOAD["tickers"])

    @patch(SCAN_CSP_TARGET, return_value=[MOCK_RESULT])
    def test_elapsed_ms_non_negative(self, _):
        assert client.post("/scan/csp", json=VALID_PAYLOAD).json()["elapsed_ms"] >= 0

    @patch(SCAN_CSP_TARGET, return_value=[MOCK_RESULT])
    def test_result_field_types(self, _):
        res = client.post("/scan/csp", json=VALID_PAYLOAD).json()["results"][0]
        assert isinstance(res["ticker"],           str)
        assert isinstance(res["price"],            float)
        assert isinstance(res["strike"],           float)
        assert isinstance(res["premium"],          float)
        assert isinstance(res["DTE"],              int)
        assert isinstance(res["cushion_pct"],      float)
        assert isinstance(res["roi_pct"],          float)
        assert isinstance(res["annual_yield_pct"], float)
        assert isinstance(res["collateral"],       float)
        assert isinstance(res["credit"],           float)
        assert isinstance(res["expiration"],       str)

    @patch(SCAN_CSP_TARGET, return_value=[])
    def test_empty_results_still_200(self, _):
        r = client.post("/scan/csp", json=VALID_PAYLOAD)
        assert r.status_code == 200
        assert r.json()["count"] == 0

    @patch(SCAN_CSP_TARGET, return_value=[MOCK_RESULT])
    def test_omitting_optional_fields_uses_defaults(self, _):
        assert client.post("/scan/csp", json={"tickers": ["AMD"]}).status_code == 200

    @patch(SCAN_CSP_TARGET, return_value=[])
    def test_empty_body_uses_all_defaults(self, _):
        assert client.post("/scan/csp", json={}).status_code == 200


# ─── POST /scan/csp — validation ─────────────────────────────────────────────

class TestScanCspValidation:

    def test_dte_min_gte_dte_max_returns_422(self):
        r = client.post("/scan/csp", json={**VALID_PAYLOAD, "dte_min": 50, "dte_max": 30})
        assert r.status_code == 422

    def test_dte_min_equal_dte_max_returns_422(self):
        r = client.post("/scan/csp", json={**VALID_PAYLOAD, "dte_min": 35, "dte_max": 35})
        assert r.status_code == 422

    def test_invalid_safety_returns_422(self):
        r = client.post("/scan/csp", json={**VALID_PAYLOAD, "safety": "yolo"})
        assert r.status_code == 422

    def test_dte_min_below_1_returns_422(self):
        r = client.post("/scan/csp", json={**VALID_PAYLOAD, "dte_min": 0})
        assert r.status_code == 422

    def test_max_results_above_20_returns_422(self):
        r = client.post("/scan/csp", json={**VALID_PAYLOAD, "max_results": 99})
        assert r.status_code == 422

    def test_negative_collateral_returns_422(self):
        r = client.post("/scan/csp", json={**VALID_PAYLOAD, "collateral_budget": -500})
        assert r.status_code == 422


# ─── POST /scan/csp — params forwarded correctly ──────────────────────────────

class TestScanCspParamsForwarded:

    @patch(SCAN_CSP_TARGET, return_value=[])
    def test_safety_forwarded(self, mock_scan):
        client.post("/scan/csp", json={**VALID_PAYLOAD, "safety": "conservative"})
        assert mock_scan.call_args.kwargs["safety"] == "conservative"

    @patch(SCAN_CSP_TARGET, return_value=[])
    def test_collateral_forwarded(self, mock_scan):
        client.post("/scan/csp", json={**VALID_PAYLOAD, "collateral_budget": 7500})
        assert mock_scan.call_args.kwargs["collateral_budget"] == 7500

    @patch(SCAN_CSP_TARGET, return_value=[])
    def test_dte_range_forwarded(self, mock_scan):
        client.post("/scan/csp", json={**VALID_PAYLOAD, "dte_min": 21, "dte_max": 42})
        assert mock_scan.call_args.kwargs["dte_min"] == 21
        assert mock_scan.call_args.kwargs["dte_max"] == 42

    @patch(SCAN_CSP_TARGET, return_value=[])
    def test_tickers_forwarded(self, mock_scan):
        client.post("/scan/csp", json={**VALID_PAYLOAD, "tickers": ["NVDA", "AMD"]})
        assert set(mock_scan.call_args.kwargs["tickers"]) == {"NVDA", "AMD"}


# ─── POST /scan/csp — safety levels ──────────────────────────────────────────

class TestScanCspSafetyLevels:

    @pytest.mark.parametrize("safety", ["conservative", "balanced", "aggressive"])
    def test_all_safety_levels_accepted(self, safety):
        with patch(SCAN_CSP_TARGET, return_value=[]):
            r = client.post("/scan/csp", json={**VALID_PAYLOAD, "safety": safety})
            assert r.status_code == 200


# ─── POST /scan/cc ────────────────────────────────────────────────────────────

class TestScanCcEndpoint:

    def test_returns_200(self):
        assert client.post("/scan/cc", json=VALID_PAYLOAD).status_code == 200

    def test_phase3_stub_returns_empty(self):
        assert client.post("/scan/cc", json=VALID_PAYLOAD).json()["count"] == 0

    def test_response_shape_matches_csp(self):
        body = client.post("/scan/cc", json=VALID_PAYLOAD).json()
        for key in ("results", "count", "elapsed_ms", "tickers_scanned"):
            assert key in body


# ─── CORS ─────────────────────────────────────────────────────────────────────

class TestCORS:

    def test_cors_header_present_on_post(self):
        with patch(SCAN_CSP_TARGET, return_value=[]):
            r = client.post(
                "/scan/csp",
                json=VALID_PAYLOAD,
                headers={"Origin": "http://localhost:5500"},
            )
            assert "access-control-allow-origin" in r.headers

    def test_options_preflight(self):
        r = client.options(
            "/scan/csp",
            headers={
                "Origin": "http://localhost:5500",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert r.status_code in (200, 204)
