"""RTSP camera REST API."""

from fastapi import APIRouter, Depends

from miloco.middleware import verify_token
from miloco.rtsp.schema import RtspCameraCreate, RtspCameraUpdate
from miloco.rtsp.service import get_rtsp_service
from miloco.schema.common_schema import NormalResponse

router = APIRouter(prefix="/miot", tags=["RTSP Cameras"])


@router.get("/rtsp_cameras", response_model=NormalResponse)
async def list_rtsp_cameras(current_user: str = Depends(verify_token)):
    records = get_rtsp_service().list_state(denied=set(), connected=set())
    return NormalResponse(code=0, message="ok", data=records)


@router.post("/rtsp_cameras", response_model=NormalResponse)
async def create_rtsp_camera(
    request: RtspCameraCreate,
    current_user: str = Depends(verify_token),
):
    record = get_rtsp_service().create(request)
    return NormalResponse(code=0, message="RTSP camera created", data=record.model_dump())


@router.put("/rtsp_cameras/{did}", response_model=NormalResponse)
async def update_rtsp_camera(
    did: str,
    request: RtspCameraUpdate,
    current_user: str = Depends(verify_token),
):
    record = get_rtsp_service().update(did, request)
    return NormalResponse(code=0, message="RTSP camera updated", data=record.model_dump())


@router.delete("/rtsp_cameras/{did}", response_model=NormalResponse)
async def delete_rtsp_camera(
    did: str,
    current_user: str = Depends(verify_token),
):
    get_rtsp_service().delete(did)
    return NormalResponse(code=0, message="RTSP camera deleted", data=None)