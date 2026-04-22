from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class SyncRequest(BaseModel):
    mfa_code: Optional[str] = None


class SyncResponse(BaseModel):
    status:         str
    synced:         int
    message:        str
    accounts_found: int = 0
