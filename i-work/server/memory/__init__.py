"""L1 原子记忆：从对话自动抽取结构化事实，每轮自动召回注入。

设计见 docs/chapters/5-记忆模块(未实现).md 第一部分。分层职责：

- types     领域模型与常量（仓储接口的交换格式）
- l0        L0 读取与四步清洗、质量门
- prompts   抽取 / 去重提示词（设计文档原文）
- extractor 场景切分 + 记忆抽取（LLM 调用一）
- dedup     候选召回 + 冲突判断（LLM 调用二）
- retriever 检索抽象（当前 TF-IDF 实现）
- store     落地写入
- recall    召回侧：清洗 / 检索 / 格式化 / 预算
- scheduler 轮询 sweep：四种触发
"""
