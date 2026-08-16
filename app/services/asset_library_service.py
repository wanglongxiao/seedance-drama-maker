# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

import json
import time
from typing import Any, Dict, List, Optional

from app.config import config
from app.utils.logger import get_logger

try:
    from byteplus_sdk.ApiInfo import ApiInfo
    from byteplus_sdk.Credentials import Credentials
    from byteplus_sdk.ServiceInfo import ServiceInfo
    from byteplus_sdk.base.Service import Service
except ImportError:  # pragma: no cover - covered by runtime dependency install
    ApiInfo = None
    Credentials = None
    ServiceInfo = None
    Service = None

logger = get_logger("asset_library_service")


class AssetLibraryError(RuntimeError):
    """虚拟素材库通用异常。"""


class AssetActivationFailedError(AssetLibraryError):
    """素材预处理失败或超时。"""


class _ArkAssetOpenAPI(Service or object):
    _ACTIONS = (
        "CreateAssetGroup",
        "GetAssetGroup",
        "ListAssetGroups",
        "DeleteAssetGroup",
        "CreateAsset",
        "GetAsset",
        "ListAssets",
        "DeleteAsset",
    )

    def __init__(
        self,
        ak: str,
        sk: str,
        region: str,
        host: str,
        connection_timeout: int,
        socket_timeout: int,
    ) -> None:
        if not all([ApiInfo, Credentials, ServiceInfo, Service]):
            raise AssetLibraryError(
                "byteplus-sdk is required for asset library access. Please install dependencies again."
            )

        credentials = Credentials(ak, sk, "ark", region)
        service_info = ServiceInfo(
            host=host,
            header={},
            credentials=credentials,
            connection_timeout=connection_timeout,
            socket_timeout=socket_timeout,
            scheme="https",
        )
        api_info = {
            action: ApiInfo(
                method="POST",
                path="/",
                query={"Action": action, "Version": "2024-01-01"},
                form={},
                header={},
            )
            for action in self._ACTIONS
        }
        super().__init__(service_info, api_info)


