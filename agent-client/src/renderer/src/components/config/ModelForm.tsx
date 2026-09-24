/** 模型配置的新增 / 编辑弹窗。
 *
 *  **字段随 `deployment_type` 显隐**，这是一页两形态的原因：公网模型要 key、按量计费有单价；
 *  内网自建模型常常免鉴权、按并发限流，单价填了也没处结算。
 *
 *  | 字段 | 公网 | 内网 | 为什么 |
 *  |---|---|---|---|
 *  | API Key | 出 | 不出 | 内网端点普遍免鉴权；真需要也能填（后端照存） |
 *  | 单价 | 出 | 不出 | 内网模型单价留空 = 不计费，是常态（`ResolvedModel.billable`） |
 *  | 最大并发 | 不出 | 出 | 公网按量计费不用客户端排队；自建实例的并发上限要人工设 |
 *
 *  编辑时 key 那一栏**先摆只读掩码**（`api_key_hint`）而不是空输入框 —— 一个空框看起来就是
 *  "没配过"，而库里的明文本来就拿不回来。要换 key 得先点「替换」，此时才是可输入的空框；
 *  没点「替换」时 `apiKey` 恒为空串 → 保存时 key 字段干脆不出现在请求体里（不是传空串，
 *  空串与"没填"在服务端是同一个语义，但别让读代码的人去猜）。
 *
 *  ⚠️ 别在这里用 `window.prompt()`：Electron 未实现它，会静默返回 null。
 *  `confirm()` 是可用的。
 */
import { useState } from 'react'
import type { LlmModelAdmin, LlmModelPayload } from '../../services/api'

type Protocol = 'openai_compatible' | 'anthropic'
type Deployment = 'public' | 'intranet'

/** 与服务端 `_KEY_RE` 同一口径。`model_key` 会出现在 URL 路径、`sessions.model`、
 *  `messages.model` 里，所以只允许不折腾 URL 的字符。**编辑时不给改** —— 改它等于让存量
 *  会话解析不到模型。 */
const KEY_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,49}$/

const INPUT =
  'w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0] placeholder:text-[#cbd5e1]'

function Label({ text, hint }: { text: string; hint?: string }) {
  return (
    <label className="block text-[12px] font-medium text-[#64748b] mb-1">
      {text}
      {hint && <span className="ml-1 font-normal text-[#94a3b8]">{hint}</span>}
    </label>
  )
}

