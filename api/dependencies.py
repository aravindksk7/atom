from etl_framework.repository.database import get_db
from sqlalchemy.orm import Session
from fastapi import Depends


def get_session(db: Session = Depends(get_db)) -> Session:
    return db
