"""
Tests for report endpoints (quarterly performance and monthly trends).
"""
import pytest

import main


def orders_matching(**criteria):
    """Orders whose fields equal every given value, computed straight from the mock data."""
    return [o for o in main.orders if all(o.get(k) == v for k, v in criteria.items())]


class TestQuarterlyReports:
    """Test suite for GET /api/reports/quarterly."""

    def test_quarterly_structure(self, client):
        """Test the response shape and chronological ordering."""
        response = client.get("/api/reports/quarterly")
        assert response.status_code == 200

        data = response.json()
        assert [q["quarter"] for q in data] == ["Q1-2025", "Q2-2025", "Q3-2025", "Q4-2025"]
        for q in data:
            for key in ["quarter", "total_orders", "total_revenue", "avg_order_value",
                        "fulfillment_rate", "delivered_orders"]:
                assert key in q

    def test_quarterly_totals_cover_all_orders(self, client):
        """Test that unfiltered quarters add up to every order."""
        data = client.get("/api/reports/quarterly").json()
        assert sum(q["total_orders"] for q in data) == len(main.orders)
        assert sum(q["total_revenue"] for q in data) == pytest.approx(
            sum(o["total_value"] for o in main.orders), abs=0.05)

    def test_quarterly_metrics_are_consistent(self, client):
        """Test avg order value and fulfillment rate match their inputs."""
        for q in client.get("/api/reports/quarterly").json():
            assert q["avg_order_value"] == pytest.approx(q["total_revenue"] / q["total_orders"], abs=0.01)
            assert q["fulfillment_rate"] == pytest.approx(
                q["delivered_orders"] / q["total_orders"] * 100, abs=0.05)

    def test_quarterly_warehouse_filter(self, client):
        """Test that the warehouse filter narrows the orders counted."""
        data = client.get("/api/reports/quarterly?warehouse=Tokyo").json()
        assert sum(q["total_orders"] for q in data) == len(orders_matching(warehouse="Tokyo"))

    def test_quarterly_month_filter_returns_one_quarter(self, client):
        """Test that a single-month filter yields only that month's quarter."""
        data = client.get("/api/reports/quarterly?month=2025-05").json()
        assert [q["quarter"] for q in data] == ["Q2-2025"]
        expected = [o for o in main.orders if o["order_date"].startswith("2025-05")]
        assert data[0]["total_orders"] == len(expected)

    def test_quarterly_status_filter(self, client):
        """Test that filtering to Delivered gives a 100% fulfillment rate."""
        data = client.get("/api/reports/quarterly?status=Delivered").json()
        assert sum(q["total_orders"] for q in data) == len(orders_matching(status="Delivered"))
        assert all(q["fulfillment_rate"] == 100.0 for q in data)

    def test_quarterly_no_matches_returns_empty(self, client):
        """Test that filters matching nothing return an empty list."""
        response = client.get("/api/reports/quarterly?warehouse=Nowhere")
        assert response.status_code == 200
        assert response.json() == []

    def test_quarter_label_handles_any_year(self):
        """Test quarters are derived from the date, not a hard-coded year."""
        assert main._quarter_label("2026-11-12T08:00:00") == "Q4-2026"
        assert main._quarter_label("2024-01-01") == "Q1-2024"
        assert main._quarter_label("not-a-date") is None


class TestMonthlyTrends:
    """Test suite for GET /api/reports/monthly-trends."""

    def test_monthly_structure(self, client):
        """Test the response shape and month ordering."""
        response = client.get("/api/reports/monthly-trends")
        assert response.status_code == 200

        data = response.json()
        months = [m["month"] for m in data]
        assert months == sorted(months)
        assert len(months) == 12
        for key in ["month", "order_count", "revenue", "delivered_count"]:
            assert key in data[0]

    def test_monthly_category_filter(self, client):
        """Test that the category filter narrows the orders counted (case-insensitive)."""
        data = client.get("/api/reports/monthly-trends?category=sensors").json()
        assert sum(m["order_count"] for m in data) == len(orders_matching(category="Sensors"))

    def test_monthly_month_filter(self, client):
        """Test that a month filter returns only that month."""
        data = client.get("/api/reports/monthly-trends?month=2025-03").json()
        assert [m["month"] for m in data] == ["2025-03"]

    def test_monthly_combined_filters(self, client):
        """Test warehouse + status filters combine."""
        data = client.get("/api/reports/monthly-trends?warehouse=London&status=Shipped").json()
        expected = orders_matching(warehouse="London", status="Shipped")
        assert sum(m["order_count"] for m in data) == len(expected)
        assert sum(m["revenue"] for m in data) == pytest.approx(
            sum(o["total_value"] for o in expected), abs=0.05)

    def test_monthly_all_is_unfiltered(self, client):
        """Test that 'all' values behave like no filter."""
        unfiltered = client.get("/api/reports/monthly-trends").json()
        all_values = client.get(
            "/api/reports/monthly-trends?warehouse=all&category=all&status=all&month=all").json()
        assert all_values == unfiltered
