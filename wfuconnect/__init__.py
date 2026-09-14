"""潍坊学院校园网（gwifi 门户）断线自动重连。

对外入口：

    wfuconnect login          # 安装后生成的命令行程序
    python -m wfuconnect login
"""

from __future__ import annotations

__version__ = "0.3.0"
__all__ = ["DEFAULT_BASE_URL", "PortalClient", "PortalError", "__version__"]


def __getattr__(name: str):
    """按需从 wfuconnect.portal 导出，避免导入包时就加载网络依赖。"""
    if name in ("DEFAULT_BASE_URL", "PortalClient", "PortalError"):
        from . import portal

        return getattr(portal, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
