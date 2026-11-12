"""
Tests for restocking API endpoints (recommendations and submitted orders).
"""
import pytest

import main


@pytest.fixture(autouse=True)
def clear_restock_orders():
    """Submitted orders live in module memory; reset them so tests stay independent."""
    main.restock_orders.clear()
    yield
    main.restock_orders.clear()


def full_restock_cost():
    return round(sum(f["unit_cost"] * f["forecasted_demand"] for f in main.demand_forecasts), 2)


class TestRestockRecommendations:
    """Test suite for GET /api/restocking/recommendations."""

    def test_recommendations_structure(self, client):
        """Test the response shape for a typical budget."""
        response = client.get("/api/restocking/recommendations?budget=30000")
        assert response.status_code == 200

        data = response.json()
        for key in ["budget", "max_budget", "total_cost", "remaining_budget", "items"]:
            assert key in data
        assert data["budget"] == 30000
        assert len(data["items"]) > 0

        item = data["items"][0]
        for key in ["item_sku", "item_name", "trend", "recommended_quantity",
                    "unit_cost", "line_total", "lead_time_days", "is_partial"]:
            assert key in item

    def test_total_never_exceeds_budget(self, client):
        """Test that recommendations stay within budget across a range of budgets."""
        for budget in [0, 1, 99.99, 5000, 12345.67, 30000, 60000, 95000]:
            data = client.get(f"/api/restocking/recommendations?budget={budget}").json()
            assert data["total_cost"] <= budget
            assert data["remaining_budget"] == pytest.approx(budget - data["total_cost"], abs=0.01)
            assert data["total_cost"] == pytest.approx(sum(i["line_total"] for i in data["items"]), abs=0.01)

    def test_zero_budget_recommends_nothing(self, client):
        """Test that a zero budget yields no items."""
        data = client.get("/api/restocking/recommendations?budget=0").json()
        assert data["items"] == []
        assert data["total_cost"] == 0

    def test_full_budget_recommends_every_item_in_full(self, client):
        """Test that a budget covering everything recommends every forecast at full quantity."""
        max_budget = full_restock_cost()
        data = client.get(f"/api/restocking/recommendations?budget={max_budget}").json()

        assert data["max_budget"] == pytest.approx(max_budget, abs=0.01)
        assert len(data["items"]) == len(main.demand_forecasts)
        assert all(not i["is_partial"] for i in data["items"])
        assert all(i["recommended_quantity"] == i["forecasted_demand"] for i in data["items"])

    def test_increasing_trend_items_come_first(self, client):
        """Test priority order: increasing, then stable, then decreasing."""
        data = client.get(f"/api/restocking/recommendations?budget={full_restock_cost()}").json()
        rank = {"increasing": 0, "stable": 1, "decreasing": 2}
        ranks = [rank[i["trend"]] for i in data["items"]]
        assert ranks == sorted(ranks)

    def test_highest_growth_first_within_trend(self, client):
        """Test that the fastest-growing increasing item (WDG-001, +50%) is recommended first."""
        data = client.get("/api/restocking/recommendations?budget=30000").json()
        assert data["items"][0]["item_sku"] == "WDG-001"

    def test_only_last_item_can_be_partial(self, client):
        """Test that the fill stops at the first item that doesn't fully fit."""
        data = client.get("/api/restocking/recommendations?budget=30000").json()
        items = data["items"]
        assert all(not i["is_partial"] for i in items[:-1])
        assert items[-1]["is_partial"]
        assert 0 < items[-1]["recommended_quantity"] < items[-1]["forecasted_demand"]

    def test_budget_is_required(self, client):
        """Test that the budget query param is required."""
        response = client.get("/api/restocking/recommendations")
        assert response.status_code == 422

    def test_negative_budget_rejected(self, client):
        """Test that a negative budget is rejected."""
        response = client.get("/api/restocking/recommendations?budget=-1")
        assert response.status_code == 422


class TestRestockOrders:
    """Test suite for POST/GET /api/restocking/orders."""

    def test_no_orders_initially(self, client):
        """Test that no restock orders exist before one is placed."""
        response = client.get("/api/restocking/orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_order(self, client):
        """Test placing an order prices it from server data and sets lead time."""
        payload = {
            "budget": 30000,
            "items": [
                {"item_sku": "FLT-405", "quantity": 950},  # $12.40, 10 days
                {"item_sku": "WDG-001", "quantity": 450},  # $24.50, 14 days
            ],
        }
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 201

        order = response.json()
        assert order["status"] == "Submitted"
        assert order["order_number"].startswith("RST-")
        assert order["total_value"] == pytest.approx(950 * 12.40 + 450 * 24.50)
        # Lead time is the slowest item's
        assert order["lead_time_days"] == 14
        assert len(order["items"]) == 2

    def test_expected_delivery_is_created_plus_lead_time(self, client):
        """Test the expected delivery date is order date + lead time."""
        from datetime import datetime, timedelta

        order = client.post("/api/restocking/orders",
                            json={"items": [{"item_sku": "MTR-304", "quantity": 1}]}).json()
        created = datetime.fromisoformat(order["created_date"])
        expected = datetime.fromisoformat(order["expected_delivery"])
        assert expected - created == timedelta(days=order["lead_time_days"])
        assert order["lead_time_days"] == 35

    def test_created_order_is_listed(self, client):
        """Test that submitted orders appear in the list, newest first."""
        client.post("/api/restocking/orders", json={"items": [{"item_sku": "GSK-203", "quantity": 10}]})
        client.post("/api/restocking/orders", json={"items": [{"item_sku": "VLV-506", "quantity": 5}]})

        data = client.get("/api/restocking/orders").json()
        assert len(data) == 2
        assert data[0]["items"][0]["item_sku"] == "VLV-506"
        assert data[0]["order_number"] != data[1]["order_number"]

    def test_recommendations_can_be_placed_as_order(self, client):
        """Test the round trip: a recommendation submitted as-is is accepted within its budget."""
        rec = client.get("/api/restocking/recommendations?budget=30000").json()
        payload = {
            "budget": 30000,
            "items": [{"item_sku": i["item_sku"], "quantity": i["recommended_quantity"]} for i in rec["items"]],
        }
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 201
        assert response.json()["total_value"] == pytest.approx(rec["total_cost"], abs=0.01)

    def test_order_over_budget_rejected(self, client):
        """Test that an order above its stated budget is rejected."""
        response = client.post("/api/restocking/orders",
                               json={"budget": 100, "items": [{"item_sku": "FLT-405", "quantity": 950}]})
        assert response.status_code == 400
        assert client.get("/api/restocking/orders").json() == []

    def test_unknown_sku_rejected(self, client):
        """Test that SKUs not in the demand forecast are rejected."""
        response = client.post("/api/restocking/orders", json={"items": [{"item_sku": "NOPE-000", "quantity": 1}]})
        assert response.status_code == 400

    def test_duplicate_sku_rejected(self, client):
        """Test that the same SKU can't appear twice in one order."""
        payload = {"items": [{"item_sku": "GSK-203", "quantity": 1}, {"item_sku": "GSK-203", "quantity": 2}]}
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 400

    @pytest.mark.parametrize("payload", [
        {"items": []},
        {"items": [{"item_sku": "GSK-203", "quantity": 0}]},
        {"items": [{"item_sku": "GSK-203", "quantity": -5}]},
        {"budget": -1, "items": [{"item_sku": "GSK-203", "quantity": 1}]},
    ])
    def test_invalid_payload_rejected(self, client, payload):
        """Test validation of empty orders, non-positive quantities, and negative budgets."""
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 422
