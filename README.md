# MCStudio（MC 工坊）

**Minecraft Java 1.21.1 命令生成器 + 世界与 NBT 编辑器。**

面向 Minecraft Java 版 **1.21.1** 的桌面工具：一边是命令生成器（`/summon`、
`/give`、`/execute` 等 44 条命令，NBT、物品组件、execute 条件链全支持），
另一边是**世界存档与 NBT 编辑器**（`level.dat`、`playerdata`、实体、容器、
地图、区域索引），两者之间用**联动总线**做双向桥接。

- **技术栈**：Python 3.10+ / PySide6
- **目标版本**：Minecraft Java 1.21.1
- **平台**：Windows / macOS / Linux
- **内置命令**：44 条（3 条手写核心 + 41 条 Schema 驱动）
- **旧名字**：MCCmdGen —— 见下方「项目改名」一节，旧入口与旧数据全部兼容

---

## 项目改名（MCCmdGen → MCStudio）

功能早已不局限于命令生成，所以重新命名。候选名称：

| 候选 | 中文 | 说明 |
|---|---|---|
| **MCStudio** | MC 工坊 | 命令 + 世界/NBT 一体的「工坊」，短、好记。**★ 本次采用** |
| MCForge | MC 锻造台 | 强调打造命令与存档，但与 Forge / NeoForge 模组加载器撞名 |
| MCWorldsmith | MC 世界工匠 | 最直白地表达「世界编辑器」，但偏长，也不像命令生成器 |
| CraftConsole | 世界控制台 | 强调控制台式操作，但不如 MC 前缀直观 |
| MCBlockBench | 方块工作台 | 突出方块 / NBT 编辑，但 Blockbench 是知名 3D 建模软件 |

**改名影响与处理**（`python mcgen.py rename-info` 也能打印这一节）：

