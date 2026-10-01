"""主题管理：深色 / 浅色 / 跟随系统。

QSS 里顺带统一了几处容易「显得挤、显得脏」的细节：
    * 表头 / 树头不再出现系统默认的浅色底（深色主题下尤其刺眼）；
    * QSpinBox 的上下箭头默认是白色方框，这里改成与输入框同色；
    * QGroupBox 标题只保留文字，边框改由更淡的描边承担；
    * 滚动条收窄，避免在窄面板里挤占内容宽度。
"""
from __future__ import annotations

# ---- 深色 ----

DARK_QSS = """
QMainWindow, QWidget { background: #0D1117; color: #C9D1D9; }

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {
    background: #161B22; color: #C9D1D9;
    border: 1px solid #30363D; border-radius: 4px;
    padding: 3px 6px; selection-background-color: #1F6FEB;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #1F6FEB;
}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {
    color: #6E7681; background: #10151C;
}

QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background: #21262D; border: none; width: 16px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #30363D; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: none; width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid #C9D1D9;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: none; width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid #C9D1D9;
}

QComboBox {
    background: #161B22; color: #C9D1D9;
    border: 1px solid #30363D; border-radius: 4px; padding: 3px 6px;
}
QComboBox:hover { border: 1px solid #484F58; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox::down-arrow {
    image: none; width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid #8B949E;
}
QComboBox QAbstractItemView {
    background: #161B22; color: #C9D1D9;
    border: 1px solid #30363D;
    selection-background-color: #1F6FEB; selection-color: #FFFFFF;
    outline: none;
}

QPushButton {
    background: #21262D; color: #C9D1D9;
    border: 1px solid #30363D; padding: 4px 12px; border-radius: 4px;
}
QPushButton:hover { background: #30363D; border-color: #484F58; }
QPushButton:pressed { background: #161B22; }
QPushButton:disabled { color: #6E7681; background: #161B22; }
QPushButton:default { border-color: #1F6FEB; }

QCheckBox { spacing: 6px; }
QCheckBox::indicator { width: 14px; height: 14px; }
QCheckBox::indicator:unchecked {
    border: 1px solid #484F58; background: #161B22; border-radius: 3px;
}
QCheckBox::indicator:checked {
    border: 1px solid #1F6FEB; background: #1F6FEB; border-radius: 3px;
}

QTreeWidget, QListWidget, QTableWidget, QTreeView, QListView, QTableView {
    background: #0D1117; color: #C9D1D9;
    border: 1px solid #30363D; border-radius: 4px;
    alternate-background-color: #11161D;
    selection-background-color: #1F6FEB; selection-color: #FFFFFF;
    outline: none;
}
QTreeWidget::item, QListWidget::item { padding: 3px 2px; }
QTreeWidget::item:hover, QListWidget::item:hover { background: #161B22; }
QHeaderView { background: #161B22; }
QHeaderView::section {
    background: #161B22; color: #8B949E;
    padding: 5px 8px; border: none;
    border-right: 1px solid #21262D; border-bottom: 1px solid #30363D;
}
QHeaderView::section:hover { color: #C9D1D9; }
QTableCornerButton::section { background: #161B22; border: none; }
QTableView QTableCornerButton::section { background: #161B22; }
QTreeView::branch { background: transparent; }

QTabWidget::pane { border: 1px solid #30363D; border-radius: 4px; top: -1px; }
QTabBar::tab {
    background: transparent; color: #8B949E;
    padding: 6px 14px; margin-right: 2px;
    border: 1px solid transparent; border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:hover { color: #C9D1D9; background: #161B22; }
QTabBar::tab:selected {
    background: #0D1117; color: #C9D1D9;
    border: 1px solid #30363D; border-bottom-color: #0D1117;
}
QTabBar::tab:disabled { color: #484F58; }

QGroupBox {
    border: 1px solid #21262D; border-radius: 6px;
    margin-top: 10px; padding: 10px 8px 8px 8px;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 10px; padding: 0 4px;
    color: #8B949E; font-size: 12px;
}

QSplitter::handle { background: #21262D; }
QSplitter::handle:horizontal { width: 4px; }
QSplitter::handle:vertical { height: 4px; }
QSplitter::handle:hover { background: #1F6FEB; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical {
    background: #30363D; border-radius: 5px; min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #484F58; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal {
    background: #30363D; border-radius: 5px; min-width: 24px;
}
QScrollBar::handle:horizontal:hover { background: #484F58; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QMenuBar { background: #161B22; color: #C9D1D9; }
QMenuBar::item { padding: 4px 10px; background: transparent; }
QMenuBar::item:selected { background: #30363D; border-radius: 4px; }
QMenu { background: #161B22; color: #C9D1D9; border: 1px solid #30363D; padding: 4px; }
QMenu::item { padding: 5px 22px 5px 12px; border-radius: 4px; }
QMenu::item:selected { background: #1F6FEB; color: #FFFFFF; }
QMenu::separator { height: 1px; background: #30363D; margin: 4px 6px; }

QToolBar { background: #161B22; border: none; padding: 3px 6px; spacing: 4px; }
QToolBar::separator { background: #30363D; width: 1px; margin: 3px 6px; }
QToolButton { color: #C9D1D9; padding: 4px 8px; border-radius: 4px; }
QToolButton:hover { background: #30363D; }

QStatusBar { background: #161B22; color: #8B949E; }
QStatusBar::item { border: none; }

QScrollArea { border: none; }
QToolTip {
    background: #161B22; color: #C9D1D9;
    border: 1px solid #30363D; padding: 4px 6px;
}
"""

