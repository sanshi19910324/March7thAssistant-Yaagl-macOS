<div align="center">

# March7thAssistant for macOS（Yaagl / Wine 移植）

**在 macOS 上用 Wine 跑 Windows 版《崩坏：星穹铁道》，并自动化日常任务**

简体中文

</div>

---

## ⚠️ 重要声明

> **本项目是 [moesnow/March7thAssistant](https://github.com/moesnow/March7thAssistant) 的第三方衍生作品，遵循 GPL-3.0 发布。**
>
> - 原项目：**[moesnow/March7thAssistant](https://github.com/moesnow/March7thAssistant)** — 三月七小助手，所有核心功能均来自原项目
> - 本项目基底：**[luoxia001/March7thAssistant-macOS](https://github.com/luoxia001/March7thAssistant-macOS)** — PlayCover / iOS 版 macOS 移植，本项目的目录结构与移动端适配来自该仓库
> - **本次改造**：把输入层从「PlayCover MaaTools 触控」替换为「自研 Wine TCP 桥 + SendInput」，使项目支持 **Yaagl + Wine 运行的 Windows PC 版游戏**，而非 iOS 版
>
> 本项目作者不是 moesnow，也不是 luoxia001。本项目的问题请在本仓库提 Issue，**不要打扰上游作者**。

### 关于游戏服务条款

根据《米哈游游戏使用许可及服务协议》与《崩坏：星穹铁道》公平游戏宣言，**严禁使用外挂、脚本或其他破坏游戏公平性的第三方工具**。违规可能导致扣除收益、冻结或永久封禁账号。

> 亲爱的开拓者：
> 1、严禁使用外挂、加速器、脚本或其他破坏游戏公平性的第三方工具。
> 一经发现，米哈游将视违规严重程度及违规次数，采取扣除违规收益、冻结游戏账号、永久封禁游戏账号等措施。

**本项目仅用于个人技术研究与学习交流。** 使用本项目的全部风险由使用者自行承担，包括但不限于账号被封禁、数据丢失。作者不对任何后果负责。

---

## 这是什么

macOS 上自动化玩《崩坏：星穹铁道》有三条技术路线，本项目是**第三条**：

| 项目 | 路线 | 游戏版本 | 输入方式 |
|---|---|---|---|
| [moesnow/March7thAssistant](https://github.com/moesnow/March7thAssistant) | Windows 原生 | Windows PC 版 | Win32 SendInput |
| [luoxia001/March7thAssistant-macOS](https://github.com/luoxia001/March7thAssistant-macOS) | PlayCover | iOS 版 | MaaTools 触控注入 |
| **本项目** | **Yaagl + Wine** | **Windows PC 版** | **自研 Wine TCP 桥** |

### 与 PlayCover 路线的差异

| 能力 | PlayCover 路线 | 本项目（Yaagl + Wine） |
|---|---|---|
| 游戏版本 | iOS 版（需重新下载） | **Windows PC 版（账号通用）** |
| 兑换码 | ⛔ iOS 版无入口 | ✅ **可用** |
| 需装 PlayCover | 是（须 hguandl 分支） | 否 |
| 需 Apple Silicon | 是 | 否（Intel 亦可，未充分测试） |
| 稳定性 | 较成熟 | 需断网绕过反作弊 |

---

## 技术架构

```
┌─────────────────────────────────────────────────────────┐
│  macOS 宿主                                              │
│                                                          │
│  ┌────────────────────┐      ┌──────────────────────┐   │
│  │ M7A (Python 3.13)  │      │  m7a_daemon.py       │   │
│  │ 任务逻辑 / OCR      │      │  常驻守护            │   │
│  │ 模板匹配            │      │  · 自动断网 120s     │   │
│  └─────────┬──────────┘      │  · 自动起桥          │   │
│            │                 │  · 游戏退出清理      │   │
│            │ TCP 17400       └──────────────────────┘   │
│            ▼                                             │
│  ┌────────────────────┐                                 │
│  │  Wine 桥（Python）  │  ← 运行在 wineprefix 内        │
│  │  SendInput 注入     │                                 │
│  └─────────┬──────────┘                                 │
│            │                                             │
│            ▼                                             │
│  ┌────────────────────────────────────────────────────┐ │
│  │  Yaagl（Wine + DXMT）                              │ │
│  │  └── StarRail.exe（Windows PC 版）                 │ │
│  └────────────────────────────────────────────────────┘ │
│                                                          │
│  截图：Quartz.CGWindowListCreateImage（窗口级，不占前台） │
└─────────────────────────────────────────────────────────┘
```

### 为什么需要断网

游戏的 `HoYoKProtect` 反作弊依赖 Windows 内核驱动：

```
err:module:import_dll Library WDFLDR.SYS (which is needed by HoYoKProtect.sys) not found
err:ntoskrnl:ZwLoadDriver failed to create driver "HoYoProtect": c0000142
```

Wine 没有内核驱动环境，驱动加载必然失败，反作弊会主动终止游戏。

**绕过方式**：检测到游戏进程后立即断网并保持 120 秒，让反作弊连不上服务器、超时放行，之后恢复网络。这不是破解，只是让反作弊的联网校验超时。

---

## 组件说明

### 上游 M7A 本体（未改动核心逻辑）

```
main.py / app.py          任务入口
tasks/                    任务实现（日常、清体力、周常、挑战等）
module/                   截图、OCR、输入抽象、界面识别
assets/                   模板图片、配置模板
```

### 本项目新增（`tools/`）

| 文件 | 作用 |
|---|---|
| `m7a_wine_bridge.py` | **Wine 侧输入桥**：Python + ctypes 调 `SendInput`，监听 TCP 注入键鼠事件 |
| `start_game_with_bypass.sh` | **断网启动脚本**：检测进程 → 断网 120s → 恢复网络 |
| `m7a_daemon.py` | **常驻守护**：自动断网、自动起桥、游戏退出清理 |
| `m7a_daily.sh` | **一键日常**：状态检查 → 启动 → 跑任务 → 领奖 → 摘要 |
| `farm_relics.py` | 遗器连续刷取（配合培养目标） |
| `claim_daily_rewards.py` | 补领每日实训活跃度（M7A 只做任务不领奖） |
| `preflight_yaagl.py` | 六层链路预检 |

### 本项目修改的上游文件

| 文件 | 改动 |
|---|---|
| `module/game/yaagl.py` | **新增**：YaaglGameController，Quartz 截图 + 桥输入 |
| `module/game/wine_bridge_client.py` | **新增**：桥协议客户端 |
| `module/game/__init__.py` | 按平台/配置选择游戏控制器 |
| `module/automation/automation.py` | `input_handler` 改延迟解析；mask 匹配语义修正 |
| `utils/image_utils.py` | `TM_SQDIFF` → `TM_SQDIFF_NORMED` 归一化 |
| `tasks/game/starrailcontroller.py` | 动态基类，macOS 下跳过 Windows 专属操作 |
| `tasks/power/instance.py` | 侧栏滚动回退（侵蚀隧洞等折叠项） |
| `tasks/reward/dispatch.py` | 委托文案多版本兼容 |

---

## 安装

### 前置要求

| 项目 | 要求 |
|---|---|
| macOS | 12.0+（开发测试：macOS 26 / Apple M1 Ultra） |
| Python | 3.11+（开发测试：3.13） |
| Yaagl HSR | 0.3.21（**不要升级到 0.4.x**，见下方已知问题） |
| 游戏 | Windows PC 版《崩坏：星穹铁道》，国服或国际服 |

### 步骤

```bash
# 1. 装 Yaagl HSR
brew install --cask yaagl-hsr
# 或用官方 release：https://github.com/yaagl/yet-another-anime-game-launcher

# 2. 用 Yaagl 下载并安装游戏（首次需要，约 60-100 GB）

# 3. clone 本项目
git clone https://github.com/<你的用户名>/March7thAssistant-Yaagl-macOS.git
cd March7thAssistant-Yaagl-macOS

# 4. 建虚拟环境
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# 5. 复制配置模板
cp assets/config/config.example.yaml config.yaml
# 编辑 config.yaml，开启：
#   yaagl_enable: true
#   yaagl_bridge_port: 17400
#   yaagl_bridge_token: "<自定义 token，32 字节以上>"

# 6. 部署 Wine 侧 Python（桥的运行环境）
#    下载 python-3.11.9-embed-amd64.zip 解压到：
#    ~/Library/Application Support/Yaagl HSR/wineprefix/drive_c/m7apy/
#    然后把 tools/m7a_wine_bridge.py 复制进去

# 7. 预检
./.venv/bin/python tools/preflight_yaagl.py
```

---

## 使用

### 方式一：常驻守护（推荐）

守护进程会自动处理断网和起桥，你只需要点「开始游戏」。

```bash
# 启动守护（前台，看日志）
./.venv/bin/python tools/m7a_daemon.py

# 或后台常驻
nohup ./.venv/bin/python tools/m7a_daemon.py &

# 开机自启（可选）
cp tools/com.m7a.daemon.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.m7a.daemon.plist
```

之后：
1. 打开 Yaagl 启动器
2. **点「开始游戏」** ← 唯一手动步骤
3. 守护自动断网 120s → 恢复 → 起桥
4. 进游戏后跑任务

### 方式二：一键日常

```bash
bash tools/m7a_daily.sh routine      # 完整日常
bash tools/m7a_daily.sh daily        # 只做每日实训
bash tools/m7a_daily.sh power        # 只清体力
bash tools/m7a_daily.sh --status     # 只看状态
```

### 方式三：直接调用 M7A

```bash
MARCH7TH_DOCKER_STARTED=true ./.venv/bin/python main.py routine
MARCH7TH_DOCKER_STARTED=true ./.venv/bin/python main.py power
MARCH7TH_DOCKER_STARTED=true ./.venv/bin/python main.py currencywars
```

`MARCH7TH_DOCKER_STARTED=true` 用于绕过首次运行检测（`auto_update: false` 时 CLI 会拒绝启动）。

---

## 功能状态

### ✅ 已实测通过

| 功能 | 说明 |
|---|---|
| 每日实训 | 4/4 完成，活跃度 500/500，含单项与里程碑领奖 |
| 清体力 | 支持培养目标自动识别副本 |
| 历战余响 | 3/3 打完，周本材料到手 |
| 兑换码 | 5 个码批量兑换成功 |
| 邮件 / 委托 / 支援 / 无名勋礼 | 自动领取 |
| 培养目标 | 自动读取游戏内设置，识别待刷副本 |
| 遗器装配 | 快速装配 + 手动换件 |
| 货币战争 | 可跑（积分已满时无收益） |

### ⚠️ 技术可行但未充分验证

| 功能 | 说明 |
|---|---|
| 混沌回忆 / 虚构叙事 / 末日幻影 | 只需短按 W 靠近怪物，不受长按 bug 影响；队伍配置需自填 |
| 差分宇宙 | 战斗、选祝福正常；**卡在「按住 W 走进门」**（见已知问题 1） |

### ❌ 不可用

| 功能 | 原因 |
|---|---|
| 锄大地 | M7A 硬编码 `if sys.platform != 'win32': return False` |
| 模拟宇宙 | 依赖 Windows 原生 exe |
| 自动对话（AutoPlot） | 仅挂 GUI，macOS 下未验证 |

---

## 已知问题

### 1. 按键长按失效（影响跑图）

**现象**：`press_key_down('w')` 后角色不动。

**原因**：客户端发送 3 字段载荷（含 down/up 标志），桥端只解析 vk 和 hold，忽略标志，统一执行「按下+抬起」的 `press_key()`。

```python
# 客户端
payload = struct.pack('<BHH', KIND_KEY, vk, 0)   # 0=按下, 1=抬起
# 桥端
vk = struct.unpack('<H', payload[1:3])[0]
hold = struct.unpack('<H', payload[3:5])[0] if len(payload) >= 5 else 50
return press_key(vk, hold)     # ← 标志被丢弃
```

**影响**：差分宇宙进门、剧情跑图、任何需要持续移动的场景。

**状态**：已定位，未修复（欢迎 PR）。

### 2. Yaagl 0.4.x 启动器永远显示「下载」

`game_scan_info` 的 exe hash 与 manifest 不一致（上游 issue #780）。

**解决**：回滚 Yaagl 到 0.3.21，**并**把 `resources.neu` 替换为 0.3.21 的 `resources_hkrpgcn.neu`。

### 3. DXMT 概率性崩溃

**现象**：恢复网络后游戏偶发闪退。

```
NSInvalidArgumentException: -[NSIndexPath newComputePipelineStateWithDescriptor:...]
unrecognized selector sent to instance
```

**原因**：DXMT（DirectX→Metal 转译层）在资源密集加载时创建 compute pipeline 失败。与自动化代码无关，属转译层稳定性问题。

**缓解**：重试通常可过。

### 4. 残留 wine 进程导致启动器报错

**现象**：点「开始游戏」弹出 `Fatal error: Command return non-zero code (1)`，指 `winedrv_config.bat`，StdErr 为空。

**原因**：`winedevice.exe` 占用 wineprefix，`wine cmd /c` 无法初始化。

**解决**：清理完整进程组

```bash
pkill -9 -f winedevice
pkill -9 -f plugplay
pkill -9 -f services.exe
pkill -9 -f wineserver
```

`m7a_daemon.py` 已在启动时自动预清理。

### 5. 桥是独占资源

**不要**在 M7A 任务运行时另开进程访问 17400 端口，会导致双方抢连接、`截图超时`、任务崩溃。看进度请只读日志。

---

## 桥协议

TCP 自定义协议，24 字节头：

| 偏移 | 长度 | 含义 |
|---|---|---|
| 0x00 | 4 | magic `0x31494742`（ASCII `BGI1`） |
| 0x04 | 2 | flag = 5 |
| 0x06 | 2 | command |
| 0x0C | 4 | payload_len |
| 0x14 | 4 | reserved = 0 |

命令字：

| 值 | 名称 | 用途 |
|---|---|---|
| 1 | PROBE | 版本探测 |
| 2 | AUTH | Token 认证 |
| 3 | INPUT | 构造 INPUT（kind=1 键盘 / 2 点击 / 3 滚轮 / 4 文本） |
| 7 | SET_FOREGROUND | SetForegroundWindow |
| 0x17 | MOUSE_ABS | 鼠标绝对移动 |

输入签名 `INPUT_SIGNATURE = 0x4D374157494E45`（ASCII `M7AWINE`）。

---

## 开发日志

完整的技术记录（含每次踩坑的根因分析、修复过程、验证证据）见：

- [`docs/技术报告.md`](docs/技术报告.md) — 方案验证、12+ 处修复、实测数据
- [`docs/协议逆向.md`](docs/协议逆向.md) — Wine 桥协议逆向过程

---

## 许可证

本项目遵循 **[GPL-3.0](LICENSE)**，继承自上游：

- [moesnow/March7thAssistant](https://github.com/moesnow/March7thAssistant) — GPL-3.0
- [luoxia001/March7thAssistant-macOS](https://github.com/luoxia001/March7thAssistant-macOS) — GPL-3.0

依 GPL-3.0 要求，本项目：
- 保留原许可证全文（见 `LICENSE`）
- 标明上游来源与修改内容（见本 README 顶部声明）
- 同样以 GPL-3.0 发布，允许再分发与修改

---

## 致谢

- **[moesnow](https://github.com/moesnow)** — March7thAssistant 原作者，所有核心功能
- **[luoxia001](https://github.com/luoxia001)** — macOS 移植基底
- **[yaagl](https://github.com/yaagl)** — Wine + DXMT 启动器
- **[3Shain/dxmt](https://github.com/3Shain/dxmt)** — DirectX → Metal 转译层
- **[hguandl](https://github.com/hguandl)** — PlayCover 分支与 MaaTools（本项目的移动端适配来源）

---

<div align="center">

**本项目仅供技术研究与学习交流。请遵守游戏服务条款，风险自负。**

</div>
