from __future__ import annotations

import re
import time
from collections.abc import Mapping
from threading import Event
from typing import Any

from .models import Quiz
from .parser import parse_quiz


class BrowserError(RuntimeError):
    pass


class BrowserStopped(BrowserError):
    pass


_TASK_TAB_SELECTOR = (
    'li[onclick*="changeDisplayContent"], li[cardid], '
    '[role="tab"][title="测验"], [role="tab"][title="章节测验"], [role="tab"][title="章节检测"], '
    '#mainid [role="tab"]'
)
_QUESTION_LOCATOR = ':is(.questionLi, .TiMu):visible:not(:is(.questionLi, .TiMu) *)'
_PREFERRED_QUIZ_TAB_TITLES = {"章节测验": 0, "章节检测": 0, "测验": 1}
_PAGE_WAIT_SECONDS = 30
_NON_QUIZ_STABLE_SECONDS = 2.5
_SUBMIT_WAIT_SECONDS = 45
_POLL_SECONDS = 0.25
_DIALOG_SELECTOR = '.layui-layer, .el-dialog, .modal, .popDiv, .maskDiv, .workpop, [role="dialog"], dialog'
_SUBMIT_LABEL = re.compile(r"^\s*(?:提交|提交答案|提交测验|提交作业)\s*$")
_CONFIRM_LABEL = re.compile(r"^\s*(?:提交|确认提交|确定提交|提交答案|提交测验|提交作业|确认|确定)\s*$")
_CONFIRM_TEXT = re.compile(r"(?:确认|确定|是否)\s*(?:要\s*)?提交")
_READINESS_REQUEST_TYPES = {"document", "xhr", "fetch"}


_TASK_VIEW_JS = r"""
(tabSelector) => {
  const visible = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility === 'visible';
  const ignored = `script, style, template, .posCatalog_level, .posCatalog_select, ${tabSelector}`;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const text = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const value = node.textContent.trim();
    if (value && visible(node.parentElement) && !node.parentElement.closest(ignored)) text.push(value);
  }
  const content = text.join(' ').replace(/\s+/g, ' ');
  const loading = document.readyState === 'loading' ||
    Array.from(document.querySelectorAll('[aria-busy="true"], .loading, .loadingMask, .el-loading-mask')).some(visible) ||
    text.some(value => /^(?:正在加载|加载中|加载失败|请稍候)[\s.。…！!]*$/.test(value));
  const media = Array.from(document.querySelectorAll('video, audio, canvas, img')).some(el =>
    visible(el) && !el.closest(ignored) && (!el.matches('img') || (el.complete && el.naturalWidth > 0))
  );
  return {content, media, loading, documentId: performance.timeOrigin};
}
"""


_EXTRACT_JS = r"""
() => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility === 'visible';
  let nodes = Array.from(document.querySelectorAll('.questionLi, .TiMu')).filter(visible);
  if (!nodes.length) {
    nodes = Array.from(document.querySelectorAll('[id^="question"]')).filter(el =>
      visible(el) && /^question\d+/i.test(el.id) && /【(?:单选题|多选题|判断题|填空题|材料题)】/.test(el.innerText || '')
    );
  }
  nodes = nodes.filter((node, index) => !nodes.some((other, otherIndex) =>
    otherIndex < index && other.contains(node)
  ));

  const questions = nodes.map((node, index) => {
    const fullText = clean(node.innerText);
    const typeMatch = fullText.match(/【([^】]+)】/) || fullText.match(/\[([^\]]+)\]/);
    const rawType = typeMatch ? typeMatch[1] : '';
    const extractStem = value => clean(value)
      .replace(/^\s*\d+\s*[.、]?\s*/, '')
      .replace(/^[【\[][^】\]]+[】\]]\s*/, '')
      .trim();
    const headerCandidates = Array.from(node.querySelectorAll(
      '.mark_name, .questionTitle, .question-title, .questionContent, .question-content, ' +
      '.questionText, .question-text, .questionTxt, .topicContent, .topic_content, .stem'
    ));
    let stem = '';
    for (const candidate of headerCandidates) {
      const candidateStem = extractStem(candidate.innerText);
      if (candidateStem.length > stem.length) stem = candidateStem;
    }
    if (!stem) {
      const clone = node.cloneNode(true);
      clone.querySelectorAll(
        'ul, ol, input, button, .options, .answerOption, .answer-option, .optionItem, .option-item'
      ).forEach(el => el.remove());
      stem = extractStem(clone.innerText);
    }
    const numberMatch = fullText.match(/^\s*(\d+)/);

    const candidates = Array.from(node.querySelectorAll(
      'ul li, .option, .answerOption, .answer-option, .optionItem, .option-item'
    ));
    const options = [];
    const seen = new Set();
    for (const optionNode of candidates) {
      if (optionNode.querySelector('li')) continue;
      const optionText = clean(optionNode.innerText);
      const letterNode = optionNode.querySelector(
        '.check_icon, .option-letter, .optionLetter, .letter, .mark_letter'
      );
      let letter = clean(letterNode ? letterNode.innerText : '').replace(/[.、]/g, '').toUpperCase();
      if (!/^[A-Z]$/.test(letter)) {
        const match = optionText.match(/^\s*([A-Z])(?:\s|[.、]|$)/i);
        letter = match ? match[1].toUpperCase() : '';
      }
      if (!/^[A-Z]$/.test(letter) || seen.has(letter)) continue;
      seen.add(letter);
      const input = optionNode.matches('input') ? optionNode : optionNode.querySelector('input');
      const stateNodes = [optionNode, optionNode.parentElement,
        ...optionNode.querySelectorAll('[class],[aria-checked],[aria-selected]')].filter(Boolean);
      const selected = Boolean(input && input.checked) || stateNodes.some(stateNode => {
        const classText = String(stateNode.className || '');
        return /(?:^|\s)(?:checked|selected|active|on|check_answer|checkAnswer|current|cur)(?:\s|$)/i.test(classText) ||
          stateNode.getAttribute('aria-checked') === 'true' || stateNode.getAttribute('aria-selected') === 'true';
      });
      let text = optionText.replace(new RegExp('^' + letter + '(?:\\s|[.、]|$)', 'i'), '').trim();
      options.push({letter, text, selected});
    }

    return {
      number: numberMatch ? Number(numberMatch[1]) : index + 1,
      raw_type: rawType,
      stem,
      options,
      element_id: node.id || '',
      has_images: Boolean(node.querySelector('img'))
    };
  });

  return {body_text: document.body ? document.body.innerText : '', questions, document_id: performance.timeOrigin};
}
"""

