"""Admin: API keys. A new key's plaintext appears in the create response only."""

from uuid import UUID

from fastapi import APIRouter

from abtest.api.deps import ConnDep, ServerProject
from abtest.db import api_keys
from abtest.models import ApiKey, ApiKeyCreate, ApiKeyCreated

router = APIRouter(prefix="/admin/api-keys", tags=["api keys"])


@router.get("")
def list_api_keys(conn: ConnDep, project: ServerProject) -> list[ApiKey]:
    return api_keys.list_api_keys(conn, project)


@router.post("", status_code=201)
def create_api_key(data: ApiKeyCreate, conn: ConnDep, project: ServerProject) -> ApiKeyCreated:
    return api_keys.create_api_key(conn, project, data.kind)


@router.post("/{key_id}/revoke")
def revoke_api_key(key_id: UUID, conn: ConnDep, project: ServerProject) -> ApiKey:
    return api_keys.revoke_api_key(conn, project, key_id)