class AssetLibraryService:
    """封装 ModelArk 虚拟素材库（AK/SK 鉴权）。"""

    def __init__(self) -> None:
        self.ak = config.get("byteplus.ak")
        self.sk = config.get("byteplus.sk")
        self.region = config.asset_library_region
        self.api_host = config.asset_library_api_host
        self.project_name = config.asset_library_project_name
        self.group_type = str(config.get("asset_library.group_type", "AIGC")).strip() or "AIGC"
        self.moderation_strategy = str(config.get("asset_library.moderation_strategy", "Skip")).strip() or "Skip"
        self.activation_poll_interval = max(1, int(config.get("asset_library.activation_poll_interval", 3)))
        self.activation_timeout = max(10, int(config.get("asset_library.activation_timeout", 300)))
        self.connection_timeout = max(1, int(config.get("asset_library.connection_timeout", 30)))
        self.socket_timeout = max(1, int(config.get("asset_library.socket_timeout", 300)))

        self.client: Optional[_ArkAssetOpenAPI] = None
        if self.ak and self.sk and all([ApiInfo, Credentials, ServiceInfo, Service]):
            self.client = _ArkAssetOpenAPI(
                ak=self.ak,
                sk=self.sk,
                region=self.region,
                host=self.api_host,
                connection_timeout=self.connection_timeout,
                socket_timeout=self.socket_timeout,
            )
        elif self.ak and self.sk:
            logger.warning("byteplus-sdk is unavailable, asset library service is disabled")
        else:
            logger.warning("BytePlus AK/SK missing, asset library service is disabled")

    def _ensure_client(self) -> _ArkAssetOpenAPI:
        if not self.client:
            raise AssetLibraryError("Asset library is unavailable because BytePlus AK/SK is not configured")
        return self.client

    def _call(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        client = self._ensure_client()
        logger.info("[ASSET_LIBRARY] action=%s payload=%s", action, json.dumps(payload, ensure_ascii=False))
        try:
            raw = client.json(action, {}, payload)
            parsed = json.loads(raw)
        except Exception as exc:
            message = str(exc)
            logger.error("[ASSET_LIBRARY] action=%s failed: %s", action, message)
            if "SubscriptionRequired" in message:
                raise AssetLibraryError(
                    "Virtual portrait library subscription is not enabled for the current BytePlus account"
                ) from exc
            raise AssetLibraryError(f"{action} failed: {message}") from exc

        result = parsed.get("Result", {})
        logger.info("[ASSET_LIBRARY] action=%s result=%s", action, json.dumps(result, ensure_ascii=False))
        return result

    def build_asset_uri(self, asset_id: str) -> str:
        return f"asset://{asset_id}"

    def create_asset_group(
        self,
        name: str,
        description: str = "",
        project_name: Optional[str] = None,
    ) -> str:
        payload: Dict[str, Any] = {
            "Name": name,
            "GroupType": self.group_type,
            "ProjectName": project_name or self.project_name,
        }
        clean_description = str(description or "").strip()
        if clean_description:
            payload["Description"] = clean_description[:300]

        result = self._call("CreateAssetGroup", payload)
        group_id = str(result.get("Id") or "").strip()
        if not group_id:
            raise AssetLibraryError("CreateAssetGroup succeeded but no group id was returned")
        return group_id

    def get_asset_group(self, group_id: str, project_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call(
            "GetAssetGroup",
            {
                "Id": group_id,
                "ProjectName": project_name or self.project_name,
            },
        )

    def list_asset_groups(
        self,
        *,
        name: Optional[str] = None,
        group_ids: Optional[List[str]] = None,
        page_number: int = 1,
        page_size: int = 100,
        project_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "Filter": {
                "GroupType": self.group_type,
            },
            "PageNumber": page_number,
            "PageSize": page_size,
            "SortBy": "CreateTime",
            "SortOrder": "Desc",
            "ProjectName": project_name or self.project_name,
        }
        if name:
            payload["Filter"]["Name"] = name
        if group_ids:
            payload["Filter"]["GroupIds"] = group_ids
        return self._call("ListAssetGroups", payload)

    def delete_asset_group(self, group_id: str, project_name: Optional[str] = None) -> None:
        self._call(
            "DeleteAssetGroup",
            {
                "Id": group_id,
                "ProjectName": project_name or self.project_name,
            },
        )

    def create_asset(
        self,
        *,
        group_id: str,
        url: str,
        name: str,
        asset_type: str = "Image",
        project_name: Optional[str] = None,
        moderation_strategy: Optional[str] = None,
    ) -> str:
        moderation_value = str(moderation_strategy or self.moderation_strategy).strip() or "Skip"
        payload: Dict[str, Any] = {
            "GroupId": group_id,
            "URL": url,
            "Name": name[:64],
            "AssetType": asset_type,
            "ProjectName": project_name or self.project_name,
        }
        if moderation_value:
            payload["Moderation"] = {"Strategy": moderation_value}
        result = self._call("CreateAsset", payload)
        asset_id = str(result.get("Id") or "").strip()
        if not asset_id:
            raise AssetLibraryError("CreateAsset succeeded but no asset id was returned")
        return asset_id

    def get_asset(self, asset_id: str, project_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call(
            "GetAsset",
            {
                "Id": asset_id,
                "ProjectName": project_name or self.project_name,
            },
        )

    def list_assets(
        self,
        *,
        group_ids: Optional[List[str]] = None,
        statuses: Optional[List[str]] = None,
        name: Optional[str] = None,
        page_number: int = 1,
        page_size: int = 100,
        project_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "Filter": {
                "GroupType": self.group_type,
            },
            "PageNumber": page_number,
            "PageSize": page_size,
            "SortBy": "CreateTime",
            "SortOrder": "Desc",
            "ProjectName": project_name or self.project_name,
        }
        if group_ids:
            payload["Filter"]["GroupIds"] = group_ids
        if statuses:
            payload["Filter"]["Statuses"] = statuses
        if name:
            payload["Filter"]["Name"] = name
        return self._call("ListAssets", payload)

    def delete_asset(self, asset_id: str, project_name: Optional[str] = None) -> None:
        self._call(
            "DeleteAsset",
            {
                "Id": asset_id,
                "ProjectName": project_name or self.project_name,
            },
        )

    def wait_for_asset_active(
        self,
        asset_id: str,
        *,
        project_name: Optional[str] = None,
        timeout: Optional[int] = None,
        poll_interval: Optional[int] = None,
    ) -> Dict[str, Any]:
        max_wait = max(1, int(timeout or self.activation_timeout))
        interval = max(1, int(poll_interval or self.activation_poll_interval))
        deadline = time.time() + max_wait

        while time.time() < deadline:
            asset = self.get_asset(asset_id, project_name=project_name)
            status = str(asset.get("Status") or "").strip()
            if status == "Active":
                return asset
            if status == "Failed":
                error = asset.get("Error") or {}
                message = str(error.get("Message") or "unknown asset preprocessing failure")
                raise AssetActivationFailedError(f"Asset {asset_id} preprocessing failed: {message}")
            time.sleep(interval)

        raise AssetActivationFailedError(f"Asset {asset_id} did not become Active within {max_wait} seconds")

    def register_image_asset(
        self,
        *,
        group_id: str,
        url: str,
        name: str,
        project_name: Optional[str] = None,
        moderation_strategy: Optional[str] = None,
    ) -> Dict[str, Any]:
        asset_id = self.create_asset(
            group_id=group_id,
            url=url,
            name=name,
            asset_type="Image",
            project_name=project_name,
            moderation_strategy=moderation_strategy,
        )
        return self.wait_for_asset_active(asset_id, project_name=project_name)

    def ensure_project_asset_group(
        self,
        *,
        project_id: str,
        group_name: str,
        description: str = "",
        project_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        project_name = project_name or self.project_name
        groups = self.list_asset_groups(name=group_name, project_name=project_name).get("Items", []) or []
        for group in groups:
            if str(group.get("Name") or "").strip() == group_name:
                return group

        group_id = self.create_asset_group(
            name=group_name,
            description=description,
            project_name=project_name,
        )
        group = self.get_asset_group(group_id, project_name=project_name)
        logger.info("Created asset group for project %s: %s", project_id, group_id)
        return group

    def cleanup_asset_group(
        self,
        *,
        group_id: str,
        project_name: Optional[str] = None,
    ) -> None:
        project_name = project_name or self.project_name
        page_number = 1
        while True:
            result = self.list_assets(
                group_ids=[group_id],
                page_number=page_number,
                page_size=100,
                project_name=project_name,
            )
            items = result.get("Items", []) or []
            for item in items:
                asset_id = str(item.get("Id") or "").strip()
                if asset_id:
                    try:
                        self.delete_asset(asset_id, project_name=project_name)
                    except Exception as exc:
                        logger.error("Failed to delete asset %s in group %s: %s", asset_id, group_id, str(exc))
            total_count = int(result.get("TotalCount") or 0)
            if page_number * 100 >= total_count or not items:
                break
            page_number += 1

        self.delete_asset_group(group_id, project_name=project_name)


asset_library_service = AssetLibraryService()
