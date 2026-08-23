from __future__ import annotations

from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.secrets import get_secret_store
from app.models import ModelConfig
from app.schemas.domain import BootstrapModelsRequest, ModelConfigCreate, ModelDiscoveryRequest
from app.services.model_provisioning import bootstrap_recommended_models
from app.services.registry import registry
from app.services.translation.providers import api_auth_headers, normalize_api_base_url, resolve_api_protocol

router = APIRouter(tags=["models"])


def _safe_config(item: ModelConfig) -> dict:
    capability = next(
        (entry for entry in registry.describe() if entry["kind"] == item.kind and entry["name"] == item.provider),
        {},
    )
    return {
        "id": item.id,
        "kind": item.kind,
        "name": item.name,
        "provider": item.provider,
        "enabled": item.enabled,
        "is_default": item.is_default,
        "config": item.config,
        "has_api_key": bool(item.encrypted_api_key),
        "capabilities": capability,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


@router.get("/models")
def list_models(db: Session = Depends(get_db)) -> dict:
    configured = list(db.scalars(select(ModelConfig).order_by(ModelConfig.kind, ModelConfig.name)).all())
    return {"available": registry.describe(), "configured": [_safe_config(item) for item in configured]}


@router.post("/models/discover")
async def discover_models(payload: ModelDiscoveryRequest, db: Session = Depends(get_db)) -> dict:
    raw_base_url = payload.base_url.strip().rstrip("/")
    parsed = urlsplit(raw_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(422, "API Base URL 必须是有效的 HTTP(S) 地址，且不能包含账号或密码")
    protocol = resolve_api_protocol(payload.api_protocol, raw_base_url)
    base_url = normalize_api_base_url(raw_base_url)
    model_urls = _model_endpoint_candidates(base_url)

    api_key = payload.api_key.strip() if payload.api_key else None
    if not api_key and payload.config_id:
        configured = db.get(ModelConfig, payload.config_id)
        if configured is not None:
            api_key = get_secret_store().decrypt(configured.encrypted_api_key)
    headers = api_auth_headers(protocol, api_key)
    last_status = 404
    invalid_json = False
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            for models_url in model_urls:
                response = await client.get(models_url, headers=headers)
                last_status = response.status_code
                if response.status_code == 404:
                    continue
                if response.status_code in {401, 403}:
                    raise HTTPException(422, "模型列表请求被拒绝，请检查 API Key")
                response.raise_for_status()
                try:
                    body = response.json()
                except ValueError:
                    invalid_json = True
                    continue
                model_ids = _extract_model_ids(body)
                if model_ids:
                    resolved_base_url = models_url.removesuffix("/models")
                    return {
                        "models": model_ids,
                        "endpoint": models_url,
                        "base_url": resolved_base_url,
                        "protocol": protocol,
                    }
    except httpx.HTTPStatusError as exc:
        raise HTTPException(422, f"模型列表请求失败（HTTP {exc.response.status_code}）") from exc
    except httpx.RequestError as exc:
        raise HTTPException(422, f"无法连接模型接口：{exc}") from exc
    if invalid_json:
        raise HTTPException(422, "模型接口没有返回有效 JSON")
    if last_status == 404:
        raise HTTPException(422, "模型列表请求失败（HTTP 404），已尝试 /models 和 /v1/models")
    raise HTTPException(422, "接口已连接，但返回结果中没有可用模型")


def _model_endpoint_candidates(base_url: str) -> list[str]:
    candidates = [f"{base_url}/models"]
    path = urlsplit(base_url).path.rstrip("/").lower()
    if not path.endswith("/v1"):
        candidates.append(f"{base_url}/v1/models")
    return candidates


def _extract_model_ids(body: object) -> list[str]:
    if not isinstance(body, dict):
        return []
    candidates = body.get("data")
    if not isinstance(candidates, list):
        candidates = body.get("models")
    if not isinstance(candidates, list):
        return []
    output: list[str] = []
    for item in candidates:
        if isinstance(item, str):
            model_id = item
        elif isinstance(item, dict):
            model_id = item.get("id") or item.get("name") or item.get("model")
        else:
            continue
        if isinstance(model_id, str) and model_id.strip() and model_id.strip() not in output:
            output.append(model_id.strip())
    return output


@router.post("/models/config", status_code=201)
def configure_model(payload: ModelConfigCreate, db: Session = Depends(get_db)) -> dict:
    descriptions = {(entry["kind"], entry["name"]) for entry in registry.describe()}
    if (payload.kind, payload.provider) not in descriptions:
        raise HTTPException(422, f"Provider {payload.provider} is not registered for {payload.kind}")
    if payload.is_default:
        db.execute(update(ModelConfig).where(ModelConfig.kind == payload.kind).values(is_default=False))
    item = ModelConfig(
        kind=payload.kind,
        name=payload.name,
        provider=payload.provider,
        enabled=payload.enabled,
        is_default=payload.is_default,
        config=payload.config,
        encrypted_api_key=get_secret_store().encrypt(payload.api_key),
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "A model config with this kind and name already exists") from exc
    db.refresh(item)
    return _safe_config(item)


@router.post("/models/bootstrap")
def bootstrap_models(payload: BootstrapModelsRequest, db: Session = Depends(get_db)) -> dict:
    report = bootstrap_recommended_models(
        db,
        stages=payload.stages,
        preload=payload.preload,
        upgrade_fallbacks=payload.upgrade_fallbacks,
    )
    return {
        "ok": all(entry["status"] not in {"error", "dependency_missing"} for entry in report),
        "models": report,
    }


@router.patch("/models/config/{config_id}")
def update_model_config(config_id: str, payload: ModelConfigCreate, db: Session = Depends(get_db)) -> dict:
    item = db.get(ModelConfig, config_id)
    if item is None:
        raise HTTPException(404, "Model config not found")
    descriptions = {(entry["kind"], entry["name"]) for entry in registry.describe()}
    if (payload.kind, payload.provider) not in descriptions:
        raise HTTPException(422, f"Provider {payload.provider} is not registered for {payload.kind}")
    if payload.is_default:
        db.execute(update(ModelConfig).where(ModelConfig.kind == payload.kind).values(is_default=False))
    previous_provider = item.provider
    item.kind = payload.kind
    item.name = payload.name
    item.provider = payload.provider
    item.enabled = payload.enabled
    item.is_default = payload.is_default
    item.config = payload.config
    if payload.api_key is not None:
        item.encrypted_api_key = get_secret_store().encrypt(payload.api_key)
    elif previous_provider != payload.provider and payload.provider != "openai-compatible":
        item.encrypted_api_key = None
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "A model config with this kind and name already exists") from exc
    db.refresh(item)
    return _safe_config(item)


@router.delete("/models/config/{config_id}", status_code=204)
def delete_model_config(config_id: str, db: Session = Depends(get_db)) -> None:
    item = db.get(ModelConfig, config_id)
    if item is None:
        raise HTTPException(404, "Model config not found")
    db.delete(item)
    db.commit()
