from dataclasses import dataclass, field
from typing import List, Optional

from .framework.schemas import BaseSession, BaseBusiness


@dataclass
class Business(BaseBusiness):
    """
    本插件专属的业务模型
    """

    share_recieve_path: Optional[str] = None
    share_recieve_url: Optional[str] = None

    share_strm_u115_url: Optional[str] = None

    offline_download_path: Optional[str] = None
    offline_download_urls: Optional[List[str]] = None


@dataclass
class Session(BaseSession):
    """
    组装成本插件专属的 Session
    """

    # 指定默认视图，用于错误兜底
    default_view = "close"

    business: Business = field(default_factory=Business)
