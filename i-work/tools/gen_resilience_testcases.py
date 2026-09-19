"""生成第3层（韧性 / 鲁棒性）测试用例 Excel。

依据 docs/chapters/15-测试评估逻辑.md 第3层：异常按「故障有没有卡在某个 LLM 指令的应答上」分成两类——
A 类（没卡上，不回灌，代码处理，确定性可断言）与 B 类（卡上了，回灌给 LLM 自决，非确定只能采样）。

首批七条，按「人工好不好造」排序：**易造的四条在前，LLM 连接层三条在后**。

前四条只需要一条命令或干脆不用造（缺依赖、死地址、缺文件、静态不一致），异常落在
**工具 / skill 边界**上，不用改 `.env`、不用重启服务、不用编写 stub：
  R1  缺/坏依赖族 —— pandas 当下就没装，`python -c "import pandas"` 直接复现，零制造成本。
  R2  换外部端点族（死地址）—— 把 skill 的联网端点指到没人监听的端口，连接被动失败。
  R3  缺/坏文件族 —— 不创建目标脚本，bash 真抛 [Errno 2]。
  R4  制造不一致族（静态版）—— 同名目标写两处，edit_file 真报匹配不唯一。

后三条统一用「换外部端点」这一族，但**作用在 LLM 连接本身**，代价高一档（改 base_url、重启服务）：
  R5  B 类下界——**断一次再恢复**；重试接上后 LLM 回来，中断期间那次的失败要不要回灌给它自决。
      全表唯一需要自建**有状态** stub 的一条（一次性 TCP 转发代理），人工成本最高。
  R6  B 类上界——**持续断开**；重试耗尽之后框架收不收住，还是空转到轮次上限。
  R7  A 类——同一条杠杆，**持续 429**；重试耗尽后的终止与收尾，全程确定性，可进 CI 硬门禁。
      无状态 stub（永远返 429），比 R5 便宜得多。

列结构与样式复刻 docs/chapters/复杂任务-内置工具+Skill测试用例.xlsx，另加一列
「异常制造方式」：第4列写**注入什么**（Fixture），第5列写**怎么造**（打法 / 步骤 / 预期）。
制造方式只留可照抄的操作，压到 4 行以内，不写设计论证。

与第2层三档用例的根本差别：第2层跑**正常轨迹**，用例只承载期望、不制造故障；
本层必须**注入**——这些故障在正常轨迹里根本不触发。注入点选在 **LLM 连接边界**
（本地 stub 顶替真端点），不往引擎里打补丁，这样控制的是三段链的第①段、
观察②③段，三段分得开。

B 类用例的「期望行为」一律写全三个检查点，缺一个就没法定位判负该改哪：
  ① 注入的**原始**错误文本是什么（写在 Fixture 列）
  ② 进入下一轮上下文的文本（是否被截断 / 摘要掉 / 静默丢弃）
  ③ 下一轮的动作与出错那次对比

「运行层」列的取值与第4层的 markers 对齐：
  fast —— FakeLLM / 本地 stub，确定性，可断言，进 PR 阻断式门禁
  slow —— 真实 LLM 采样，同一注入跑多次看分布，红一次不代表代码有 bug

用法:
    python i-work/tools/gen_resilience_testcases.py
"""
import math
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HEADERS = [
    "用例编号",
    "被测组合",
    "用例名称",
    "前置条件（Fixture）",
    "异常制造方式",
    "输入 Prompt",
    "期望行为",
    "参考轨迹（完整序列）",
    "协作特征",
    "运行层",
]

COLUMN_WIDTHS = [9, 30, 22, 40, 34, 32, 62, 84, 22, 14]

# 居中列：A 用例编号、J 运行层；其余左对齐
CENTER_COLUMNS = {1, 10}

OUTPUT_NAME = "第3层-韧性测试用例.xlsx"

SHEET_NAME = "韧性测试"

# 每条用例的类别：B 类的「期望行为」必须写全三个检查点，A 类不必（它全靠确定性断言）
CASE_CLASS = {
    "R1": "B（下界）", "R2": "B（下界）", "R3": "B（下界）", "R4": "B（下界）",
    "R5": "B（下界）", "R6": "B（上界）", "R7": "A",
}

