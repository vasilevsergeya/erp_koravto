from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth import current_user
from app.core.db import get_db
from app.models import IntegrationHealth
from app.services.integration_monitor import WARNINGS

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


@router.get("/health")
def health(user=Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(IntegrationHealth, "wazzup")
    if not row:
        return {"checked_at": None, "warnings": [], "channels": []}
    return {
        "checked_at": row.checked_at,
        "warnings": [
            {"code": code, "text": WARNINGS.get(code, code)}
            for code in row.details.get("warnings", [])
        ],
        "channels": row.details.get("channels", []),
        "last_webhook_at": row.details.get("last_webhook_at"),
    }
