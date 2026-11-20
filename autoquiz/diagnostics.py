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

    if "api 连接失败" in lowered or "connection error" in lowered or "api 请求超时" in lowered:
        if "tls 证书校验失败" in lowered:
            solution = "检查 Windows 的日期时间和当前代理的证书设置，连接恢复后点击“重新检测并继续”。"
        elif "服务返回 http" in lowered:
            solution = "等待 API 服务恢复后点击“重新检测并继续”，保留浏览器的当前测验页面。"
        else:
            solution = "检查网络和代理连接。如果正在使用代理，确认它仍在运行并能访问 API 服务。连接恢复后保留当前页面，点击“重新检测并继续”。"
        return ErrorGuidance("无法连接 AI 服务", text, solution)

    if "NetAcad 要求重新启动课程以记录进度" in text:
        return ErrorGuidance(
            "需要重新进入 NetAcad 课程",
            text,
            "在专用 Edge 中回到 NetAcad 课程目录，重新进入课程和当前测验，再点击“重新检测并继续”。",
        )

    if "保存状态未知" in text or "已保存题目" in text:
        return ErrorGuidance(
            "中途续答状态未核实",
            text,
            "请停在最后一个已解锁且未作答的题目后点击“重新检测并继续”。"
            "若仍失败，检查此前题目的已作答标记和现有答案，并复制完整日志；不要提交空卷。",
        )

    if "当前测验已显示 100%" in text:
        return ErrorGuidance(
            "当前测验已满分",
            text,
            "在专用 Edge 进入下一待完成节点后继续；当前试卷已有满分成绩，无需重新答题或提交。",
        )

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
            "点击“启动专用浏览器”；若仍失败，关闭软件专用 Edge 窗口后重试，并恢复调试地址 http://127.0.0.1:9222。",
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