# ---- 深色：壳层（导航条 / 首页卡片 / 引导条 / 命令面板）----
#
# 这些是 2.1 版「重新设计」新增的可视语言：主按钮、卡片、步骤条、命令面板。
# 全部用 objectName 选择器，面板代码只 setObjectName，不写内联样式，
# 这样换主题不会留下一半深色一半浅色的残迹。

DARK_SHELL_QSS = """
QFrame#NavBar {
    background: #161B22;
    border: none; border-bottom: 1px solid #30363D;
}
QLabel#NavBrand { color: #C9D1D9; font-size: 14px; font-weight: 600; }
QLabel#NavSub { color: #6E7681; font-size: 10px; }
QPushButton#NavMode {
    background: transparent; color: #8B949E;
    border: 1px solid transparent; border-radius: 6px;
    padding: 5px 14px; font-size: 12px;
}
QPushButton#NavMode:hover { background: #21262D; color: #C9D1D9; }
QPushButton#NavMode:checked {
    background: #1F6FEB; color: #FFFFFF; border-color: #1F6FEB;
    font-weight: 600;
}
QPushButton#NavAction {
    background: transparent; color: #C9D1D9;
    border: 1px solid #30363D; border-radius: 6px; padding: 5px 10px;
}
QPushButton#NavAction:hover { background: #21262D; border-color: #484F58; }

QPushButton#Primary {
    background: #238636; color: #FFFFFF; font-weight: 600;
    border: 1px solid #2EA043; border-radius: 6px; padding: 6px 14px;
}
QPushButton#Primary:hover { background: #2EA043; }
QPushButton#Primary:pressed { background: #1A7F37; }
QPushButton#Primary:disabled { background: #21262D; color: #6E7681;
    border-color: #30363D; }
QPushButton#Secondary {
    background: #21262D; color: #C9D1D9;
    border: 1px solid #30363D; border-radius: 6px; padding: 6px 12px;
}
QPushButton#Secondary:hover { background: #30363D; border-color: #484F58; }

QFrame#Card {
    background: #161B22; border: 1px solid #30363D; border-radius: 8px;
}
QFrame#Card[hover="true"] {
    background: #1C2430; border: 1px solid #1F6FEB;
}
QFrame#Card:focus { border: 1px solid #1F6FEB; }
QLabel#CardTitle { color: #C9D1D9; font-size: 13px; font-weight: 600; }
QLabel#CardDesc { color: #8B949E; font-size: 11px; }
QLabel#CardKey {
    color: #79C0FF; font-size: 10px;
    background: #12233A; border: 1px solid #1F6FEB;
    border-radius: 3px; padding: 1px 5px;
}
QLabel#HeroTitle { color: #C9D1D9; font-size: 21px; font-weight: 600; }
QLabel#HeroSub { color: #8B949E; font-size: 12px; }
QLabel#HomeSection {
    color: #8B949E; font-size: 11px; font-weight: 600; letter-spacing: 1px;
}
QFrame#HomeBanner {
    background: #12233A; border: 1px solid #1F6FEB; border-radius: 8px;
}
QLabel#BannerText { color: #C9D1D9; font-size: 12px; }

QFrame#GuideStrip {
    background: #11161D; border: none;
    border-top: 1px solid #30363D; border-left: 3px solid #1F6FEB;
}
QLabel#GuideText { color: #C9D1D9; font-size: 12px; }
QLabel#GuideStep {
    color: #6E7681; font-size: 11px;
    border: 1px solid #30363D; border-radius: 9px; padding: 2px 10px;
}
QLabel#GuideStep[state="current"] {
    color: #FFFFFF; background: #1F6FEB; border-color: #1F6FEB;
}
QLabel#GuideStep[state="done"] {
    color: #3FB950; border-color: #238636; background: #0F2419;
}

QDialog#Palette { background: #0D1117; }
QLineEdit#PaletteSearch {
    background: #161B22; color: #C9D1D9; font-size: 14px;
    border: 1px solid #1F6FEB; border-radius: 6px; padding: 9px 12px;
}
QListWidget#PaletteList {
    background: #0D1117; border: 1px solid #30363D; border-radius: 6px;
    outline: none;
}
QListWidget#PaletteList::item { padding: 7px 10px; border-radius: 4px; }
QListWidget#PaletteList::item:selected { background: #1F6FEB; color: #FFFFFF; }
QLabel#PaletteHint { color: #6E7681; font-size: 11px; }
QLabel#Toast {
    background: #238636; color: #FFFFFF; font-size: 12px;
    border: 1px solid #2EA043; border-radius: 6px; padding: 8px 14px;
}
"""

