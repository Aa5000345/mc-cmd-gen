"""壳层（shell）：2.1 重新设计新增的首页 / 导航条 / 命令面板 / 引导条。

设计目标（针对「太难用了、交互引导太烂」的反馈）：

* **看得出来在哪**：顶部导航条显示当前模式，模式按钮带选中态；
* **知道下一步点哪里**：底部引导条按状态给出动作按钮；
* **不用记路径**：Ctrl+K 命令面板覆盖全部命令、页签与动作；
* **首屏有路可走**：首页把常见任务做成卡片，并给 3 步上手清单；
* **空状态不空**：各处空面板都有说明与入口按钮。

壳层只依赖 Qt 与 `theme`，业务动作全部通过信号回主窗口，方便单独测试。
"""
from app.ui.shell.cards import Banner, Card, SectionTitle
from app.ui.shell.guidance import GuideStrip
from app.ui.shell.home import HomePage
from app.ui.shell.navbar import NavBar
from app.ui.shell.palette import CommandPalette, PaletteEntry, score_entry
from app.ui.shell.welcome import WelcomeDialog

__all__ = [
    "Banner", "Card", "SectionTitle", "GuideStrip", "HomePage", "NavBar",
    "CommandPalette", "PaletteEntry", "score_entry", "WelcomeDialog",
]
