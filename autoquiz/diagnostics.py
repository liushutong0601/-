from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ErrorGuidance:
    title: str
    cause: str
    solution: str


def explain_error(message: str) -> ErrorGuidance:
    text = message.strip() or "未知错误"
    lowered = text.lower()

    if "api key" in lowered or "401" in text or "invalid authentication" in lowered:
        return ErrorGuidance(
            "API Key 缺失或无效",
            "没有填写 Key、Key 复制不完整，或 Key 与所选服务商不匹配。",
            "检查服务商选择，到对应开放平台重新复制完整 Key，然后重新开始。",
        )
    if "404" in text or "not found the model" in lowered or "permission denied" in lowered:
        return ErrorGuidance(
            "模型不存在或账号无权限",
            text,
            "在高级设置中恢复默认模型（Kimi 为 kimi-k2.6，DeepSeek 为 deepseek-flash），并检查账号是否开通该模型。",
        )
    if "429" in text or "频率" in text or "rate_limit" in lowered or "max rpm" in lowered:
        return ErrorGuidance(
            "API 请求频率持续受限",
            "账号或组织单位时间内的请求额度已经用完，自动重试后仍未恢复。",
            "等待 1～2 分钟后点击“重新检测并继续”。软件已自动限速，无需反复快速重启。",
        )
    if "未检测到学习通小节" in text or "登录" in text or "没有可用标签页" in text:
        return ErrorGuidance(
            "未进入可答题的学习通页面",
            text,
            "在软件专用 Edge 中登录学习通，打开课程并进入准备开始答题的小节，再点击“重新检测并继续”。",
        )
    if "edge" in lowered or "调试" in text or "浏览器" in text:
        return ErrorGuidance(
            "浏览器启动或连接失败",
            text,
            "点击“启动学习通浏览器”；若仍失败，关闭软件专用 Edge 窗口后重试，并恢复调试地址 http://127.0.0.1:9222。",
        )
    if "提交" in text:
        return ErrorGuidance(
            "提交结果需要人工确认",
            text,
            "先检查网页当前状态，不要重复点击提交。若未提交，点击“重新检测并继续”；若已提交，等目录出现绿勾后再继续。",
        )
    if "复核" in text or "填写" in text or "有效答案" in text:
        return ErrorGuidance(
            "答案填写或复核未通过",
            text,
            "程序已禁止提交。点击“重新检测并继续”；若某小节反复失败，请跳过该小节并导出日志检查题型。",
        )
    if "测验" in text or "题目" in text or "页面" in text:
        return ErrorGuidance(
            "章节或题目页面识别失败",
            text,
            "保持网络稳定，不要手动刷新页面，点击“重新检测并继续”。仍失败时打开完整日志并复制错误信息。",
        )
    return ErrorGuidance(
        "任务已暂停",
        text,
        "根据上方原因处理后点击“重新检测并继续”；无法判断时打开完整日志并复制错误信息。",
    )