# ---- 浅色 ----

LIGHT_QSS = """
QMainWindow, QWidget { background: #FFFFFF; color: #24292F; }

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {
    background: #F6F8FA; color: #24292F;
    border: 1px solid #D0D7DE; border-radius: 4px;
    padding: 3px 6px; selection-background-color: #0969DA;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #0969DA;
}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {
    color: #8C959F; background: #EAEEF2;
}

QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background: #EAEEF2; border: none; width: 16px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #D8DEE4; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: none; width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid #57606A;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: none; width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid #57606A;
}

QComboBox {
    background: #F6F8FA; color: #24292F;
    border: 1px solid #D0D7DE; border-radius: 4px; padding: 3px 6px;
}
QComboBox::drop-down { border: none; width: 18px; }
QComboBox::down-arrow {
    image: none; width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid #57606A;
}
QComboBox QAbstractItemView {
    background: #FFFFFF; color: #24292F; border: 1px solid #D0D7DE;
    selection-background-color: #0969DA; selection-color: #FFFFFF;
    outline: none;
}

QPushButton {
    background: #F6F8FA; color: #24292F;
    border: 1px solid #D0D7DE; padding: 4px 12px; border-radius: 4px;
}
QPushButton:hover { background: #EAEEF2; border-color: #AFB8C1; }
QPushButton:pressed { background: #D8DEE4; }
QPushButton:disabled { color: #8C959F; background: #EAEEF2; }

QCheckBox { spacing: 6px; }
QCheckBox::indicator { width: 14px; height: 14px; }
QCheckBox::indicator:unchecked {
    border: 1px solid #AFB8C1; background: #FFFFFF; border-radius: 3px;
}
QCheckBox::indicator:checked {
    border: 1px solid #0969DA; background: #0969DA; border-radius: 3px;
}

QTreeWidget, QListWidget, QTableWidget, QTreeView, QListView, QTableView {
    background: #FFFFFF; color: #24292F; border: 1px solid #D0D7DE;
    border-radius: 4px; alternate-background-color: #F6F8FA;
    selection-background-color: #0969DA; selection-color: #FFFFFF;
    outline: none;
}
QTreeWidget::item, QListWidget::item { padding: 3px 2px; }
QTreeWidget::item:hover, QListWidget::item:hover { background: #F6F8FA; }
QHeaderView { background: #F6F8FA; }
QHeaderView::section {
    background: #F6F8FA; color: #57606A;
    padding: 5px 8px; border: none;
    border-right: 1px solid #EAEEF2; border-bottom: 1px solid #D0D7DE;
}
QHeaderView::section:hover { color: #24292F; }
QTableView QTableCornerButton::section { background: #F6F8FA; border: none; }

QTabWidget::pane { border: 1px solid #D0D7DE; border-radius: 4px; top: -1px; }
QTabBar::tab {
    background: transparent; color: #57606A;
    padding: 6px 14px; margin-right: 2px;
    border: 1px solid transparent; border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:hover { color: #24292F; background: #F6F8FA; }
QTabBar::tab:selected {
    background: #FFFFFF; color: #24292F;
    border: 1px solid #D0D7DE; border-bottom-color: #FFFFFF;
}

QGroupBox {
    border: 1px solid #EAEEF2; border-radius: 6px;
    margin-top: 10px; padding: 10px 8px 8px 8px;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 10px; padding: 0 4px;
    color: #57606A; font-size: 12px;
}

QSplitter::handle { background: #EAEEF2; }
QSplitter::handle:horizontal { width: 4px; }
QSplitter::handle:vertical { height: 4px; }
QSplitter::handle:hover { background: #0969DA; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical {
    background: #D0D7DE; border-radius: 5px; min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #AFB8C1; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal {
    background: #D0D7DE; border-radius: 5px; min-width: 24px;
}
QScrollBar::handle:horizontal:hover { background: #AFB8C1; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QMenuBar { background: #F6F8FA; color: #24292F; }
QMenuBar::item { padding: 4px 10px; background: transparent; }
QMenuBar::item:selected { background: #EAEEF2; border-radius: 4px; }
QMenu { background: #FFFFFF; color: #24292F; border: 1px solid #D0D7DE; padding: 4px; }
QMenu::item { padding: 5px 22px 5px 12px; border-radius: 4px; }
QMenu::item:selected { background: #0969DA; color: #FFFFFF; }
QMenu::separator { height: 1px; background: #D0D7DE; margin: 4px 6px; }

QToolBar { background: #F6F8FA; border: none; padding: 3px 6px; spacing: 4px; }
QToolBar::separator { background: #D0D7DE; width: 1px; margin: 3px 6px; }
QToolButton { color: #24292F; padding: 4px 8px; border-radius: 4px; }
QToolButton:hover { background: #EAEEF2; }

QStatusBar { background: #F6F8FA; color: #57606A; }
QStatusBar::item { border: none; }

QScrollArea { border: none; }
QToolTip {
    background: #FFFFFF; color: #24292F;
    border: 1px solid #D0D7DE; padding: 4px 6px;
}
"""