CASE_DATA: list[list[str]] = [
    # ══ 易造的四条：工具 / skill 边界，一条命令可造 ═══════════════════════
    # ── R1  缺/坏依赖族（零制造成本） ─────────────────────────────────────
    [
        "R1",
        "smart-charts skill 脚本（bash 子进程）+ 回灌链",
        "skill 依赖真没装 → 脚本非零退出 → 错误回灌 → 换路",
        "当前解释器真没装 pandas（`python -c \"import pandas\"` 报 ModuleNotFoundError）"
        "——smart-charts 的 requirements 依赖它，环境当下就不满足。\n"
        "workspace 放 data/2026-09/sales.csv（几行日期 + 金额）。",
        "打法：缺/坏依赖族 —— 不装，也不人为卸载（环境本来就不满足）\n"
        "步骤：1) 先确认 `python -c \"import pandas\"` 报错，这一步就是注入检查\n"
        "      2) 让 agent 用 smart-charts 把 data/2026-09/sales.csv 画成柱状图"
        "（skill 入口脚本在 bash 子进程里真 import pandas）\n"
        "预期：脚本非零退出，stderr 含 \"ModuleNotFoundError: No module named 'pandas'\"；"
        "该片段原样进下一轮上下文；下一轮动作与出错那次不同\n"
        "复原：无需（本来就没装）。注意：模型可能自己 pip install pandas 装回来，"
        "跑完要 pip uninstall -y pandas，否则这条不可重复——或整轮跑在一次性 venv 里",
        "用 smart-charts 把 data/2026-09/sales.csv 画成一张柱状图",
        "三个检查点：①（见 Fixture）脚本真抛的 ModuleNotFoundError 原文 → 非零退出；"
        "②该片段是否**原样**进入下一轮上下文，不得被截断、摘要掉或静默丢弃；"
        "③LLM 下一轮的动作与出错那次**存在差异**——自装依赖 / 换 xlsx skill / 改走内置工具，"
        "至少一处不同。\n"
        "Prompt 里点名 smart-charts 是刻意的：不点名时模型可能改走 xlsx skill"
        "（openpyxl 是装着的），那条路径不触发缺依赖。\n"
        "可见性（永不静默）：失败在 system.status / 工具结果里可见。",
        "bash 跑 skill 入口脚本 ✗ ModuleNotFoundError（注入） → [错误原样入上下文] "
        "→ 换路（pip install 自装 / 换 xlsx skill / 改走 read_file + 内置工具） → 结论\n"
        "不变量：stderr 证据片段原样出现；下一轮不与出错那次同参重试。",
        "缺依赖 / 错误回灌 / 换方案",
        "fast + slow",
    ],
    # ── R2  换外部端点族·死地址（不用起 stub） ────────────────────────────
    [
        "R2",
        "paddleocr-doc-parsing skill 脚本（bash 子进程）+ 回灌链",
        "skill 联网端点不可达 → 脚本非零退出 → 错误回灌 → 换路",
        "客户端环境里 PADDLEOCR_API_URL=http://127.0.0.1:9/v1/ocr（没人监听的端口）、"
        "PADDLEOCR_ACCESS_TOKEN=dummy。\n"
        "workspace 放 scans/receipt.png。",
        "打法：换外部端点族 —— 把 skill 的联网端点指到**没人监听的端口**，让连接被动失败\n"
        "步骤：1) 在启动客户端的环境里设 PADDLEOCR_API_URL 指向死端口"
        "（:9 或任意未监听的高位端口）\n"
        "      2) 要更真：直接断网（禁用网卡）或设 HTTPS_PROXY=http://127.0.0.1:9\n"
        "      3) 让 agent 把 scans/receipt.png OCR 成文字\n"
        "预期：parse_document.py 真抛 requests 的 ConnectionError（实测 stderr 形如 "
        "\"Error: HTTPConnectionPool(host='127.0.0.1', port=9): Max retries exceeded …"
        "Failed to establish a new connection\"），打到 stderr、退出码 1；"
        "该片段原样进下一轮；下一轮动作与出错那次不同\n"
        "复原：恢复 PADDLEOCR_API_URL",
        "把 scans/receipt.png 里的文字提取出来",
        "三个检查点：①（见 Fixture）脚本真抛的连接错误原文 → 非零退出；"
        "断言取**证据片段**（\"Max retries exceeded\" / \"Failed to establish a new connection\"），"
        "不锁死原文——真文本含 WinError 与本地化系统提示，随机器变；"
        "②该片段是否**原样**进入下一轮上下文；"
        "③LLM 下一轮的动作与出错那次**存在差异**——换 parse_pdf.py / 换别的本地手段 / "
        "直接告知用户端点不可达。\n"
        "与 R5–R7 的差别：死地址是**被动**失败，不用写 stub、不用改 .env、不用重启服务；"
        "异常落在**工具边界**而非 LLM 应答上，回灌链更短、更好观察。\n"
        "可见性（永不静默）：失败在 system.status / 工具结果里可见。",
        "bash 跑 parse_document.py ✗ 连接被拒（注入） → [错误原样入上下文] "
        "→ 换路（换 parse_pdf.py / 本地手段 / 告知端点不可达） → 结论\n"
        "不变量：连接错误证据片段原样出现；下一轮不与出错那次同参重试；不谎报 OCR 成功。",
        "网络不可达 / 错误回灌 / 换方案",
        "fast + slow",
    ],
    # ── R3  缺/坏文件族 ──────────────────────────────────────────────────
    [
        "R3",
        "内置 bash + 回灌链 + 引擎状态机",
        "脚本真的不存在 → [Errno 2] → 错误回灌 → 换只读工具取数",
        "workspace 有 data/2026-09/sales.csv，**刻意不建** scripts/rollup.py——这就是注入。",
        "打法：缺/坏文件族 —— 不创建目标文件\n"
        "步骤：1) 只放 data/2026-09/sales.csv，不建 scripts/\n"
        "      2) 让 agent 用 scripts/rollup.py 汇总\n"
        "预期：bash 非零退出，stderr 含 \"[Errno 2] No such file or directory\"；"
        "该片段原样进下一轮；下一轮动作与出错那次不同\n"
        "复原：无需（本来就没建）",
        "用 scripts/rollup.py 把 data/2026-09/sales.csv 按月汇总，写到 report.md",
        "三个检查点：①（见 Fixture）bash 真抛的 [Errno 2] 原文 → 非零退出；"
        "②该片段是否**原样**进入下一轮上下文；"
        "③LLM 下一轮的动作与出错那次**存在差异**——不再重跑同一条 bash，改走 read_file 取数。\n"
        "断言取**证据片段**（[Errno 2] / No such file or directory），不锁死原文——"
        "真 stderr 含解释器全路径与盘符，随机器变；这反而更贴 B3「证据充分」。\n"
        "可见性（永不静默）：失败在 system.status / 工具结果里可见。",
        "bash ✗ [Errno 2]（注入） → [错误原样入上下文] → 换 read_file 取数 "
        "→ write_file 产出 report.md → 结论\n"
        "不变量：证据片段原样出现；bash 只发一次、不原样重试；最终产物 report.md 真落盘。",
        "缺文件 / 错误回灌 / 换方案",
        "fast + slow",
    ],
    # ── R4  制造不一致族·静态版 ──────────────────────────────────────────
    [
        "R4",
        "内置 edit_file + 回灌链",
        "old_string 真匹配多处 → 编辑失败 → 错误回灌 → 重读后收窄入参",
        "conf.yaml 里同一 key 出现两处：port: 80 / port: 8080——"
        "文件实际内容与 LLM 以为的不一致（静态版，不用 race）。",
        "打法：制造不一致族（静态版）—— 把同名目标写两处，让文件实际内容与 LLM 以为的不一致\n"
        "步骤：1) 按上面写入 conf.yaml\n"
        "      2) 让 agent 把端口改成 9000\n"
        "预期：编辑返回「匹配到 2 处」；文件**未被改动**（失败的编辑不留半写）；"
        "该错误原样进下一轮；下一轮动作与出错那次不同\n"
        "复原：无需",
        "把 conf.yaml 的端口改成 9000",
        "三个检查点：①（见 Fixture）edit_file 真报的「匹配到 2 处」原文 → 失败返回；"
        "②该片段是否**原样**进入下一轮上下文；"
        "③LLM 下一轮的动作与出错那次**存在差异**——先 read_file 重读原文，再收窄 old_string 重写。\n"
        "失败不留半写：编辑失败时 conf.yaml 内容不得被改动，这是与成功路径的分界。\n"
        "可见性（永不静默）：失败在 system.status / 工具结果里可见。",
        "edit_file ✗ 匹配不唯一（注入） → [错误原样入上下文] → read_file 重读原文 "
        "→ 收窄 old_string 重写 → 结论\n"
        "不变量：错误证据原样出现；文件在失败那轮未被改动；下一轮改入参而非原样重试。",
        "编辑冲突 / 错误回灌 / 换方案",
        "fast + slow",
    ],
    # ══ LLM 连接层三条：要改 base_url / 重启服务，代价高一档 ════════════════
    # ── R5  B 类下界（全表唯一需自建有状态 stub 的一条） ───────────────────
    [
        "R5",
        "LLM 连接层（断一次后恢复）+ 引擎状态机",
        "断连一次 → 重试接上 → 中断期间的失败回灌",
        "IWORK_DEEPSEEK_BASE_URL 指向本地 stub：对**第 1 次** /v1/chat/completions "
        "直接断开（不返响应 / 流中途掐断），**第 2 次起**正常回 SSE。\n"
        "会话正常，无工具调用参与——故障只落在 LLM 应答上，无 out/。",
        "打法：换外部端点族 —— 让 LLM 端点只断第一次"
        "（全表唯一需自建**有状态** stub 的一条，人工成本最高）\n"
        "步骤：1) 起一个一次性 TCP 转发代理：accept 第 1 条连接后立即 close，"
        "之后每条连接纯转发到真实 LLM 端点；IWORK_DEEPSEEK_BASE_URL 指向它\n"
        "      2) 正常发一条消息，让客户端自己退避重试。"
        "零代码兜底：发出消息后立刻断网约 2s 再恢复，近似「断一次」\n"
        "预期：断连那次真报错，退避后第 2 次尝试接上；接上后 LLM 回来，"
        "中断期间的失败进不进下一轮上下文、下一轮动作变不变，是这条要看的两点",
        "帮我把 data 下的表汇总一下，起个脚本写出来",
        "三个检查点：①（见 Fixture）断连时客户端真抛的错误 → 退避后第 2 次尝试成功；"
        "②中断期间那次的失败信息是否**原样**进入下一轮上下文，不得被截断、摘要掉或静默丢弃；"
        "③LLM 下一轮的动作与断连前那次**存在差异**——看得出它知道刚才断过，"
        "不是当无事发生。\n"
        "换路发生在同一轮推理内，不引入额外用户确认；"
        "断连在 system.status / retry chunk 里可见（永不静默），不是静默重试。",
        "LLM 调用 ✗ 断连（注入） → [退避 1s，推 retry chunk] "
        "→ LLM 调用 ✓ 恢复 → [失败信息入上下文] → 下一轮动作（与断连前不同） → 结论\n"
        "不变量：只有第 1 次请求被断；重试接上后 LLM 不把断连当无事发生；"
        "失败不被静默吞掉。",
        "断连恢复 / 失败回灌 / 退避重试 / 永不静默",
        "slow",
    ],
    # ── R6  B 类上界 ──────────────────────────────────────────────────────
    [
        "R6",
        "LLM 连接层（持续断开）+ 引擎状态机",
        "持续断开 → 重试耗尽 → 到顶收住",
        "IWORK_DEEPSEEK_BASE_URL 指向本地 stub：对**每一条** /v1/chat/completions "
        "都直接断开（含流中途掐断）。无半成品产物，无 out/。",
        "打法：换外部端点族（持续版）—— 让每次应答都断\n"
        "步骤：起本地 stub 对每条请求都断开；IWORK_DEEPSEEK_BASE_URL 指向它，正常发消息\n"
        "预期：重试耗尽即收住（终止 / 暂停等人），不得试到 max_turns(25)；"
        "状态机回 IDLE、无半写产物，用户看得到「因 LLM 持续不可用已停止」",
        "把这批数据整理成一份分析报告",
        "**上界**：重试耗尽后必须停止自愈，升级为终止或暂停等人——"
        "不得一路试到 max_turns(25) 才被兜住，也不得全程空转到用户干等。\n"
        "触发边界后：状态机回 IDLE 或 SUSPENDED、current_message_id 清空、"
        "无半写入产物；用户能看到'因 LLM 持续不可用已停止'的可见反馈"
        "（message.error 或 system.status）。\n"
        "三个检查点：①（见 Fixture）每次断连的真错误；②每一轮都进了上下文、未被吞；"
        "③达到上限后不再产生新的 LLM 调用（不是原样重试到 25）。\n"
        "判据归属：同一端点的连续重试由 client.py 的 max_retries=3 覆盖；本条测的是"
        "**耗尽之后**框架收没收住，当前是缺口（12.4.5 自愈边界 / 12.6 缺口 6），先标 known-gap。",
        "LLM 调用 ✗（注入） → 退避 1s → ✗ → 退避 2s → ✗ "
        "→ **[重试耗尽]** → 终止 / 暂停等用户 → message.error(fatal=true) → 回 IDLE → 结论\n"
        "不变量：尝试次数 = 3；耗尽后不再有新的 LLM 调用；无半写产物。",
        "自愈边界 / 空转检测 / 有始有终 / 状态一致性",
        "fast + slow",
    ],
    # ── R7  A 类 ─────────────────────────────────────────────────────────
    [
        "R7",
        "LLM 连接层（持续 429）+ 引擎状态机",
        "重试耗尽 → 终止、收尾、可见",
        "会话处于 processing，已有一条 plan_confirmed 内存态与一个已落盘的中间产物 out/part1.csv。\n"
        "IWORK_DEEPSEEK_BASE_URL 指向本地 stub 服务，该服务对 /v1/chat/completions 一律返 429。",
        "打法：换外部端点族 —— 让 LLM 端点真的返 429\n"
        "步骤：起本地 stub 对 /v1/chat/completions 一律返 429；"
        "IWORK_DEEPSEEK_BASE_URL 指向它，重启服务\n"
        "预期：共 3 次尝试（首次 + 2 次重试）、退避 1s、2s，第 3 次失败即终止；"
        "out/part1.csv 仍在且未被写坏；最终 chunk 告知「已执行 X，未回滚」\n"
        "复原：恢复 .env 里的 base_url，停掉 stub 服务",
        "帮我把这批数据整理成一份分析报告",
        "共 **3 次尝试**（首次 + 2 次重试），退避 1s、2s，不多不少；第 3 次失败即终止。\n"
        "终止动作：推 message.error(fatal=true)、消息标记 error、引擎回 IDLE。\n"
        "收尾：current_message_id 清空、内存态（plan_confirmed）重置、stream buffer 冲刷或丢弃有确定行为；"
        "已落盘且不可回滚的副作用（out/part1.csv）记入清单并在最终 chunk 告知——"
        "**记录但不回滚**（12.4.6），不做半回滚。\n"
        "全程确定性：同一注入重复跑，重试次数、终止态、收尾后的状态机完全一致，可直接断言。",
        "[更早的工具调用已落盘 out/part1.csv] → 结论性输出 "
        "→ LLM 调用 ✗ 429 → [退避 1s，推 retry chunk llm_rate_limited] ✗ "
        "→ [退避 2s] ✗ "
        "→ **终止** → message.error(fatal=true) → 状态机回 IDLE "
        "→ 最终 chunk 告知「已执行 X，未回滚」→ 结论\n"
        "不变量：恰好 3 次尝试、无第 4 次；终止后不再有 tool_use 下发；"
        "out/part1.csv 仍在、且没有被删除或写坏。",
        "重试上限 / 终止收尾 / 状态一致性 / 永不静默",
        "fast",
    ],
]


