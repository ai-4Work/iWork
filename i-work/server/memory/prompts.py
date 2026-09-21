"""L1 提示词：设计文档原文，逐字照抄，不改写。

来源 docs/chapters/5-记忆模块(未实现).md：
- EXTRACTION_SYSTEM_PROMPT   L1-4.1（对话模式；团队模式 code 本次不实现）
- DEDUP_SYSTEM_PROMPT        L1-4.4
- MEMORY_TOOLS_GUIDE         L1-3.4（注入 system 提示词末尾的稳定部分）
- render_*                   对应的用户提示词模板（L1-4.3 / L1-4.6）

提示词里含 JSON 花括号，因此一律用拼接而非 str.format。
"""
from __future__ import annotations

import json

from server.memory.l0 import L0Message
from server.memory.types import L1Memory

EXTRACTION_SYSTEM_PROMPT = """你是专业的"情境切分与记忆提取专家"。
你的任务是分析用户的对话，判断情境切换，并从中提取结构化的核心记忆（仅限 persona, episodic, instruction 三类）。

**输出语言**：所有自由文本字段（`scene_name`、memory `content`）使用与用户消息相同的语言；JSON 字段名、枚举值、ISO 时间戳保持英文。

### 任务一：情境切分（Scene Segmentation）
分析【待提取的新消息】，结合【上一个情境】，判断并输出当前对话的情境。
- 继承：无明显切换，沿用上一个情境。
- 切换条件：用户发出明确指令（如"换话题"）、意图转变、或提出独立新目标。
- 一段对话可能只有一个情境，也可能有多个情境（话题多次切换时）。
- 命名规则："我（AI）在和xxx（用户身份）做xxx（目标活动）"（**使用上述输出语言**，约 30-50 个字符或等价长度，单句，全局唯一）。

---

### 任务二：核心记忆提取（Memory Extraction）
结合背景和当前情境，仅从【待提取的新消息】中提取核心信息。

【通用提取原则】
1. 宁缺毋滥：过滤琐碎闲聊、临时性指令和一次性操作（如"这次、本单"）；剔除不可靠的边缘信息。
2. 独立完整：记忆必须"跳出当前对话依然成立"，无上下文也能看懂。提取主体必须以"用户（姓名）"或"AI"为核心。
3. 归纳合并：强关联或因果关系的多条消息，必须合并为一条完整记忆，不可碎片化。

【支持提取的三大类型】（必须严格遵守类型规则）
> 下面给出的"提取句式"和"触发词"仅作为中文骨架参考；**实际 `content` 必须按上述输出语言书写**（例如英文用户 → "The user (Maya) is a senior product manager based in Berlin"）。

1. 个性化记忆 (type: "persona")
   - 定义：用户的稳定属性、偏好、技能、价值观、习惯（如住所、职业、饮食禁忌）。
   - 提取句式："用户（[姓名]）喜欢/是/擅长..."
   - 打分 (priority)：80-100（健康/禁忌/核心特质）；50-70（一般喜好/技能）；<50（模糊次要，可丢弃）。
   - 触发词：喜欢、习惯、经常、我这个人...

2. 客观事件记忆 (type: "episodic")
   - 定义：客观发生的动作、决定、计划或达成结果。绝不包含纯主观感受。
   - 提取句式："用户（[姓名]）在 [最好是精确绝对时间] 于 [地点] [做了某事（可以包含起因、经过、结果）]"。
   - 时间约束：尽量基于消息的 timestamp 推算绝对时间，如能确定则在 metadata 中输出 activity_start_time 和 activity_end_time（ISO 8601格式）。无法确定时可省略。
   - 打分 (priority)：80-100（重要事件/计划）；60-70（一般完整活动）；<60（琐碎事项，直接丢弃）。

3. 全局指令记忆 (type: "instruction")
   - 定义：用户对 AI 提出的长期行为规则、格式偏好、语气控制。
   - 提取句式："用户要求/希望 AI 以后回答时..."
   - 触发词：以后都、从现在开始、记住、必须。
   - 打分 (priority)：-1（极其严格的全局死命令）；90-100（核心行为规则）；70-80（重要要求）；<70（临时要求，直接丢弃）。

---

### 不应该提取的内容
- 琐碎闲聊、问候；临时性的纯工具性请求（如"这次帮我翻译一下"）
- 一次性操作指令（如"这次、本单"相关）
- 重复的内容；AI助手自身的行为或输出
- 不属于以上3类的信息
- 纯主观感受（不带客观事件的情绪表达）

---

### 任务三：输出格式规范（JSON）
返回且仅返回一个合法的 JSON 数组。数组的每一项是一个情境，包含该情境的消息范围和抽取到的记忆：

[
  {
    "scene_name": "当前生成或继承的情境名称",
    "message_ids": ["属于该情境的消息ID列表"],
    "memories": [
      {
        "content": "完整、独立的记忆陈述（按对应类型的句式要求）",
        "type": "persona|episodic|instruction",
        "priority": 80,
        "source_message_ids": ["消息ID_1", "消息ID_2"],
        "metadata": {}
      }
    ]
  }
]

metadata 字段说明：
- episodic 类型：如能确定活动时间，填入 {"activity_start_time": "ISO8601", "activity_end_time": "ISO8601"}
- 其他类型或无法确定时间：输出空对象 {}

如果整段对话无有意义的记忆，也要输出情境分割结果，memories 为空数组：
[
  {
    "scene_name": "情境名称",
    "message_ids": ["id1", "id2"],
    "memories": []
  }
]

请严格按上述 JSON 数组格式输出，不要输出任何额外的 Markdown 代码块修饰符（如 ```json）或解释文本。
"""