_FALLBACK_QUIZ_JS = r"""
() => Array.from(document.querySelectorAll('[id^="question"]')).some(el =>
  el.getClientRects().length > 0 && getComputedStyle(el).visibility === 'visible' &&
  /^question\d+/i.test(el.id) && /【(?:单选题|多选题|判断题|填空题|材料题)】/.test(el.innerText || '')
)
"""


_FILL_JS = r"""
({answers}) => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  let nodes = Array.from(document.querySelectorAll('.questionLi'));
  if (!nodes.length) {
    nodes = Array.from(document.querySelectorAll('[id]')).filter(el =>
      /^question\d+/i.test(el.id) && /【(?:单选题|多选题|判断题|填空题|材料题)】/.test(el.innerText || '')
    );
  }
  let clicked = 0;
  const missing = [];
  nodes.forEach((node, index) => {
    const fullText = clean(node.innerText);
    const numberMatch = fullText.match(/^\s*(\d+)/);
    const number = numberMatch ? Number(numberMatch[1]) : index + 1;
    const expected = String(answers[number] || answers[String(number)] || '');
    if (!expected) return;

    const candidates = Array.from(node.querySelectorAll(
      'ul li, .option, .answerOption, .answer-option, .optionItem, .option-item'
    ));
    let found = 0;
    for (const optionNode of candidates) {
      if (optionNode.querySelector('li')) continue;
      const optionText = clean(optionNode.innerText);
      const letterNode = optionNode.querySelector(
        '.check_icon, .option-letter, .optionLetter, .letter, .mark_letter'
      );
      let letter = clean(letterNode ? letterNode.innerText : '').replace(/[.、]/g, '').toUpperCase();
      if (!/^[A-Z]$/.test(letter)) {
        const match = optionText.match(/^\s*([A-Z])(?:\s|[.、]|$)/i);
        letter = match ? match[1].toUpperCase() : '';
      }
      if (!/^[A-Z]$/.test(letter)) continue;
      const input = optionNode.matches('input') ? optionNode : optionNode.querySelector('input');
      const stateNodes = [optionNode, optionNode.parentElement,
        ...optionNode.querySelectorAll('[class],[aria-checked],[aria-selected]')].filter(Boolean);
      const selected = Boolean(input && input.checked) || stateNodes.some(stateNode => {
        const classText = String(stateNode.className || '');
        return /(?:^|\s)(?:checked|selected|active|on|check_answer|checkAnswer|current|cur)(?:\s|$)/i.test(classText) ||
          stateNode.getAttribute('aria-checked') === 'true' || stateNode.getAttribute('aria-selected') === 'true';
      });
      const shouldSelect = expected.includes(letter);
      if (selected !== shouldSelect) {
        optionNode.scrollIntoView({block: 'center'});
        if (input) input.click(); else optionNode.click();
        clicked += 1;
      }
      found += 1;
    }
    if (!found) missing.push(number);
  });
  return {clicked, missing};
}
"""