# ---- 浅色：壳层（与 DARK_SHELL_QSS 一一对应）----

LIGHT_SHELL_QSS = """
QFrame#NavBar {
    background: #F6F8FA;
    border: none; border-bottom: 1px solid #D0D7DE;
}
QLabel#NavBrand { color: #24292F; font-size: 14px; font-weight: 600; }
QLabel#NavSub { color: #8C959F; font-size: 10px; }
QPushButton#NavMode {
    background: transparent; color: #57606A;
    border: 1px solid transparent; border-radius: 6px;
    padding: 5px 14px; font-size: 12px;
}
QPushButton#NavMode:hover { background: #EAEEF2; color: #24292F; }
QPushButton#NavMode:checked {
    background: #0969DA; color: #FFFFFF; border-color: #0969DA;
    font-weight: 600;
}
QPushButton#NavAction {
    background: transparent; color: #24292F;
    border: 1px solid #D0D7DE; border-radius: 6px; padding: 5px 10px;
}
QPushButton#NavAction:hover { background: #EAEEF2; border-color: #AFB8C1; }

QPushButton#Primary {
    background: #1F883D; color: #FFFFFF; font-weight: 600;
    border: 1px solid #1A7F37; border-radius: 6px; padding: 6px 14px;
}
QPushButton#Primary:hover { background: #1A7F37; }
QPushButton#Primary:pressed { background: #186D31; }
QPushButton#Primary:disabled { background: #EAEEF2; color: #8C959F;
    border-color: #D0D7DE; }
QPushButton#Secondary {
    background: #F6F8FA; color: #24292F;
    border: 1px solid #D0D7DE; border-radius: 6px; padding: 6px 12px;
}
QPushButton#Secondary:hover { background: #EAEEF2; border-color: #AFB8C1; }

QFrame#Card {
    background: #FFFFFF; border: 1px solid #D0D7DE; border-radius: 8px;
}
QFrame#Card[hover="true"] {
    background: #F0F6FF; border: 1px solid #0969DA;
}
QFrame#Card:focus { border: 1px solid #0969DA; }
QLabel#CardTitle { color: #24292F; font-size: 13px; font-weight: 600; }
QLabel#CardDesc { color: #57606A; font-size: 11px; }
QLabel#CardKey {
    color: #0969DA; font-size: 10px;
    background: #DDF4FF; border: 1px solid #54AEFF;
    border-radius: 3px; padding: 1px 5px;
}
QLabel#HeroTitle { color: #24292F; font-size: 21px; font-weight: 600; }
QLabel#HeroSub { color: #57606A; font-size: 12px; }
QLabel#HomeSection {
    color: #57606A; font-size: 11px; font-weight: 600; letter-spacing: 1px;
}
QFrame#HomeBanner {
    background: #DDF4FF; border: 1px solid #54AEFF; border-radius: 8px;
}
QLabel#BannerText { color: #24292F; font-size: 12px; }

QFrame#GuideStrip {
    background: #F6F8FA; border: none;
    border-top: 1px solid #D0D7DE; border-left: 3px solid #0969DA;
}
QLabel#GuideText { color: #24292F; font-size: 12px; }
QLabel#GuideStep {
    color: #8C959F; font-size: 11px;
    border: 1px solid #D0D7DE; border-radius: 9px; padding: 2px 10px;
}
QLabel#GuideStep[state="current"] {
    color: #FFFFFF; background: #0969DA; border-color: #0969DA;
}
QLabel#GuideStep[state="done"] {
    color: #1A7F37; border-color: #1A7F37; background: #DAFBE1;
}

QDialog#Palette { background: #FFFFFF; }
QLineEdit#PaletteSearch {
    background: #FFFFFF; color: #24292F; font-size: 14px;
    border: 1px solid #0969DA; border-radius: 6px; padding: 9px 12px;
}
QListWidget#PaletteList {
    background: #FFFFFF; border: 1px solid #D0D7DE; border-radius: 6px;
    outline: none;
}
QListWidget#PaletteList::item { padding: 7px 10px; border-radius: 4px; }
QListWidget#PaletteList::item:selected { background: #0969DA; color: #FFFFFF; }
QLabel#PaletteHint { color: #8C959F; font-size: 11px; }
QLabel#Toast {
    background: #1F883D; color: #FFFFFF; font-size: 12px;
    border: 1px solid #1A7F37; border-radius: 6px; padding: 8px 14px;
}
"""