DEDUP_SYSTEM_PROMPT = """你是记忆冲突检测器。批量比较多条【新记忆】与【统一候选记忆池】中的已有记忆，逐条决定如何处理。

**输出语言**：`merged_content` 使用与候选池中已有记忆相同的语言；JSON 字段名、枚举值、record_id、ISO 时间戳保持英文。

## 核心规则

- **跨 type 合并**：不同 type（persona / episodic / instruction / work_fact / work_task / work_method / work_artifact）的记忆如果语义上描述同一事实/事件，**可以合并**。
- **多对多合并**：一条新记忆可以同时替换/合并候选池中的**多条**已有记忆（通过 target_ids 数组指定）。
- 合并后你必须判断新记忆的最佳 type（merged_type）。

## 判断逻辑

1. **分辨记忆性质**：
   - **状态类**（persona/instruction）：偏好、特质、长期设定、相对稳定的事实、行为规则
   - **事件类**（episodic）：一次性经历、带时间点的客观记录，建议合并同一件事的前因后果

2. **判断是否同一事实/事件**：主体相同、主题一致、时间接近、scene_name 相似

3. **选择动作**：
   - "store"：视为新信息，新增当前记忆。
   - "skip"：已有记忆更好，新记忆无增量或更模糊，忽略当前记忆。
   - "update"：同一事实/事件，新记忆在内容或时间上更优（更具体、更晚或纠错），以新记忆为主覆盖旧记忆，可保留旧记忆中仍正确的细节。
   - "merge"：同一事实或同一演化过程，多条记忆信息互补且不矛盾，合并成一条更完整记忆，信息尽量不冗余。

4. **策略倾向**：
   - 状态类：多条描述同一偏好/特质 → 倾向 merge；无增量 → skip；明确更新 → update
   - 事件类：同一事件的前因后果、不同阶段 → 倾向 merge 为一条完整叙述；完全相同 → skip
   - 跨类型示例：一条 episodic "用户在 2018 年开始做播客" + 一条 persona "用户有播客制作经验" → 可 merge 为一条 persona 或 episodic（取决于信息侧重）

5. **timestamp 处理**：
   - merge / update 时，merged_timestamps 应包含**所有相关记忆的时间戳并集**（去重排序）
   - 这样可以保留事件发生的完整时间线

## 输出格式

严格输出 JSON 数组，每个元素对应一条新记忆的决策。不输出任何其他内容：

[
  {
    "record_id": "新记忆的 record_id",
    "action": "store|update|skip|merge",
    "target_ids": ["要删除的候选记忆 record_id 1", "record_id 2"],
    "merged_content": "合并/更新后的记忆内容（merge/update 时必填）",
    "merged_type": "合并后的最佳 type：persona|episodic|instruction|work_fact|work_task|work_method|work_artifact（merge/update 时必填）",
    "merged_priority": 85,
    "merged_timestamps": ["合并后的时间戳数组，包含所有新旧记忆时间戳的并集（merge/update 时必填）"]
  }
]

字段说明：
- target_ids：要删除替换的旧记忆 ID **数组**（可以 1 条或多条）。store/skip 时省略或为空。
- merged_content：merge/update 时的最终记忆文本。store/skip 时省略。
- merged_type：merge/update 后记忆应归属的 type。根据合并后内容本质判断。
- merged_priority：merge/update 后的新优先级（0-100 整数，merge/update 时必填）。合并后信息更完整、更确定，通常应**酌情提升** priority（例如两条 priority 70 的记忆合并后可提升到 80）。参考标准：80-100（核心特质/重要事件），60-79（一般偏好/普通活动），<60（次要信息）。
- merged_timestamps：合并后的时间戳数组。收集新记忆 + 所有被合并旧记忆的时间戳，去重排序。
"""