HEADER_FILL = PatternFill("solid", fgColor="FF1F4E79")
DATA_FILL = PatternFill("solid", fgColor="FFF2F7FB")
THIN = Side(style="thin")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

# 行高的**下界**（中度档 60 / 复杂档 110 是固定值）。本表不固定：R1–R4 的「异常制造方式」
# 要装打法 / 步骤 / 预期三段，最长的折行后近 20 行，按 90 装会被裁掉——而这一列正是
# 测试人员要照着敲的东西，裁了就白写。所以逐行按内容算高度，矮行仍保底 90 以维持原版式。
DATA_ROW_HEIGHT = 90
LINE_HEIGHT = 13.0      # Calibri 10 折行后每行约占的高度（pt）
MAX_ROW_HEIGHT = 409.0  # Excel 行高上限


def _wrapped_lines(text, col_width: int) -> int:
    """估算一个单元格按列宽折行后占几行。中日韩字符按 2 个宽度单位算。

    只是估个够用的高度，不追求和 Excel 的排版算法一致——宁可估高一点，别裁内容。
    """
    total = 0
    for seg in str(text or "").split("\n"):
        units = sum(2 if ord(ch) > 0x2E80 else 1 for ch in seg)
        total += max(1, math.ceil(units / max(1, col_width - 1)))
    return total