#: 「跟随系统」时的壳层样式（用 palette() 取系统色，避免浅深写死）
NEUTRAL_SHELL_QSS = """
QFrame#NavBar { border: none; border-bottom: 1px solid palette(mid); }
QLabel#NavBrand { font-size: 14px; font-weight: 600; }
QLabel#NavSub { font-size: 10px; color: palette(dark); }
QPushButton#NavMode {
    background: transparent; border: 1px solid transparent;
    border-radius: 6px; padding: 5px 14px;
}
QPushButton#NavMode:checked {
    background: palette(highlight); color: palette(highlighted-text);
    font-weight: 600;
}
QPushButton#Primary {
    background: palette(highlight); color: palette(highlighted-text);
    font-weight: 600; border-radius: 6px; padding: 6px 14px;
}
QFrame#Card {
    border: 1px solid palette(mid); border-radius: 8px;
    background: palette(base);
}
QFrame#Card:focus { border: 1px solid palette(highlight); }
QLabel#CardTitle { font-size: 13px; font-weight: 600; }
QLabel#HeroTitle { font-size: 21px; font-weight: 600; }
QLabel#HomeSection { font-size: 11px; font-weight: 600; color: palette(dark); }
QFrame#GuideStrip {
    border: none; border-top: 1px solid palette(mid);
    border-left: 3px solid palette(highlight);
}
QLabel#GuideStep { border: 1px solid palette(mid); border-radius: 9px;
    padding: 2px 10px; color: palette(dark); }
QLabel#GuideStep[state="current"] {
    background: palette(highlight); color: palette(highlighted-text);
}
QLineEdit#PaletteSearch { font-size: 14px; padding: 9px 12px; }
QListWidget#PaletteList::item { padding: 7px 10px; }
QLabel#PaletteHint, QLabel#CardDesc, QLabel#HeroSub { color: palette(dark); }
QLabel#Toast {
    background: palette(highlight); color: palette(highlighted-text);
    border-radius: 6px; padding: 8px 14px;
}
"""

