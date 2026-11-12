from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from pydantic import BaseModel, Field
from mock_data import inventory_items, orders, demand_forecasts, backlog_items, spending_summary, monthly_spending, category_spending, recent_transactions, purchase_orders

app = FastAPI(title="Factory Inventory Management System")

# Quarter mapping for date filtering
QUARTER_MAP = {
    'Q1-2025': ['2025-01', '2025-02', '2025-03'],
    'Q2-2025': ['2025-04', '2025-05', '2025-06'],
    'Q3-2025': ['2025-07', '2025-08', '2025-09'],
    'Q4-2025': ['2025-10', '2025-11', '2025-12']
}

def filter_by_month(items: list, month: Optional[str]) -> list:
    """Filter items by month/quarter based on order_date field"""
    if not month or month == 'all':
        return items

    if month.startswith('Q'):
        # Handle quarters
        if month in QUARTER_MAP:
            months = QUARTER_MAP[month]
            return [item for item in items if any(m in item.get('order_date', '') for m in months)]
    else:
        # Direct month match
        return [item for item in items if month in item.get('order_date', '')]

    return items

def apply_filters(items: list, warehouse: Optional[str] = None, category: Optional[str] = None,
                 status: Optional[str] = None) -> list:
    """Apply common filters to a list of items"""
    filtered = items

    if warehouse and warehouse != 'all':
        filtered = [item for item in filtered if item.get('warehouse') == warehouse]

    if category and category != 'all':
        filtered = [item for item in filtered if item.get('category', '').lower() == category.lower()]

    if status and status != 'all':
        filtered = [item for item in filtered if item.get('status', '').lower() == status.lower()]

    return filtered

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data models
class InventoryItem(BaseModel):
    id: str
    sku: str
    name: str
    category: str
    warehouse: str
    quantity_on_hand: int
    reorder_point: int
    unit_cost: float
    location: str
    last_updated: str

class Order(BaseModel):
    id: str
    order_number: str
    customer: str
    items: List[dict]
    status: str
    order_date: str
    expected_delivery: str
    total_value: float
    actual_delivery: Optional[str] = None
    warehouse: Optional[str] = None
    category: Optional[str] = None

class DemandForecast(BaseModel):
    id: str
    item_sku: str
    item_name: str
    current_demand: int
    forecasted_demand: int
    trend: str
    period: str
    unit_cost: Optional[float] = None
    lead_time_days: Optional[int] = None

class BacklogItem(BaseModel):
    id: str
    order_id: str
    item_sku: str
    item_name: str
    quantity_needed: int
    quantity_available: int
    days_delayed: int
    priority: str
    has_purchase_order: Optional[bool] = False

class PurchaseOrder(BaseModel):
    id: str
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    status: str
    created_date: str
    notes: Optional[str] = None

class CreatePurchaseOrderRequest(BaseModel):
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    notes: Optional[str] = None

class RestockRecommendationItem(BaseModel):
    item_sku: str
    item_name: str
    trend: str
    current_demand: int
    forecasted_demand: int
    recommended_quantity: int
    unit_cost: float
    line_total: float
    lead_time_days: int
    is_partial: bool

class RestockRecommendation(BaseModel):
    budget: float
    max_budget: float
    total_cost: float
    remaining_budget: float
    items: List[RestockRecommendationItem]

class RestockOrderLineRequest(BaseModel):
    item_sku: str
    quantity: int = Field(gt=0)

class CreateRestockOrderRequest(BaseModel):
    items: List[RestockOrderLineRequest] = Field(min_length=1)
    budget: Optional[float] = Field(default=None, ge=0)

class RestockOrderLine(BaseModel):
    item_sku: str
    item_name: str
    quantity: int
    unit_cost: float
    line_total: float
    lead_time_days: int

class RestockOrder(BaseModel):
    id: str
    order_number: str
    status: str
    created_date: str
    expected_delivery: str
    lead_time_days: int
    total_value: float
    budget: Optional[float] = None
    items: List[RestockOrderLine]

# Submitted restocking orders. Kept in memory like the rest of the app's data,
# so they are cleared whenever the server restarts.
restock_orders: List[dict] = []

# Lower number = restock first. Unknown trends sort last.
TREND_PRIORITY = {'increasing': 0, 'stable': 1, 'decreasing': 2}

def _to_cents(amount: float) -> int:
    # Budget math runs in integer cents so repeated subtraction can't drift
    # (e.g. 0.1 + 0.2 != 0.3) and push a line over budget by a fraction of a cent.
    return int(round(amount * 100))

def _restockable_forecasts() -> list:
    """Forecasts that can be priced and ordered (have a cost, lead time and positive demand)."""
    return [
        f for f in demand_forecasts
        if f.get('unit_cost') and f.get('lead_time_days') is not None and f.get('forecasted_demand', 0) > 0
    ]

