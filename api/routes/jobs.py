from fastapi import APIRouter
from api.schemas import JobOut

router = APIRouter(tags=["jobs"])

# Static job registry — in production this would come from a DB or config file
_JOBS: list[JobOut] = [
    JobOut(name="orders_reconciliation", description="Reconcile orders table", tags=["orders", "daily"]),
    JobOut(name="customers_reconciliation", description="Reconcile customers table", tags=["customers"]),
    JobOut(name="products_reconciliation", description="Reconcile products table", tags=["products"]),
    JobOut(name="inventory_check", description="Check inventory counts", tags=["inventory", "daily"]),
    JobOut(name="sales_summary_validation", description="Validate sales summary aggregates", tags=["sales"]),
]


@router.get("", response_model=list[JobOut])
def list_jobs():
    return _JOBS