# 「跟随系统」时用到的轻量样式：只解决表头/微调控件的默认外观问题
NEUTRAL_QSS = """
QHeaderView::section {
    padding: 5px 8px; border: none;
    border-right: 1px solid palette(midlight);
    border-bottom: 1px solid palette(mid);
}
QSpinBox::up-button, QSpinBox::down-button { width: 16px; }
QGroupBox { margin-top: 10px; padding: 10px 8px 8px 8px; }
QSplitter::handle:horizontal { width: 4px; }
QSplitter::handle:vertical { height: 4px; }
QToolTip { padding: 4px 6px; }
"""


def apply_theme(app, name: str) -> None:
    if name == "dark":
        app.setStyleSheet(DARK_QSS + DARK_SHELL_QSS)
    elif name == "light":
        app.setStyleSheet(LIGHT_QSS + LIGHT_SHELL_QSS)
    else:
        app.setStyleSheet(NEUTRAL_QSS + NEUTRAL_SHELL_QSS)


# ---------------------------------------------------------------------------
# 设计 token（排版唯一事实来源）
# ---------------------------------------------------------------------------
#
# 之前各面板的间距是「随手写」的：有的 4、有的 6、有的 8；标题颜色到处
# 硬编码 #8B949E；按钮宽度靠 setFixedWidth(160) 猜。结果就是同一页里
# 疏密不一致、浅色主题下灰字过浅、窄窗口里按钮被挤成两行。
#
# 这里把间距 / 圆角 / 字号 / 控件尺寸 / 语义颜色集中成一组 token，
# 界面代码只引用 token，不再写魔法数字。

#: 间距阶梯（4 的倍数）
SPACING = {
    "xxs": 2,
    "xs": 4,
    "sm": 6,
    "md": 8,
    "lg": 12,
    "xl": 16,
}

#: 常用「页边距」预设（左, 上, 右, 下）
MARGINS = {
    "page": (8, 8, 8, 8),        # 单个页签的内容区
    "panel": (6, 6, 6, 6),       # 面板最外层
    "section": (10, 8, 10, 8),   # QGroupBox 内部
    "compact": (4, 4, 4, 4),     # 紧凑容器
    "none": (0, 0, 0, 0),
}

