"""地点模块（业务模块）。

用户 2026-11-01 的架构（见 `docs/ARCHITECTURE.md`）：

    每个地点有自己的模块，只管自己的部分：
    **内页判据**（这是不是我的页面）+ **买什么/做什么** + **自己的状态**。

    地点模块**不需要**互相知道。
    「今天本来该进步行街，但现在在餐厅」这种问题由**地图模块**回答
    （说明是钱不够，或没有合适的扭蛋）。

    拆分粒度：按**业务**拆，不按界面拆 ——
    「地图上步行街那个按钮」和「步行街内页」是同一家事的两个阶段，
    所以住在一起。

每个模块只提供**判断**，不点击 —— 点击由 `handlers/` 里的 handler 做。
"""

from src.tasks.training.places.arcade import ArcadePlace
from src.tasks.training.places.map import MapPlace
from src.tasks.training.places.park import ParkPlace
from src.tasks.training.places.restaurant import RestaurantPlace
from src.tasks.training.places.shop import ShopPlace
from src.tasks.training.places.street import StreetPlace

__all__ = ["ArcadePlace", "MapPlace", "ParkPlace", "RestaurantPlace",
           "ShopPlace", "StreetPlace"]