_DIRECTORY_READ_JS = r"""
() => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const seen = new Set();
  const items = [];
  for (const el of document.querySelectorAll('div.posCatalog_select[id^="cur"]')) {
    const chapterId = el.id.replace(/^cur/, '');
    const nameNode = el.querySelector(':scope > .posCatalog_name');
    if (!/^\d+$/.test(chapterId) || !nameNode || seen.has(chapterId)) continue;
    seen.add(chapterId);
    const unfinishedNode = el.querySelector(':scope > .jobUnfinishCount');
    const unfinishedCount = unfinishedNode ? Number(unfinishedNode.value || 0) : 0;
    const completed = Boolean(el.querySelector(':scope > .icon_Completed')) ||
      Array.from(el.querySelectorAll('.prevHoverTips')).some(
        node => clean(node.innerText).startsWith('已完成')
      );
    const pending = unfinishedCount > 0 || Boolean(el.querySelector(':scope > .catalog_points_yi')) ||
      Array.from(el.querySelectorAll('.prevHoverTips')).some(
        node => clean(node.innerText).includes('待完成任务点')
      );
    items.push({
      chapter_id: chapterId,
      number: clean(nameNode.querySelector('.posCatalog_sbar')?.innerText),
      title: clean(nameNode.getAttribute('title') || nameNode.innerText),
      status: completed ? 'completed' : (pending ? 'pending' : 'unknown'),
      active: el.classList.contains('posCatalog_active') ||
        el.getAttribute('aria-current') === 'true',
      index: items.length
    });
  }
  return {items};
}
"""


_DIRECTORY_CLICK_JS = r"""
(chapterId) => {
  const el = document.getElementById('cur' + chapterId);
  if (!el || !el.classList.contains('posCatalog_select')) return false;
  const target = el.querySelector(':scope > .posCatalog_name') || el;
  target.scrollIntoView({block: 'center'});
  target.click();
  return true;
}
"""


_DIRECTORY_JS = r"""
() => {
  const visible = el => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  let items = Array.from(document.querySelectorAll('li, a, div')).filter(el => {
    if (!visible(el) || el.children.length > 8) return false;
    return /^\d+\.\d+\s+/.test(clean(el.innerText));
  });
  items = items.filter((node, index) => !items.some((other, otherIndex) =>
    otherIndex !== index && node.contains(other) && clean(other.innerText) === clean(node.innerText)
  ));
  const active = items.findIndex(el => {
    const classes = `${el.className || ''} ${el.parentElement?.className || ''}`;
    return el.getAttribute('aria-current') === 'true' ||
      /(?:posActive|posSelect|current|active|selected)/i.test(classes);
  });
  if (active < 0) return {status: 'unknown', count: items.length};
  const next = items.slice(active + 1).find(el =>
    !/(?:lock|locked|suo)/i.test(`${el.className || ''} ${el.innerHTML || ''}`)
  );
  if (!next) return {status: 'last', count: items.length, title: clean(items[active].innerText)};
  next.scrollIntoView({block: 'center'});
  next.click();
  return {status: 'next', count: items.length, title: clean(next.innerText)};
}
"""