| 位置 | 处理 |
|---|---|
| 窗口标题 / 关于页 / 状态栏 | 统一显示 `MCStudio`（MC 工坊） |
| `pyproject.toml` | `name = "mcstudio"`，脚本入口 `mcstudio`（保留 `mc-cmd-gen`） |
| README / CLI 帮助 | 全部更新 |
| 安装包名 | `mcstudio`（旧的 `mc-cmd-generator` 用法仍可 `pip install -e .`） |
| 应用数据目录 | 新 `%LOCALAPPDATA%\mcstudio\`，旧 `%LOCALAPPDATA%\MCCmdGen\` 自动迁移 |
| 数据包 generator 标识 | 写 `MCStudio`，读的时候兼容 `MCCmdGen` |

### 兼容与迁移

- **Python 导入路径不变**：包名仍是 `app`，`from app.core import ...` 这类写法
  与现有测试都不用改；
- **保留 `mcgen.py` 兼容入口**，新增等价的 `mcstudio.py`：
  `python mcgen.py …` 与 `python mcstudio.py …` 完全一样；
- **旧数据目录自动迁移**（只复制、不删除、幂等）：
  首次启动时把 `MCCmdGen/` 下的 `data.db`、`user_data/`、`scenes/`、
  `update_source.txt` 复制到 `mcstudio/`，旧目录原样保留，随时可以回退；
  迁移结果记录在 `mcstudio/migration.json`；
- 已经有新目录数据时**不会覆盖**（逐文件跳过），所以迁移可以安全地重复执行；
- 想关掉自动迁移：设环境变量 `MCSTUDIO_NO_MIGRATE=1`；
- 想指定数据目录：`MCSTUDIO_DATA_DIR`（兼容旧的 `MCCMDGEN_DATA_DIR`）；
- 旧的导出文件（`generator: "MCCmdGen"`）仍可导入，只会多一条提示；
- 新目录还没建立时，程序会继续沿用有数据的旧目录，不会出现「数据凭空消失」。

---

## 双面板架构

```
┌──────────────┬──────────────────────────────────┬───────────────┐
│ 命令生成器    │  编辑器（世界 / NBT）             │ 输出 + 联动总线 │
│ 命令树        │  世界总览 玩家 实体 容器 NBT 树   │ 命令输出       │
│ 命令面板      │  地图 区域 数据包 数据包工程      │ 联动总线       │
│              │  蓝图/配方 可视化编排 中文世界编辑 │               │
│              │  差异 跨存档 健康检查              │               │
└──────────────┴──────────────────────────────────┴───────────────┘
```

- 主窗口是 `QMainWindow + QSplitter + QTabWidget/QStackedWidget`，
  三栏都可拖动并记忆；
- **工作区模式**（`视图 → 工作区`，`Ctrl+Shift+1/2/3`）：
  **命令模式**（只要命令面板）/ **编辑模式**（只要世界编辑器）/
  **双栏联动**（三栏并列）；
- **编辑器可独立成窗口**（`Ctrl+Shift+D`），方便丢到副屏；
- 两侧通过 **联动总线**（右侧「联动总线」页签）交换**结构化对象**，
  不是纯文本：物品、实体、方块、坐标、NBT 路径、玩家、容器。

### 统一上下文

`current_world / current_player / current_dimension / current_pos /
selected_item / selected_entity / selected_block / selected_nbt_path /
selected_container / selected_map`，显示在联动总线抽屉与状态栏右侧。

- 右上「→ 存档」按钮 / `Ctrl+Shift+B`：把输出区命令送进总线；
- 物品卡片「→ /give 面板」、实体卡片「→ /summon 面板」；
- `Ctrl+Shift+W` 直接跳到世界编辑器；
- 顶部「启用联动总线」开关：**关掉后两个面板完全独立**（不发也不收）；
- 底部/抽屉里的**桥接日志**记录来源、目标、差异条数、备份名与撤销入口。

---

## 2.1 界面重新设计（易用性改造）

旧版首屏会把人直接丢进**一个世界都没打开的空白编辑器**：15 个空页签 +
一排灰按钮，主力功能（命令生成）连入口都看不见，填完参数也不知道下一步点哪里。
2.1 版按「看得出来在哪、知道下一步点哪里、不用记路径、空状态不空」重做了外壳。

### 新的四段式导航

```
[MCStudio]  [首页]  ｜ [命令生成][双栏联动][世界编辑] ｜ 查找命令 / 中文输入 ｜ [命令面板 Ctrl+K][打开存档][打开 NBT][帮助 ▾][更多 ▾]
```

* **模式切换带选中态**：一眼知道自己在命令模式、编辑模式还是双栏；
* **更多 ▾** 收着旧版工具条上的全部次要动作（重新生成、NBT → 输出、
  目标选择器 → 输出、输出 → 存档、折叠面板、编辑器独立成窗口…），一个都没丢。

### 首页（首屏）

六个任务卡直接跳到对应面板：造一件自定义物品 / 生成一条命令 / 编辑世界存档 /
编辑 NBT 文件 / 数据包工程 / 用中文写命令。下面还有：

* **3 步上手清单**（生成第一条命令 → 复制到剪贴板 → 打开一个存档），
  做一步自动打勾并落盘，全打勾后引导条自动收起；
* **最近命令 / 最近存档**，点一下就回到那条命令或那个存档。

### 命令面板（Ctrl+K）

一个输入框搜**全部命令 / 页签 / 动作**，中文关键字也行（「存档」「主题」「附魔」），
↑↓ 选择、Enter 执行、Esc 关闭。44 条命令不必再在左侧树里翻。

### 上下文引导条（窗口底部常驻）

左侧是步骤胶囊（① 选命令 ② 填参数 ③ 复制到游戏），中间一句话说明当前状态，
右侧是**此刻该点的按钮**。它会随状态变化：

| 状态 | 引导条说什么 |
|---|---|
| 输出区空着 | 「在面板里填好参数，命令会实时出现在右侧」+ 重新生成 / 查看输出区 |
| 有命令了 | 「点复制命令粘到游戏聊天栏，或 → 存档预览差异后写进世界」+ 复制命令 / → 存档 |
| 面板里还是初始示例 | 明确说「这是示例，改一项就变成你的命令」 |
| 存档没打开 | 「还没有打开存档」+ 打开世界存档… / 看最近打开的世界 |
| 存档只读 | 「想改内容先解锁编辑，写入前会备份」+ 解锁编辑 / 检查存档健康 |
| 有未应用修改 | 「先预览差异，再应用修改」+ 预览差异 / 应用修改 |

### 每个命令面板都有主行动按钮

面板底部固定一行：`清空参数` `发送到输出区` **`复制命令`**，
并实时显示「当前命令 N 字符」。输出区标题行的**「复制命令」是主按钮**，
复制成功还会弹一个居中浮层提示（不再只闪 2 秒状态栏）。

### 世界编辑器

* **没打开存档时**用一块引导页盖住 15 个空表格，给「打开世界存档…」
  「看最近打开的世界」两个按钮；
* 工具条加**「全部页签 ▾」**下拉，页签太多也不怕被滚动箭头藏起来；
* **双栏模式下点中间栏的「世界编辑器」页签**就能就地编辑存档
  （旧版这个按钮点了没反应），切回别的页签自动回到命令面板；
* 只读门禁修正：会写存档的按钮才灰，并且 tooltip 说明
  「只读模式：先点『解锁编辑』」；创建快照 / 导出这类只读安全的操作保持可用；
* 写入链路修正：**「应用修改」现在真的会算差异、真的先备份**
  （备份目录写进完成提示），**「撤销」之后也不会再假装磁盘是干净的**；
  需要连磁盘一起退回去时用「**撤销并回滚磁盘**」（用这次写入前的备份恢复文件，
  并清空撤销栈）。
* 大存档不卡界面：**区域批量替换**与**跨存档的来源存档读取**都在后台线程跑，
  带进度条与「取消」；运行期间不会重复触发写入。

> 完整的「问题 → 修法 → 验证」清单见 [docs/ui-redesign-2.1.md](docs/ui-redesign-2.1.md)；
> 前后对比截图在 `build/shots-before/`（旧）与 `build/shots/`（新），
> 用 `python build/shot.py` 可重新生成（离屏，不需要显示器）。

### 新增快捷键

| 快捷键 | 作用 |
|---|---|
| `Ctrl+K` | 命令面板：搜命令 / 功能 / 页签 / 动作 |
| `F1` | 快速上手向导 |
| `Shift+F1`（或 `Ctrl+/`） | 快捷键一览（不用 F2：F2 是表格的就地编辑键） |
| `Ctrl+Shift+H` | 回到首页（任务卡 / 上手清单 / 最近） |
| `Ctrl+Shift+F` | 收藏输出命令（`Ctrl+Shift+S` 留给「NBT 另存为」） |

---

## 功能概览

| 模块 | 说明 |
|---|---|
| `/summon` | 实体选择（搜索 + 分类 + 手动输入）、坐标、NBT 树形编辑器 |
| `/give` | 目标预设、物品下拉/浏览（自动补 `minecraft:`）、快捷数量、组件表格 + 分组选择器、附魔专用编辑器、快速处方、补全建议、实时校验 |
| `/execute` | 链式子命令编辑器 |
| 目标选择器 | 完整筛选条件，图形表单 ↔ 文本双向切换 |
| **NBT 文件编辑器** | 独立页签，交互对标 NBTExplorer：三列树、标量就地编辑、按类型值编辑对话框、详情面板（路径 / 类型 / hex dump）、数组编辑器、筛选高亮、多选复制粘贴、拖放改层级、查找替换、撤销重做；打开 `.dat` / `.dat_old` / `.nbt` / `.snbt` / `.schem`，保持原压缩格式与根标签名 |
| **世界存档编辑器** | 打开 `.minecraft/saves/世界名`（含启动器版本隔离目录），自动识别 `level.dat` / `level.dat_old` / `playerdata/` / `stats/` / `advancements/` / `data/` / `region/` / `entities/` / `poi/` / `DIM1/` / `DIM-1/` / `datapacks/`；世界总览 + 11 个编辑页签 |
| **安全事务写入** | 默认只读；解锁后所有写入走「备份 → 内存模型 → 校验 → dry-run 差异 → 确认 → 原子替换 → 可回滚」；备份进 `backups/<时间戳>/` |
| **命令 ↔ 存档双向桥接** | 见下方专节 |
| **蓝图 / 结构模板** | 从存档选区导出 `.nbt` / `.schem`，生成 `/setblock`、`/fill`、`/place` 或数据包函数 |
| **物品组件配方工坊** | 内置处方 + 用户配方，一套组件批量应用到玩家 / 容器 |
| **世界时间线 / 快照** | 打开世界自动快照，`差异` 页签可预览差异、回滚任意备份 |
| **世界健康检查** | 损坏 / 越界区块、缺失数据包、版本不匹配、超大 NBT、UUID 冲突 |
| **中文自然语言世界编辑** | 「把时间设置为夜晚」「在出生点生成 10 只僵尸」「把背包塞满钻石剑」，先 diff 再应用 |
| **模组兼容** | 识别 Forge / NeoForge `fml` / `neoforge` 标签，只读展示、安全跳过 |
| 专有名词翻译 | NBT 类型、注册表分类、其它术语逐条改成中文，三种显示范围 |
| 物品组件引擎 | 独立解析 / 生成 / 校验层，支持 `!组件` 移除、值里含逗号与空格 |
| 全量注册表数据 | 物品 1333 / 方块 1059 / 实体 130 / 附魔 43，中文名取自官方语言文件 |
| 存储层 | SQLite 历史 / 收藏 / 片段 / 会话草稿 + **世界索引缓存**（`worlds` / `world_players` / `world_regions` 三张表） |
| ID 速查面板 | 实体 / 物品 / 方块 / 物品组件四类浏览器 |
| 中文语义化输入 | 工具栏输入中文句子直接得到命令 |
| 批量生成增强 | 一键填充示例、按列类型弹出选择器、整批发送到输出区 |
| 收藏 / 片段联动 | 占位符填写、另存为 `.mcfunction` / `.snbt` |
| 命令行工具 | `mcgen.py` / `mcstudio.py`：生成 / 迁移 / 校验 / 中文解析 / CSV 批量 / **世界查看与编辑** |
| 版本迁移 | 自动识别命令所属版本（1.20.4 → 1.21.1），迁移后弹差异对照 |

---


## 安装

### 1. 安装 Python

需要 Python 3.10 或更高版本。

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

或使用 `pyproject.toml`：

```bash
pip install -e .
```

---

## 运行

### 通用

```bash
python main.py
```

`mcstudio.py` / `mcgen.py` 是**命令行**入口（不开 GUI）；GUI 入口始终是 `main.py`。

### Windows

```bat
run.bat
```

### macOS / Linux

```bash
./run.sh
```

---

## 世界存档编辑（重点）

### 打开

- 编辑器面板「打开世界存档…」或菜单 `文件 → 打开世界存档…`（`Ctrl+Shift+K`）；
- 支持 `.minecraft/saves/世界名`、启动器版本隔离目录
  （`.minecraft/versions/<版本>/saves/世界名`）、以及任意直接指向存档的目录；
- 大存档在**后台线程**读取，进度条可以取消（读了半截不会污染界面）；
- 「最近打开 ▾」用 SQLite 缓存的摘要列出最近的世界，会用
  `level.dat` 的 mtime + size 判断缓存是否过期。

### 世界总览

世界名、DataVersion（+ 识别出的版本）、存档版本号、种子、难度（含锁定）、
允许命令、极限模式、默认游戏模式、时间（DayTime / Time / 时段）、天气
（雨 / 雷剩余刻数）、出生点、世界边界（中心 / 边长 / 伤害）、上次游玩、
数据包列表、玩家列表、模组标签、游戏规则条数、目录体积与文件数。

### 可编辑内容

| 类别 | 能改什么 | 入口 |
|---|---|---|
| 世界设置 | 存档名、时间、天气、难度（含锁定）、允许命令、出生点、世界边界 | `世界总览` 页签 |
| 游戏规则 | 任意 gamerule（常用项有中文说明） | `世界总览` → 游戏规则表，双击取值 |
| 玩家数据 | 生命、饥饿、饱和度、经验、等级、位置、维度、游戏模式、效果、属性、进度、统计 | `玩家` 页签，双击「取值」列 |
| 背包 / 末影箱 | 槽位、物品、数量、组件；写入支持首个空槽 / 合并堆叠 / 指定槽 / 覆盖 | `玩家` 页签 + 「塞满物品…」 |
| 容器与方块实体 | 箱子、桶、潜影盒、熔炉等（`block_entities`） | `容器` 页签 |
| 实体 | 读取、编辑、复制、生成（`/summon`），可选扫描区域文件里的实体 | `实体` 页签 |
| 地图物品 | `data/map_*.dat` 的比例、锁定、追踪、中心、旗帜 / 画框数量、颜色字节 | `地图` 页签 |
| 区域文件 | **只读索引**（区块坐标 / 偏移 / 扇区 / 时间戳），可导出 `forceload` 函数 | `区域` 页签 |
| NBT 树 | 完整 NBT 树浏览、按路径定位、复制为 SNBT / `/data` 命令 | `NBT 树` 页签 |
| 数据包 | 列出已启用 / 缺失的数据包，导出世界规则为函数 | `数据包` 页签 |

> 区域文件的**写入**属于实验功能：本工具只做只读索引，需要改区域内容时
> 优先导出 `.mcfunction`（`/setblock`、`/fill`、`forceload`）让游戏执行。

### 安全与兼容（重要）

1. **打开默认只读**：除「解锁编辑」外所有写入入口都是禁用的；解锁会有
   二次确认，并显示 DataVersion 兼容性结论；
2. 所有写入都进事务：
   `备份 → 修改内存模型 → 校验 → dry-run 差异 → 确认 → 原子写入 → 可回滚`；
3. `level.dat` 写回**保留原压缩方式与根标签名**（不会把未压缩的 `level.dat`
   压成 gzip 让游戏读不出来）；
4. 备份落在 `backups/<时间戳>/`，带 `manifest.json`，`差异` 页签可以直接回滚；
5. **不会直接覆盖用户存档**：原子替换用「临时文件 + `os.replace`」，
   避免半写坏档；
6. DataVersion 检查：高于支持窗口（>3955）或早于 1.20.1 时只提供只读浏览；
7. 模组兼容：Forge / NeoForge 的 `fml` / `neoforge` 段只读展示，写入时安全跳过。

### 一键世界场景

`世界总览 → 一键世界场景…`：永久白天（DayTime=6000 + 关昼夜循环）、
禁止下雨 / 天气变化、难度设为和平、出生点、世界边界、清空天气计时，
一次勾选、一次备份、一次写入。

### 工作区预设（一键规则组合）

`世界总览 → 工作区预设…`，内置四套常用组合：

| 预设 | 内容 |
|---|---|
| 创造沙盒 | 永久白天 + 禁止天气 + 和平 + 不刷怪 + 保留物品 |
| 硬核生存 | 困难 + 昼夜/天气循环 + 死亡掉落 + 关闭自然回血 |
| 建筑展示 | 永远白天 + 晴天 + 不刷怪 + 允许命令 + 关闭火焰蔓延与生物破坏 |
| PvP 竞技场 | 困难 + 边界 500 + 保留物品 + 关闭自然回血与昼夜循环 |

也可以「把当前世界设置存为预设…」，存进 `user_data/presets/`；
CLI 用 `world presets --use / --save`。套用同样走「预览差异 → 备份 → 写入」。

### 排版与可读性

界面排版不再是「每个面板各写一套数字」，而是统一到
`app/ui/theme.py` 的**设计 token**：

- `SPACING`（2/4/6/8/12/16）、`MARGINS`（page / panel / section / compact）、
  `SIZES`（行高 24、输入框 26、列表宽 232…）、`FONTS`（标题 13 / 正文 12 /
  说明 11 / 等宽 9）；
- `COLORS_DARK` / `COLORS_LIGHT` 两套语义色（`muted` / `faint` / `ok` /
  `warn` / `error` / `danger_bg`…），浅色主题下不会再出现「灰字看不清」；
- `apply_spacing()` / `polish_tables()` / `polish_form()` 三个助手负责统一
  边距、行高、表头与表单对齐。

世界编辑器的 13 个页签全部由 `SectionBox`（标题 + 说明 + 右上角动作）
＋ `FilterBar`（过滤框 + 命中计数 + 导出 CSV）＋ `make_table()`（统一行高、
交替行、无网格线、末列拉伸）组装，所以：

- 每个页签都有 **`Ctrl+F` 过滤**（12 个页签覆盖表格与玩家列表，
  玩家过滤还能按 UUID / 路径 / 维度搜）；
- 每个表格都能**导出 CSV**（只导出当前可见行，UTF-8 BOM，
  Excel 直接打开不乱码），工具条右侧还有「导出当前表」一键导出当前页签；
- 顶部多了一条**状态条**：绿色/黄色/红色圆点 + 世界名 + DataVersion /
  玩家数 / 区域文件数 / 地图数 + 「只读（未解锁）/ 可编辑 · 有未应用的修改」；
- 工具条拆成两行（文件与状态 / 编辑动作），窄窗口下不再挤成一团；
- 差异、备份、健康检查、区域、配方这些「多表格页签」用
  `QSplitter` 上下分区，比例可拖动。

> 有回归测试盯着这件事：`tests/test_world_gui.py::TestTypography` 会检查
> 每个页签的边距 / 间距是否等于 token、所有表格行高与表头是否一致、
> 12 个页签是否有过滤条，并且**源码里不许再出现写死的 `#8B949E`**。

