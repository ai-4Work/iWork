"""生成中度任务测试用例 Excel。

中度 = 3–5 次工具调用组成，跨 ≥2 个工具面，且命中 ≥1 项协作特征（见 docs/chapters/15-测试评估逻辑.md 第2层）。
一条用例把内置工具与 skill 的功能串成一条链，联合完成一个中等任务；本表是这一档的第一批，
后续增删只需往 CASE_DATA 里加/改条目，HEADERS / COLUMN_WIDTHS / apply_style / build / main 均无需改动。

列结构与样式逐项复刻 docs/chapters/简单任务-内置工具+Skill测试用例.xlsx 的 skill sheet。

计数口径：计入 3–5 额度的是**客户端工具往返**（bash / read_file / write_file / edit_file / glob / grep）。
skill(name=…) 只把 SKILL.md 文本注入上下文、不产生客户端往返，故在「参考轨迹」里标出但不计数；
skill 的功能通过 bash 跑其脚本，计 1 次。

用法:
    python i-work/tools/gen_medium_testcases.py

各条命令串均已对 i-work/server/skills/definitions/ 下的真实脚本核对（脚本名、参数、输出位置）；
凡真实行为与 SKILL.md 不符之处，已写进「期望行为」第二段。要点:
  - smart-charts 产出的 HTML 文件名是 {safe_title[:30]}_{md5前6}.html，后缀随内容变，不可预知。
  - smart-charts 把 assets/echarts.min.js 内联进 HTML，离线自包含、无 CDN 外链。
  - image-processor 的 --compress 在 Pillow 路径下只对 JPEG 输出生效，PNG 上等价空操作。
  - image-processor 默认输出名 <stem>_processed.<ext>，手写 argv 解析（不是 argparse）。
  - summarize 的 --length 默认即 medium（3–6 句 / 600 字内），产物只到 stdout，不落文件。
  - pdf 的表格抽取按 SKILL.md 推荐走 pdfplumber 的 page.extract_tables()。
  - browser-skill 要求 session start / stop 成对，stop 在成功与失败路径都要跑。
  - weekly-report 是纯提示词技能（零代码），四段结构 + 明令禁止凭空编造。
  - paddleocr：只有 parse_pdf_direct.py 能正确解析现网 API（内容嵌在
    result.result.layoutParsingResults[].markdown.text）；parse_document.py / extract_fields.py
    读顶层 result["layout"] 等键，输出近乎为空、字段全 null。--output 在前者是目录、后者是文件路径。
  - pptx：add_slide.py（恰好 3 参）/ clean.py（恰好 2 参）是手写 argv 且 SKILL.md 未记载；
    office/pack.py --validate true 单独用是空操作（须同时给 --original）；thumbnail.py --cols 上限 6、超了静默夹紧。
  - imap-smtp-email：手写 parseArgs()，未知 flag 静默接受；--account 被 config.js 先从 argv 剥掉（全局参数）；
    download 落盘用附件自带文件名（不可预知）；结果 JSON 到 stdout、错误到 stderr。
  - editorial-diagrams：只有 validate_svg.py，无任何 flag（--help 会被当文件名）；
    ALLOWED_FONT_SIZES = {8,12,16,20,24,28,32,40} 把 SKILL.md 要求的 sublabel 9px 判为 ERROR。
  - obsidian-cli：无 scripts/ 目录，全靠 PATH 上的 obsidian 二进制（1.12+，须已运行且开了 CLI）；
    参数是 key=value、vault= 必须在子命令之前；move 的目标要带 .md 而其余命令用无扩展名（SKILL.md 自相矛盾）。
  - wecom-unified：主力是外部 wecom-cli（≥1.1.0，须已完成扫码授权；auth init --noninteractive 会阻塞等扫码）；
    build_docx.py 无 --output、输出固定落 {WRITABLE_ROOT}/docx/{spec_stem}.docx，其路径只在 references 里。
  - pdf：操作用 pypdf 代码片段（合并 add_page、水印 merge_page）；图片提取走 poppler 的
    pdfimages -j in.pdf prefix，产物按 prefix-000.jpg 编号命名（可预知）。
  - image-processor 的格式转换 flag 是 --convert（不是 --format）。
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
    "输入 Prompt",
    "期望行为",
    "参考轨迹（完整序列）",
    "协作特征",
    "运行层",
]

COLUMN_WIDTHS = [9, 22, 16, 30, 28, 58, 60, 18, 14]

# 居中列：A 用例编号、I 运行层；其余左对齐
CENTER_COLUMNS = {1, 9}

OUTPUT_NAME = "中度任务-内置工具+Skill测试用例.xlsx"

SHEET_NAME = "中度任务"


CASE_DATA: list[list[str]] = [
    # ── M1 ────────────────────────────────────────────────────────────────
    [
        "M1",
        "read_file + xlsx + smart-charts",
        "CSV 转 Excel 再出趋势图",
        "workspace 有 sales.csv（13 行含表头，列：月份/销售额/成本）；无 out/",
        "把 sales.csv 转成 Excel，再画一张销售额的趋势图",
        "把 sales.csv 原样转成 sales.xlsx，再基于销售额列出趋势图\n"
        "sales.xlsx 存在且列名与行数与 csv 一致（12 行数据）；out/ 下新增一个 "
        "{safe_title}_{6位hex}.html——后缀取自图表内容哈希，不可预知，别按固定名找；"
        "该 HTML 内联了 echarts.min.js，不含 src=\"http 外链；line 图 x 轴为月份、series 为销售额",
        "skill(xlsx) → read_file(sales.csv) → bash(python pandas read_csv → df.to_excel(\"sales.xlsx\", index=False)) "
        "→ skill(smart-charts) → bash(python scripts/cli.py sales.xlsx line --x-axis 月份 --y-axis 销售额 --output-dir out) → 结论",
        "读并发 / skill 注入 / 脚本执行",
        "slow + skill",
    ],
    # ── M2 ────────────────────────────────────────────────────────────────
    [
        "M2",
        "glob + read_file + write_file + summarize",
        "挑最长文档摘要落盘",
        "docs/ 下 3 个 .md：a.md 2KB / b.md 5KB / report.md 12KB",
        "docs 里最长的那篇帮我摘要一下，存成 summary.md",
        "先比出最长的一篇，摘要后把摘要正文落成 summary.md\n"
        "summary.md 内容即 summarize 的 stdout（含「【摘要】」段），不是转述或改写；"
        "摘要句数落在 medium 档（3–6 句 / 600 字内，--length 不传时默认就是 medium）；"
        "write_file 单独成写段，未与任何只读调用并发下发；判定「最长」用的是字节数而非文件名长度",
        "glob(docs/*.md) → [读段] bash(wc -c docs/*.md) ∥ read_file(docs/report.md) "
        "→ skill(summarize) → bash(python summarize.py --file docs/report.md --length medium --output markdown) "
        "→ [写段] write_file(summary.md) → 结论",
        "读并发 / 写串行 / skill 注入",
        "slow + skill",
    ],
    # ── M3 ────────────────────────────────────────────────────────────────
    [
        "M3",
        "read_file + pdf + xlsx + smart-charts",
        "PDF 表格导 Excel 再出柱状图",
        "quarterly.pdf（12 页），第 3 页有一张 6 列 × 20 行表格",
        "把 quarterly.pdf 第 3 页的表格导成 Excel，再画个柱状图",
        "只取第 3 页的表格做成 quarterly.xlsx，再出柱状图\n"
        "未把 12 页整份读进上下文（pdfplumber 只取 pages[2]，或 pdftotext 用 -f 3 -l 3）；"
        "quarterly.xlsx 为 20 行 × 6 列（不含表头）；out/ 下 HTML 内联 ECharts 无外链；"
        "抽取的中间件（如 p3.csv）落在 workspace，而不是只存在于某一步的 stdout 里",
        "skill(pdf) → bash(python pdfplumber pages[2].extract_tables() → p3.csv) → read_file(p3.csv) "
        "→ skill(xlsx) → bash(python pandas read_csv → df.to_excel(\"quarterly.xlsx\", index=False)) "
        "→ skill(smart-charts) → bash(python scripts/cli.py quarterly.xlsx bar --output-dir out) → 结论",
        "大文件不整读 / 外部 CLI / 脚本执行",
        "slow + skill",
    ],
    # ── M4 ────────────────────────────────────────────────────────────────
    [
        "M4",
        "glob + image-processor + docx",
        "图片批量压缩后生成多页 Word",
        "images/ 下 5 张 .jpg（共约 18MB）；无 out/",
        "把这些图压一下，然后每张一页放进一个 Word 里",
        "5 张图批量压缩后，生成一份一页一张图的 Word\n"
        "out/ 下 5 张压缩图体积都小于各自原图且 > 0；压缩只走一次批量命令，不是 5 段独立调用；"
        "生成的 docx 通过 scripts/office/validate.py；图片顺序与 glob 排序一致。"
        "注意 --compress 在 Pillow 路径下只对 JPEG 输出生效（PNG 输入上等价空操作），故 fixture 用 jpg",
        "skill(image-processor) → glob(images/*.jpg) "
        "→ bash(for f in images/*.jpg; do python scripts/process-image.py \"$f\" --compress 70 "
        "--output \"out/$(basename \"$f\" .jpg)_small.jpg\"; done) "
        "→ skill(docx) → bash(node docx-js：5 个段落各插一张 ImageRun → photos.docx) "
        "→ bash(python scripts/office/validate.py photos.docx) → 结论",
        "脚本执行 / 批量幂等 / 写串行",
        "slow + skill",
    ],
    # ── M5 ────────────────────────────────────────────────────────────────
    [
        "M5",
        "glob + grep + read_file + edit_file",
        "定位唯一配置项并修改后复查",
        "conf/ 下 4 个 .ini；只有 app.ini 含 debug=false",
        "找到哪个配置文件里 debug=false，改成 true，然后确认一下",
        "先定位到唯一命中的 app.ini 再改，改完复查确认\n"
        "3 个只读调用（glob/grep/read_file）可并发，edit_file 必须单独成写段；"
        "只有 conf/app.ini 被改，其余 3 个文件字节未变；edit_file 的 old_string 在该文件内唯一"
        "（不得触发 multiple matches 报错）；复查输出 debug=true，行号与改动点一致",
        "glob(conf/*.ini) → [读段] grep(\"debug=\", conf/) ∥ read_file(conf/app.ini) "
        "→ [写段] edit_file(conf/app.ini：debug=false → debug=true) "
        "→ [读段] bash(grep -n \"debug=\" conf/*.ini) → 结论",
        "读并发 / 写串行 / 幂等重放",
        "slow + skill",
    ],
    # ── M6 ────────────────────────────────────────────────────────────────
    [
        "M6",
        "bash + read_file + edit_file",
        "大日志定位错误并修配置",
        "app.log 8MB（其中 37 行 ERROR）；conf/app.ini 的 pool_size=5",
        "看下 app.log 报什么错，然后修一下",
        "从日志里定位到错误原因，据此改掉对应配置项\n"
        "不出现 read_file(app.log)——8MB 不得整读进上下文，用 grep/tail 取片段；"
        "改的配置项与日志错误指向一致（pool_size）；edit_file 只动该一行，未顺手改其他配置；"
        "若改完日志仍报同一错误，走换路而不是原地重试同一入参",
        "bash(grep -c ERROR app.log) → [读段] bash(tail -n 50 app.log) ∥ read_file(conf/app.ini) "
        "→ [写段] edit_file(conf/app.ini：pool_size=5 → 20) → 结论",
        "大文件不整读 / 读并发 / 写串行",
        "slow + skill",
    ],
    # ── M7 ────────────────────────────────────────────────────────────────
    [
        "M7",
        "browser-skill + image-processor",
        "网页截图后压缩归档",
        "已装 bsk CLI 与浏览器扩展、Chromium 已登录；目标页可访问",
        "打开 <url> 截个图，压一下放到 out/",
        "截到整页截图，压缩成更小的文件放进 out/\n"
        "session start 与 session stop 成对出现，stop 在成功与失败路径都要跑；"
        "导航后先 observe 再截图，不猜元素；out/ 下同时有原始截图与压缩图，压缩图体积更小且 > 0；"
        "不复用他人 tab，未凭空发明 tab id",
        "skill(browser-skill) → bash(bsk session start) → bash(bsk navigate <url> --session <id>) "
        "→ bash(bsk screenshot --out out/page.png --session <id>) → bash(bsk session stop <id>) "
        "→ skill(image-processor) → bash(python scripts/process-image.py out/page.png --compress 70 "
        "--output out/page_small.jpg) → 结论",
        "外部 CLI / 有始有终（start↔stop）/ 写串行",
        "slow + skill",
    ],
    # ── M8 ────────────────────────────────────────────────────────────────
    [
        "M8",
        "read_file + weekly-report + docx",
        "零散记录整理成周报 Word",
        "notes/week.txt 含 9 条零散记录（其中 2 条重复、1 条无日期）",
        "把 notes/week.txt 整理成周报，输出成 Word 给我",
        "零散记录去重补全后，按周报模板写成 Word\n"
        "weekly.docx 通过 scripts/office/validate.py；正文含四段结构"
        "（本周完成 / 进行中 / 问题与风险 / 下周计划）；那 2 条重复记录合并为 1 条；"
        "未出现 notes/week.txt 之外的内容——weekly-report 明令禁止凭空编造；"
        "记录不足 3 项时应先向用户提问而非硬编",
        "read_file(notes/week.txt) → skill(weekly-report) → [提示词生成四段式周报] "
        "→ skill(docx) → bash(node docx-js: sections → weekly.docx) "
        "→ bash(python scripts/office/validate.py weekly.docx) → 结论",
        "提示词 / 示例代码 / 写串行",
        "slow + skill",
    ],
    # ── M9 ────────────────────────────────────────────────────────────────
    [
        "M9",
        "glob + paddleocr + read_file + summarize",
        "扫描件走 OCR 再摘要",
        "scans/invoice_scan.pdf（3 页扫描件，无文本层）；已配 PADDLEOCR_API_URL / PADDLEOCR_ACCESS_TOKEN 且可联网",
        "scans 里那个扫描件帮我认一下字，再给我个摘要",
        "先 OCR 出 markdown，再基于 OCR 文本出摘要\n"
        "只调用 scripts/parse_pdf_direct.py，不用 parse_document.py / extract_fields.py"
        "（后两者对现网 API 的嵌套响应结构不兼容，输出近乎为空、字段全 null）；"
        "该脚本的 --output 是目录语义，页文件按 page_1.md / page_2.md 固定命名；未把原 PDF 整读进上下文",
        "glob(scans/*.pdf) → skill(paddleocr) → bash(python scripts/parse_pdf_direct.py scans/invoice_scan.pdf "
        "--output out/ocr --format markdown) → read_file(out/ocr/page_1.md) → skill(summarize) "
        "→ bash(python summarize.py --file out/ocr/page_1.md --length medium --output markdown) → 结论",
        "大文件不整读 / skill 注入 / 脚本执行",
        "slow + skill",
    ],
    # ── M10 ───────────────────────────────────────────────────────────────
    [
        "M10",
        "glob + paddleocr + read_file + xlsx",
        "发票截图批量 OCR 汇总",
        "invoices/ 下 6 张 png 发票截图；已配 PADDLEOCR_API_URL / PADDLEOCR_ACCESS_TOKEN 且可联网",
        "invoices 里这几张发票截图，把关键信息抽出来整成一张 Excel",
        "6 张图批量 OCR 后汇总成一张 Excel\n"
        "只跑一次批量循环命令，不是 6 段独立调用（幂等重放不重复识别）；"
        "用 parse_pdf_direct.py --format json 逐张出结果，不用 extract_fields.py"
        "（对现网 API 响应结构不兼容，抽的字段全 null）；invoices.xlsx 数据行数 = 图片张数 6，每行一张票",
        "glob(invoices/*.png) → skill(paddleocr) → bash(for f in invoices/*.png; do python "
        "scripts/parse_pdf_direct.py \"$f\" --output out/ocr --format json; done) → read_file(out/ocr/page_1.json) "
        "→ skill(xlsx) → bash(python pandas: 合并 out/ocr/*.json → invoices.xlsx) → 结论",
        "批量幂等 / 脚本执行 / 大文件不整读",
        "slow + skill",
    ],
    # ── M11 ───────────────────────────────────────────────────────────────
    [
        "M11",
        "paddleocr + write_file",
        "扫描件 OCR 结果整理成报告",
        "report_scan.pdf（5 页扫描件）；已配 PADDLEOCR_API_URL / PADDLEOCR_ACCESS_TOKEN 且可联网",
        "把 report_scan.pdf 扫描版认一下字，整理成一份 markdown 报告存下来",
        "5 页 OCR 后整理成一份 markdown 报告落盘\n"
        "--output 是目录语义（5 页各出 page_N.md），不是单个文件路径——"
        "这一点与 parse_document.py 的 --output 语义相反（后者是文件路径），两脚本别混用；"
        "未把原 PDF 整读进上下文；write_file 单独成写段",
        "skill(paddleocr) → bash(python scripts/parse_pdf_direct.py report_scan.pdf --output out/ocr --format markdown) "
        "→ bash(cat out/ocr/page_*.md) → [写段] write_file(report_ocr.md) → 结论",
        "写串行 / 脚本执行 / 大文件不整读",
        "slow + skill",
    ],
    # ── M12 ───────────────────────────────────────────────────────────────
    [
        "M12",
        "read_file + write_file + pptx",
        "按大纲生成演示文稿",
        "outline.md（1 个 H1 + 5 个 H2 要点）；已装 pptxgenjs(npm -g)、LibreOffice soffice、Poppler pdftoppm",
        "照 outline.md 给我做一份 PPT",
        "按大纲生成 deck.pptx 并自检可解包\n"
        "用 pptxgenjs 从零生成，不是改模板、不走 add_slide.py"
        "（后者是手写 argv、恰好 3 参，且 SKILL.md 里根本未记载）；"
        "生成的 out/deck.pptx 能被 scripts/office/unpack.py 正常解开；标题与章节与 outline.md 一一对应",
        "read_file(outline.md) → skill(pptx) → [写段] write_file(build_deck.js) "
        "→ bash(node build_deck.js → out/deck.pptx) → bash(python scripts/office/unpack.py out/deck.pptx out/unpacked) → 结论",
        "示例代码 / 写串行 / 脚本执行",
        "slow + skill",
    ],
    # ── M13 ───────────────────────────────────────────────────────────────
    [
        "M13",
        "pptx + summarize + write_file",
        "读演示文稿文本再摘要",
        "deck.pptx（12 页，含标题与要点）；已装 markitdown[pptx]",
        "deck.pptx 讲了啥，给我摘要一下存成文件",
        "抽取 pptx 文本后摘要并落盘\n"
        "用 python -m markitdown deck.pptx 抽文本，不徒手解 OOXML；"
        "摘要句数落 medium 档（3–6 句 / 600 字内）；write_file 单独成写段",
        "skill(pptx) → bash(python -m markitdown deck.pptx > out/deck.md) → read_file(out/deck.md) "
        "→ skill(summarize) → bash(python summarize.py --file out/deck.md --length medium --output markdown) "
        "→ [写段] write_file(summary_deck.md) → 结论",
        "读并发 / skill 注入 / 写串行",
        "slow + skill",
    ],
    # ── M14 ───────────────────────────────────────────────────────────────
    [
        "M14",
        "pptx + image-processor",
        "演示文稿缩略图网格压缩",
        "deck.pptx（20 页）；已装 soffice 与 pdftoppm；无 out/",
        "把 deck.pptx 导成一页缩略图看看版式，图压一下放 out/",
        "导出缩略图网格后压缩归档\n"
        "thumbnail.py 的 --cols 上限 6，超了会被静默夹紧并打警告；"
        "20 页超过 cols*(cols+1) 时产出 thumb-1.jpg / thumb-2.jpg 分片，不是单张；"
        "压缩图体积小于原图且 > 0",
        "skill(pptx) → bash(python scripts/thumbnail.py deck.pptx out/thumb) → skill(image-processor) "
        "→ bash(for f in out/thumb*.jpg; do python scripts/process-image.py \"$f\" --compress 70 "
        "--output \"out/$(basename \"$f\" .jpg)_small.jpg\"; done) → bash(ls -l out/*.jpg 复核体积) → 结论",
        "外部 CLI / 脚本执行 / 幂等重放",
        "slow + skill",
    ],
    # ── M15 ───────────────────────────────────────────────────────────────
    [
        "M15",
        "imap-smtp-email + glob + write_file",
        "搜索邮件附件并落盘归档",
        "已配 ~/.config/mail-skills/.env 且联网；收件箱有 ≥2 封带附件的邮件；ALLOWED_WRITE_DIRS 含 workspace",
        "把最近几封带附件的邮件存下来，给我个清单",
        "搜出带附件的邮件，附件落盘并写一份清单\n"
        "node scripts/imap.js search ... 取列表（结果 JSON 到 stdout、错误到 stderr）；"
        "download 落盘文件名取自附件本身、不可预知（别按固定名找）；"
        "--account 是全局参数（config.js 会先从 argv 里把它剥掉）；attachments/index.md 每行一个附件",
        "skill(imap-smtp-email) → bash(node scripts/imap.js search --recent --limit 5) "
        "→ bash(node scripts/imap.js download <uid> --dir attachments) → glob(attachments/*) "
        "→ [写段] write_file(attachments/index.md) → 结论",
        "外部 CLI / 有始有终 / 写串行",
        "slow + skill",
    ],
    # ── M16 ───────────────────────────────────────────────────────────────
    [
        "M16",
        "imap-smtp-email + summarize + write_file",
        "读邮件正文摘要落盘",
        "已配 ~/.config/mail-skills/.env 且联网；收件箱有一封长正文邮件",
        "最近那封长邮件帮我读一下，摘个要点存成文件",
        "取指定邮件正文后摘要落盘\n"
        "用 fetch <uid> 取正文（JSON→stdout），不整读整个邮箱、不把全部邮件列表倒进上下文；"
        "落盘内容即 summarize 的 stdout；摘要句数落 medium 档",
        "skill(imap-smtp-email) → bash(node scripts/imap.js search --recent --limit 1) "
        "→ bash(node scripts/imap.js fetch <uid> > out/mail_body.md) → skill(summarize) "
        "→ bash(python summarize.py --file out/mail_body.md --length medium --output markdown) "
        "→ [写段] write_file(mail_summary.md) → 结论",
        "外部 CLI / skill 注入 / 写串行",
        "slow + skill",
    ],
    # ── M17 ───────────────────────────────────────────────────────────────
    [
        "M17",
        "imap-smtp-email + xlsx + smart-charts",
        "邮箱概览导表出图",
        "已配 ~/.config/mail-skills/.env 且联网；收件箱 ≥20 封",
        "看看我邮件都是谁发来的，出个图",
        "邮件概览导成 Excel 再出一张图\n"
        "用 check --limit 20 取列表（JSON→stdout）；导出的 xlsx 三列 = 发件人 / 主题 / 日期；"
        "out/ 下 HTML 内联 ECharts、无 src=\"http 外链",
        "skill(imap-smtp-email) → bash(node scripts/imap.js check --limit 20) → skill(xlsx) "
        "→ bash(python pandas: 解析 JSON → mail_list.xlsx) → skill(smart-charts) "
        "→ bash(python scripts/cli.py mail_list.xlsx bar --x-axis 发件人 --output-dir out) → 结论",
        "外部 CLI / 脚本执行 / 写串行",
        "slow + skill",
    ],
    # ── M18 ───────────────────────────────────────────────────────────────
    [
        "M18",
        "read_file + write_file + editorial-diagrams",
        "按说明画架构图并过校验",
        "system.md 描述 5 个组件及其调用关系；Python 3 标准库即可、无网络",
        "照 system.md 画一张系统架构图",
        "产出通过校验的自包含架构图\n"
        "产物是自包含 HTML（内嵌 SVG，不引用外部 JS/CSS）；"
        "python scripts/validate_svg.py out/arch.html 退出码 0；"
        "字号只取白名单 {8,12,16,20,24,28,32,40}——SKILL.md 排版表要求的 sublabel 9px "
        "会被校验器判 ERROR 并整体退出 1，照文档写必然失败",
        "read_file(system.md) → skill(editorial-diagrams) → [写段] write_file(out/arch.html) "
        "→ bash(python scripts/validate_svg.py out/arch.html) → 结论",
        "示例代码 / 外部 CLI / 写串行",
        "slow + skill",
    ],
    # ── M19 ───────────────────────────────────────────────────────────────
    [
        "M19",
        "read_file + write_file + editorial-diagrams",
        "按调用链画时序图",
        "src/ 下 3 个文件构成一条调用链；Python 3 标准库即可、无网络",
        "看下 src 里的调用顺序，画张时序图",
        "按源码调用顺序画时序图并过校验\n"
        "只读必要片段、不整读全部源文件；validate_svg.py 对传入的每个文件逐个校验、"
        "任一 ERROR 即整体退出 1；该脚本没有任何 flag，--help 会被当成文件名",
        "read_file(src/a.py) ∥ read_file(src/b.py) → skill(editorial-diagrams) "
        "→ [写段] write_file(out/sequence.svg) → bash(python scripts/validate_svg.py out/sequence.svg) → 结论",
        "大文件不整读 / 外部 CLI / 写串行",
        "slow + skill",
    ],
    # ── M20 ───────────────────────────────────────────────────────────────
    [
        "M20",
        "read_file + edit_file + editorial-diagrams",
        "改图配色并复验",
        "out/flow.html（已通过校验）；目标配色为蓝色系主色",
        "这张流程图的配色换成蓝色系",
        "改配色后用校验器复验通过\n"
        "只改颜色，不破坏 4px 网格与字号白名单；edit_file 的 old_string 在文件内唯一"
        "（不得触发 multiple matches）；改后重跑 validate_svg.py 退出 0",
        "read_file(out/flow.html) → [写段] edit_file(out/flow.html：主色 → 蓝色系) "
        "→ bash(python scripts/validate_svg.py out/flow.html) → 结论",
        "写串行 / 幂等重放 / 外部 CLI",
        "slow + skill",
    ],
    # ── M21 ───────────────────────────────────────────────────────────────
    [
        "M21",
        "read_file + obsidian-cli",
        "工作记录追加进日记",
        "Obsidian 1.12+ 已安装且正在运行、设置里已开 CLI、obsidian 在 PATH；vault 名已知",
        "把今天这条进展记到我的日记里",
        "读记录后追加进当天日记\n"
        "obsidian vault=<name> daily:append content=\"…\"——vault= 必须紧跟在 obsidian 之后、"
        "在子命令之前，不是放在末尾；参数是 key=value 形式不是 --flag；"
        "该 skill 没有 scripts/ 目录，整条链只靠外部 obsidian 二进制；追加后 daily:read 能看到该条",
        "read_file(notes/today.md) → skill(obsidian-cli) → bash(obsidian vault=MyVault daily:append content=\"…\") "
        "→ bash(obsidian vault=MyVault daily:read) → 结论",
        "外部 CLI / 有始有终 / 写串行",
        "slow + skill",
    ],
    # ── M22 ───────────────────────────────────────────────────────────────
    [
        "M22",
        "obsidian-cli + summarize + write_file",
        "搜索笔记并摘要落盘",
        "同 M21；vault 内有 ≥10 篇笔记",
        "帮我把 vault 里讲部署的笔记找出来，摘要存一份到工作区",
        "搜到相关笔记后摘要，产物落工作区、不进笔记库\n"
        "obsidian search query=\"…\" format=json limit=N 输出 JSON 到 stdout；"
        "read file=<name> 用无扩展名的 wikilink 式名字，path= 才是带 .md 的 vault 相对路径（两者别混）；"
        "write_file 的目标在 workspace 而不是 vault 内",
        "skill(obsidian-cli) → bash(obsidian vault=MyVault search query=\"部署\" format=json limit=5) "
        "→ bash(obsidian vault=MyVault read file=部署笔记) → skill(summarize) "
        "→ bash(python summarize.py --file out/note.md --output markdown) → [写段] write_file(deploy_summary.md) → 结论",
        "外部 CLI / skill 注入 / 写串行",
        "slow + skill",
    ],
    # ── M23 ───────────────────────────────────────────────────────────────
    [
        "M23",
        "read_file + obsidian-cli",
        "从文档建笔记",
        "同 M21；workspace 有 doc.md",
        "把 doc.md 整理成一篇笔记放进 vault",
        "在 vault 里建出新笔记\n"
        "obsidian vault=<name> create name=… content=…（覆盖需显式 overwrite）；"
        "move 的目标要带 .md 而其它命令用无扩展名——SKILL.md 自相矛盾，按 move 的参考写",
        "read_file(doc.md) → skill(obsidian-cli) → bash(obsidian vault=MyVault vault) "
        "→ bash(obsidian vault=MyVault create name=DocNote content=\"…\") → 结论",
        "外部 CLI / 写串行 / 提示词",
        "slow + skill",
    ],
    # ── M24 ───────────────────────────────────────────────────────────────
    [
        "M24",
        "read_file + wecom-unified",
        "查联系人并发送消息",
        "Node + 全局 @wecom/cli ≥1.1.0；已完成扫码授权（auth show --status 输出 authorized）；网络可达",
        "把这条通知发给张伟",
        "查到人后按指定内容发一条消息\n"
        "先 identity whoami / 通讯录查人再 message aibot send --json '{…}'；"
        "输出里不得出现 mail_id / chat_id / docid / cursor 等原始 ID"
        "（SKILL.md 硬规矩，但脚本层无强制、须人工对照）；"
        "auth init --noninteractive 会阻塞等扫码，测试里不可用",
        "read_file(notice.md) → skill(wecom-unified) → bash(wecom-cli identity whoami) "
        "→ bash(wecom-cli message aibot send --json '{\"msg_type\":\"markdown\",\"markdown\":{\"content\":…}}') → 结论",
        "外部 CLI / 有始有终 / 写串行",
        "slow + skill",
    ],
    # ── M25 ───────────────────────────────────────────────────────────────
    [
        "M25",
        "write_file + wecom-unified",
        "建在线表格并写入",
        "同 M24；要写的两行供应商数据在 workspace",
        "给我在企业微信建个表，把 out/vendors.csv 这两个供应商填进去",
        "建出在线表格并写入两行后返回可访问产物\n"
        "写入走 wecom-cli（表格接口）；产物 ID（docid 等）不进入最终输出；"
        "失败时按 SKILL.md 换路重试，不在原地重复同一入参",
        "[写段] write_file(vendors_spec.json) → skill(wecom-unified) → bash(wecom-cli 建表 --json '{…}') "
        "→ bash(wecom-cli 写行 --json '{…}') → 结论",
        "外部 CLI / 有始有终 / 写串行",
        "slow + skill",
    ],
    # ── M26 ───────────────────────────────────────────────────────────────
    [
        "M26",
        "read_file + write_file + wecom-unified",
        "按 JSONL spec 生成 Word",
        "同 M24；Python + python-docx；WRITABLE_ROOT 可写",
        "用 scripts/build_docx.py 把这份纪要规约打成 Word",
        "按 JSONL spec 生成 docx 并复核\n"
        "python scripts/build_docx.py out/report.jsonl → 落 {WRITABLE_ROOT}/docx/report.docx"
        "（无 --output 参数，名字由 spec 文件名决定）；重名时生成 {stem}_{ms}_{pid}.docx；"
        "该脚本路径只在 references 里、SKILL.md 正文没写；它是 argparse，给未知 flag 会 exit 2",
        "read_file(notes/minutes.md) → [写段] write_file(out/report.jsonl) → skill(wecom-unified) "
        "→ bash(python scripts/build_docx.py out/report.jsonl) "
        "→ bash(python -c \"from docx import Document: 打印段落复核\") → 结论",
        "示例代码 / 外部 CLI / 写串行",
        "slow + skill",
    ],
    # ── M27 ───────────────────────────────────────────────────────────────
    [
        "M27",
        "read_file + xlsx",
        "清理脏表并另存",
        "raw.csv（含 3 行重复、2 处空值、1 列日期格式不统一）",
        "raw.csv 太乱了，收拾干净给我一份",
        "清洗后另存为 Excel，原始文件不动\n"
        "重复行合并、空值按列语义处理、日期列格式统一；clean.xlsx 存在且行数 = 原行数 − 重复行数；"
        "raw.csv 字节未变（不得原地覆盖）",
        "read_file(raw.csv) → skill(xlsx) → bash(python pandas: 去重 / 补空 / 统一日期 → clean.xlsx) "
        "→ bash(python pandas: 复核 clean.xlsx 行列数) → 结论",
        "读并发 / 写串行 / 幂等重放",
        "slow + skill",
    ],
    # ── M28 ───────────────────────────────────────────────────────────────
    [
        "M28",
        "glob + read_file + xlsx + smart-charts",
        "汇总多份日报表再出图",
        "daily/ 下 5 个同构 xlsx（列一致、行数不一）",
        "daily 里这几张表合起来，看看总量趋势",
        "5 份同构表合并后出一张趋势图\n"
        "先 glob 再并发读，不逐个串行试探；汇总表行数 = 各表数据行之和；"
        "out/ 下 HTML 内联 ECharts、无外链",
        "glob(daily/*.xlsx) → [读段] read_file(daily/d1.xlsx) ∥ read_file(daily/d2.xlsx) → skill(xlsx) "
        "→ bash(python pandas: concat → daily_all.xlsx) → skill(smart-charts) "
        "→ bash(python scripts/cli.py daily_all.xlsx line --output-dir out) → 结论",
        "读并发 / 脚本执行 / 写串行",
        "slow + skill",
    ],
    # ── M29 ───────────────────────────────────────────────────────────────
    [
        "M29",
        "read_file + smart-charts",
        "同一数据出双图",
        "metrics.xlsx（6 个类目 × 4 个指标）",
        "这张表给我出两张图，一个柱状一个饼图",
        "同一份数据出两张图\n"
        "一次 scripts/cli.py 调用产一张图，两图 = 两次调用；"
        "HTML 文件名 = {safe_title[:30]}_{md5前6}.html，后缀取自内容哈希、不可预知（别按固定名找）；"
        "两份 HTML 都内联 ECharts、无外链",
        "read_file(metrics.xlsx) → skill(smart-charts) → bash(python scripts/cli.py metrics.xlsx bar --output-dir out) "
        "→ bash(python scripts/cli.py metrics.xlsx pie --output-dir out) → 结论",
        "脚本执行 / 写串行 / 外部 CLI",
        "slow + skill",
    ],
    # ── M30 ───────────────────────────────────────────────────────────────
    [
        "M30",
        "glob + read_file + smart-charts",
        "从 JSON 数据出饼图",
        "data/ 下有 share.json（类目 + 占比）",
        "data 里那份数据给我画个饼图",
        "用 JSON 数据出饼图\n"
        "scripts/cli.py 接受 CSV/TSV/TXT/XLSX/XLS/JSON；x 轴为类目、y 轴为占比；"
        "out/ 下 HTML 内联 ECharts、无 src=\"http 外链",
        "glob(data/*) → read_file(data/share.json) → skill(smart-charts) "
        "→ bash(python scripts/cli.py data/share.json pie --x-axis 类目 --y-axis 占比 --output-dir out) → 结论",
        "脚本执行 / 读并发 / 外部 CLI",
        "slow + skill",
    ],
    # ── M31 ───────────────────────────────────────────────────────────────
    [
        "M31",
        "browser-skill + write_file",
        "抓网页正文落盘",
        "已装 bsk CLI 与浏览器扩展、Chromium 已登录；目标页可访问",
        "把那个页面的正文抓下来存成文件",
        "抓到正文并落盘，不把整页塞进上下文\n"
        "session start / stop 成对，stop 在成功与失败路径都要跑；"
        "导航后先 observe 再取内容、不猜元素；write_file 单独成写段",
        "skill(browser-skill) → bash(bsk session start) → bash(bsk navigate <url> --session <id>) "
        "→ bash(bsk observe --session <id>) → bash(bsk session stop <id>) → [写段] write_file(page.md) → 结论",
        "外部 CLI / 大文件不整读 / 写串行",
        "slow + skill",
    ],
    # ── M32 ───────────────────────────────────────────────────────────────
    [
        "M32",
        "read_file + summarize + write_file",
        "长文档分档摘要",
        "long.md 40KB",
        "long.md 太长，给我个短一点的版本",
        "按指定档位摘要并落盘\n"
        "--length 取 short / medium / long 三档，不传默认 medium（3–6 句 / 600 字内）；"
        "summarize 脚本自身不落文件、产物只到 stdout，落盘靠 write_file；摘要字数 < 原文",
        "read_file(long.md) → skill(summarize) → bash(python summarize.py --file long.md --length short --output markdown) "
        "→ [写段] write_file(long_short.md) → 结论",
        "skill 注入 / 写串行 / 幂等重放",
        "slow + skill",
    ],
    # ── M33 ───────────────────────────────────────────────────────────────
    [
        "M33",
        "glob + pdf",
        "合并多份 PDF 并加水印",
        "parts/ 下 4 个 pdf（页数 3/5/2/4）",
        "把这几个 pdf 合成一个，再打上「内部资料」水印",
        "合并后加水印，源文件不动\n"
        "用 pypdf（PdfWriter.add_page 合并、page.merge_page 压水印）；"
        "合并件页数 = 四份之和（14）；每页带「内部资料」水印；parts/ 下四个源文件字节未变",
        "glob(parts/*.pdf) → skill(pdf) → bash(python pypdf: 逐份 add_page → merged.pdf) "
        "→ bash(python pypdf: page.merge_page(水印) → merged_wm.pdf) → bash(python pypdf: 复核总页数) → 结论",
        "外部 CLI / 写串行 / 批量幂等",
        "slow + skill",
    ],
    # ── M34 ───────────────────────────────────────────────────────────────
    [
        "M34",
        "glob + pdf + image-processor",
        "提取 PDF 内嵌图并压缩",
        "report.pdf（含 8 张图，其中 5 张 jpg）；已装 poppler-utils",
        "report.pdf 里的图抠出来，压一下",
        "提取内嵌图后压缩\n"
        "用 pdfimages -j report.pdf out/imgs/report 提取，产物按 {prefix}-000.jpg / -001.jpg 编号命名（可预知）；"
        "--compress 在 Pillow 路径下只对 JPEG 输出生效（PNG 上等价空操作，别把它当失败）；压缩图体积小于原图且 > 0",
        "glob(*.pdf) → skill(pdf) → bash(pdfimages -j report.pdf out/imgs/report) → skill(image-processor) "
        "→ bash(for f in out/imgs/*.jpg; do python scripts/process-image.py \"$f\" --compress 70 "
        "--output \"${f%.jpg}_small.jpg\"; done) → 结论",
        "外部 CLI / 脚本执行 / 写串行",
        "slow + skill",
    ],
    # ── M35 ───────────────────────────────────────────────────────────────
    [
        "M35",
        "glob + docx + xlsx",
        "Word 表格导成 Excel",
        "contract.docx（含 2 张表：3×4 / 20×6）",
        "contract.docx 里的表导出来给我个 Excel",
        "把 Word 里的两张表导成 Excel\n"
        "抽表后行/列数与 docx 一致（3×4 与 20×6）；不整篇正文读进上下文；两张表落同一个 xlsx 的两个 sheet",
        "glob(contract.docx) → skill(docx) → bash(python-docx: 遍历 doc.tables 抽两张表) → skill(xlsx) "
        "→ bash(python pandas: 两 sheet → contract_tables.xlsx) → bash(python pandas: 复核行列数) → 结论",
        "大文件不整读 / 脚本执行 / 读并发",
        "slow + skill",
    ],
    # ── M36 ───────────────────────────────────────────────────────────────
    [
        "M36",
        "read_file + docx",
        "模板查找替换生成新件",
        "template.docx（含 3 处 {{name}} 占位）",
        "按 template.docx 生成给王工的合同，名字替换一下",
        "按模板替换占位后生成新件\n"
        "输出 report.docx，3 处 {{name}} 全部替换；源模板 template.docx 字节未变；"
        "不手改 OOXML 里的 XML，走 docx 技能给的脚本/库",
        "read_file(customer.txt) → skill(docx) → bash(node docx-js / python-docx: 替换 3 处占位 → report.docx) "
        "→ bash(python scripts/office/validate.py report.docx) → 结论",
        "示例代码 / 写串行 / 幂等重放",
        "slow + skill",
    ],
    # ── M37 ───────────────────────────────────────────────────────────────
    [
        "M37",
        "glob + image-processor + write_file",
        "批量加水印并归档清单",
        "photos/ 下 10 张 jpg",
        "这些图都打上公司水印，然后给我个清单",
        "10 张图批量加水印并输出清单\n"
        "只跑一次循环命令而非 10 段独立调用；输出名默认 <stem>_processed.<ext>，源图不动；photo_index.md 每行一图",
        "glob(photos/*.jpg) → skill(image-processor) "
        "→ bash(for f in photos/*.jpg; do python scripts/process-image.py \"$f\" --watermark \"公司\" "
        "--output \"out/$(basename \"$f\")\"; done) → [写段] write_file(photo_index.md) → 结论",
        "批量幂等 / 写串行 / 脚本执行",
        "slow + skill",
    ],
    # ── M38 ───────────────────────────────────────────────────────────────
    [
        "M38",
        "write_file + image-processor + pptx",
        "logo 转格式后插入演示",
        "logo.png；已装 soffice / pptxgenjs",
        "把 logo.png 转成 jpg，插到一页新的 PPT 里",
        "转格式后生成一份含该图的新演示\n"
        "格式转换用 image-processor 的 --convert（引擎自动探测 ffmpeg / ImageMagick），不是 --format；"
        "转换产物先落盘再被 node 脚本引用，不直接读内存里的图；生成的 pptx 能被 office/unpack.py 解开",
        "[写段] write_file(build_logo_deck.js) → skill(image-processor) "
        "→ bash(python scripts/process-image.py logo.png --convert jpg --output out/logo.jpg) → skill(pptx) "
        "→ bash(node build_logo_deck.js → out/logo_deck.pptx) "
        "→ bash(python scripts/office/unpack.py out/logo_deck.pptx out/unpacked) → 结论",
        "外部 CLI / 写串行 / 脚本执行",
        "slow + skill",
    ],
    # ── M39 ───────────────────────────────────────────────────────────────
    [
        "M39",
        "browser-skill + xlsx",
        "抓网页表格导 Excel",
        "已装 bsk CLI 与浏览器扩展、Chromium 已登录；目标页有一张 ≥10 行的表",
        "把那个页面上的表格导成 Excel",
        "抓到表格落成 Excel\n"
        "session start / stop 成对，stop 在成功与失败路径都要跑；导航后先 observe 再取数、不猜元素；"
        "xlsx 行数与页面表一致；不复用他人 tab、未凭空发明 tab id",
        "skill(browser-skill) → bash(bsk session start) → bash(bsk navigate <url> --session <id>) "
        "→ bash(bsk observe --session <id>) → bash(bsk session stop <id>) "
        "→ bash(python pandas: 解析表 → page_table.xlsx) → 结论",
        "外部 CLI / 有始有终 / 写串行",
        "slow + skill",
    ],
    # ── M40 ───────────────────────────────────────────────────────────────
    [
        "M40",
        "browser-skill + write_file",
        "失效 URL 的失败路径与换路重试",
        "已装 bsk + 扩展、Chromium 已登录；dead URL 已失效、backup URL 可用",
        "打开这个页面看看，打不开就换备份地址",
        "失效路径也要收尾，换路后取到内容并记录\n"
        "首访 dead URL 失败时 session stop 仍要执行（不裸奔、不原地重试同一入参）；"
        "不复用他人 tab、不发明 tab id；换 backup URL 后 observe 成功；结果写进 log.md",
        "skill(browser-skill) → bash(bsk session start) → bash(bsk navigate <dead-url> --session <id>) "
        "→ bash(bsk navigate <backup-url> --session <id>) → bash(bsk session stop <id>) "
        "→ [写段] write_file(open_result.md) → 结论",
        "失败恢复 / 有始有终 / 写串行",
        "slow + skill",
    ],
    # ── M41 ───────────────────────────────────────────────────────────────
    [
        "M41",
        "imap-smtp-email + weekly-report + docx",
        "邮件线索整理成周报",
        "已配 IMAP 凭据且联网；收件箱有本周 8 封相关工作邮件；已装 soffice",
        "把我这周的邮件整理成周报，出个 Word",
        "从邮件里提炼线索，按周报模板出 Word\n"
        "只写邮件里出现过的内容（weekly-report 明令禁止凭空编造）；"
        "正文含四段结构（本周完成 / 进行中 / 问题与风险 / 下周计划）；"
        "weekly.docx 过 scripts/office/validate.py；记录不足 3 项时应先提问而非硬编",
        "skill(imap-smtp-email) → bash(node scripts/imap.js search --since <date> --limit 20) "
        "→ bash(node scripts/imap.js fetch <uid> > out/mail.txt) → skill(weekly-report) "
        "→ [提示词生成四段式周报] → [写段] write_file(weekly.md) → skill(docx) "
        "→ bash(node docx-js: sections → weekly.docx) → bash(python scripts/office/validate.py weekly.docx) → 结论",
        "外部 CLI / 提示词 / 写串行",
        "slow + skill",
    ],
    # ── M42 ───────────────────────────────────────────────────────────────
    [
        "M42",
        "obsidian-cli + weekly-report + write_file",
        "日记汇总成周报",
        "Obsidian 已运行且开了 CLI；vault 内有本周 5 篇日记；vault 名已知",
        "把我这周的日记汇总成一份周报存到工作区",
        "汇总成周报并落在工作区\n"
        "只用本周范围内的日记、不越界取更早的；重复项合并；"
        "产物写 workspace 而不是 vault 内（不污染笔记库）；obsidian vault=<name> search/read（vault= 在子命令之前）",
        "skill(obsidian-cli) → bash(obsidian vault=MyVault search query=\"日记\" format=json limit=7) "
        "→ bash(obsidian vault=MyVault read file=日记) → skill(weekly-report) "
        "→ [写段] write_file(weekly_from_diary.md) → 结论",
        "外部 CLI / 提示词 / 写串行",
        "slow + skill",
    ],
    # ── M43 ───────────────────────────────────────────────────────────────
    [
        "M43",
        "read_file + xlsx + smart-charts + docx",
        "数据到图表再到报告",
        "kpi.xlsx（12 个月 × 5 项指标）；无 out/",
        "这份 KPI 数据给我清一下、出张图，再写份月度报告 Word",
        "清洗、出图、成文三件产物齐全\n"
        "clean 后的 kpi_clean.xlsx 无重复行；out/ 下 HTML 内联 ECharts；"
        "报告 docx 过 scripts/office/validate.py，且正文引用的数字与图表数据一致（不得编造）",
        "read_file(kpi.xlsx) → skill(xlsx) → bash(python pandas: 清洗 → kpi_clean.xlsx) → skill(smart-charts) "
        "→ bash(python scripts/cli.py kpi_clean.xlsx line --output-dir out) → skill(docx) "
        "→ bash(node docx-js: 报告 → kpi_report.docx) "
        "→ bash(python scripts/office/validate.py kpi_report.docx) → 结论",
        "脚本执行 / 写串行 / 外部 CLI",
        "slow + skill",
    ],
    # ── M44 ───────────────────────────────────────────────────────────────
    [
        "M44",
        "pdf + editorial-diagrams + write_file",
        "读设计文档画架构图",
        "design.pdf（6 页，第 2 页是系统架构描述）；Python 3 标准库即可",
        "看下 design.pdf 第 2 页，把系统架构画出来",
        "只读目标页后画架构图并通过校验\n"
        "只取第 2 页、不整份读进上下文；python scripts/validate_svg.py out/design_arch.svg 退出码 0；"
        "字号取白名单 {8,12,16,20,24,28,32,40}（sublabel 用 8 或 12，不要照 SKILL.md 写 9px——会被判 ERROR）",
        "skill(pdf) → bash(python pdfplumber pages[1].extract_text() → out/p2.txt) → read_file(out/p2.txt) "
        "→ skill(editorial-diagrams) → [写段] write_file(out/design_arch.svg) "
        "→ bash(python scripts/validate_svg.py out/design_arch.svg) → 结论",
        "大文件不整读 / 外部 CLI / 写串行",
        "slow + skill",
    ],
    # ── M45 ───────────────────────────────────────────────────────────────
    [
        "M45",
        "glob + read_file + write_file + write_memory",
        "从文档记下项目约定",
        "docs/ 下 4 个 .md，其中 decisions.md 含 3 条项目约定",
        "把 docs 里的项目约定记到长期记忆里，顺便在工作区留个索引",
        "约定写进长期记忆，索引落工作区\n"
        "走 write_memory（服务端工具，不产生客户端往返、不计入 3–5 额度）而不是写普通文件；"
        "write_file 单独成写段；重复跑同一条约定不产生重复记忆",
        "glob(docs/*.md) → [读段] read_file(docs/decisions.md) → write_memory(3 条约定) "
        "→ [写段] write_file(memory_index.md) → 结论",
        "记忆写入 / 写串行 / 幂等重放",
        "slow + skill",
    ],
    # ── M46 ───────────────────────────────────────────────────────────────
    [
        "M46",
        "read_file + edit_file + recall",
        "按历史约定改配置",
        "conf/app.yaml；记忆中已存有「超时统一 30s」约定",
        "按我们之前定的规矩把配置改对",
        "召回既定约定后据此改配置\n"
        "recall（服务端工具，不计入 3–5 额度）召回到「超时统一 30s」；"
        "只改该一处、其余字段不动；edit_file 单独成写段、old_string 唯一；改后复核输出与约定一致",
        "recall(query=\"超时约定\") → read_file(conf/app.yaml) → [写段] edit_file(conf/app.yaml：timeout → 30s) "
        "→ bash(grep -n \"timeout\" conf/app.yaml 复核) → 结论",
        "记忆召回 / 写串行 / 读并发",
        "slow + skill",
    ],
    # ── M47 ───────────────────────────────────────────────────────────────
    [
        "M47",
        "glob + grep + bash + write_file",
        "日志巡检出报告",
        "logs/ 下 7 个 .log（合计约 40MB，其中 12 行 ERROR）",
        "看看 logs 里有没有报错，给我个巡检结论",
        "巡检出报告落盘\n"
        "不出现 read_file(任意 .log)——40MB 不得整读进上下文，用 grep -c / tail 取片段；"
        "报告里每个文件的 ERROR 数与 grep -c 输出一致；一次批量命令而非 7 段独立调用",
        "glob(logs/*.log) → bash(for f in logs/*.log; do printf '%s ' \"$f\"; grep -c ERROR \"$f\"; done) "
        "→ bash(tail -n 50 logs/app.log) → [写段] write_file(log_audit.md) → 结论",
        "大文件不整读 / 批量幂等 / 写串行",
        "slow + skill",
    ],
    # ── M48 ───────────────────────────────────────────────────────────────
    [
        "M48",
        "glob + read_file + edit_file",
        "三份配置对齐",
        "conf/ 下 a.conf / b.conf / c.conf；仅 b.conf 的 timeout 与另两份不一致",
        "这三份配置对一下，把不一样的那份改齐",
        "改齐三份配置\n"
        "三个只读调用（glob + 两次 read_file）可并发；edit_file 单独成写段、old_string 在 b.conf 内唯一；"
        "改后三份 timeout 行字节级一致，a.conf / c.conf 未被改动",
        "glob(conf/*.conf) → [读段] read_file(conf/a.conf) ∥ read_file(conf/b.conf) "
        "→ [写段] edit_file(conf/b.conf：timeout=5 → timeout=30) "
        "→ bash(grep -n \"timeout\" conf/*.conf 复核) → 结论",
        "读并发 / 写串行 / 幂等重放",
        "slow + skill",
    ],
    # ── M49 ───────────────────────────────────────────────────────────────
    [
        "M49",
        "read_file + write_file + editorial-diagrams + obsidian-cli",
        "画图并存入笔记库",
        "Obsidian 已运行且开了 CLI；vault 名已知；workspace 有 spec.md",
        "按 spec.md 画张流程图，然后存进我的笔记库",
        "画出并通过校验后存进 vault\n"
        "validate_svg.py 退出 0；obsidian vault=<name> create … ——vault= 必须在子命令之前、是所有参数里的第一个；"
        "笔记 content 引用图的相对路径；vault 外的 write_file 产物保留",
        "read_file(spec.md) → skill(editorial-diagrams) → [写段] write_file(out/flow.svg) "
        "→ bash(python scripts/validate_svg.py out/flow.svg) → skill(obsidian-cli) "
        "→ bash(obsidian vault=MyVault create name=FlowDiagram content=\"![[flow.svg]]\") → 结论",
        "外部 CLI / 有始有终 / 写串行",
        "slow + skill",
    ],
    # ── M50 ───────────────────────────────────────────────────────────────
    [
        "M50",
        "imap-smtp-email + pdf + xlsx + smart-charts",
        "邮件 PDF 附件抽表出图",
        "已配 ~/.config/mail-skills/.env 且联网；收件箱有一封带 monthly.pdf 附件的邮件",
        "最新那封带 PDF 附件的邮件，把里面的表导出来出张图",
        "附件 → 抽表 → 出图一条链\n"
        "附件落盘文件名取自邮件本身、不可预知（别按固定名找）；PDF 只取目标页、不整份读进上下文；"
        "导出的 xlsx 列数与表一致；out/ 下 HTML 内联 ECharts、无外链",
        "skill(imap-smtp-email) → bash(node scripts/imap.js search --recent --limit 1) "
        "→ bash(node scripts/imap.js download <uid> --dir attachments) → skill(pdf) "
        "→ bash(python pdfplumber: 抽表 → monthly.csv) → skill(xlsx) "
        "→ bash(python pandas: → monthly.xlsx) → skill(smart-charts) "
        "→ bash(python scripts/cli.py monthly.xlsx bar --output-dir out) → 结论",
        "外部 CLI / 大文件不整读 / 脚本执行",
        "slow + skill",
    ],
]


HEADER_FILL = PatternFill("solid", fgColor="FF1F4E79")
DATA_FILL = PatternFill("solid", fgColor="FFF2F7FB")
THIN = Side(style="thin")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


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
        ws.row_dimensions[row].height = 60

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
    for row in CASE_DATA:
        calls = count_client_calls(row[6])
        print(f"  {row[0]}  调用 {calls} 次  面 {len(row[1].split(' + '))} 个  {row[2]}")
    print(f"  total: {len(CASE_DATA)} 条")


def count_client_calls(trajectory: str) -> int:
    """数一遍痕迹里的客户端工具往返（skill(...) 不计）。"""
    import re

    return len(re.findall(r"\b(?:read_file|write_file|edit_file|glob|grep|bash)\(", trajectory))


if __name__ == "__main__":
    main()