export function ModelForm({ editing, onClose, onSubmit }: {
  /** null = 新增 */
  editing: LlmModelAdmin | null
  onClose: () => void
  /** 返回 false 时弹窗不关（标识被占、显示名为空、密钥没配主密钥都能改了再交） */
  onSubmit: (modelKey: string, payload: LlmModelPayload) => Promise<boolean>
}) {
  const isEdit = editing !== null

  const [modelKey, setModelKey] = useState(editing?.model_key ?? '')
  const [displayName, setDisplayName] = useState(editing?.display_name ?? '')
  const [deployment, setDeployment] = useState<Deployment>(editing?.deployment_type ?? 'public')
  const [protocol, setProtocol] = useState<Protocol>(editing?.protocol ?? 'openai_compatible')
  const [apiName, setApiName] = useState(editing?.model_api_name ?? '')
  const [baseUrl, setBaseUrl] = useState(editing?.base_url ?? '')
  /** 编辑时永远从空开始：库里那把明文拿不回来，掩码只是显示给人看的 */
  const [apiKey, setApiKey] = useState('')
  /** 有存量的 key 时先只读展示掩码，点「替换」才切成可输入的空框 */
  const [replacingKey, setReplacingKey] = useState(false)
  const [contextWindow, setContextWindow] = useState(String(editing?.context_window ?? 65536))
  /** 空串 = 不设，服务端按窗口折算触发线（与单价留空同一种"没填"语义） */
  const [compressThreshold, setCompressThreshold] = useState(
    editing?.compress_threshold_tokens?.toString() ?? ''
  )
  const [maxOutput, setMaxOutput] = useState(String(editing?.max_output_tokens ?? 20000))
  const [timeoutSec, setTimeoutSec] = useState(String(editing?.timeout_seconds ?? 120))
  const [maxRetries, setMaxRetries] = useState(String(editing?.max_retries ?? 3))
  const [supportsTools, setSupportsTools] = useState(editing?.supports_tools ?? true)
  const [supportsThinking, setSupportsThinking] = useState(editing?.supports_thinking ?? false)
  const [thinkingBudget, setThinkingBudget] = useState(
    String(editing?.thinking_budget_tokens ?? 4096)
  )
  const [maxConcurrency, setMaxConcurrency] = useState(String(editing?.max_concurrency ?? 0))
  const [priceIn, setPriceIn] = useState(editing?.price_input_per_1m?.toString() ?? '')
  const [priceOut, setPriceOut] = useState(editing?.price_output_per_1m?.toString() ?? '')
  const [enabled, setEnabled] = useState(editing?.enabled ?? true)
  const [remark, setRemark] = useState(editing?.remark ?? '')
  const [submitting, setSubmitting] = useState(false)

  const isPublic = deployment === 'public'
  const window_ = Number(contextWindow)
  const needsBaseUrl = protocol === 'openai_compatible'
  const hasStoredKey = isEdit && editing.has_api_key
  /** 只读展示态：编辑一个已经有 key 的行，且没点「替换」 */
  const keyReadOnly = hasStoredKey && !replacingKey
  const keyHint = editing?.api_key_hint || '已配置'
  const baseUrlClean = baseUrl.trim().replace(/\/+$/, '')
  /** 与服务端 `_validate_common` 同一条规则（同 KEY_RE 那样，两端各留一份）：
   *  客户端会自行拼 `/v1/chat/completions`，地址里再带一份就是 `/v1/v1/...`，只会 404。 */
  const baseUrlOk = !needsBaseUrl || !/\/(v1|chat\/completions)$/i.test(baseUrlClean)
  const thresholdRaw = compressThreshold.trim()
  /** 留空 = 不设（走窗口折算）。填了就必须是可以装得下的正数。 */
  const thresholdOk =
    !thresholdRaw ||
    (Number.isFinite(Number(thresholdRaw)) &&
      Number(thresholdRaw) >= 1024 &&
      Number(thresholdRaw) <= window_)

  const isValid =
    (isEdit || KEY_RE.test(modelKey.trim())) &&
    displayName.trim().length > 0 &&
    apiName.trim().length > 0 &&
    (!needsBaseUrl || baseUrl.trim().length > 0) &&
    baseUrlOk &&
    Number.isFinite(window_) &&
    window_ >= 1024 &&
    thresholdOk

  /** 空串 → 不传这个字段（服务端按"不改"处理）。单价空串 → null（= 不计费）。 */
  const num = (raw: string, fallback: number) => {
    const n = Number(raw)
    return Number.isFinite(n) ? n : fallback
  }
  const priceOrNull = (raw: string) => {
    const t = raw.trim()
    if (!t) return null
    const n = Number(t)
    return Number.isFinite(n) ? n : null
  }

  const handleSubmit = async () => {
    if (!isValid || submitting) return
    const payload: LlmModelPayload = {
      display_name: displayName.trim(),
      deployment_type: deployment,
      protocol,
      model_api_name: apiName.trim(),
      base_url: baseUrl.trim().replace(/\/+$/, ''),
      context_window: Math.round(window_),
      // 留空传 null（= 改回按窗口折算）。传空串在服务端也是"不改"，但语义靠猜。
      compress_threshold_tokens: thresholdRaw ? Math.round(Number(thresholdRaw)) : null,
      max_output_tokens: num(maxOutput, 20000),
      timeout_seconds: num(timeoutSec, 120),
      max_retries: num(maxRetries, 3),
      supports_tools: supportsTools,
      supports_thinking: supportsThinking,
      max_concurrency: isPublic ? 0 : num(maxConcurrency, 0),
      enabled,
      remark: remark.trim(),
    }
    // 单价只有公网模型才被送出去：内网那份界面不显示，留着旧值没意义
    if (isPublic) {
      payload.price_input_per_1m = priceOrNull(priceIn)
      payload.price_output_per_1m = priceOrNull(priceOut)
    }
    // 思考预算只有 Anthropic 协议认：OpenAI 兼容那支的客户端连这个形参都没有
    if (protocol === 'anthropic') {
      payload.thinking_budget_tokens = num(thinkingBudget, 4096)
    }
    // 留空 = 不修改。**不要**传空串：那会把语义从"没动"变成"清空"的前身，
    // 虽然服务端也把空串当"不改"，但读代码的人得去翻服务端才能确认。
    if (apiKey.trim()) payload.api_key = apiKey.trim()

    setSubmitting(true)
    try {
      if (await onSubmit(modelKey.trim(), payload)) onClose()
    } finally {
      setSubmitting(false)
    }
  }

  return (
    // 遮罩不关弹窗：表单字段多，误点一下主页面就把填了一半的内容全丢了。
    // 关闭只剩右上角 ✕ 与「取消」两个显式入口。
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20">
      <div className="bg-white rounded-xl shadow-lg p-6 w-[520px] max-h-[86vh] overflow-y-auto">
        <div className="flex items-center justify-between">
          <h3 className="text-[15px] font-semibold text-[#0f172a]">
            {isEdit ? `编辑模型「${editing.display_name}」` : '新增模型'}
          </h3>
          <button onClick={onClose} className="text-[#94a3b8] hover:text-[#0f172a] cursor-pointer">
            <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 4l8 8M12 4l-8 8"/></svg>
          </button>
        </div>
        <p className="mt-1 mb-4 text-[12px] text-[#64748b]">
          保存后重启无需重启服务，模型会立刻出现在聊天下拉里。
          {isEdit && ' 模型标识不可改 —— 存量会话按它找模型。'}
        </p>

        <div className="space-y-3.5">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label text="显示名 *" hint="下拉里给用户看的名字" />
              <input
                autoFocus
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                spellCheck={false}
                placeholder="如 DeepSeek V4"
                className={INPUT}
              />
            </div>
            <div>
              <Label text="模型标识 *" hint="唯一，建后不可改" />
              <input
                value={modelKey}
                onChange={(e) => setModelKey(e.target.value)}
                disabled={isEdit}
                spellCheck={false}
                placeholder="如 deepseek-v4"
                className={`${INPUT} font-mono disabled:bg-[#f8fafc] disabled:text-[#94a3b8]`}
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label text="部署类型" />
              <select
                value={deployment}
                onChange={(e) => setDeployment(e.target.value as Deployment)}
                className={`${INPUT} cursor-pointer`}
              >
                <option value="public">公网模型</option>
                <option value="intranet">内网自建</option>
              </select>
            </div>
            <div>
              <Label text="协议" />
              <select
                value={protocol}
                onChange={(e) => setProtocol(e.target.value as Protocol)}
                className={`${INPUT} cursor-pointer`}
              >
                <option value="openai_compatible">OpenAI 兼容（vLLM / Ollama / 各家云）</option>
                <option value="anthropic">Anthropic</option>
              </select>
            </div>
          </div>

          <div>
            <Label text="模型名 *" hint="发给端点的 model 字段，不是显示名" />
            <input
              value={apiName}
              onChange={(e) => setApiName(e.target.value)}
              spellCheck={false}
              placeholder="如 deepseek-chat / Qwen3-32B"
              className={`${INPUT} font-mono`}
            />
          </div>

          {needsBaseUrl ? (
            <div>
              <Label text="接口地址 *" hint="只填到域名，/v1/chat/completions 客户端自己拼" />
              <input
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                spellCheck={false}
                placeholder={isPublic ? 'https://api.deepseek.com' : 'http://10.0.0.8:8000'}
                className={`${INPUT} font-mono`}
              />
              {baseUrlClean && (
                <div
                  className={`mt-1 text-[11px] font-mono truncate ${
                    baseUrlOk ? 'text-[#94a3b8]' : 'text-[#b45309]'
                  }`}
                >
                  {baseUrlOk
                    ? `实际请求 ${baseUrlClean}/v1/chat/completions`
                    : '末尾不要带 /v1 —— 客户端会自己拼 /v1/chat/completions'}
                </div>
              )}
            </div>
          ) : (
            <div className="text-[12px] text-[#94a3b8]">
              Anthropic 协议走固定的官方端点，不需要接口地址。
            </div>
          )}

          {isPublic && (
            <div>
              <Label
                text={isEdit ? 'API Key' : 'API Key *'}
                hint={isEdit ? (hasStoredKey ? '已填过，不点「替换」就不会改动它' : '未配置') : ''}
              />
              <div className="flex items-center gap-2">
                {keyReadOnly ? (
                  // 只读展示块而不是 disabled input：不可聚焦，就不可能被误敲进去一个字
                  <div className={`${INPUT} font-mono flex-1 bg-[#f8fafc] text-[#334155] truncate`}>
                    {keyHint}
                  </div>
                ) : (
                  <input
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    // 新增态由「显示名」那个 autoFocus 占着，这里只在点开「替换」时才抢焦点
                    autoFocus={isEdit}
                    autoComplete="new-password"
                    spellCheck={false}
                    placeholder={isEdit ? '粘贴新的 key' : '端点不给的话留空'}
                    className={`${INPUT} font-mono flex-1`}
                  />
                )}
                {hasStoredKey && (
                  <button
                    onClick={() => {
                      setReplacingKey(!replacingKey)
                      setApiKey('') // 两个方向都清空：切进来是空框，切回去不留半截输入
                    }}
                    className="text-[12px] text-[#047857] bg-transparent border-none cursor-pointer p-0 hover:underline shrink-0"
                  >
                    {replacingKey ? '取消' : '替换'}
                  </button>
                )}
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label text="上下文窗口 *" hint="token，模型能力" />
              <input
                value={contextWindow}
                onChange={(e) => setContextWindow(e.target.value)}
                inputMode="numeric"
                className={INPUT}
              />
            </div>
            <div>
              <Label text="压缩阈值" hint="token，留空 = 按窗口折算" />
              <input
                value={compressThreshold}
                onChange={(e) => setCompressThreshold(e.target.value)}
                inputMode="numeric"
                placeholder="留空 = 自动"
                className={INPUT}
              />
              {thresholdRaw && !thresholdOk && (
                <p className="mt-1 text-[11px] text-[#b45309]">
                  需 ≥ 1024 且不超过上下文窗口 {Number.isFinite(window_) ? window_.toLocaleString() : '—'}
                </p>
              )}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label text="最大输出" hint="token" />
              <input
                value={maxOutput}
                onChange={(e) => setMaxOutput(e.target.value)}
                inputMode="numeric"
                className={INPUT}
              />
            </div>
            <div>
              <Label text="超时" hint="秒" />
              <input
                value={timeoutSec}
                onChange={(e) => setTimeoutSec(e.target.value)}
                inputMode="numeric"
                className={INPUT}
              />
            </div>
          </div>

          {isPublic && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label text="输入单价" hint="元 / 百万 token，留空不计费" />
                <input
                  value={priceIn}
                  onChange={(e) => setPriceIn(e.target.value)}
                  inputMode="decimal"
                  placeholder="留空"
                  className={INPUT}
                />
              </div>
              <div>
                <Label text="输出单价" hint="元 / 百万 token" />
                <input
                  value={priceOut}
                  onChange={(e) => setPriceOut(e.target.value)}
                  inputMode="decimal"
                  placeholder="留空"
                  className={INPUT}
                />
              </div>
            </div>
          )}

          {!isPublic && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label text="最大并发" hint="0 = 不限" />
                <input
                  value={maxConcurrency}
                  onChange={(e) => setMaxConcurrency(e.target.value)}
                  inputMode="numeric"
                  className={INPUT}
                />
              </div>
              <div>
                <Label text="失败重试次数" />
                <input
                  value={maxRetries}
                  onChange={(e) => setMaxRetries(e.target.value)}
                  inputMode="numeric"
                  className={INPUT}
                />
              </div>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-4 pt-1">
            <label className="flex items-center gap-1.5 text-[12px] text-[#64748b] cursor-pointer select-none">
              <input type="checkbox" checked={supportsTools} onChange={(e) => setSupportsTools(e.target.checked)} />
              是否工具调用
            </label>
            <label className="flex items-center gap-1.5 text-[12px] text-[#64748b] cursor-pointer select-none">
              <input type="checkbox" checked={supportsThinking} onChange={(e) => setSupportsThinking(e.target.checked)} />
              是否思考
            </label>
            {supportsThinking && protocol === 'anthropic' && (
              <span className="flex items-center gap-1.5 text-[12px] text-[#64748b]">
                思考预算
                <input
                  value={thinkingBudget}
                  onChange={(e) => setThinkingBudget(e.target.value)}
                  inputMode="numeric"
                  className="w-[80px] px-2 py-1 border border-[#e2e8f0] rounded text-[12px] outline-none focus:border-[#a7f3d0]"
                />
              </span>
            )}
          </div>

          <div>
            <Label text="备注" />
            <input
              value={remark}
              onChange={(e) => setRemark(e.target.value)}
              spellCheck={false}
              className={INPUT}
            />
          </div>

          <div className="text-[12px] text-[#94a3b8] leading-relaxed border-t border-[#f1f5f9] pt-2.5">
            上下文窗口是模型能力声明；用量达到压缩阈值才触发压缩，阈值留空则按窗口的 80%
            （问答模式 55%）折算。
            <br />
            {protocol === 'openai_compatible'
              ? '请求体按 OpenAI 兼容格式拼装，思考开关与工具调用由「支持思考 / 支持工具调用」决定 —— 端点不认哪个就关掉哪个。'
              : '历史消息按 Anthropic 的内容块格式转换，与 OpenAI 兼容模型的历史不通用。'}
          </div>
        </div>

        <div className="flex items-center justify-between gap-2 mt-5">
          <label className="flex items-center gap-1.5 text-[12px] text-[#64748b] cursor-pointer select-none">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            启用
            {!enabled && (
              <span className="text-[#94a3b8]">停用后新会话选不到它，存量会话会回落到默认模型</span>
            )}
          </label>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="px-4 py-1.5 rounded-md text-[13px] border border-[#e2e8f0] text-[#64748b] cursor-pointer hover:bg-[#f8fafc]"
            >
              取消
            </button>
            <button
              onClick={() => void handleSubmit()}
              disabled={!isValid || submitting}
              className={`px-4 py-1.5 rounded-md text-[13px] font-medium cursor-pointer border ${
                isValid && !submitting
                  ? 'border-[#a7f3d0] text-[#047857] bg-[#f0fdf4] hover:bg-[#a7f3d0]'
                  : 'border-[#e2e8f0] text-[#cbd5e1] bg-[#f8fafc] cursor-not-allowed'
              }`}
            >
              {submitting ? '保存中…' : '确定'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