### 区域文件写入（实验功能）

`区域` 页签的「写入区块…（实验）」与 CLI 的 `world region` 都遵循同一套
保守策略（`app/core/region_io.py`）：

1. **只支持等长或更短的原地替换**：新区块占用的扇区数超过原位置就**拒绝**，
   绝不移动其它区块、绝不动位置表；
2. 先 `dry-run` 给出可行性结论与字段级差异；
3. 写前把整个 `.mca` 复制进 `backups/<时间戳>/region/`；
4. 用「临时文件 + `os.replace`」原子替换；
5. 写后 `verify_region()` 自检（位置表 vs 数据块长度是否自洽），
   自检不通过会直接告诉你。

### 区域多区块批量替换

`区域` 页签的「批量替换全部区块…」与 CLI 的 `region-batch` 用来做
「一次改完所有区块」的事，比如统一 `DataVersion`、清掉某个旧字段：

| 操作 | 说明 |
|---|---|
| 设置字段 | 按路径写固定取值（不存在则创建，SNBT 类型自动识别） |
| 删除字段 | 删掉某个路径（数据变小，更容易通过空间检查） |
| 文本替换 | 在字符串字段里替换子串 |
| 统一 DataVersion | 把 `DataVersion` 改成目标版本（升级存档结构） |

流程刻意分两步：**① 全量 dry-run → ② 确认执行**。dry-run 会逐个区块给出
「可写 / 跳过」以及跳过的**具体原因**（读不出、没变化、扇区不够…）；
执行时**每个区域文件只备份一次**，逐区块原子替换 + 自检，最后给完整报告。
批量操作是幂等的：写完之后再跑一次 dry-run，会显示「可写 0、跳过 N，
原因：没有变化」。

### 可视化编排（坐标 / 区块选择器 + 方块调色板）

`可视化编排` 页签是一个真正能「画」的画布（`app/ui/world_creative.py`）：

- **俯视图**：按 16×16 画区块网格，鼠标中键拖动平移、滚轮缩放（0.25×~8×）、
  「适应视野」按内容自动取景；左下角实时显示 Y 层 / 缩放 / 已编排格数 /
  标记数 / 悬停坐标与区块号；
- **标记**：把存档里的出生点（×）、玩家（★）、实体（●）、容器（■）、
  区块中心（◆）画上去，「刷新标记」重新采一遍；
- **五种工具**：选择（框选区块范围）、画方块、擦除、同层填充（连通区域）、
  吸管（取格子上的方块当画笔）；
- **多层**：Y 层可调，其它层的方块用半透明提示，不会「看不见东西」；
- **独立撤销栈**：编排的撤销 / 重做与存档事务互不干扰，最多 100 步；
- **四种导出**：
  - `/setblock`（逐格，最精确）；
  - `/fill`（按 Y 层做**矩形贪心分解**，尽量少几条命令）；
  - 蓝图对象（走 `.nbt` / `.schem` + `/place`）；
  - `/data modify block …`（把本层编排打包成 NBT）；