def build_restock_recommendations(budget: float) -> dict:
    """Recommend restock quantities from the demand forecast that fit within `budget`.

    Priority fill: items are ranked by trend (increasing, then stable, then
    decreasing) and, within a trend, by forecast growth relative to current
    demand. Each item is recommended at its full forecasted quantity until the
    budget runs out; the first item that doesn't fully fit gets as many whole
    units as the remaining budget allows, and the fill stops there so the
    ranking order is respected rather than skipping ahead to cheaper items.
    """
    candidates = _restockable_forecasts()

    def growth(f):
        current = f.get('current_demand') or 0
        # Treat zero current demand as maximal growth instead of dividing by zero.
        return (f['forecasted_demand'] - current) / current if current else float('inf')

    ranked = sorted(candidates, key=lambda f: (TREND_PRIORITY.get(f['trend'].lower(), 3), -growth(f)))

    remaining = _to_cents(budget)
    items = []
    for f in ranked:
        unit = _to_cents(f['unit_cost'])
        full_qty = f['forecasted_demand']
        qty = full_qty if unit * full_qty <= remaining else remaining // unit
        if qty <= 0:
            break
        line = unit * qty
        remaining -= line
        items.append({
            'item_sku': f['item_sku'],
            'item_name': f['item_name'],
            'trend': f['trend'],
            'current_demand': f['current_demand'],
            'forecasted_demand': full_qty,
            'recommended_quantity': qty,
            'unit_cost': f['unit_cost'],
            'line_total': line / 100,
            'lead_time_days': f['lead_time_days'],
            'is_partial': qty < full_qty,
        })
        if qty < full_qty:
            break

    max_budget = sum(_to_cents(f['unit_cost']) * f['forecasted_demand'] for f in candidates)
    total = sum(_to_cents(i['line_total']) for i in items)
    return {
        'budget': budget,
        'max_budget': max_budget / 100,
        'total_cost': total / 100,
        'remaining_budget': (_to_cents(budget) - total) / 100,
        'items': items,
    }

# API endpoints
@app.get("/")
def root():
    return {"message": "Factory Inventory Management System API", "version": "1.0.0"}

@app.get("/api/inventory", response_model=List[InventoryItem])
def get_inventory(
    warehouse: Optional[str] = None,
    category: Optional[str] = None
):
    """Get all inventory items with optional filtering"""
    return apply_filters(inventory_items, warehouse, category)

