"""生成第3层（韧性 / 鲁棒性）测试用例 Excel。

依据 docs/chapters/15-测试评估逻辑.md 第3层：异常按「故障有没有卡在某个 LLM 指令的应答上」分成两类——
A 类（没卡上，不回灌，代码处理，确定性可断言）与 B 类（卡上了，回灌给 LLM 自决，非确定只能采样）。

首轮只落 3 条，**统一用「换外部端点」这一族**（本地 stub 断开 / 429）——一个手法覆盖三条，
区别只在断开次数。选它是因为故障落在 LLM 连接本身，任何用例都天然依赖它，
不用编排 workspace、也不会出现「注入的东西和 prompt 对不上」：
  R1  B 类下界——**断一次再恢复**；重试接上后 LLM 回来，中断期间那次的失败要不要回灌给它自决。
  R2  B 类上界——**持续断开**；重试耗尽之后框架收不收住，还是空转到轮次上限。
  R3  A 类——同一条杠杆，**持续 429**；重试耗尽后的终止与收尾，全程确定性，可进 CI 硬门禁。

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
CASE_CLASS = {"R1": "B（下界）", "R2": "B（上界）", "R3": "A"}

CASE_DATA: list[list[str]] = [
    # ── R1  B 类下界 ──────────────────────────────────────────────────────
    [
        "R1",
        "LLM 连接层（断一次后恢复）+ 引擎状态机",
        "断连一次 → 重试接上 → 中断期间的失败回灌",
        "IWORK_DEEPSEEK_BASE_URL 指向本地 stub：对**第 1 次** /v1/chat/completions "
        "直接断开（不返响应 / 流中途掐断），**第 2 次起**正常回 SSE。\n"
        "会话正常，无工具调用参与——故障只落在 LLM 应答上，无 out/。",
        "打法：换外部端点族 —— 让 LLM 端点真的断一次\n"
        "步骤：1) 起本地 stub：第 1 次请求直接断开，第 2 次起正常；"
        "IWORK_DEEPSEEK_BASE_URL 指向它\n"
        "      2) 正常发一条消息，让客户端自己退避重试\n"
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
    # ── R2  B 类上界 ──────────────────────────────────────────────────────
    [
        "R2",
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
    # ── R3  A 类 ─────────────────────────────────────────────────────────
    [
        "R3",
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

# 本档的行高：期望行为要装三个检查点、参考轨迹含注入点，取 90（中度档 60 / 复杂档 110）
DATA_ROW_HEIGHT = 90


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
        ws.row_dimensions[row].height = DATA_ROW_HEIGHT

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
    # （「复原」可选——本批三条都是 stub 端点，不落盘，没有要还原的现场）
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
