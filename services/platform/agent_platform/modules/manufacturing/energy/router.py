"""能耗分析接口复用制造与数据权限，不新增设备控制或任意数据库连接入口。"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from agent_platform.core.context import require_permission
from agent_platform.core.database import get_session
from agent_platform.core.settings import get_settings

from .schemas import EnergyInput, EnergyPatch
from .service import EnergyService

router = APIRouter()


@router.get("/energy-analyses")
def analyses(q: str = "", device_id: str | None = None, limit: int = 50, offset: int = 0, session=Depends(get_session), actor=Depends(require_permission("m10.read")), settings=Depends(get_settings)):
    return EnergyService(session, actor, settings).listing(q=q, device_id=device_id, limit=limit, offset=offset)


@router.post("/energy-analyses", status_code=201)
def calculate(data: EnergyInput, session=Depends(get_session), actor=Depends(require_permission("m10.write")), settings=Depends(get_settings)):
    return EnergyService(session, actor, settings).calculate(data)


@router.get("/energy-analyses/{id}")
def detail(id: str, session=Depends(get_session), actor=Depends(require_permission("m10.read")), settings=Depends(get_settings)):
    service = EnergyService(session, actor, settings)
    return service.energy_view(service.energy(id))


@router.patch("/energy-analyses/{id}")
def rename(id: str, data: EnergyPatch, session=Depends(get_session), actor=Depends(require_permission("m10.write")), settings=Depends(get_settings)):
    return EnergyService(session, actor, settings).rename(id, data.model_dump())


@router.delete("/energy-analyses/{id}")
def remove(id: str, revision: int = Query(ge=1), session=Depends(get_session), actor=Depends(require_permission("m10.delete")), settings=Depends(get_settings)):
    return EnergyService(session, actor, settings).remove(id, revision)


@router.post("/energy-analyses/{id}/exports")
def export(id: str, session=Depends(get_session), actor=Depends(require_permission("m10.read")), settings=Depends(get_settings)):
    return Response(EnergyService(session, actor, settings).export(id).encode("utf-8"), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="energy-analysis.csv"'})
