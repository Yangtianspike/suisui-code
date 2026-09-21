# 上下文管理硬编码常量（全部不可配置，调整需代码变更）

# 第 1 层：单条工具结果超过此字节数时落盘
SINGLE_RESULT_LIMIT = 50000

# 第 1 层：单条 RoleTool 消息内剩余工具结果聚合字节超过此数时继续落盘
MESSAGE_AGGREGATE_LIMIT = 200000

# 第 2 层：给摘要 LLM 输出预留的 token 空间
SUMMARY_RESERVE = 20000

# 自动触发额外安全余量：防止估算误差与单轮波动
AUTO_SAFETY_MARGIN = 13000

# 手动触发安全余量：仅用于判断摘要请求自身能否容纳
MANUAL_SAFETY_MARGIN = 3000

# 恢复段最多展示的文件数
RECOVERY_FILE_LIMIT = 5

# 单个文件快照 token 上限（超出时保留头部、截掉尾部）
RECOVERY_TOKENS_PER_FILE = 5000

# 摘要后保留近期原文的 token 下界
RECENT_KEEP_TOKENS = 10000

# 摘要后保留近期原文的条数下界
RECENT_KEEP_MESSAGES = 5

# 自动摘要连续失败熔断阈值
MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES = 3

# 摘要请求自身 PTL 时的直接重试次数（每次丢最旧 1 组）
PTL_RETRY_LIMIT = 3

# 直接重试用尽后每次丢弃剩余组数的比例
PTL_DROP_PERCENTAGE = 0.2

# 增量估算的字符数 / token 比值
ESTIMATE_CHARS_PER_TOKEN = 3.5

# 预览体头部字节数上限
PREVIEW_HEAD_BYTES = 2048

# 预览体头部行数上限
PREVIEW_HEAD_LINES = 20