@app.get("/api/inventory/{item_id}", response_model=InventoryItem)
def get_inventory_item(item_id: str):
    """Get a specific inventory item"""
    item = next((item for item in inventory_items if item["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

@app.get("/api/orders", response_model=List[Order])
def get_orders(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get all orders with optional filtering"""
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)
    return filtered_orders

@app.get("/api/orders/{order_id}", response_model=Order)
def get_order(order_id: str):
    """Get a specific order"""
    order = next((order for order in orders if order["id"] == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order

@app.get("/api/demand", response_model=List[DemandForecast])
def get_demand_forecasts():
    """Get demand forecasts"""
    return demand_forecasts

@app.get("/api/backlog", response_model=List[BacklogItem])
def get_backlog():
    """Get backlog items with purchase order status"""
    # Add has_purchase_order flag to each backlog item
    result = []
    for item in backlog_items:
        item_dict = dict(item)
        # Check if this backlog item has a purchase order
        has_po = any(po["backlog_item_id"] == item["id"] for po in purchase_orders)
        item_dict["has_purchase_order"] = has_po
        result.append(item_dict)
    return result

@app.get("/api/dashboard/summary")
def get_dashboard_summary(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get summary statistics for dashboard with optional filtering"""
    # Filter inventory
    filtered_inventory = apply_filters(inventory_items, warehouse, category)

    # Filter orders
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)

    total_inventory_value = sum(item["quantity_on_hand"] * item["unit_cost"] for item in filtered_inventory)
    low_stock_items = len([item for item in filtered_inventory if item["quantity_on_hand"] <= item["reorder_point"]])
    pending_orders = len([order for order in filtered_orders if order["status"] in ["Processing", "Backordered"]])
    total_backlog_items = len(backlog_items)

    return {
        "total_inventory_value": round(total_inventory_value, 2),
        "low_stock_items": low_stock_items,
        "pending_orders": pending_orders,
        "total_backlog_items": total_backlog_items,
        "total_orders_value": sum(order["total_value"] for order in filtered_orders)
    }

@app.get("/api/spending/summary")
def get_spending_summary():
    """Get spending summary statistics"""
    return spending_summary

@app.get("/api/spending/monthly")
def get_monthly_spending():
    """Get monthly spending breakdown"""
    return monthly_spending

@app.get("/api/spending/categories")
def get_category_spending():
    """Get spending by category"""
    return category_spending

@app.get("/api/spending/transactions")
def get_recent_transactions():
    """Get recent transactions"""
    return recent_transactions

def _report_orders(warehouse, category, status, month) -> list:
    """Orders that the report endpoints aggregate, narrowed by the global filter bar."""
    filtered = apply_filters(orders, warehouse, category, status)
    return filter_by_month(filtered, month)

def _quarter_label(order_date: str) -> Optional[str]:
    """Map a YYYY-MM-DD date to 'Qn-YYYY'.

    Derived from the date itself rather than substring checks against a fixed
    year, so orders outside 2025 are no longer silently dropped from the report.
    """
    try:
        parsed = datetime.strptime(order_date[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    return f"Q{(parsed.month - 1) // 3 + 1}-{parsed.year}"

@app.get("/api/reports/quarterly")
def get_quarterly_reports(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get quarterly performance reports with optional filtering"""
    quarters = {}

    for order in _report_orders(warehouse, category, status, month):
        quarter = _quarter_label(order.get('order_date', ''))
        if quarter is None:
            continue

        if quarter not in quarters:
            quarters[quarter] = {
                'quarter': quarter,
                'total_orders': 0,
                'total_revenue': 0,
                'delivered_orders': 0,
                'avg_order_value': 0,
                'fulfillment_rate': 0
            }

        quarters[quarter]['total_orders'] += 1
        quarters[quarter]['total_revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            quarters[quarter]['delivered_orders'] += 1

    result = []
    for data in quarters.values():
        data['total_revenue'] = round(data['total_revenue'], 2)
        data['avg_order_value'] = round(data['total_revenue'] / data['total_orders'], 2)
        data['fulfillment_rate'] = round((data['delivered_orders'] / data['total_orders']) * 100, 1)
        result.append(data)

    # 'Qn-YYYY' strings don't sort chronologically across years, so sort by (year, quarter)
    result.sort(key=lambda x: (x['quarter'][3:], x['quarter'][:2]))
    return result

@app.get("/api/reports/monthly-trends")
def get_monthly_trends(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get month-over-month trends with optional filtering"""
    months = {}

    for order in _report_orders(warehouse, category, status, month):
        order_date = order.get('order_date', '')
        if not order_date:
            continue

        month_key = order_date[:7]  # YYYY-MM

        if month_key not in months:
            months[month_key] = {
                'month': month_key,
                'order_count': 0,
                'revenue': 0,
                'delivered_count': 0
            }

        months[month_key]['order_count'] += 1
        months[month_key]['revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            months[month_key]['delivered_count'] += 1

    result = list(months.values())
    for data in result:
        data['revenue'] = round(data['revenue'], 2)
    result.sort(key=lambda x: x['month'])
    return result

@app.get("/api/restocking/recommendations", response_model=RestockRecommendation)
def get_restock_recommendations(budget: float = Query(..., ge=0)):
    """Recommend items from the demand forecast to restock within the given budget"""
    return build_restock_recommendations(budget)

@app.get("/api/restocking/orders", response_model=List[RestockOrder])
def get_restock_orders():
    """Get submitted restocking orders, newest first"""
    return list(reversed(restock_orders))

@app.post("/api/restocking/orders", response_model=RestockOrder, status_code=201)
def create_restock_order(request: CreateRestockOrderRequest):
    """Submit a restocking order. Prices and lead times come from the server's forecast
    data, never from the client, so the stored total can't be tampered with."""
    forecasts_by_sku = {f['item_sku']: f for f in _restockable_forecasts()}

    skus = [line.item_sku for line in request.items]
    if len(skus) != len(set(skus)):
        raise HTTPException(status_code=400, detail="Each SKU may appear only once per order")

    lines = []
    total = 0
    for line in request.items:
        forecast = forecasts_by_sku.get(line.item_sku)
        if not forecast:
            raise HTTPException(status_code=400, detail=f"SKU {line.item_sku} is not available for restocking")
        line_cents = _to_cents(forecast['unit_cost']) * line.quantity
        total += line_cents
        lines.append({
            'item_sku': forecast['item_sku'],
            'item_name': forecast['item_name'],
            'quantity': line.quantity,
            'unit_cost': forecast['unit_cost'],
            'line_total': line_cents / 100,
            'lead_time_days': forecast['lead_time_days'],
        })

    if request.budget is not None and total > _to_cents(request.budget):
        raise HTTPException(status_code=400, detail="Order total exceeds the budget")

    created = datetime.now().replace(microsecond=0)
    # The order ships complete, so it arrives when its slowest item does.
    lead_time = max(l['lead_time_days'] for l in lines)
    sequence = len(restock_orders) + 1
    order = {
        'id': f"rst-{sequence}",
        'order_number': f"RST-{created.year}-{sequence:04d}",
        'status': 'Submitted',
        'created_date': created.isoformat(),
        'expected_delivery': (created + timedelta(days=lead_time)).isoformat(),
        'lead_time_days': lead_time,
        'total_value': total / 100,
        'budget': request.budget,
        'items': lines,
    }
    restock_orders.append(order)
    return order

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