- **直接写进区域区块**：框选后点「把选区写进区域区块…（实验）」，
  会把你画的方块合并进区块的 `sections`（按 1.18+ 的
  `palette` + 位数组结构），然后走与区域写入完全相同的安全链路
  （逐区块 dry-run → 备份 → 原子替换 → 自检），不可写的区块会列出原因。

### 数据包工程（图形化编辑器）

`数据包工程` 页签把数据包当成可反复编辑的工程（`app/core/datapack_project.py`）：

- 左：资源树（函数 / 进度 / 战利品表 / 谓词 / 函数标签）+ 新建 / 删除 /
  右键菜单（复制命名空间 ID、查看标签）；
- 中：编辑器——函数写命令（一行一条，`/` 前缀会自动去掉），
  JSON 资源带语法检查 + 「插入模板」（主 / 加载 / 每刻 / 商店 / 套装 +
  进度 / 战利品表 / 谓词模板）；
- 右：概览（各类型数量、总命令数、布局）+ 自检报告
  （命名空间、JSON 语法、标签里有没有引用不存在的函数）+ 命令预览；
- 「挂到 load / tick」一键把选中的函数写进 `minecraft:load` / `tick` 标签；
- 「← 输出区 / → 输出区」在命令面板和数据包之间双向搬运；
- 落盘走原子替换（不会留 `.tmp`），工程目录默认在
  `user_data/datapacks/<命名空间>/`，也可以「另存到…」直接写进存档的
  `datapacks/`，游戏立刻能读到；
- **布局兼容**：1.21+ 用 `function` / `advancement` / `loot_table` /
  `tags/function`，1.20- 用复数形式；读取时两种都认（看到复数目录就按复数
  布局处理），写入时按布局决定，切换布局会顺手清掉旧目录里的同名文件。

---

## 命令 ↔ 存档双向桥接

### 命令生成器 → 编辑器

| 命令 | 落到哪里 |
|---|---|
| `/give @p minecraft:stone[...] 64` | 玩家背包 / 末影箱 / 指定容器 / 指定槽位（首个空槽、合并堆叠、覆盖、指定槽） |
| `/summon minecraft:zombie …` | 地图实体数据（`level.dat` 的 `Entities`），或导出数据包函数 |
| `/setblock`、`/fill` | 方块与区域编辑（`fill` 默认只生成命令，不写存档） |
| `/data modify … set value …` | 指定 NBT 路径（不存在时自动创建中间层） |
| `/effect`、`/gamemode`、`/xp` | 玩家数据 |
| `/time`、`/weather`、`/worldborder`、`/gamerule` | `level.dat` |

用法：把命令贴进输出区 → 点「→ 存档」或 `Ctrl+Shift+B` → 联动总线里
「预览差异」→「应用到世界（备份后）」。

### 编辑器 → 命令生成器

| 来源 | 生成 |
|---|---|
| 玩家背包物品 | `/give` 面板（保留数量、槽位、组件、附魔），或直接复制 `/give` |
| 实体 NBT | `/summon` 面板（保留 id、Pos、NBT）或数据包函数 |
| 方块实体 / 容器 | `/setblock`（含容器 NBT）、`/data` |
| NBT 路径 | `/data modify … set value …` |
| 玩家 | 目标选择器、`/execute as … at @s run …` |
| 坐标 | `execute positioned …`、`summon …` |
| 世界规则 | `/time`、`/weather`、`/gamerule`、`/difficulty`、`/worldborder` |

两个方向都有**转换预览**、**差异对照**与**撤销入口**，并且记录在桥接日志里。

---

## 创新功能

1. **世界时间线 / 快照**：打开世界自动快照（可在 `视图 → 工作区` 关闭），
   `差异` 页签支持 diff 与回滚到任意备份；
2. **命令-存档双向拖拽 / 联动**：物品卡片 → `/give`，实体卡片 → `/summon`，
   NBT 路径 → `/data`，坐标 → `positioned`；
3. **可视化坐标与区块选择器**：地图页签显示中心与比例，
   区域页签列出区块网格并可导出 `forceload`；
4. **蓝图 / 结构模板**：从存档选区导出 `.nbt`（原版结构规范）与 `.schem`
   （Sponge v2），生成 `/setblock`、`/fill`、`/place` 与数据包函数；
5. **一键世界场景**：永久白天 / 禁止下雨 / 和平 / 出生点 / 边界 / 清天气；
6. **数据包生成器**：把面板操作导出为函数（`功能 → 面板操作 → 函数…`）；
7. **中文自然语言世界编辑**：例如「把 Steve 背包塞满钻石剑」
   「在出生点生成 10 只僵尸」，**先 diff 再应用**；
8. **安全审阅模式**：所有写入先 dry-run 出差异，确认后执行；
9. **跨存档复制**：物品、实体、玩家数据、进度、地图、容器（用
   `导出 → 另一个存档导入` 的方式：物品走 `/give` 命令，NBT 走蓝图 / SNBT）；
10. **世界健康检查**：损坏 / 越界区块、缺失数据包、版本不匹配、超大 NBT、
    UUID 冲突、区域文件缺失；
11. **物品 / NBT 配方工坊**：内置 10 个处方 + 用户配方，可批量应用到玩家 / 容器；
12. **模组兼容**：识别 Forge / NeoForge 的 `fml` / `neoforge` 标签，只读展示。

---

## 安装

### 1. 安装 Python

需要 Python 3.10 或更高版本。

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

或使用 `pyproject.toml`：

```bash
pip install -e .
```

---

## 命令行工具（无需 GUI）

```bash
python mcstudio.py list                       # 列出全部命令（mcgen.py 等价）
python mcgen.py list --category 世界 --json   # 按分类，附 JSON
python mcgen.py gen setblock pos="~ ~ ~" block=minecraft:stone
python mcgen.py gen time action=add amount=1000 --validate
python mcgen.py nlp "给所有玩家10个钻石"       # 中文 → 命令
python mcgen.py id 僵尸 钻石剑 minecraft:stone # 中文名 → 命名空间 ID
python mcgen.py migrate "give @p diamond_sword 1 {display:{Name:\"Excalibur\"}}" -v
python mcgen.py validate "tp @p 1 2 3"         # 用校验规则体检
python mcgen.py batch rows.csv -o out.mcfunction
python mcgen.py --version                      # MCStudio 2.0.0
python mcgen.py rename-info                    # 改名说明与迁移方式
```

### 世界存档子命令

```bash
# 总览（--json 便于脚本消费）
python mcstudio.py world info "…/.minecraft/saves/世界"

# 按字段编辑 level.dat：默认 dry-run，加 --apply 才写盘
# （写入走 WorldSession 事务：备份 → 校验 → 原子写入 → 记撤销点）
python mcstudio.py world edit "…/saves/世界" DayTime=1000
python mcstudio.py world edit "…/saves/世界" GameRules.keepInventory=true --apply
python mcstudio.py world edit "…/saves/世界" SpawnX=0 SpawnY=64 SpawnZ=0 --apply --health

# 健康检查（有 error 时返回码 1）
python mcstudio.py world health "…/saves/世界" --json

# 备份 / 世界时间线
python mcstudio.py world backups "…/saves/世界"
python mcstudio.py world backups "…/saves/世界" --restore 20250101-120000
python mcstudio.py world snapshot "…/saves/世界" --label "开新档前"

# 存档 → 命令
python mcstudio.py world commands "…/saves/世界" --what items -o items.mcfunction
python mcstudio.py world commands "…/saves/世界" --what entities --limit 200
python mcstudio.py world commands "…/saves/世界" --what rules,scene

# 中文自然语言（默认 dry-run）
python mcstudio.py world nlp "…/saves/世界" "把时间设置为夜晚"
python mcstudio.py world nlp "…/saves/世界" "在出生点生成 5 只僵尸" --apply

# 工作区预设：列出 / 套用 / 保存
python mcstudio.py world presets
python mcstudio.py world presets "…/saves/世界" --use creative_sandbox
python mcstudio.py world presets "…/saves/世界" --use build_showcase --apply
python mcstudio.py world presets "…/saves/世界" --save 我的展示档

# 区域文件（实验功能）：列索引 / 看区块 / dry-run / 写入
python mcstudio.py world region "…/saves/世界/region/r.0.0.mca"
python mcstudio.py world region "…/saves/世界/region/r.0.0.mca" --x 0 --z 0 --show
python mcstudio.py world region "…/saves/世界/region/r.0.0.mca" --x 0 --z 0 \
    --snbt '{DataVersion:3955,xPos:0,zPos:0,InhabitedTime:100L,Entities:[]}'
python mcstudio.py world region "…/saves/世界/region/r.0.0.mca" --x 0 --z 0 \
    --snbt @chunk.snbt --apply      # 写前自动备份 + 写后自检
```