#: 控件尺寸
SIZES = {
    "row_height": 24,        # 表格行高
    "header_height": 26,     # 表头高度
    "toolbar_height": 28,    # 工具条按钮高度
    "input_height": 26,      # 单行输入框
    "field_width": 132,      # 表单里数值 / 短文本输入
    "field_width_wide": 200,  # 路径之类的长文本输入
    "combo_min": 96,
    "list_width": 232,       # 玩家 / 列表类侧栏
    "split_handle": 4,
}

#: 字号（pt 的近似 px 值，直接给 QSS / QFont 用）
FONTS = {
    "title": 13,
    "body": 12,
    "label": 11,
    "mono": 9,
}

#: 语义颜色：深色 / 浅色两套，避免浅色主题下「灰字看不到」
COLORS_DARK = {
    "muted": "#8B949E",
    "faint": "#6E7681",
    "border": "#30363D",
    "surface": "#161B22",
    "surface_alt": "#0D1117",
    "accent": "#1F6FEB",
    "ok": "#3FB950",
    "warn": "#D29922",
    "error": "#F85149",
    "danger_bg": "#3D1518",
    "chip_bg": "#21262D",
    "chip_text": "#C9D1D9",
}

COLORS_LIGHT = {
    "muted": "#57606A",
    "faint": "#6E7781",
    "border": "#D0D7DE",
    "surface": "#FFFFFF",
    "surface_alt": "#F6F8FA",
    "accent": "#0969DA",
    "ok": "#1A7F37",
    "warn": "#9A6700",
    "error": "#CF222E",
    "danger_bg": "#FFEBE9",
    "chip_bg": "#EAEEF2",
    "chip_text": "#24292F",
}


def colors(widget=None) -> dict:
    """按当前主题取语义色（「跟随系统」也能正确判断）。"""
    return dict(COLORS_DARK if _is_dark(widget) else COLORS_LIGHT)


def muted_style(widget=None, *, size: str = "label") -> str:
    """次要文字的样式表（颜色跟随主题，字号用 token）。"""
    palette = colors(widget)
    return f"color: {palette['muted']}; font-size: {FONTS[size]}pt;"


def hint_style(widget=None) -> str:
    """更淡的提示文字（比 muted 再轻一档）。"""
    palette = colors(widget)
    return f"color: {palette['faint']}; font-size: {FONTS['label']}pt;"


def apply_spacing(layout, key: str = "page", *, spacing: str = "sm",
                  stretch_at: int | None = None):
    """一次性把边距 + 间距套到布局上，返回布局本身（方便链式写）。

    `stretch_at` 给定时在该位置插入伸缩项（常见于按钮行右对齐）。
    """
    from PySide6.QtWidgets import QBoxLayout
    if isinstance(layout, QBoxLayout) and stretch_at is not None:
        layout.addStretch(stretch_at)
    layout.setContentsMargins(*MARGINS.get(key, MARGINS["sm" if key == "sm"
                                                         else "page"]))
    layout.setSpacing(SPACING.get(spacing, 6))
    return layout


def polish_tables(tables, widget=None) -> None:
    """统一美化一组表格：行高、表头高度、交替行、不显示竖向表头。

    抽出来是因为 11 个页签都在做同一件事，之前每个都抄一遍。
    """
    palette = colors(widget)
    qss = (
        f"QTableWidget {{ gridline-color: {palette['border']}; }}"
        f"QHeaderView::section {{ padding: 4px 6px; }}"
    )
    for table in tables:
        if table is None:                               # pragma: no cover
            continue
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(SIZES["row_height"])
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.setShowGrid(False)
        table.setStyleSheet(qss)


def polish_form(form) -> None:
    """统一表单：行间距、标签对齐、字段增长策略。"""
    if form is None:                                    # pragma: no cover
        return
    form.setContentsMargins(*MARGINS["section"])
    form.setHorizontalSpacing(SPACING["md"])
    form.setVerticalSpacing(SPACING["sm"])
    form.setLabelAlignment(_label_alignment())
    form.setFieldGrowthPolicy(
        form.FieldGrowthPolicy.ExpandingFieldsGrow)