MEMORY_TOOLS_GUIDE = """<memory-tools-guide>
## 记忆工具调用指南

当上方注入的记忆片段不足以回答用户问题时,可主动调用以下工具获取更多信息:

- **memory_search**:搜索结构化记忆(L1),适用于回忆用户偏好、历史事件节点、规则等关键信息。

### ⚠️ 调用次数限制
每轮对话中,memory_search **最多调用 3 次**。
- 首次搜索无结果时,可换关键词重试,但总调用次数不要超过 3 次。
- 若 3 次搜索后仍无结果,说明该信息不在记忆中,请直接根据已有信息回复用户,不要继续搜索。
</memory-tools-guide>"""

# 动态注入块（拼到本轮用户消息前缀，不进 system，避免打爆提示词缓存）
RELEVANT_MEMORIES_HEADER = "以下与当前对话相关,仅作参考,不代表当前任务进程:"


def render_relevant_memories(entries: list[str]) -> str:
    """把已格式化的记忆行包进 <relevant-memories> 标签。空列表返回空串。"""
    if not entries:
        return ""
    body = "\n".join(f"- {line}" for line in entries)
    return (
        "<relevant-memories>\n"
        + RELEVANT_MEMORIES_HEADER + "\n"
        + body + "\n"
        + "</relevant-memories>"
    )


def render_extraction_user_prompt(
    previous_scene: str,
    background: list[L0Message],
    new_messages: list[L0Message],
) -> str:
    """L1-4.3 用户提示词模板。"""
    bg = "\n\n".join(m.render() for m in background) or "无"
    new = "\n\n".join(m.render() for m in new_messages) or "无"
    return (
        '**输出语言**：根据下方"待提取的新消息"中 user 发言的主导语言书写 '
        "`scene_name` 和 memory `content`。\n\n"
        f"【上一个情境】：{previous_scene or '无'}\n\n"
        f"【背景对话】（仅供理解上下文推断关系/时间，严禁从中提取记忆）：\n{bg}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"【待提取的新消息】（务必结合 timestamp 推算时间，只从这里提取记忆！）：\n{new}"
    )


def render_dedup_user_prompt(
    pool: list[L1Memory], new_items: list[tuple[L1Memory, list[str]]],
) -> str:
    """L1-4.6 用户提示词模板。new_items 为 (新记忆, 关联候选 id 列表)。"""
    if pool:
        lines = [
            json.dumps(
                {
                    "record_id": m.id,
                    "content": m.content,
                    "type": m.type,
                    "priority": m.priority,
                    "scene_name": m.scene_name,
                    "timestamps": list(m.timestamps),
                },
                ensure_ascii=False,
            )
            for m in pool
        ]
        pool_block = "\n".join(lines)
        pool_header = f"## 统一候选记忆池（共 {len(pool)} 条已有记忆）"
    else:
        pool_block = "（空，没有已有记忆，所有新记忆直接 store）"
        pool_header = "## 统一候选记忆池（共 0 条已有记忆）"

    blocks = []
    for i, (memory, candidate_ids) in enumerate(new_items, start=1):
        block = (
            f"### 第 {i} 条新记忆 (record_id: {memory.id})\n"
            + json.dumps(
                {
                    "record_id": memory.id,
                    "content": memory.content,
                    "type": memory.type,
                    "priority": memory.priority,
                    "scene_name": memory.scene_name,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n\n"
        )
        if candidate_ids:
            block += f"【关联候选 ID】{json.dumps(candidate_ids, ensure_ascii=False)}"
        else:
            block += "【关联候选 ID】[]（无相似候选，直接 store）"
        blocks.append(block)

    return (
        "**输出语言**：`merged_content` 使用与候选池中已有记忆相同的语言。\n\n"
        f"{pool_header}\n{pool_block}\n\n"
        "══════════════════════════════════════════════════════\n\n"
        f"## 待判断的新记忆（共 {len(new_items)} 条）\n\n"
        + "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n".join(blocks)
        + "\n\n请逐条判断并输出决策 JSON 数组。当某条新记忆的候选列表为空时，"
        "该条直接输出 action=store。"
    )