### 编排 / 区域批量 / 数据包子命令

```bash
# 命令行版「画布」：矩形 + 散点 → 命令或 SNBT
python mcstudio.py paint --rect 0,0,3,3 --y 64 --block minecraft:stone
python mcstudio.py paint --rect 0,0,3,3 --point 9,9 --mode fill   # 自动矩形分解
python mcstudio.py paint --point 0,0 --point 1,0 --mode snbt -o paint.snbt

# 区域多区块批量替换（默认 dry-run，--apply 才写）
python mcstudio.py region-batch "…/saves/世界" --op set --field LightPopulated --value 0b
python mcstudio.py region-batch "…/saves/世界" --op set --field LightPopulated --value 0b --apply
python mcstudio.py region-batch "…/saves/世界" --op bump_data_version --data-version 3955 --apply
python mcstudio.py region-batch "…/saves/世界" --op replace_text \
    --field Status --search minecraft:full --replace minecraft:empty

# 数据包工程
python mcstudio.py pack --namespace demo --list
python mcstudio.py pack --namespace demo --function "say hello" \
    --function "gamerule keepInventory true" --name main --tag-load main --save
python mcstudio.py pack --namespace demo --advancement first_join --save
python mcstudio.py pack --namespace demo --check        # 自检（有问题返回 1）
python mcstudio.py pack --import-from "…/.minecraft/saves/世界/datapacks/example" --list
```


### `/give` 专用子命令

```bash
# 生成：物品 ID 自动补 minecraft:，组件可重复
python mcgen.py give --target @a --item diamond_sword --count 3 \
    -c 'minecraft:custom_name="神剑"' \
    -c 'minecraft:enchantments={"levels":{"minecraft:sharpness":5}}'

# 在已有命令上改：加组件 / 移除组件（生成 !组件）
python mcgen.py give --from "give @p minecraft:stone[damage=5,unbreakable=1b]" \
    -r minecraft:damage -c "minecraft:max_stack_size=99"

# 套用内置「物品处方」
python mcgen.py give --preset sword.sharp5 --explain
python mcgen.py give-presets --list-presets     # 查看全部处方
python mcgen.py give-presets --search 附魔       # 搜索物品组件 ID

# 组件值很长时可以从文件读（@文件）
python mcgen.py give --item stone -c minecraft:lore=@lore.snbt --validate
```

`--validate` 在出现 error 级问题时返回码为 1，方便接进脚本；`--explain`
会把所有提示（含 warn/info）打印到 stderr。

---

## 目录结构

```
mc-cmd-gen/                   （仓库目录名保留，改名不影响现有克隆）
├─ main.py                 # GUI 入口
├─ mcstudio.py             # 命令行入口（新名，等价于 mcgen.py）
├─ mcgen.py                # 命令行入口（旧名，保留兼容）
├─ requirements.txt
├─ pyproject.toml
├─ README.md
├─ run.bat / run.sh
├─ app/
│  ├─ branding.py          # 品牌常量 / 窗口标题 / 关于页 / 改名说明
│  ├─ migration.py         # MCCmdGen → mcstudio 数据目录迁移（只复制、幂等）
│  ├─ core/
│  │  ├─ base.py           # BuildContext / ValidationIssue / CommandBuilder / 注册表
│  │  ├─ schema.py         # ArgSpec / SchemaBuilder / register_schema / load_schemas
│  │  ├─ rules.py          # 正则校验规则（可被用户 JSON 覆盖）
│  │  ├─ ids.py            # 中文/别名 → 命名空间 ID 解析
│  │  ├─ item_components.py # 物品组件引擎：解析 / 生成 / 校验 / 处方分组
│  │  ├─ builders/         # 手写 Builder（summon / give / execute）+ rules 兼容层
│  │  ├─ nbt_binary/       # 二进制 NBT 读写 + 文档模型（格式/根名保持）
│  │  ├─ nbt_bridge.py     # 存档 NBT ↔ 命令：物品 / 实体识别与转换
│  │  ├─ world.py          # ★ 世界存档：level.dat / playerdata / 容器 / 实体 /
│  │  │                    #   地图 / 区域只读索引 / 健康检查 / 备份
│  │  ├─ world_session.py  # ★ 只读门禁 + 事务（备份→校验→diff→原子写入）+ 撤销
│  │  ├─ bridge.py         # ★ 命令 ↔ 存档双向桥接 + 结构化操作 + 中文自然语言
│  │  ├─ blueprint.py      # ★ 蓝图 / 结构模板 + 物品组件配方工坊
│  │  ├─ region_io.py      # ★ 区域写入（等长替换 + 原子替换 + 自检）+ 工作区预设
│  │  ├─ datapack_project.py # ★ 数据包工程（命名空间/资源/标签 + 布局兼容）
│  │  ├─ settings.py       # 应用设置与「专有名词翻译」
│  │  ├─ nlp/              # 中文句式引擎 + patterns.json
│  │  ├─ snbt.py           # SNBT 数据模型 / 解析器 / 序列化器 / 语法高亮
│  │  ├─ datapack.py       # 数据包导出 / 占位符 / 版本迁移
│  │  ├─ workbench.py      # 命令编排脚本 + 目标选择器模型
│  │  ├─ data.py           # JSON 加载 / 联网更新 / 推荐引擎
│  │  └─ scenes.py         # 场景存取
│  ├─ data/builtin/        # 内置数据：entities(130) / items(1333) / blocks(1059)
│  │                       #           item_components / item_presets
│  │                       #           enchantments / recommendations
│  │                       #           templates / command_schemas
│  ├─ storage/             # SQLite（历史 / 预设 / 片段 / 草稿）
│  │  └─ world_cache.py    # ★ 世界索引缓存（worlds / world_players / world_regions）
│  └─ ui/
│     ├─ main_window.py    # 主窗口 + 工作区模式 + 布局记忆与折叠
│     ├─ world_panel.py    # ★ 世界与 NBT 编辑器面板（15 个页签 + 工具条 / 状态条）
│     ├─ world_common.py   # ★ 排版构件：SectionBox / FilterBar / 表格与 CSV
│     ├─ world_creative.py # ★ 可视化编排：坐标/区块画布 + 方块调色板
│     ├─ world_datapack.py # ★ 数据包工程页签（资源树 / 编辑器 / 自检）
│     ├─ world_region_batch.py # ★ 区域多区块批量替换（dry-run → 执行）
│     ├─ world_dialogs.py  # ★ 场景 / 写入物品 / 蓝图 / 跨存档 / 预设 / 区域写入
│     ├─ world_tabs_world.py # ★ 总览与玩家页签
│     ├─ world_tabs_data.py  # ★ 实体/容器/NBT/地图/区域/数据包/蓝图/差异/健康/中文/NLP/跨存档
│     ├─ bridge_drawer.py  # ★ 联动总线抽屉（上下文 / 待执行队列 / 桥接日志）
│     ├─ world_loader.py   # ★ 世界异步加载（QThreadPool + QRunnable）
│     ├─ panels.py         # 命令面板 / 批量 / 数据包 / 历史 / 收藏 / 片段 / 工作台 / ID 速查
│     ├─ dialogs/          # 保存 / 迁移 / 导入 / 差异 / 快捷键 / 设置 / 存档物品一览
│     ├─ widgets/          # NBT 编辑器（nbt_tree/nbt_tags/nbt_dialogs）
│     │                    #   + 通用控件 + 各类选择器 + 输出区
│     ├─ graph/            # execute 节点图
│     ├─ theme.py / mime.py
├─ tools/
│  ├─ fetch_registry.py    # 下载官方注册表与语言文件到 build/mcdata/
│  ├─ build_registry.py    # 生成内置 items / blocks / entities / enchantments
│  ├─ inspect_nbt.py       # 命令行查看一个 NBT 文件里到底有哪些标签
│  ├─ fake_world.py        # ★ 生成假世界存档（测试与冒烟用，不依赖游戏）
│  ├─ smoke_gui.py         # 离屏 GUI 冒烟测试（命令 + NBT + 世界 + 桥接 + 蓝图）
│  ├─ fix_data.py          # 内置数据语法修正脚本
│  └─ fix_schemas.py       # Schema 可选参数修正脚本
└─ tests/                  # 单元测试（828 项）
```

