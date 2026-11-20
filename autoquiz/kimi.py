from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from contextlib import closing
from threading import Event

from .answers import normalize_answer
from .browser import BrowserStopped
from .models import Question, Quiz, question_to_dict


class SolverError(RuntimeError):
    pass


class AISolver:
    display_name = "AI"
    default_model = ""
    base_url = ""
    env_var = ""
    request_interval = 0.0
    max_rate_limit_retries = 4
    max_connection_retries = 2

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        logger: Callable[[str], None] | None = None,
        stop_event: Event | None = None,
    ) -> None:
        self.api_key = (api_key or os.environ.get(self.env_var) or "").strip()
        self.model = (model or "").strip() or self.default_model
        self.logger = logger or (lambda _message: None)
        self.stop_event = stop_event
        self._last_request_started = 0.0
        if not self.api_key:
            raise SolverError(
                f"未配置 {self.display_name} API Key，请填写 API Key 或设置 {self.env_var}。"
            )
        if not self.api_key.isascii():
            raise SolverError(
                "API Key 含有中文或其他非英文字符。请回到对应开放平台重新复制完整 Key，"
                "不要包含“API Key”、中文冒号、引号或说明文字。"
            )

    def _client(self):
        try:
            from openai import OpenAI
            try:
                from openai import Timeout
            except ImportError:
                from httpx import Timeout  # 较旧的 SDK 使用 httpx 提供的 Timeout。
        except ImportError as exc:
            raise SolverError(
                "缺少 openai 运行组件。源码运行请安装 requirements.txt；"
                "免安装软件请重新下载并完整解压新版软件包。"
            ) from exc
        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=Timeout(120.0, connect=10.0),
            max_retries=0,  # 重试由答题器统一记录和取消。
        )

    @staticmethod
    def _instruction() -> str:
        return (
            "请独立完成以下题目，不检索题库。只返回一个 JSON 对象，不要输出解析、Markdown 或其他文字。\n"
            "格式：{\"answers\":[{\"number\":题号,\"answer\":\"选项字母\"}]}。\n"
            "单选题和判断题只能返回一个字母；多选题返回全部应选字母，例如 AC；"
            "不得返回题目中不存在的选项。无法确定时 answer 返回空字符串。"
            "excluded_answers 是本轮禁止返回的完整字母组合，不能重复；"
            "多选题被排除的完整组合不表示其中每个选项都错。"
            "答案 number 必须使用消息标注的题号，不使用截图中的原网页编号。"
        )

    @classmethod
    def _message_content(cls, questions: list[Question]):
        if not any(question.screenshot_b64 for question in questions):
            payload = [question_to_dict(question) for question in questions]
            return f"{cls._instruction()}\n题目如下：\n{json.dumps(payload, ensure_ascii=False)}"

        content: list[dict] = [{
            "type": "text",
            "text": cls._instruction() + "\n带截图的题目必须以截图中实际显示的文字为准，不要使用任何乱码文本。",
        }]
        for question in questions:
            if question.screenshot_b64:
                content.append({
                    "type": "text",
                    "text": (
                        f"第 {question.number} 题，{question.kind.value}。读取紧随其后的题目截图并作答。"
                        + ("\n" + question.visual_context if question.visual_context else "")
                        + '\n' + json.dumps({k:v for k,v in question_to_dict(question).items()
                            if k in {'number','previous_attempt_feedback','excluded_answers','required_choices'}},ensure_ascii=False)
                    ),
                })
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{question.screenshot_b64}"},
                })
            else:
                content.append({
                    "type": "text",
                    "text": json.dumps(question_to_dict(question), ensure_ascii=False),
                })
        return content

    def _request_options(self) -> dict:
        return {}

    def _wait_for_request_slot(self) -> None:
        self._wait(0)
        if self.request_interval > 0 and self._last_request_started > 0:
            remaining = self.request_interval - (time.monotonic() - self._last_request_started)
            if remaining > 0:
                self.logger(f"为遵守 {self.display_name} 请求频率限制，等待 {remaining:.0f} 秒……")
                self._wait(remaining)
        self._last_request_started = time.monotonic()

    def _wait(self, seconds: float) -> None:
        if self.stop_event is not None:
            if self.stop_event.wait(seconds):
                raise BrowserStopped("已按用户要求停止。")
        elif seconds > 0:
            time.sleep(seconds)

    @staticmethod
    def _connection_detail(error: BaseException) -> str:
        causes = []
        for _ in range(8):
            if error is None or error in causes:
                break
            causes.append(error)
            error = error.__cause__ or error.__context__
        details = ' '.join(f'{type(cause).__name__} {cause}' for cause in causes).lower()
        if 'certificate_verify_failed' in details or 'certificate verify failed' in details:
            return 'TLS 证书校验失败'
        if any(token in details for token in ('getaddrinfo', 'name resolution', 'gaierror', 'name or service not known')):
            return 'DNS 解析失败'
        if 'proxyerror' in details or 'proxy error' in details:
            return '代理连接失败'
        if 'timeout' in details or 'timed out' in details:
            return 'API 请求超时'
        if 'connection refused' in details or 'actively refused' in details or '10061' in details:
            return '连接被拒绝'
        return '连接中断或无法建立'

    @staticmethod
    def _rate_limit_delay(error: str, retry_index: int) -> float:
        match = re.search(
            r"try again after\s+(\d+(?:\.\d+)?)\s+seconds?", error, flags=re.IGNORECASE
        )
        server_delay = float(match.group(1)) if match else 0.0
        backoff = min(60.0, 5.0 * (2**retry_index))
        return max(server_delay, backoff)

    def _request(self, questions: list[Question]) -> dict[int, str]:
        with closing(self._client()) as client:
            return self._request_with_retry(client, questions)

    def _request_with_retry(self, client, questions: list[Question]) -> dict[int, str]:
        from openai import APIConnectionError

        rate_retries = connection_retries = 0
        while True:
            self._wait_for_request_slot()
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": "你是严谨的答题引擎。只依据题目内容作答，并严格输出指定 JSON。",
                        },
                        {"role": "user", "content": self._message_content(questions)},
                    ],
                    **self._request_options(),
                )
            except Exception as exc:
                self._wait(0)
                error = str(exc).replace(self.api_key, '[已隐藏]')
                status = getattr(exc, 'status_code', None)
                if isinstance(exc, APIConnectionError) or status in {408, 409} or (
                        isinstance(status, int) and 500 <= status < 600):
                    detail = self._connection_detail(exc) if isinstance(exc, APIConnectionError) else f'服务返回 HTTP {status}'
                    message = f"{self.display_name} API 连接失败（{detail}）。"
                    if detail == 'TLS 证书校验失败' or connection_retries >= self.max_connection_retries:
                        raise SolverError(message) from exc
                    connection_retries += 1
                    delay = 2 ** connection_retries
                    self.logger(f"{message} {delay} 秒后重试当前 API 请求（{connection_retries}/{self.max_connection_retries}）……")
                    self._wait(delay)
                    continue
                is_rate_limited = (
                    status == 429 or (status is None and (
                        "Error code: 429" in error or "rate_limit" in error.lower() or "max rpm" in error.lower()
                    ))
                )
                if is_rate_limited:
                    if rate_retries >= self.max_rate_limit_retries:
                        raise SolverError(
                            f"{self.display_name} API 请求频率持续受限，"
                            f"自动重试 {self.max_rate_limit_retries} 次后仍失败：{error}"
                        ) from exc
                    delay = self._rate_limit_delay(error, rate_retries)
                    rate_retries += 1
                    self.logger(
                        f"{self.display_name} 请求达到频率上限，{delay:.0f} 秒后自动重试"
                        f"（{rate_retries}/{self.max_rate_limit_retries}）……"
                    )
                    self._wait(delay)
                    continue
                if "404" in error and ("Not found the model" in error or "Permission denied" in error):
                    raise SolverError(
                        f"{self.display_name} 模型 {self.model} 不存在或当前账号无权限。"
                        "请使用官方可用模型；Kimi 默认使用 kimi-k2.6。"
                        f"原始错误：{error}"
                    ) from exc
                raise SolverError(f"{self.display_name} API 调用失败：{error}") from exc
            self._wait(0)
            content = response.choices[0].message.content or ""
            return self._parse_response(content, questions)

    @staticmethod
    def _parse_response(content: str, questions: list[Question]) -> dict[int, str]:
        text = content.strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise SolverError("AI 未返回 JSON 答案。")
        try:
            payload = json.loads(text[start : end + 1])
            raw_answers = payload["answers"]
            if isinstance(raw_answers, dict):
                items = raw_answers.items()
            else:
                items = ((item["number"], item.get("answer", "")) for item in raw_answers)
            by_number = {int(number): str(answer) for number, answer in items}
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise SolverError("AI 返回的答案格式无法解析。") from exc

        result: dict[int, str] = {}
        for question in questions:
            if question.number in by_number:
                result[question.number] = by_number[question.number]
        return result

    def solve(self, quiz: Quiz) -> dict[int, str]:
        if not quiz.questions:
            raise SolverError(f"当前测验没有可发送给 {self.display_name} 的题目。")

        self.logger(f"正在调用 {self.display_name}（{self.model}）解答 {len(quiz.questions)} 道题……")
        raw = self._request(quiz.questions)
        answers: dict[int, str] = {}

        for question in quiz.questions:
            normalized = normalize_answer(question, raw.get(question.number))
            if normalized is not None:
                answers[question.number] = normalized
                continue

            self.logger(f"第 {question.number} 题答案无效，正在单独重试一次……")
            retry_raw = self._request([question])
            retry_normalized = normalize_answer(question, retry_raw.get(question.number))
            if retry_normalized is not None:
                answers[question.number] = retry_normalized
            else:
                self.logger(f"第 {question.number} 题重试后仍无有效答案。")
        return answers


class KimiSolver(AISolver):
    display_name = "Kimi"
    default_model = "kimi-k2.6"
    base_url = "https://api.moonshot.cn/v1"
    env_var = "MOONSHOT_API_KEY"
    # 账号日志显示 organization max RPM: 3，请求起点至少间隔 21 秒。
    request_interval = 21.0

    def _request_options(self) -> dict:
        if self.model == "kimi-k2.6":
            # 官方支持的提速参数：答题只需短 JSON，不启用长思考过程。
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        return {}


class DeepSeekSolver(AISolver):
    display_name = "DeepSeek"
    default_model = "deepseek-flash"
    base_url = "https://api.deepseek.com"
    env_var = "DEEPSEEK_API_KEY"
