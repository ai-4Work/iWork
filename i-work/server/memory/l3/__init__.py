"""L3 画像记忆（设计文档 docs/chapters/5-记忆模块 第三部分）。

- `types`   领域模型与触发常量
- `prompts` 两套系统提示词 + 用户模板 + 注入渲染 + 边界标签转义
- `reader`  增量读取：画像行 + 变化场景 + 记忆总数 → L3Input
- `generator` 唯一一次 LLM 调用（返回画像全文）+ 确定性后处理
- `recall`  召回侧：整份读出行，包成 <user-persona> 注入 system 末尾
- `scheduler` 四优先级触发 + 落库 + 清 P1 信号（由 L2 的 sweep 级联调用，没有独立定时任务）
"""