def apply_style(ws, num_data_rows: int) -> None:
    """复刻源表样式：表头深蓝白字、数据行浅蓝、细边框、列宽、行高、冻结表头。"""
    for idx, width in enumerate(COLUMN_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width

    for col in range(1, len(HEADERS) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(name="宋体", size=11, bold=True, color="FFFFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[1].height = 26

    for row in range(2, num_data_rows + 2):
        for col in range(1, len(HEADERS) + 1):
            cell = ws.cell(row=row, column=col)
            cell.font = Font(
                name="Calibri",
                size=10,
                bold=(col == 1),
            )
            cell.fill = DATA_FILL
            cell.alignment = Alignment(
                horizontal="center" if col in CENTER_COLUMNS else "left",
                vertical="top",
                wrap_text=True,
            )
            cell.border = BORDER
        lines = max(
            _wrapped_lines(ws.cell(row=row, column=col).value, COLUMN_WIDTHS[col - 1])
            for col in range(1, len(HEADERS) + 1)
        )
        ws.row_dimensions[row].height = min(
            MAX_ROW_HEIGHT, max(DATA_ROW_HEIGHT, lines * LINE_HEIGHT)
        )

    ws.freeze_panes = "A2"


def build(output_path: Path) -> None:
    wb = Workbook()
    wb.remove(wb.active)

    ws = wb.create_sheet(title=SHEET_NAME)
    ws.append(HEADERS)
    for row in CASE_DATA:
        ws.append(row)
    apply_style(ws, len(CASE_DATA))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)


def main() -> None:
    base = Path(__file__).resolve().parent.parent
    output_path = base / "docs" / "chapters" / OUTPUT_NAME
    build(output_path)

    print(f"written: {output_path}")
    # 「异常制造方式」是给测试人员的操作指引：打法 / 步骤 / 预期缺一就没法照着敲
    # （「复原」可选——造环境类不落盘就没有要还原的现场，改了 env / 端点指向的才写）
    HOW_SECTIONS = ("打法", "步骤", "预期")

    for row in CASE_DATA:
        cls = CASE_CLASS[row[0]]
        fixture = bool(row[3].strip())
        missing = [k for k in HOW_SECTIONS if k not in row[4]]
        if cls == "A":
            # A 类不查三个检查点，只要求 Fixture 写清故障条件、制造方式四段齐全
            flag = "OK " if fixture and not missing else "BAD"
            detail = "确定性断言"
        else:
            # B 类的「期望行为」必须写全三个检查点，否则判负时无法定位该改哪一段
            checkpoints = sum(chk in row[6] for chk in ("①", "②", "③"))
            flag = "OK " if checkpoints == 3 and fixture and not missing else "BAD"
            detail = f"检查点 {checkpoints}/3"
        if missing:
            detail += f" 制造方式缺 {'/'.join(missing)}"
        print(f"  [{flag}] {row[0]}  {cls:<8} {row[9]:<11} {detail}  {row[2]}")
    print(f"  total: {len(CASE_DATA)} 条")


if __name__ == "__main__":
    main()