---

## 数据存放位置

应用数据目录（自动创建）：

- Windows：`%LOCALAPPDATA%\mcstudio\`
- Linux：`~/.local/share/mcstudio/`
- macOS：`~/Library/Application Support/mcstudio/`

> 旧目录 `MCCmdGen/` 会在首次启动时**自动迁移**到新目录（只复制，旧目录保留）。
> 关掉自动迁移：`MCSTUDIO_NO_MIGRATE=1`；
> 手动指定目录：`MCSTUDIO_DATA_DIR`（兼容 `MCCMDGEN_DATA_DIR`）。

其中：

- `data.db` — SQLite 数据库（历史 / 预设 / 片段 / 草稿 / 窗口布局
  / 工作区模式 / **世界索引缓存**）
- `user_data/` — 用户导入 / 联网更新的 JSON、`settings.json`（设置与专有名词翻译）、
  `validation_rules.json`、`blueprints/`（蓝图）、`recipes/`（物品配方）
- `scenes/` — 场景
- `update_source.txt` — 联网更新源 URL（可选）
- `migration.json` — 迁移记录（如果发生过迁移）

> 世界的备份不在这里，而是落在**存档自己的** `backups/<时间戳>/` 下，
> 这样换工具、换机器都能直接回滚。

---

## 内置数据与重建

`app/data/builtin/` 里的注册表数据来自官方数据，可以用两个脚本重建：

```bash
python tools/fetch_registry.py    # 下载到 build/mcdata/（需要联网）
python tools/build_registry.py    # 生成 items / blocks / entities / enchantments
```

- 数据源：`PrismarineJS/minecraft-data`（1.21.1 注册表）+ `Misode/mcmeta`
  的官方 `zh_cn.json` 语言文件（中文名）。
- 重建时会**按 ID 合并保留原有的中文名、category、tags**，所以人工润色不会被冲掉。
- 当前规模：物品 1333（含可放置的方块物品）、方块 1059、实体 130、附魔 43。
- 想加自己的条目，直接在 `user_data/` 放同名 JSON 即可覆盖 / 追加，
  不需要改内置文件。

---

## 联网更新

在应用数据目录创建 `update_source.txt`，写入一行基础 URL，例如：

```
https://example.com/mcdata
```

程序会依次拉取：

```
https://example.com/mcdata/entities.json
https://example.com/mcdata/items.json
https://example.com/mcdata/item_components.json
https://example.com/mcdata/item_presets.json
https://example.com/mcdata/enchantments.json
```

文件需符合内置 JSON 的结构（缺失的文件会自动跳过）。下载成功后会写入
`user_data/`，并自动刷新选择器、ID 速查与 `/give` 面板的组件表。

> 任一内置数据（`item_presets.json` / `enchantments.json` 等）都可以直接在
> `user_data/` 放同名文件来覆盖或扩充，格式与内置文件一致即可。

---

## 自定义校验规则

在应用数据目录的 `user_data/validation_rules.json` 写一个数组即可覆盖内置规则：

```json
[
  {"id": "no_tp", "pattern": "^/?tp\\b", "level": "error", "message": "本项目禁用 /tp"}
]
```

`level` 取 `error` / `warn` / `info`，会分别显示在「校验信息」页签与面板状态徽章上。

---

## 使用要点

### 界面布局

- 顶部是**四段式导航条**：品牌 + 首页 ｜ 模式切换 ｜ 查找命令 + 中文输入 ｜
  常用动作 + 更多 ▾。旧的工具条动作全部收在「更多 ▾」里（一个都没删）。
- 首屏是**首页**（任务卡 + 3 步上手清单 + 最近命令 / 最近存档）；
  `Ctrl+Shift+H` 随时回来。想看命令面板点中间栏的「命令面板」页签即可。
- 底部常驻**上下文引导条**：步骤胶囊 + 一句提示 + 此刻该点的按钮。
- 三栏（命令列表 / 编辑区 / 输出区）都是可拖动的分隔条，拖完会自动记住，
  下次打开保持一样；底部「历史 / 收藏 / 片段 / 校验」面板高度也会记住。
- 三处折叠按钮：命令列表标题右侧、输出区标题右侧、底部面板右上角；
  快捷键 `Ctrl+Shift+O` 折叠输出区。左侧与输出区折叠后展开会回到原来的宽度。
- 命令树上方有**筛选框**（命令名 / 中文名 / 分类都能匹配），
  窄栏里名称被截断时把鼠标停上去会显示完整名称。
- 「视图 → 布局」提供 专注编辑 / 对照输出 / 三栏并列 三种预设，
  以及「恢复默认布局并记住」。
- 主题在 深色 / 浅色 / 跟随系统 之间切换，会写进会话草稿；
  深色主题下表格表头、微调按钮、滚动条都做了统一处理，不会出现突兀的白色块。

### 用 `/give` 造一件神装（重点功能）

左侧命令树选 `/give`（或按 `Ctrl+G` 直接跳过去）：

1. **目标**：直接输入 `@a`，或用下拉「常用…」、按钮「编辑…」打开完整目标选择器。
2. **物品**：下拉里可以按 ID 搜索，也可以直接手打 `diamond_sword`
   —— 生成时会自动补成 `minecraft:diamond_sword`。点「浏览…」可用中文名挑。
3. **数量**：勾「指定」后填数字，或从「快捷数量…」里选 1/8/16/32/64/100。
   不勾选时命令里不写数量（等于 1 个）；数量超过堆叠上限、物品不可堆叠都会有提示。
4. **组件**：
   - 「添加组件…」按分组 + 中文搜索多选，一次加好几个；
   - 表格双击行即可编辑值：布尔/枚举是下拉框，附魔是「附魔 + 等级」列表，
     其余走多行 SNBT 编辑器（实时校验并给出错误定位）；
   - 右键行可切换「设置 / 移除」（移除会生成 `!minecraft:damage`）、复制一份、
     上移下移、复制组件文本；
   - 右侧「补全建议」会随物品变化列出常用组件，双击即可加入；
     「套用建议」一次补齐，按钮「批量填充…」把当前内容带到批量生成页签。
5. **快速处方**：一键套用「锋利 V 神剑」「无法破坏的效率镐」等整套组件，
   换物品时如果已有组件会先确认，避免误覆盖。

顶部提示行会实时列出问题（物品缺命名空间、组件用错物品、SNBT 语法错误、
组件重复……），没有问题时显示「没有发现问题，可以直接复制使用」。

> 1.21 起物品 NBT（`{display:{Name:…}}`）已不可用。如果粘贴了旧写法，
> 解析时会直接提示改用物品组件，也可以先用「数据 → 迁移命令…」自动转换。

### 编辑 .dat / .nbt 等 NBT 文件

「NBT 编辑器」页签是完整的 NBT 文件编辑器，交互参照 **NBTExplorer**。

**文件**（`Ctrl+O` 打开、`Ctrl+S` 保存、`Ctrl+Shift+S` 另存为、`F5` 重新载入）

- 支持 `.dat`（level.dat / playerdata 等）、`.dat_old`、`.nbt`、`.snbt`，
  以及 `.schem` / `.schematic`；也可以把文件**直接拖进窗口**。
- 工具条上有「最近打开 ▾」记着最近 12 个文件；「重新载入」可丢掉当前修改
  重新从磁盘读。
- 打开时记录源文件的**压缩方式**（未压缩 / gzip / zlib）与**根标签名**，
  「保存」按原格式写回，不会把 level.dat 压成 gzip 让游戏读不出来；
  也可以用「写出格式」下拉强制换容器。
- 打开 `.snbt` 会自动切到文本模式（文本文件没有压缩格式的概念）。
- 工具条「结构…」（或菜单「文件 → NBT 编辑器 → 文件结构总览…」）打开
  **文件结构总览**：显示压缩格式、字节数、**根标签名**、根类型与直接子标签数，
  并列出整棵结构（可复制成清单）。用来确认「某个标签到底在不在文件里」——
  例如世界存档应该能在根下同时看到 `Data` 与 `fml` 两个子标签。
  命令行同理：`python tools/inspect_nbt.py <文件> --depth 2`。

**树形视图（三列：名称 / 类型 / 取值）**

- 类型列直接写出 `Byte / Short / Int / Long / Float / Double / String /
  List / Compound / ByteArray / IntArray / LongArray`，按类型着色，默认按
  「专有名词翻译」显示中文（`字符串` / `复合标签` / `整型数组`…），
  tooltip 里保留英文原名；名称前还有按类型区分的小图标。
- 取值列显示内容本身（不再要求手写 SNBT），并且**把字段列出来**而不是只写
  `{ 2 项 }` —— 例如 `{ModId: neoforge, ModSize: 0}`、`{Slot: 0b, id:
  "minecraft:stone"}`；优先展示 `id / ModId / type / Name / count` 这些
  一眼能看懂结构的字段，字段太多（超过 8 个）才退回 `{ 130 项 }`。
  查找与筛选仍按「简短取值」匹配，不会把字段名当成取值命中。
- 数组与列表：`[B; 10, 20, 255]（3 项）`、`[ a, b ]`（大列表只预览前几项并标注总数）。
- 打开已知结构时会给出说明：Forge/NeoForge 的模组清单（根标签 `fml`）会提示
  「这不是世界存档，每条只有 ModId 与 ModSize」；世界存档则提示
  `Data.Player.Inventory` 是背包、可以直接送进 `/give`。
- 工具勾选「字节视图」后，数组按十六进制显示（字节为 `0A FF`，整数为 `0x…`）；
  勾选「字节转字符串」可把 ByteArray 按 UTF-8 读出来。两个设置都会被记住。
- **元素按需渲染**：几万个元素的数组不会拖慢界面，展开哪个容器才建哪一层，
  「展开元素」按钮可把某个容器的元素铺开（超过 256 项只渲染前 256 项）。

**打开存档就能看到物品（重点）**

打开 `level.dat` / `playerdata` 后，编辑器会自动识别背包数据：

- 物品所在的标签直接显示成摘要行，例如
  `钻石剑 ×1 · 锋利 V · 耐久 III · 2 个组件 · 槽位 0`；
  物品容器（背包 / 末影箱 / 箱子）默认展开，不用一层层点进去。
- 选中物品后，详情面板变成**物品卡片**：中文名、数量、槽位、分类、堆叠上限、
  完整位置、附魔列表（含中文名与 ID）以及全部物品组件的取值。
- 选中生物（`id` 命中实体注册表）后，详情面板变成**实体卡片**：
  中文名、生命、标签、分类、它身上的装备，以及去掉 `id` 后的完整 NBT。
- 工具条「存档物品一览…」（或右键菜单）打开**图形化物品表**：
  按容器分组或「合并同类物品」（数量相加），每行显示槽位 / 中文名 / 物品 ID /
  数量 / 附魔与组件 / 位置，可多选后批量处理。

**把存档数据变成命令**（物品 → `/give`，生物 → `/summon`）

| 想做的事 | 入口 |
|---|---|
| 物品填进 `/give` 面板继续改 | 物品卡片「→ /give 面板」/ 物品一览里双击或按按钮 / 右键「→ 送进 /give 面板」 |
| 直接复制 `/give` 命令 | 物品卡片「复制 /give 命令」/ 右键「复制物品 /give 命令」 |
| 复制物品 SNBT / `/data` 命令 | 物品卡片上的对应按钮（`/data modify … set value {id:…,count:…}`） |
| 生物填进 `/summon` 面板 | 实体卡片「→ /summon 面板」/ 右键「→ 送进 /summon 面板」 |
| 复制 `/summon` 命令或实体 NBT | 实体卡片按钮 / 右键菜单 |
| 整批物品 → 输出区 | 物品一览「全部 → 输出」（每条生成一条 `/give` 命令） |

送进面板后命令是**可继续编辑**的：目标选择器、数量、组件都能再调，
不会只丢一段文本给输出区。

**编辑操作**

| 操作 | 入口 |
|---|---|
| 编辑取值（就地） | 双击「取值」列 / 回车：整数出微调框、小数出数字框、字符串出输入框，布尔是复选框 |
| 编辑取值（完整） | 详情面板「完整编辑…」：按类型定制的窗口，整数可写 `0x` 十六进制与 `1b/1s/1L` 后缀，字符串是多行编辑器 |
| 改标签名 | 双击「名称」列 / `F2` / `Shift+回车` |
| 新建标签 | `Ctrl+N` / `Insert` / 工具栏「新建」或右键「新建子标签 ▾」，可选 12 种类型 |
| 追加元素 | 选中 List / 数组 → 详情面板「追加元素…」，一次可填多个值（`,` 或空格分隔） |
| 删除 | `Delete` 或右键；**支持多选**（Ctrl / Shift 点选）后一次删除 |
| 复制 / 剪切 / 粘贴 | `Ctrl+C` / `Ctrl+X` / `Ctrl+V`，内部剪贴板保留整棵子树，也能解析系统剪贴板里的 SNBT 文本 |
| 复制一份、上移下移 | 右键或工具栏（`Ctrl+D`、`Alt+↑` / `Alt+↓`） |
| 拖放调整层级 | 直接把节点拖到别的 Compound / 同类型 List 上；**类型不合法会拒绝并说明原因**（例如往 IntArray 里拖字符串） |
| 撤销 / 重做 | `Ctrl+Z` / `Ctrl+Y`（100 步），撤销后会回到原来的选中位置 |
| 复制路径 / SNBT / 命令 | 右键，路径形如 `Data.Player.Inventory[0].id`，也能直接复制成 `/data modify entity @s … set value …` |
| 展开 / 折叠 | `Ctrl+Shift+E` 全部展开（含元素）、`Ctrl+Shift+W` 全部折叠；方向键 `←` `→` 逐层收放 |
| 定位到路径 | 菜单「文件 → NBT 编辑器 → 定位到路径…」，支持 `Data.Player.Inventory[3].id`，目标在折叠容器里会自动展开并滚动过去 |

**详情面板**（`Ctrl+I` 开关，窄窗口里可以关掉给树让位）

- 显示选中标签的**完整路径**、类型徽章、元素类型与项数。
- 数组会同时给出内容预览与**内存中的字节**（hex dump：偏移 + 十六进制 + ASCII）。
- 容器可以直接「新建子标签 / 追加元素 / 展开元素 / 完整编辑 / 删除」。

**筛选**（`Ctrl+F` 聚焦）

- 输入关键字即时过滤整棵树，命中的片段会在树里高亮；
  默认同时匹配**名称 + 取值**（可以只留其中一个），还支持区分大小写与按类型筛选。
- 命中的父节点会自动展开；筛选会深入还没渲染的元素（搜 `minecraft:item399`
  能找到几万项数组里的那一项）。

**数组编辑器**：三种视图切换 —— 列表（十进制，逗号或空格分隔）、
十六进制（字节 `0A`、整数 `0x1F`）、字节转字符串（按 UTF-8 解释），
实时显示经典 hex dump、元素个数与预计占用字节，范围越界会直接禁用「确定」。
写入时统一按有符号折算：ByteArray 里写 `0xFF` 与写 `-1` 是同一颗字节。

**查找 / 替换**（`Ctrl+Shift+F`）：可按名称、取值或两者搜索，支持区分大小写与
「整个标签」精确匹配；替换只作用于 String 标签，「全部替换」会统计命中数量。

**文本模式**：直接写 SNBT，带语法高亮与错误定位，与树形双向同步
（停笔 0.5 秒自动回填，语法错误时保留原模型不破坏数据）。

有未保存修改时标签页标题显示 `*`，关闭标签页或退出程序前会提示保存。

> 主窗口菜单「文件 → NBT 编辑器」里有存档物品一览 / 定位 / 筛选 / 查找 / 复制等入口，
> 快捷键 `Ctrl+Shift+M` 新建文档、`Ctrl+Shift+N` 打开文件。

### 设置与专有名词翻译

「文件 → 设置…」（或 NBT 编辑器工具条上的「设置…」）打开设置对话框：

- **专有名词翻译**：三张表分别对应 **NBT 类型**、**注册表分类**、**其它术语**，
  默认已经翻好一套中文（`String → 字符串`、`Compound → 复合标签`、
  `Float → 单精度浮点`、`ModId → 模组 ID`、`LoadingModList → 已加载模组列表`…）。
  双击「译文」列就能改，留空表示回到内置默认；
  顶部「筛选」框可以按原名或译文快速找到某一条。
- **显示方式**：`全部翻译（NBT 类型 + 分类 + 术语）` /
  `只翻译 NBT 类型` / `全部显示英文原名`，另有总开关。
- **NBT 显示**：是否显示类型图标、是否识别物品并显示中文名摘要。
- **数据目录**：应用数据目录、用户数据目录、设置文件的位置。

设置保存在用户数据目录的 `settings.json`，改动**立刻生效**（树形视图会即时刷新），
并且只影响显示 —— 生成的命令里永远是英文 ID。

### 中文直接写命令

工具栏右侧输入框（或 `mcgen.py nlp`）支持这类句子：

| 输入 | 输出 |
|---|---|
| 给所有玩家10个钻石 | `give @a minecraft:diamond 10` |
| 给Steve添加夜视效果60秒 | `effect give Steve minecraft:night_vision 60` |
| 在10 64 10生成一只僵尸 | `summon minecraft:zombie 10 64 10` |
| 用石头填充0 0 0到5 5 5 | `fill 0 0 0 5 5 5 minecraft:stone` |
| 把Steve设置成创造模式 | `gamemode creative Steve` |
| 给所有人三个钻石 | `give @a minecraft:diamond 3` |

识别失败时会弹出提示并列出可用句式；中文数字（三个 / 十秒 / 五十）与中文 ID（僵尸 / 钻石剑 / 锋利）都会自动转换。

### 批量生成

「批量生成」页签：选命令 → 点「填充示例」得到一行可用样例 → 按需增删行。
- 单元格会按列类型给出辅助：「选实体… / 选物品… / 选方块… / 选目标… / 选取值…」
- 「历史取值…」下拉可复用历史记录里用过的参数值
- 「全部 → 输出」把整批命令送到输出区，也可导出 CSV / `.mcfunction`
- 切换命令时已填内容会按命令分别保留

### 收藏与片段

- 收藏右键：`填写占位符后复制…`（命令里含 `{占位符}` 时出现）、`另存为片段…`
- 片段右键：`另存为文件…`（命令 → `.mcfunction`，NBT → `.snbt`）、`加入收藏`

### ID 速查

「ID 速查」页签可搜中文名（僵尸 / 钻石剑 / 照明），双击条目或点「示例命令 → 输出」直接生成命令。

---

## 测试

```bash
python -m unittest discover -s tests -t . -v
```

共 30 个测试文件、**828 项**。除了原有的导入健壮性、Builder/Schema 往返、
全命令端到端一致性、`/give` 组件深度测试、注册表数据完整性、NBT 编辑器、
存档数据图形化、设置与专有名词翻译、存储层、离屏 GUI 之外，本次新增：

- **世界存档核心层**（`tests/test_world.py`，98 项）：字段路径解析与类型安全赋值
  （含 Byte/Short 范围、容器整体替换）、SNBT 的 `0x` 十六进制写法（含
  `0xFFb` / `0x1000L` 这类「后缀还是十六进制位」的歧义）、世界总览全部字段、
  容器识别、地图（原版 `{data:{…}}` 要下钻一层）、区域文件位置表与区块懒加载、
  区域实体扫描、健康检查（越界区块 / 缺失数据包 / 版本不匹配 / 超大 NBT /
  UUID 冲突）、写回后压缩格式与根标签名保持不变、备份与回滚、世界索引缓存；
- **会话与桥接**（`tests/test_bridge.py`，80 项）：只读门禁、事务的
  preview / commit / 异常回滚 / 无变化跳过、撤销与重做（含玩家游戏模式）、
  撤销栈上限、日志、命令解析（give / summon / setblock / fill / data /
  effect / gamemode / xp / time / weather / worldborder / gamerule）、
  写入落点、编辑器 → 命令、中文自然语言世界编辑与「先 diff 再应用」、
  结构化载荷 JSON 往返；
- **双面板 GUI 与排版**（`tests/test_world_gui.py`，83 项）：编辑器 15 个页签、
  工作区三种模式与显隐、编辑器独立成窗口、状态栏上下文、联动总线
  （队列 / 联动开关 / 预览 / 生成命令 / 日志 / 结构化发送）、世界面板各页签
  填充、只读时写入按钮禁用、解锁后启用、场景与物品对话框、
  可视化编排画布（标记刷新 / 编排 / 导出 / 合并进区块）、数据包工程页签；
  **排版回归**（每个页签的边距与间距必须等于 theme token、所有表格行高与表头
  一致、12 个页签都有 `Ctrl+F` 过滤条、源码里不许再出现写死的灰色值）、
  表格过滤与 CSV 导出、列表过滤、分节框、状态条；
- **可视化编排与区域批量替换**（`tests/test_creative.py`，59 项）：
  `BlockGrid` 的增删改查 / 调色板 / 边界 / 分集合成（矩形分解、单格退化成
  setblock、调色板分离、上限与提示）/ 转蓝图归一化；画布的坐标换算 / 缩放
  夹取 / 五种工具（含同层填充的连通性与不跨层不漏对角）/ 撤销重做 /
  选区信号；**真的渲染一遍**（网格 / 标记 / 选中 / 悬停 / 四种缩放，
  确认 paintEvent 不抛异常）；批量替换的四类操作字段级语义（`0b` 与 `0`
  视为同一个值、文本替换同值算无变化、列表下标删除、DataVersion 升级）、
  dry-run 计划与报告、幂等性、备份每文件一次、写后自检、其它字段不变、
  进度回调、对话框的字段联动与 dry-run 填充；
- **数据包工程**（`tests/test_datapack_project.py`，42 项）：命名空间规范化
  （大小写 / 非法字符 / 自动挑主命名空间时忽略只有 tags 的 `minecraft`）、
  资源增删改查与扩展名剥离、四种资源目录、单数 / 复数布局切换与旧文件清理、
  `pack.mcmeta` 与 JSON 美化 / 非法 JSON 原样写出、函数的尾换行、
  重载往返、`minecraft:load` / `tick` 标签、自检（命名空间 / JSON 语法 /
  悬空标签引用）、四种模板、`import_pack` 复制与覆盖、落盘无 `.tmp` 残留；
- **区域写入与工作区预设**（`tests/test_region_io.py`，35 项）：扇区补齐、
  区块数据块的 3 种压缩、dry-run 可行性（缺区块 / 越界 / 超大三种拒绝路径）、
  越界时**原文件必须完好**、位置表不被改动、写后自检（含故意造出的长度不
  自洽与截断）、备份、真实 NBT 区块的读写往返、实体懒加载扫描、4 个内置
  预设、用户预设覆盖与保存、预设套用到世界的端到端；
- **蓝图 / 配方与改名迁移**（`tests/test_blueprint_migration.py`，47 项）：
  蓝图选区归一化、调色板与 `block_entities` 分离、`.nbt` / `.schem` / `.snbt`
  往返（含 3D 立方体与 Y/Z/X 铺平公式回归）、命令生成、体积上限、
  配方读取 / 覆盖 / 批量应用、品牌常量与改名说明、`pyproject` 与 CLI 入口、
  数据目录迁移（复制 / 幂等 / 不覆盖 / 可关闭）。

`tests/test_storage.py` 里的目录名断言也更新成「新目录 + 旧目录兼容」两条。
所有测试都在临时目录里跑，用 `tools/fake_world.py` 生成假世界，
**不碰真实存档与真实用户数据**。

GUI 冒烟测试（离屏，无需显示器）：18 个分组，含世界编辑器、联动总线、蓝图与数据目录迁移。

```bash
python tools/smoke_gui.py
```

静态检查：

```bash
python -m ruff check app main.py mcgen.py mcstudio.py tools tests
```

---

## 许可

仅供学习与个人使用。