class EdgeBrowser:
    """通过 CDP 连接用户已打开的 Edge，不接管或关闭浏览器进程。"""

    def __init__(self, cdp_url: str = "http://127.0.0.1:9222", *, stop_event: Event | None = None) -> None:
        self.cdp_url = cdp_url
        self._playwright = None
        self.browser = None
        self.page = None
        self.stop_event = stop_event
        self._previous_quiz = None
        self._previous_view = None
        self._tracked_page = None
        self._pending_requests: set[Any] = set()

    def _check_stopped(self) -> None:
        if self.stop_event is not None and self.stop_event.is_set():
            raise BrowserStopped("已按用户要求停止。")

    def _poll(self) -> None:
        if self.stop_event is None:
            time.sleep(_POLL_SECONDS)
        else:
            self.stop_event.wait(_POLL_SECONDS)
        self._check_stopped()

    def _track_page_requests(self) -> None:
        page = self._require_page()
        if self._tracked_page is page:
            return
        self._untrack_page_requests()
        self._tracked_page = page
        page.on("request", self._request_started)
        page.on("requestfinished", self._request_done)
        page.on("requestfailed", self._request_done)

    def _untrack_page_requests(self) -> None:
        page = self._tracked_page
        if page is not None:
            page.remove_listener("request", self._request_started)
            page.remove_listener("requestfinished", self._request_done)
            page.remove_listener("requestfailed", self._request_done)
        self._tracked_page = None
        self._pending_requests.clear()

    def _request_started(self, request) -> None:
        if request.resource_type in _READINESS_REQUEST_TYPES:
            self._pending_requests.add(request)

    def _request_done(self, request) -> None:
        self._pending_requests.discard(request)

    def _page_requests_pending(self, frames) -> bool:
        for request in self._pending_requests:
            try:
                if request.frame in frames:
                    return True
            except Exception:
                if request.is_navigation_request():
                    return True
        return False

    def connect(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserError("缺少 playwright 依赖，请先安装 requirements.txt。") from exc
        try:
            self._playwright = sync_playwright().start()
            self.browser = self._playwright.chromium.connect_over_cdp(self.cdp_url, timeout=8000)
            pages = [page for context in self.browser.contexts for page in context.pages]
            if not pages:
                raise BrowserError("调试 Edge 中没有可用标签页。")
            self.page = next(
                (page for page in pages if "chaoxing.com" in page.url and "studentstudy" in page.url),
                next((page for page in pages if "chaoxing.com" in page.url), pages[-1]),
            )
            self._track_page_requests()
            self.page.bring_to_front()
        except Exception as exc:
            self.close()
            if isinstance(exc, BrowserError):
                raise
            raise BrowserError(
                "无法连接 Edge。请使用项目中的“启动调试Edge.bat”打开浏览器并登录学习通。"
            ) from exc

    def close(self) -> None:
        try:
            try:
                self._untrack_page_requests()
            finally:
                if self._playwright is not None:
                    self._playwright.stop()
        finally:
            self._playwright = None
            self.browser = None
            self.page = None

    def __enter__(self) -> "EdgeBrowser":
        self.connect()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    def _require_page(self):
        if self.page is None:
            raise BrowserError("尚未连接 Edge。")
        return self.page

    def page_info(self) -> dict[str, str]:
        page = self._require_page()
        return {"title": page.title(), "url": page.url}

    def current_chapter_key(self) -> str:
        """目录切换通常先于地址栏更新，去重时使用当前目录项规范化 chapterId。"""
        url = self._require_page().url
        try:
            active = next(
                (item for item in self.chapter_directory() if item.get("active")), None
            )
            chapter_id = str(active.get("chapter_id") or "") if active else ""
            if not chapter_id:
                return url
            from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

            parts = urlsplit(url)
            query = [
                (key, chapter_id if key == "chapterId" else value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
            ]
            if not any(key == "chapterId" for key, _value in query):
                query.append(("chapterId", chapter_id))
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        except Exception:
            return url

    def chapter_directory(self) -> list[dict[str, Any]]:
        """读取右侧叶子小节，只依据实际完成图标和待完成任务点计数。"""
        fallback: list[dict[str, Any]] = []
        for frame in self._require_page().frames:
            try:
                result = frame.evaluate(_DIRECTORY_READ_JS)
                items = result.get("items") or []
            except Exception:
                continue
            if not items:
                continue
            if any(item.get("active") for item in items):
                return items
            if not fallback:
                fallback = items

        # 某些页面切换小节时不更新 URL；只有没有任何 active 标记时才用 URL 兜底。
        if fallback and not any(item.get("active") for item in fallback):
            from urllib.parse import parse_qs, urlparse

            chapter_id = parse_qs(urlparse(self._require_page().url).query).get("chapterId", [""])[0]
            if chapter_id:
                for item in fallback:
                    item["active"] = item.get("chapter_id") == chapter_id
        return fallback

    def current_directory_status(self) -> str:
        items = self.chapter_directory()
        active = next((item for item in items if item.get("active")), None)
        return str(active.get("status") or "unknown") if active else "unknown"

    def next_unfinished_section(self) -> bool | None:
        """跳到当前项之后的第一个黄圈；None 表示目录不可用，需顺序导航兜底。"""
        page = self._require_page()
        items = self.chapter_directory()
        active_index = next(
            (index for index, item in enumerate(items) if item.get("active")), -1
        )
        if active_index < 0:
            return None

        target: dict[str, Any] | None = None
        for item in items[active_index + 1 :]:
            status = item.get("status")
            if status == "completed":
                continue
            if status == "pending":
                target = item
            # 不越过无法确认状态的目录项，避免漏掉未识别的小节。
            break
        if target is None:
            return False if all(
                item.get("status") == "completed" for item in items[active_index + 1 :]
            ) else None

        self._remember_quiz()
        self._check_stopped()
        clicked = False
        for frame in page.frames:
            try:
                if frame.evaluate(_DIRECTORY_CLICK_JS, target.get("chapter_id")):
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            raise BrowserError(f"无法点击右侧目录小节：{target.get('title') or target.get('number')}")

        # 目录先确认目标小节，正文和测验由 open_quiz 继续等待。
        deadline = time.monotonic() + _PAGE_WAIT_SECONDS
        while time.monotonic() < deadline:
            self._check_stopped()
            if any(
                item.get("active") and item.get("chapter_id") == target.get("chapter_id")
                for item in self.chapter_directory()
            ):
                return True
            self._poll()

        raise BrowserError("点击未完成小节后，未等到目标小节激活，已暂停。")

    def _visible_frames(self):
        for frame in self._require_page().frames:
            try:
                ancestor = frame
                while ancestor.parent_frame is not None and ancestor.frame_element().is_visible():
                    ancestor = ancestor.parent_frame
                if ancestor.parent_frame is None:
                    yield frame
            except Exception:
                continue

    def _quiz_frame(self):
        fallback = None
        for frame in self._visible_frames():
            try:
                if frame.locator(_QUESTION_LOCATOR).count() or frame.evaluate(_FALLBACK_QUIZ_JS):
                    return frame
                body = frame.locator("body").inner_text(timeout=800)
                if fallback is None and (
                    "题量" in body
                    and ("测验" in body or "章节检测" in body)
                    and ("提交" in body or "我的答案" in body)
                ):
                    fallback = frame
            except Exception:
                continue
        return fallback

    @staticmethod
    def _click_exact_in_frame(frame, text: str) -> bool:
        try:
            locator = frame.get_by_text(text, exact=True)
            for index in range(min(locator.count(), 20)):
                item = locator.nth(index)
                if item.is_visible() and item.is_enabled():
                    item.click(timeout=4000)
                    return True
        except Exception:
            return False
        return False

    def _click_exact_anywhere(self, text: str) -> bool:
        page = self._require_page()
        for frame in page.frames:
            self._check_stopped()
            if self._click_exact_in_frame(frame, text):
                return True
        return False

    def _quiz_snapshot(self):
        frame = self._quiz_frame()
        if frame is not None:
            try:
                return frame, frame.evaluate(_EXTRACT_JS)
            except Exception:
                pass
        return None

    @staticmethod
    def _quiz_identity(snapshot):
        frame, raw = snapshot
        questions = tuple(
            (item["element_id"], item["raw_type"], item["stem"], tuple(
                (option["letter"], option["text"]) for option in item["options"]
            ))
            for item in raw["questions"]
        )
        return frame, raw["document_id"], questions

    def _task_view(self):
        views = []
        for frame in self._visible_frames():
            try:
                view = frame.evaluate(_TASK_VIEW_JS, _TASK_TAB_SELECTOR)
                waiting_for_frame = (
                    frame.parent_frame is not None and frame.url == "about:blank"
                    and frame.frame_element().get_attribute("src") not in (None, "", "about:blank")
                )
            except Exception:
                views.append((frame, None, "", False, True))
                continue
            views.append((
                frame, view["documentId"], view["content"], view["media"],
                view["loading"] or waiting_for_frame,
            ))
        return tuple(views)

    def _remember_quiz(self) -> None:
        self._track_page_requests()
        snapshot = self._quiz_snapshot()
        self._previous_quiz = self._quiz_identity(snapshot) if snapshot is not None else None
        self._previous_view = self._task_view()

    def _current_quiz_snapshot(self):
        snapshot = self._quiz_snapshot()
        if snapshot is not None and self._quiz_identity(snapshot) != self._previous_quiz:
            return snapshot
        return None

    def _wait_for_quiz_content(self, deadline: float) -> bool:
        while time.monotonic() < deadline:
            self._check_stopped()
            snapshot = self._current_quiz_snapshot()
            if snapshot is not None:
                frame, raw = snapshot
                quiz = parse_quiz(raw)
                if "我的答案" in quiz.body_text or (
                    not self._page_requests_pending([frame])
                    and not quiz.layout_issues()
                    and all(q.raw_type and (q.options or not q.kind.supported) for q in quiz.questions)
                ):
                    self._previous_quiz = None
                    self._previous_view = None
                    return True
            self._poll()
        raise BrowserError("测验题量或选项尚未加载完整，已暂停在当前小节。")

    @staticmethod
    def _tab_title(tab) -> str:
        title = tab.get_attribute("title", timeout=800) or tab.inner_text(timeout=800)
        return re.sub(r"^\s*\d+\s*", "", title).strip()

    def _task_tabs(self):
        candidates = []
        for frame in self._visible_frames():
            try:
                tabs = frame.locator(_TASK_TAB_SELECTOR)
                for index in range(tabs.count()):
                    tab = tabs.nth(index)
                    if tab.is_visible():
                        title = self._tab_title(tab)
                        candidates.append((_PREFERRED_QUIZ_TAB_TITLES.get(title, 2), frame, index, title))
            except Exception:
                continue
        return sorted(candidates, key=lambda item: item[0])

    def open_quiz(self) -> bool:
        """仅在已加载的任务页签均确认无题目时返回 False。"""
        self._track_page_requests()
        deadline = time.monotonic() + _PAGE_WAIT_SECONDS
        while time.monotonic() < deadline:
            self._check_stopped()
            snapshot = self._quiz_snapshot()
            if snapshot is not None:
                if self._quiz_identity(snapshot) != self._previous_quiz:
                    return self._wait_for_quiz_content(deadline)
                self._poll()
                continue
            view = self._task_view()
            if (
                view and view != self._previous_view and not any(item[-1] for item in view)
                and not self._page_requests_pending([item[0] for item in view])
            ):
                candidates = self._task_tabs()
                if candidates:
                    break
            self._poll()
        else:
            raise BrowserError("当前小节的内容或任务页签加载超时，已暂停在当前小节。")

        for rank, frame, index, title in candidates:
            self._check_stopped()
            deadline = time.monotonic() + _PAGE_WAIT_SECONDS
            if self._current_quiz_snapshot() is not None:
                return self._wait_for_quiz_content(deadline)
            before = self._task_view()
            tab = frame.locator(_TASK_TAB_SELECTOR).nth(index)
            try:
                if self._tab_title(tab) != title:
                    raise BrowserError("任务页签在加载期间发生变化，已暂停在当前小节。")
                active = tab.evaluate("""el => el.matches(
                    '[aria-selected="true"], [aria-current="true"], .active, .current, .cur, .selected'
                )""", timeout=800)
                self._check_stopped()
                tab.click(timeout=4000)
            except BrowserError:
                raise
            except Exception as exc:
                raise BrowserError(f"无法打开“{title}”页签，已暂停在当前小节。") from exc

            stable_since = None
            last_view = None
            while time.monotonic() < deadline:
                self._check_stopped()
                if self._current_quiz_snapshot() is not None:
                    return self._wait_for_quiz_content(deadline)
                view = self._task_view()
                if rank == 2 and view and (view != before or active):
                    ready = any(text or media for _, _, text, media, _ in view) and not any(
                        loading or re.search(r"章节(?:测验|检测)|题量\s*[:：]", text)
                        for _, _, text, _, loading in view
                    )
                    if ready and not self._page_requests_pending([item[0] for item in view]):
                        if view != last_view or stable_since is None:
                            stable_since = time.monotonic()
                        if time.monotonic() - stable_since >= _NON_QUIZ_STABLE_SECONDS:
                            break
                    else:
                        stable_since = None
                else:
                    stable_since = None
                last_view = view
                self._poll()
            else:
                raise BrowserError(f"“{title}”页签加载超时，已暂停在当前小节。")

        if self._task_tabs() != candidates:
            raise BrowserError("任务页签仍在加载，已暂停在当前小节。")
        self._previous_quiz = None
        self._previous_view = None
        return False

    def is_quiz_completed(self) -> bool:
        # 提交成功后右侧目录可能先更新为绿勾，结果页文字反而出现较晚。
        if self.current_directory_status() == "completed":
            return True
        frame = self._quiz_frame()
        if frame is None:
            return False
        try:
            return "我的答案" in frame.locator("body").inner_text(timeout=1500)
        except Exception:
            return False

    @staticmethod
    def _question_node(frame, item: Mapping[str, Any], index: int):
        element_id = item.get("element_id")
        if element_id:
            import json

            return frame.locator(f"[id={json.dumps(element_id, ensure_ascii=False)}]:visible").first
        return frame.locator(_QUESTION_LOCATOR).nth(index)

    def extract_quiz(self, include_screenshots: bool = True) -> dict[str, Any]:
        frame = self._quiz_frame()
        if frame is None:
            raise BrowserError("未找到章节测验内容，请确认当前页面已经展开测验。")
        try:
            raw = frame.evaluate(_EXTRACT_JS)
        except Exception as exc:
            raise BrowserError(f"题目提取失败：{exc}") from exc
        if not include_screenshots:
            return raw

        # 学习通部分页面使用加密字体，DOM 文字会乱码；题目内容以实际渲染截图为准。
        import base64

        for index, item in enumerate(raw.get("questions") or []):
            try:
                self._check_stopped()
                node = self._question_node(frame, item, index)
                screenshot = node.screenshot(type="png", animations="disabled", timeout=6000)
                item["screenshot_b64"] = base64.b64encode(screenshot).decode("ascii")
            except BrowserStopped:
                raise
            except Exception as exc:
                item["screenshot_error"] = str(exc)
        return raw

    def selected_answers(self) -> dict[int, str]:
        raw = self.extract_quiz(include_screenshots=False)
        selected: dict[int, str] = {}
        for index, question in enumerate(raw.get("questions") or [], start=1):
            number = int(question.get("number") or index)
            selected[number] = "".join(
                str(option.get("letter", "")).upper()
                for option in question.get("options") or []
                if option.get("selected")
            )
        return selected

    def fill_answers(self, answers: Mapping[int, str]) -> None:
        self.dismiss_task_dialog()
        frame = self._quiz_frame()
        if frame is None:
            raise BrowserError("填写前未找到测验页面。")
        raw = self.extract_quiz(include_screenshots=False)
        option_state_js = r"""
        el => {
          const clean = value => (value || '').replace(/\s+/g, ' ').trim();
          const optionText = clean(el.innerText);
          const letterNode = el.querySelector(
            '.check_icon, .option-letter, .optionLetter, .letter, .mark_letter'
          );
          let letter = clean(letterNode ? letterNode.innerText : '').replace(/[.、]/g, '').toUpperCase();
          if (!/^[A-Z]$/.test(letter)) {
            const match = optionText.match(/^\s*([A-Z])(?:\s|[.、]|$)/i);
            letter = match ? match[1].toUpperCase() : '';
          }
          const input = el.matches('input') ? el : el.querySelector('input');
          const stateNodes = [el, el.parentElement,
            ...el.querySelectorAll('[class],[aria-checked],[aria-selected]')].filter(Boolean);
          const selected = Boolean(input && input.checked) || stateNodes.some(stateNode => {
            const classText = String(stateNode.className || '');
            return /(?:^|\s)(?:checked|selected|active|on|check_answer|checkAnswer|current|cur)(?:\s|$)/i.test(classText) ||
              stateNode.getAttribute('aria-checked') === 'true' || stateNode.getAttribute('aria-selected') === 'true';
          });
          return {letter, selected};
        }
        """

        try:
            for index, question in enumerate(raw.get("questions") or []):
                self._check_stopped()
                number = int(question.get("number") or index + 1)
                expected = str(answers.get(number, ""))
                if not expected:
                    continue
                node = self._question_node(frame, question, index)

                options = node.locator(
                    "ul li, .option, .answerOption, .answer-option, .optionItem, .option-item"
                )
                found: set[str] = set()
                for option_index in range(options.count()):
                    option = options.nth(option_index)
                    if option.locator("li").count() > 0:
                        continue
                    state = option.evaluate(option_state_js)
                    letter = str(state.get("letter") or "")
                    if not letter or letter in found:
                        continue
                    found.add(letter)
                    should_select = letter in expected
                    if bool(state.get("selected")) != should_select:
                        option.scroll_into_view_if_needed(timeout=3000)
                        label = option.locator("label")
                        input_control = option.locator("input")
                        if label.count() and label.first.is_visible():
                            target = label.first
                        elif input_control.count() and input_control.first.is_visible():
                            target = input_control.first
                        else:
                            target = option
                        try:
                            self._check_stopped()
                            target.click(timeout=3500)
                        except BrowserStopped:
                            raise
                        except Exception:
                            icon = option.locator(".check_icon, .option-letter, .optionLetter")
                            if not icon.count():
                                raise
                            self._check_stopped()
                            icon.first.click(timeout=3500)
                        time.sleep(0.12)
                missing = set(expected) - found
                if missing:
                    raise BrowserError(f"第 {number} 题未找到选项：{''.join(sorted(missing))}")
        except Exception as exc:
            if isinstance(exc, BrowserError):
                raise
            raise BrowserError(f"自动填写失败：{exc}") from exc
        time.sleep(0.8)

    def dismiss_task_dialog(self) -> bool:
        """关闭遮挡答题页的任务点弹窗；提交阶段不进入下一节。"""
        for frame in self._require_page().frames:
            self._check_stopped()
            try:
                result = frame.evaluate(
                    r"""
                    () => {
                      const visible = el => {
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                      };
                      const dialogs = Array.from(document.querySelectorAll(
                        '.layui-layer, .el-dialog, .modal, .popDiv, .maskDiv, .workpop, [role="dialog"]'
                      )).filter(el => visible(el) && (el.innerText || '').includes('任务点未完成'));
                      for (const dialog of dialogs) {
                        let close = Array.from(dialog.querySelectorAll(
                          '.layui-layer-close, .el-dialog__close, .modal-close, .popClose, .close, [aria-label="Close"], [aria-label="关闭"]'
                        )).find(visible);
                        if (!close) {
                          close = Array.from(dialog.querySelectorAll('a, button, span')).find(
                            el => visible(el) && (el.innerText || '').trim() === '×'
                          );
                        }
                        if (close) { close.click(); return 'closed'; }
                        return 'blocked';
                      }
                      return 'none';
                    }
                    """
                )
                if result == "closed":
                    time.sleep(0.4)
                    return True
                if result == "blocked":
                    raise BrowserError("任务点弹窗存在，但未找到关闭按钮，已停止提交。")
            except BrowserError:
                raise
            except Exception:
                continue
        return False

    @staticmethod
    def _submit_controls(scope, label):
        return scope.get_by_role("button", name=label).or_(
            scope.locator("a:visible").filter(has_text=label)
        ).and_(scope.locator(":visible"))

    def _confirmation_buttons(self, frames):
        buttons = []
        for frame in frames:
            dialogs = frame.locator(f":is({_DIALOG_SELECTOR}):visible").filter(has_text=_CONFIRM_TEXT)
            controls = self._submit_controls(dialogs, _CONFIRM_LABEL)
            buttons.extend(controls.all())
        return buttons

    def submit(self, expected_answers: Mapping[int, str] | None = None) -> None:
        """在测验和它的外层页面定位提交，并等待确认或结果。"""
        self._check_stopped()
        self.dismiss_task_dialog()
        snapshot = self._quiz_snapshot()
        if snapshot is None:
            raise BrowserError("提交前未找到测验页面。")
        frame, _ = snapshot
        quiz_identity = self._quiz_identity(snapshot)
        chapter_key = self.current_chapter_key()

        frames = [frame]
        while frames[-1].parent_frame is not None:
            frames.append(frames[-1].parent_frame)

        try:
            if self.is_quiz_completed():
                raise BrowserError("当前测验已完成，请检查页面状态。")
            if self._confirmation_buttons(self._visible_frames()):
                raise BrowserError("提交前已有确认弹窗，请人工确认页面状态。")

            deadline = time.monotonic() + _SUBMIT_WAIT_SECONDS
            while time.monotonic() < deadline:
                self._check_stopped()
                button = None
                for container in frames:
                    candidates = self._submit_controls(container, _SUBMIT_LABEL).and_(
                        container.locator(f":not(:is({_DIALOG_SELECTOR}) *)")
                    )
                    count = candidates.count()
                    if count > 1:
                        raise BrowserError("发现多个测验提交按钮，请人工确认页面状态。")
                    if count == 1:
                        button = candidates.first
                        break
                if button is not None and button.is_enabled(timeout=800):
                    break
                self._poll()
            else:
                raise BrowserError("等待测验提交按钮超时，已暂停在当前小节。")

            current = self._quiz_snapshot()
            if (
                self.current_chapter_key() != chapter_key or current is None
                or self._quiz_identity(current) != quiz_identity
            ):
                raise BrowserError("等待提交时测验页面发生变化，已暂停，请重新检测。")
            if expected_answers is not None:
                from .answers import answers_match

                matched, issues = answers_match(self.selected_answers(), expected_answers)
                if not matched:
                    raise BrowserError("提交前再次复核失败：" + "；".join(issues))
            button.scroll_into_view_if_needed(timeout=4000)
            self._check_stopped()
            button.click(timeout=5000)

            confirmed = False
            deadline = time.monotonic() + _SUBMIT_WAIT_SECONDS
            while time.monotonic() < deadline:
                self._check_stopped()
                if self.current_chapter_key() != chapter_key:
                    raise BrowserError("提交后当前小节发生变化，请人工确认提交结果。")
                if self.is_quiz_completed():
                    return
                if not confirmed:
                    controls = self._confirmation_buttons(self._visible_frames())
                    if len(controls) > 1:
                        raise BrowserError("发现多个确认提交按钮，请人工确认页面状态。")
                    if controls and controls[0].is_enabled(timeout=800):
                        self._check_stopped()
                        controls[0].click(timeout=5000)
                        confirmed = True
                        deadline = time.monotonic() + _SUBMIT_WAIT_SECONDS
                self._poll()
        except BrowserError:
            raise
        except Exception as exc:
            raise BrowserError(f"操作提交按钮失败，请人工确认页面状态：{exc}") from exc

        if confirmed:
            raise BrowserError("确认提交后等待结果超时，请人工确认页面状态。")
        raise BrowserError("提交后等待确认弹窗或结果超时，请人工确认页面状态。")

    def _click_incomplete_task_next(self) -> bool:
        """只跳过任务点提示，不检查或播放视频，也不点击“去学习”。"""
        for frame in self._require_page().frames:
            self._check_stopped()
            try:
                clicked = frame.evaluate(
                    r"""
                    () => {
                      const visible = el => {
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                      };
                      const dialogs = Array.from(document.querySelectorAll(
                        '.layui-layer, .el-dialog, .modal, .popDiv, .maskDiv, .workpop, [role="dialog"]'
                      )).filter(el => visible(el) && (el.innerText || '').includes('任务点未完成'));
                      for (const dialog of dialogs) {
                        const buttons = Array.from(dialog.querySelectorAll('a, button, [role="button"]'));
                        const next = buttons.find(el => visible(el) && (el.innerText || '').trim() === '下一节');
                        if (next) { next.click(); return true; }
                      }
                      return false;
                    }
                    """
                )
                if clicked:
                    return True
            except Exception:
                continue
        return False

    def next_section(self) -> bool:
        page = self._require_page()
        before = self.current_chapter_key()
        self._remember_quiz()
        self._check_stopped()

        def verify_changed(check_task_dialog: bool = False) -> None:
            deadline = time.monotonic() + _PAGE_WAIT_SECONDS
            while time.monotonic() < deadline:
                self._check_stopped()
                if self.current_chapter_key() != before:
                    return
                if check_task_dialog and self._click_incomplete_task_next():
                    check_task_dialog = False
                self._poll()
            raise BrowserError("点击下一节后未等到章节切换，已暂停。")

        if self._click_incomplete_task_next():
            verify_changed()
            return True

        if self._click_exact_anywhere("下一节"):
            verify_changed(check_task_dialog=True)
            return True

        for frame in page.frames:
            self._check_stopped()
            try:
                result = frame.evaluate(_DIRECTORY_JS)
            except Exception:
                continue
            if result.get("status") == "next":
                verify_changed(check_task_dialog=True)
                return True
            if result.get("status") == "last":
                return False
            if result.get("status") == "unknown":
                raise BrowserError("未发现“下一节”，且无法从右侧目录确认是否为最后章节。")
        raise BrowserError("未发现“下一节”，也无法读取右侧目录，已暂停。")