def _label_alignment():
    from PySide6.QtCore import Qt
    return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


# ---------------------------------------------------------------------------
# 主题判定 + NBT 编辑器配色
# ---------------------------------------------------------------------------


def _is_dark(widget=None) -> bool:
    """按当前调色板判断是否深色主题（「跟随系统」也能正确判断）。"""
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication
    if widget is not None:
        palette = widget.palette()
    else:
        app = QApplication.instance()
        if app is None:
            return True
        palette = app.palette()
    window = palette.color(QPalette.ColorRole.Window)
    text = palette.color(QPalette.ColorRole.WindowText)
    # 以窗口背景与文字的相对亮度判断，比固定取色更可靠
    return window.lightness() < text.lightness()


#: 深色主题下的类型色（GitHub Dark 系）
NBT_TYPE_COLORS_DARK = {
    "compound": "#79C0FF",
    "list": "#79C0FF",
    "byte_array": "#D2A8FF",
    "int_array": "#D2A8FF",
    "long_array": "#D2A8FF",
    "string": "#A5D6FF",
    "byte": "#7EE787",
    "short": "#7EE787",
    "int": "#7EE787",
    "long": "#7EE787",
    "bool": "#7EE787",
    "float": "#FFA657",
    "double": "#FFA657",
}

#: 浅色主题下的类型色（同样按类型分色，但压暗保证在白底上可读）
NBT_TYPE_COLORS_LIGHT = {
    "compound": "#0550AE",
    "list": "#0550AE",
    "byte_array": "#6E2594",
    "int_array": "#6E2594",
    "long_array": "#6E2594",
    "string": "#0A3069",
    "byte": "#116329",
    "short": "#116329",
    "int": "#116329",
    "long": "#116329",
    "bool": "#116329",
    "float": "#953800",
    "double": "#953800",
}

#: 其余需要跟随主题的编辑器配色
NBT_PALETTE_DARK = {
    "muted": "#8B949E",
    "error": "#FF7B72",
    "ok": "#7EE787",
    "highlight": "#9E6A03",
    "highlight_text": "#FFFFFF",
    "hexdump": "#C9D1D9",
    "badge_bg": "#21262D",
    "badge_border": "#30363D",
}

NBT_PALETTE_LIGHT = {
    "muted": "#57606A",
    "error": "#CF222E",
    "ok": "#1A7F37",
    "highlight": "#FFF3C4",
    "highlight_text": "#24292F",
    "hexdump": "#24292F",
    "badge_bg": "#EAEEF2",
    "badge_border": "#D0D7DE",
}


def nbt_type_colors(widget=None) -> dict:
    """NBT 类型列配色（按当前主题）。"""
    return dict(NBT_TYPE_COLORS_DARK if _is_dark(widget) else NBT_TYPE_COLORS_LIGHT)


def nbt_palette(widget=None) -> dict:
    """NBT 编辑器其它配色（次要文字、错误、命中高亮……）。"""
    return dict(NBT_PALETTE_DARK if _is_dark(widget) else NBT_PALETTE_LIGHT)


__all__ = [
    "DARK_QSS", "LIGHT_QSS", "NEUTRAL_QSS",
    "DARK_SHELL_QSS", "LIGHT_SHELL_QSS", "NEUTRAL_SHELL_QSS", "apply_theme",
    "NBT_TYPE_COLORS_DARK", "NBT_TYPE_COLORS_LIGHT",
    "NBT_PALETTE_DARK", "NBT_PALETTE_LIGHT",
    "nbt_type_colors", "nbt_palette",
    "SPACING", "MARGINS", "SIZES", "FONTS", "COLORS_DARK", "COLORS_LIGHT",
    "colors", "muted_style", "hint_style", "apply_spacing", "polish_tables",
    "polish_form",
]
