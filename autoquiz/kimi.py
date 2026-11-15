from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable

from .answers import normalize_answer
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

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.api_key = (api_key or os.environ.get(self.env_var) or "").strip()
        self.model = (model or "").strip() or self.default_model
        self.logger = logger or (lambda _message: None)
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
        except ImportError as exc:
            raise SolverError(
                "缺少 openai 运行组件。源码运行请安装 requirements.txt；"
                "免安装软件请重新下载并完整解压新版软件包。"
            ) from exc
        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=120.0,
        )

    @staticmethod
    def _instruction() -> str:
        return (
            "请独立完成以下题目，不检索题库。只返回一个 JSON 对象，不要输出解析、Markdown 或其他文字。\n"
            "格式：{\"answers\":[{\"number\":题号,\"answer\":\"选项字母\"}]}。\n"
            "单选题和判断题只能返回一个字母；多选题返回全部应选字母，例如 AC；"
            "不得返回题目中不存在的选项。无法确定时 answer 返回空字符串。"
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
                    "text": f"第 {question.number} 题，{question.kind.value}。读取紧随其后的题目截图并作答。",
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
        if self.request_interval > 0 and self._last_request_started > 0:
            remaining = self.request_interval - (time.monotonic() - self._last_request_started)
            if remaining > 0:
                self.logger(f"为遵守 {self.display_name} 请求频率限制，等待 {remaining:.0f} 秒……")
                time.sleep(remaining)
        self._last_request_started = time.monotonic()

    @staticmethod
    def _rate_limit_delay(error: str, retry_index: int) -> float:
        match = re.search(
            r"try again after\s+(\d+(?:\.\d+)?)\s+seconds?", error, flags=re.IGNORECASE
        )
        server_delay = float(match.group(1)) if match else 0.0
        backoff = min(60.0, 5.0 * (2**retry_index))
        return max(server_delay, backoff)

    def _request(self, questions: list[Question]) -> dict[int, str]:
        for attempt in range(self.max_rate_limit_retries + 1):
            self._wait_for_request_slot()
            try:
                response = self._client().chat.completions.create(
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
                content = response.choices[0].message.content or ""
                return self._parse_response(content, questions)
            except Exception as exc:
                error = str(exc)
                is_rate_limited = (
                    "429" in error
                    or "rate_limit" in error.lower()
                    or "max rpm" in error.lower()
                )
                if is_rate_limited:
                    if attempt >= self.max_rate_limit_retries:
                        raise SolverError(
                            f"{self.display_name} API 请求频率持续受限，"
                            f"自动重试 {self.max_rate_limit_retries} 次后仍失败：{error}"
                        ) from exc
                    delay = self._rate_limit_delay(error, attempt)
                    self.logger(
                        f"{self.display_name} 请求达到频率上限，{delay:.0f} 秒后自动重试"
                        f"（{attempt + 1}/{self.max_rate_limit_retries}）……"
                    )
                    time.sleep(delay)
                    continue
                if "404" in error and ("Not found the model" in error or "Permission denied" in error):
                    raise SolverError(
                        f"{self.display_name} 模型 {self.model} 不存在或当前账号无权限。"
                        "请使用官方可用模型；Kimi 默认使用 kimi-k2.6。"
                        f"原始错误：{error}"
                    ) from exc
                raise SolverError(f"{self.display_name} API 调用失败：{exc}") from exc
        raise SolverError(f"{self.display_name} API 请求未能完成。")

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
