"""NetAcad 网页适配。只操作可见 DOM，不访问播放器内部课程模型或答案键。"""
from __future__ import annotations

import base64
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from .browser import BrowserError, EdgeBrowser
from .course import AssessmentReport, CourseNode, NodeKind
from .models import Option, Question, QuestionType, Quiz
from . import selection_guard


_WAIT = 30.0
_POLL = 0.2
_QUESTION_BUTTON = re.compile(r"(?:^|\s)Question\s+(\d+)\s*$", re.I)
_COMPONENT = '[sgpulse-type="component"][modelid]'
_QUESTION_COMPONENT = ':is(mcq-view, object-matching-view, matching-view)' + _COMPONENT
_QUESTION_TITLES = '.block__title-inner, .component__title-inner'
_RESULT_PAGE = re.compile(r'^\s*(?:结果页面|Results Page)\s*$', re.I)
_RESET = re.compile(r'^(?:重置|Reset)$', re.I)
_DROPDOWN_ROW = '.matching__widget > matching-dropdown-view.matching__item_main'
_DROPDOWN_BUTTON = 'button.dropdown__btn[aria-haspopup="listbox"]'
_DROPDOWN_OPTION = '.dropdown__list[role="listbox"] > .dropdown__item[role="option"]'

# 这些辅助函数仅遍历 DOM 和 Shadow DOM。未读取 JS 模型或存储。
_DOM_HELPERS = f"const isQuestion = el => el.matches('{_QUESTION_COMPONENT}');" + r"""
const clean = s => (s || '').replace(/\s+/g, ' ').trim();
const deep = root => {
  const roots = [root], result = [];
  if (root.shadowRoot) roots.push(root.shadowRoot);
  for (let i = 0; i < roots.length; i++) for (const el of roots[i].querySelectorAll('*')) {
    result.push(el); if (el.shadowRoot) roots.push(el.shadowRoot);
  }
  return result;
};
const visible = el => {
  for (let cur = el; cur; cur = cur.parentElement || cur.getRootNode()?.host) {
    if (cur.hidden || cur.getAttribute('aria-hidden') === 'true') return false;
    const style = getComputedStyle(cur);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
  }
  return el.getClientRects().length > 0;
};
const inViewport = el => {
  const r = el.getBoundingClientRect();
  return visible(el) && r.right > 0 && r.left < innerWidth && r.bottom > 0 && r.top < innerHeight;
};
const text = el => clean(el?.innerText || el?.textContent);
"""

_TASKS_JS = "() => {" + _DOM_HELPERS + r"""
  const nodes = deep(document);
  const articles = nodes.filter(el => el.matches('article-view[modelid]') && visible(el));
  return articles.map(el => {
    const children = deep(el);
    const heading = children.find(e => e.matches('.article__title-inner'));
    const title = text(heading).replace(/^(?:不完整|完成|Incomplete|Complete)\s*/i, '');
    const components = children.filter(e => e.matches('[sgpulse-type="component"][modelid]') && visible(e));
    const question = components.some(isQuestion);
    const external = children.some(e => e.matches('a[href]') && /\.(?:pka|pkt)(?:[?#]|$)/i.test(e.getAttribute('href') || ''));
    const unknown = components.filter(e => !isQuestion(e) &&
      !/^(?:TEXT|HEADING|GRAPHIC|ADOBE-ANIMATE|ADOBE-ANIMATE-IA)-VIEW$/.test(e.tagName));
    return {id: el.getAttribute('modelid'), title, question, external,
      unknown: [...new Set(unknown.map(e => e.tagName))],
      top: el.getBoundingClientRect().top, bottom: el.getBoundingClientRect().bottom};
  });
}
"""

_DROPDOWN_JS = f"""
  const rowSelector = '{_DROPDOWN_ROW}';
  const dropdownButton = '{_DROPDOWN_BUTTON}';
  const dropdownOption = '{_DROPDOWN_OPTION}';
""" + r"""
  const optionText = option => {
    const label = option.querySelector('.dropdown__item-inner');
    return clean(Array.from(label?.childNodes || []).filter(n => n.nodeType === Node.TEXT_NODE ||
      n.nodeType === Node.ELEMENT_NODE && !n.matches('.sr-only,[aria-hidden="true"]'))
      .map(n => n.textContent).join(''));
  };
  const dropdowns = nodes.filter(e => e.matches(rowSelector)).map(row => {
    const root = row.shadowRoot || row;
    const buttons = root.querySelectorAll(dropdownButton);
    const menus = root.querySelectorAll('.dropdown__list[role="listbox"]');
    if (!visible(row) || buttons.length !== 1 || menus.length !== 1 || !visible(buttons[0])) return null;
    const button = buttons[0];
    const label = text(root.querySelector('.matching__item-title .matching__item-title_inner'));
    // Closed listboxes already contain the choices shown when this row is opened.
    const options = Array.from(root.querySelectorAll(dropdownOption));
    const labels = options.map(optionText);
    const selected = options.flatMap((item, i) => item.getAttribute('aria-selected') === 'true' ? [i] : []);
    const shown = text(button.querySelector('.dropdown__inner'));
    return {label, options:labels, open:visible(menus[0]),
      selected:selected.length === 1 && labels[selected[0]] === shown ? selected[0] : -1};
  });
"""

_WIDGET_JS = "el => {" + _DOM_HELPERS + r"""
  const nodes = deep(el);
""" + _DROPDOWN_JS + r"""
  const body = nodes.filter(e => e.matches('.component__body') && visible(e)).map(text).join(' ');
  const options = nodes.filter(e => e.matches('.mcq__item') && visible(e)).map(e => ({
    text: text(e.querySelector('.mcq__item-text') || e.querySelector('label') || e)
      .replace(/\s*\d+\s*of\s*\d+\s*$/i, '').trim(),
    selected: e.getAttribute('aria-checked') === 'true',
    correct: e.classList.contains('is-correct'),
    incorrect: e.classList.contains('is-incorrect'),
    multiple: e.getAttribute('role') === 'checkbox',
    declaredCount: Number(e.getAttribute('aria-setsize') || 0),
  }));
  const categories = nodes.filter(e => e.matches('button.objectMatching-category-item') && visible(e))
    .map(e => ({text: text(e.querySelector('.category-item-text')), id: e.getAttribute('data-id'),
      index: e.getAttribute('data-itemindex'), letter: text(e.querySelector('.category-item-number'))}));
  const matches = nodes.filter(e => e.matches('button.objectMatching-option-item') && visible(e))
    .map(e => ({text: text(e.querySelector('.category-item-text')), id: e.getAttribute('data-id'),
      selectedCategory: text(e.querySelector('.category-item-number'))}));
  const widget = nodes.find(e => e.matches('.component__widget'));
  // 真实播放器会把 is-submitted 与 show-user-answer 拼在同一个 class token 中。
  const states=(widget?.className||'').replace(/is-submitted(?=show-(?:user|correct)-answer)/g,'is-submitted ').split(/\s+/);
  const submitted = states.includes('is-submitted');
  const markedCorrect=options.filter(o=>o.correct);
  const correct = submitted && (states.includes('is-correct') ||
    markedCorrect.length>0 && options.every(o=>o.selected===o.correct));
  const wrong = submitted && !correct && (states.includes('is-incorrect') ||
    states.includes('show-user-answer') || options.some(o=>o.selected&&o.incorrect));
  const feedback = nodes.filter(e => e.matches('.component__feedback-content, .feedback__content, .feedback') && visible(e));
  const tables = submitted && wrong ? nodes.filter(e => e.matches('table.table-feedback') && visible(e) &&
    e.closest('.component__feedback, .component__feedback-content, .feedback__content, .feedback')) : [];
  const pairRows = tables.length === 1 ? Array.from(tables[0].rows).filter(row => visible(row) &&
    row.cells.length === 2 && Array.from(row.cells).every(cell => cell.tagName === 'TD'))
    .map(row => [text(row.cells[0]), text(row.cells[1])]) : [];
  // Dropdown feedback groups row labels under the correct option in each column.
  const tableRows = el.matches('matching-view') && tables.length === 1
    ? Array.from(tables[0].rows).map(row => Array.from(row.cells, cell =>
        [cell.tagName, visible(cell) ? clean(cell.textContent) : ''])) : [];
  const resources = nodes.filter(e => e.matches('a[href]') && /\.(pka|pkt)(?:[?#]|$)/i.test(e.getAttribute('href')||''))
    .map(e => e.getAttribute('href').split(/[?#]/, 1)[0]).sort();
  const imgs = nodes.filter(e => e.matches('.component__body img, .component__graphic img') && visible(e));
  return {id:el.getAttribute('modelid'), tag:el.tagName, stem:body, options, categories, matches, dropdowns, tableRows,
    images:imgs.length, imagesReady:imgs.every(e=>e.complete && e.naturalWidth>0),
    resources, submitted, correct, wrong, feedback:feedback.map(text).join(' '), pairRows,
    disabled:states.includes('is-disabled'), correctTexts:markedCorrect.map(o=>o.text)};
}
"""

_RESULT_JS = "() => {" + _DOM_HELPERS + r"""
  const results = deep(document).filter(e=>e.matches('.assessmentResults__inner') && inViewport(e));
  if (results.length !== 1) return null;
  const parts = [results[0], ...deep(results[0])].filter(e=>visible(e));
  const content = clean(parts.map(text).join(' '));
  const matches = [...content.matchAll(/(?:你的得分是|您的得分是|Your score is)\s*(\d+(?:\.\d+)?)\s*%/gi)];
  const scores = [...new Set(matches.map(m=>Number(m[1])))];
  return scores.length===1 ? scores[0] : null;
}
"""

_EXAM_DIRECTORY_JS = "() => {" + _DOM_HELPERS + r"""
  const root=document.querySelector('#course-outline');
  if(!root) return null;
  const buttons=Array.from(root.querySelectorAll('button[id^="submodule-button"]')).filter(visible);
  const explicitlySelected=buttons.filter(button=>{
    const container=button.closest('[class*="subModuleContainer--"]');
    return ['true','page'].includes(button.getAttribute('aria-current')) ||
      button.getAttribute('aria-selected')==='true' ||
      !!container && Array.from(container.classList).some(c=>c.startsWith('active--'));
  });
  let selected=explicitlySelected;
  // 旧版合成/兼容页面没有选中容器；真实目录不能把展开状态当成选中状态。
  if(!selected.length && !root.querySelector('[class*="subModuleContainer--"]'))
    selected=buttons.filter(b=>b.getAttribute('aria-expanded')==='true');
  if(selected.length!==1) return null;
  const button=selected[0];
  const name=button.querySelector('[class*="subModuleName--"][title]') || button.querySelector('[title]');
  const title=clean(name?.getAttribute('title'));
  if(!title || !button.id) return null;
  const states=Array.from(button.querySelectorAll('img')).map(e=>e.alt);
  const completed=states.includes('completed'),pending=states.includes('start')||states.includes('in progress');
  return {id:button.id,title,status:completed&&!pending?'completed':pending&&!completed?'pending':'unknown'};
}
"""


@dataclass(frozen=True)
class _Binding:
    source_number: int
    widget_id: str
    category: int | None
    option_texts: tuple[str, ...]
    stem: str
    categories: tuple[str, ...] = ()
    category_ids: tuple[str | None, ...] = ()
    dropdown_row: int | None = None
    dropdown_rows: tuple[tuple[str, tuple[str, ...]], ...] = ()


class NetAcadBrowser(EdgeBrowser):
    """网页课程专用适配器。需要独立操作的实验和未知组件会暂停。"""

    def __init__(self, *args, dry_run: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.dry_run = dry_run
        self._cursor = 0
        self._content_url = ""
        self._bindings: dict[int, _Binding] = {}
        self._quiz: Quiz | None = None
        self._last_score: float | None = None
        self._sequential_total: int | None = None
        self._saved_steps: dict[int, tuple[Quiz, dict, dict]] = {}
        self._step_attempted: set[int] = set()
        self._submission_attempted = False
        self._proofs = {}
        self._guard_key = '__autoquiz_selection_' + uuid.uuid4().hex
        self._guard_frames = set()
        self._native_total = None
        self._deferred_prefix_count = 0
        self._review_nav_total = None

    def close(self):
        for frame in self._guard_frames:
            try:
                frame.evaluate(selection_guard.CLOSE,self._guard_key)
            except Exception:
                pass
        self._guard_frames.clear()
        super().close()

    def _select_page(self, pages):
        candidates = [p for p in pages if
                      (urlsplit(p.url).hostname or "") in {"www.netacad.com", "netacad.com"}
                      and re.search(r"/(?:[a-z]{2}/)?launch(?:/|$)", urlsplit(p.url).path)]
        if len(candidates) != 1:
            raise BrowserError("请在专用 Edge 中只打开一个 NetAcad 课程学习标签。")
        return candidates[0]

    def _wait(self, condition, message: str):
        deadline = time.monotonic() + _WAIT
        while time.monotonic() < deadline:
            self._check_stopped()
            value = condition()
            if value is not None and value is not False:
                return value
            if self.stop_event is not None:
                self.stop_event.wait(_POLL)
            else:
                time.sleep(_POLL)
        raise BrowserError(message)

    def _frame(self):
        candidates = [f for f in self._visible_frames() if
                      (urlsplit(f.url).hostname or "") in {"www.netacad.com", "netacad.com"}
                      and "/authoring-resources/" in urlsplit(f.url).path]
        if len(candidates) != 1:
            raise BrowserError("NetAcad 课程内容尚未加载完整，请停在当前小节稍后重试。")
        frame = candidates[0]
        if frame.url != self._content_url:
            self._content_url = frame.url
            self._cursor = 0
            self._sequential_total = None
            self._saved_steps.clear()
            self._step_attempted.clear()
            self._submission_attempted = False
            self._proofs.clear()
            self._native_total = None
            self._deferred_prefix_count = 0
            self._review_nav_total = None
        return frame

    @staticmethod
    def _selector(model_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", model_id):
            raise BrowserError("播放器组件标识无法可靠定位。")
        return f'[modelid="{model_id}"]'

    def _article(self, model_id: str):
        # 标题 heading-view 会复用所属文章的 modelid，必须同时限定组件类型。
        article = self._frame().locator('article-view' + self._selector(model_id))
        if article.count() != 1 or not article.evaluate(
                'el => {' + _DOM_HELPERS + 'return visible(el);}'):
            raise BrowserError("课程节点定位不唯一或尚未显示，已暂停，未跳过该节点。")
        return article

    def _tasks(self):
        tasks = self._frame().evaluate(_TASKS_JS)
        for task in tasks:
            if not task["title"] and (task["question"] or task["external"] or
                    set(task["unknown"]) - {"BLANK-VIEW", "QUICKNAV-VIEW"}):
                raise BrowserError("发现未关联目录的交互组件，已暂停，避免漏掉网页题目。")
        # 播放器会插入无标题的封面、空白间隔和底部导航；它们不是目录任务。
        return [task for task in tasks if task["title"]]

    def _nav_snapshot(self) -> dict[int, object]:
        buttons = self._frame().get_by_role("button", name=_QUESTION_BUTTON)
        result = {}
        for button in buttons.all():
            label = button.get_attribute("aria-label") or button.inner_text()
            match = _QUESTION_BUTTON.search(label)
            if match and button.is_visible():
                number = int(match[1])
                if number in result:
                    raise BrowserError("考试导航题号重复，已暂停。")
                result[number] = button
        return result

    def _step_counter(self):
        return self._frame().evaluate("() => {" + _DOM_HELPERS + r"""
          const labels=deep(document).filter(e=>e.matches('.secure-toolbar-container .question-label') && inViewport(e));
          const counts=labels.map(e=>text(e).match(/^(\d+)\s*(?:的|of)\s*(\d+)\s*(?:问题|questions?)$/i)).filter(Boolean);
          const values=[...new Set(counts.map(m=>m[1]+':'+m[2]))];
          return values.length===1 ? values[0].split(':').map(Number) : null;
        }""")

    def _nav(self) -> dict[int, object]:
        initial = self._nav_snapshot()
        if not initial:
            return initial
        def ready():
            result = self._nav_snapshot()
            if not result or set(result) != set(range(1, max(result) + 1)):
                return None
            totals = self._declared_totals()
            counter = self._step_counter()
            toolbar = self._frame().locator('.secure-toolbar-container .submit-button')
            if counter and any(b.is_visible() for b in toolbar.all()):
                current, total = counter
                if 1 <= current <= max(result) <= total and totals == [total]:
                    if self._sequential_total not in (None, total):
                        raise BrowserError("本轮考试总题数已变化，已暂停，未继续保存或提交。")
                    self._sequential_total = total
                    return result
            expected_total = self._sequential_total or self._review_nav_total
            if expected_total is None and not totals and self._has_results_navigation() and self._result() is not None:
                # 已评分的小节检测可能隐藏计数器，但完整结果导航仍可见。
                expected_total = self._review_nav_total = len(result)
            if self._review_nav_total is not None and totals and totals != [self._review_nav_total]:
                return None
            expected = [expected_total] if expected_total is not None else totals
            return result if expected == [len(result)] else None
        return self._wait(ready, "当前考试的题数与已加载导航无法核实，已暂停，未跳过题目。")

    def _declared_totals(self):
        counter = self._step_counter()
        if counter:
            return [counter[1]]
        return self._frame().evaluate("() => {" + _DOM_HELPERS + r"""
              const totals=[];
              for(const el of deep(document).filter(e=>visible(e))) {
                const t=text(el);
                if(t.length>400) continue;
                for(const re of [/考试包括\s*(\d+)\s*问题/g, /\d+\s*的\s*(\d+)\s*问题/g,
                                 /(?:of|includes)\s+(\d+)\s+questions/gi]) {
                  for(const m of t.matchAll(re)) totals.push(Number(m[1]));
                }
              }
              return [...new Set(totals)];
            }""")

    def _exam_start_available(self):
        starts = self._frame().get_by_role("button", name=re.compile(r"^(?:开始|Start)$", re.I))
        totals = self._declared_totals()
        return any(b.evaluate('el => {' + _DOM_HELPERS + 'return inViewport(el);}')
                   for b in starts.all()) and len(totals) == 1 and totals[0] > 0

    def _directory_status(self, title: str) -> str:
        # 同一模块的 submodule-button ID 会重复，使用实际标题逐项精确匹配。
        return self._require_page().evaluate(r"""title => {
          const clean=s=>(s||'').replace(/\s+/g,' ').trim();
          const items=Array.from(document.querySelectorAll('#course-outline [role="treeitem"]'));
          const found=items.filter(el=>Array.from(el.querySelectorAll('[title]')).some(n=>clean(n.getAttribute('title'))===title));
          if(found.length!==1) return 'unknown';
          const states=Array.from(found[0].querySelectorAll('img')).map(e=>e.alt);
          if(states.includes('completed')) return 'completed';
          if(states.includes('start')||states.includes('in progress')) return 'pending';
          return 'unknown';
        }""", title)

    def _exam_directory(self):
        item = self._require_page().evaluate(_EXAM_DIRECTORY_JS)
        if item is None:
            raise BrowserError("无法唯一确定当前选中的考试目录项，请停在目标考试页面后重试。")
        return item

    def _completion_status(self, node):
        if node.key.startswith('netacad-exam:'):
            item = self._exam_directory()
            if node.key != 'netacad-exam:' + item['id'] + ':' + item['title']:
                raise BrowserError("当前考试目录项已变化，已暂停，未继续导航。")
            return item['status']
        return self._directory_status(node.title)

    def current_node(self) -> CourseNode:
        frame = self._frame()
        if self._nav() or self._exam_start_available():
            item = self._exam_directory()
            title, status = item['title'], item['status']
            # 已完成的考试也可能未满分，启动时不依据该绿勾跳过考试。
            key = "netacad-exam:" + item['id'] + ":" + title
            return CourseNode(key, title, NodeKind.ASSESSMENT,
                              "pending" if status in {"pending", "completed"} else "unknown")
        tasks = self._tasks()
        if not tasks or self._cursor >= len(tasks):
            raise BrowserError("未读取到当前 NetAcad 节点，页面可能仍在加载。")
        task = tasks[self._cursor]
        title = task["title"]
        kind = NodeKind.ASSESSMENT if task["question"] else NodeKind.READING
        if (task["external"] and not task["question"]) or task["unknown"]:
            kind = NodeKind.UNSUPPORTED
        status=self._directory_status(title)
        if kind==NodeKind.ASSESSMENT and status=='completed':
            status='pending'  # 测验的目录绿勾不代表百分百正确。
        return CourseNode(task["id"], title, kind, status)

    def _scope(self):
        frame = self._frame()
        if self._nav():
            return frame.locator('body')
        return self._article(self._tasks()[self._cursor]["id"])

    def advance_reading(self) -> bool:
        self._check_stopped()
        if self.dry_run:
            raise BrowserError("测试模式不滚动或写入课程进度。")
        task = self._tasks()[self._cursor]
        frame = self._frame()
        # 每步滚动小于一屏，随后由流程层重新核实目录状态。
        moved = frame.evaluate("""id => {
          const roots=[document]; let article;
          for(let i=0;i<roots.length;i++) for(const el of roots[i].querySelectorAll('*')) {
            if(el.shadowRoot) roots.push(el.shadowRoot);
            if(el.matches('article-view') && el.getAttribute('modelid')===id) article=el;
          }
          if(!article) return false;
          const r=article.getBoundingClientRect();
          const old=scrollY;
          scrollBy(0, Math.min(innerHeight*0.7, Math.max(0,r.bottom-innerHeight+20)));
          return scrollY>old;
        }""", task["id"])
        # 等目录进度的异步更新；停止可以打断此等待。
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            self._check_stopped()
            if self._directory_status(task["title"]) == "completed":
                break
            frame.wait_for_timeout(100)
        return moved

    def _click(self, locator):
        self._check_stopped()
        visible = [item for item in locator.all() if item.is_visible()]
        if len(visible) != 1 or not visible[0].is_enabled():
            raise BrowserError("页面按钮缺失、不唯一或未启用，已暂停。")
        classes = (visible[0].get_attribute("class") or "").split()
        if {"disabled", "is-disabled"} & set(classes):
            raise BrowserError("页面按钮尚不可用，已暂停。")
        visible[0].click(timeout=4000)
        self._check_stopped()

    def open_assessment(self) -> None:
        self._check_course_relaunch()
        score = self._result()
        if score is not None:
            if score == 100:
                raise BrowserError("当前测验已显示 100% 成绩，无需重复提交。请进入下一待完成节点继续。")
            raise BrowserError("当前测验已有成绩，请查看解析或在平台重置新轮次后继续，不能重复提交旧试卷。")
        self._sequential_total = None
        self._saved_steps.clear()
        self._step_attempted.clear()
        self._submission_attempted = False
        self._proofs.clear()
        self._native_total = None
        self._deferred_prefix_count = 0
        self._review_nav_total = None
        frame = self._frame()
        starts = [b for b in frame.get_by_role("button", name=re.compile(r"^(?:开始|Start)$", re.I)).all()
                  if b.evaluate('el => {' + _DOM_HELPERS + 'return inViewport(el);}')]
        if starts:
            if self.dry_run:
                raise BrowserError("考试尚未开始。测试模式不会启动计时考试；请先确认运行模式。")
            if len(starts) != 1:
                raise BrowserError("考试开始入口不唯一，已暂停。")
            self._click(starts[0])
            self._wait(lambda: self._nav() or None, "考试开始后题目导航尚未加载完整，已暂停。")
        self._last_score = None

    def _final_navigation(self):
        # 辅助名称可能是“已选中提交页面”，按钮可见文字才是稳定的页面名称。
        return self._frame().locator('button.block-button, button#finalnav').filter(
            has_text=re.compile(r'^\s*(?:提交页面|结果页面|Submit Page|Results Page)\s*$', re.I))

    def _has_results_navigation(self):
        return sum(b.is_visible() for b in self._final_navigation().filter(has_text=_RESULT_PAGE).all()) == 1

    def _result_reset(self):
        return self._frame().locator('.assessmentResults__inner').get_by_role('button', name=_RESET)

    def _confirmation(self):
        candidates = [c for c in self._frame().locator('#confirm-exam').all()
                      if c.evaluate('el => {' + _DOM_HELPERS + 'return inViewport(el);}')]
        if len(candidates) > 1:
            raise BrowserError("最终提交确认框不唯一，已暂停。")
        return candidates[0] if candidates else None

    def _check_unanswered(self):
        frame = self._frame()
        warning = frame.get_by_text(re.compile(
            r'(?:你有|您有|You have)\s*[1-9]\d*\s*(?:个未解答的问题|unanswered questions?)', re.I))
        listed = frame.locator('.unanswered-numbers')
        if any(item.is_visible() for item in warning.all()) or any(
                item.is_visible() and item.inner_text().strip() for item in listed.all()):
            raise BrowserError("网页仍有未解答的问题，已禁止提交。")

    def _answered_navigation(self, nav):
        return all(re.match(r'^(?:answered\s+Question\s+\d+|已(?:回答|作答|解答)\s*(?:Question|问题)\s*\d+)\s*$',
                            self._navigation_label(button), re.I)
                   for button in nav.values())

    @staticmethod
    def _navigation_label(button):
        return re.sub(r'^(?:已选中|selected)\s+', '', button.get_attribute('aria-label') or '', flags=re.I)

    def resume_assessment(self):
        """仅从明确待提交且导航全为已作答的页面，复核并恢复现有答案。"""
        from .answers import validate_answers
        confirm = self._confirmation()
        if confirm is None or self._result() is not None:
            return None
        if confirm.is_checked():
            raise BrowserError("待提交确认框已经勾选，无法核实此前是否尝试过最终提交，请先检查网页结果。")
        self._check_unanswered()
        nav = self._nav()
        if not nav or not self._answered_navigation(nav):
            raise BrowserError("待提交页的已保存作答状态不完整，已禁止恢复提交。")
        total = len(nav)
        saved = {}
        for number in range(1, total + 1):
            self._check_stopped()
            record = self._cached_saved_step(number)
            if record is None:
                break
            saved[number] = record
        if len(saved) == total:
            self._sequential_total = total
            self._saved_steps = saved
            quiz = self.finish_assessment_steps()
            return quiz, self.completed_assessment_answers()

        quiz = self.extract_assessment()
        issues = quiz.structural_issues()
        actual = self.selected_answers()
        answers, answer_issues = validate_answers(quiz, actual)
        # 当前选中题的辅助名称会临时覆盖 answered；回到确认页后再核实保存标记。
        self._click(self._final_navigation())
        self._wait(self._confirmation, "复核后未重新读取到最终确认页，已暂停。")
        if issues or answer_issues or not self._answered_navigation(self._nav()):
            raise BrowserError("待提交试卷的题目、选项或已保存答案复核失败，已禁止提交。")
        # 配对答案逐项合法之外，仍须满足组内一一对应。
        matched = {}
        for number, binding in self._bindings.items():
            if binding.category is not None:
                used = matched.setdefault(binding.widget_id, set())
                if answers[number] in used:
                    raise BrowserError("待提交试卷存在重复配对答案，已禁止提交。")
                used.add(answers[number])
        saved = {}
        for source in range(1, total + 1):
            numbers = [n for n, binding in self._bindings.items() if binding.source_number == source]
            if not numbers:
                raise BrowserError("待提交试卷缺少题目，已禁止提交。")
            questions = [replace(quiz.questions[n-1], number=i) for i, n in enumerate(numbers, 1)]
            saved[source] = (Quiz(len(questions),dict(Counter(q.kind.value for q in questions)),questions),
                             {i:self._bindings[n] for i,n in enumerate(numbers,1)},
                             {i:answers[n] for i,n in enumerate(numbers,1)})
        self._sequential_total = total
        self._saved_steps = saved
        self._check_unanswered()
        return quiz, answers

    def _review_visible(self):
        for widget in self._frame().locator(_COMPONENT).all():
            if widget.evaluate('el=>{'+_DOM_HELPERS+
                    'return isQuestion(el)&&inViewport(el);}'):
                raw=widget.evaluate(_WIDGET_JS)
                if raw['submitted'] and (raw['correct'] or raw['wrong']):
                    return True
        return False

    def _open_review(self):
        self._check_course_relaunch()
        if not self._nav() or self._review_visible():
            return False
        review = self._frame().get_by_role('button', name=re.compile(
            r'^\s*(?:审查评估|查看解析|Review Assessment)\s*$', re.I))
        if any(b.is_visible() for b in review.all()):
            self._click(review)
            return False
        if (self._result() is not None and self._has_results_navigation() and
                sum(b.is_visible() and b.is_enabled() for b in self._result_reset().all()) == 1):
            return True
        raise BrowserError("无法核实本轮结果或逐题解析入口，已暂停，未重置。")

    def _check_course_relaunch(self):
        warning = self._frame().locator('notify-view').get_by_text(re.compile(
            r'请重新启动课程以记录您的进度|(?:please\s+)?relaunch (?:the|your) course to record your progress',
            re.I,
        ))
        if any(message.is_visible() for message in warning.all()):
            raise BrowserError("NetAcad 要求重新启动课程以记录进度。")

    def resume_review_assessment(self):
        """恢复已经评分的试卷，先读取判定再重置，不再次提交旧轮次。"""
        from .answers import validate_answers
        score=self._result()
        if score is None and not self._review_visible():
            return None
        if score is None and self._nav():
            self._click(self._final_navigation())
            score=self._wait(self._result,'已有逐题判定但无法核实本轮成绩，未重置。')
        if score==100:
            self._last_score=100
            return None,{},AssessmentReport(100,reviewed=True)
        use_result_navigation = self._open_review()
        quiz=self.extract_assessment()
        if quiz.structural_issues():
            raise BrowserError('已有试卷的题目不完整，未重置。')
        answers,_=validate_answers(quiz,self.selected_answers())
        if not use_result_navigation and len(answers) != quiz.total:
            raise BrowserError('已有试卷的所选答案或题目不完整，未重置。')
        if score is None:
            marks=[self._practice_mark(binding) for binding in self._unique_bindings()]
            if not marks or any(mark is None for mark in marks):
                raise BrowserError('已有练习的判定尚未完整，未重置。')
            score=100.0*sum(mark=='correct' for mark in marks)/len(marks)
        self._last_score=score
        report = self._read_assessment_review(use_result_navigation)
        missing = {q.number for q in quiz.questions} - answers.keys()
        if missing - set(report.ungraded_numbers):
            raise BrowserError('已判定题目的原选项不完整，未重置。')
        return quiz,answers,report

    def assessment_steps(self):
        nav = self._nav()
        if self._sequential_total and not self._saved_steps and len(nav) > 1:
            self._saved_prefix_size(nav)
        return self._sequential_total

    def _saved_prefix_size(self, nav):
        last = len(nav)
        counter = self._step_counter()
        previous = {n: button for n, button in nav.items() if n < last}
        last_label = (nav[last].get_attribute('aria-label') or '') if last else ''
        unanswered = re.fullmatch(
            rf'(?:unanswered\s+Question\s+{last}|未(?:回答|作答|解答)\s*(?:Question|问题)\s*{last}|(?:已选中|selected)\s+Question\s+{last})\s*',
            last_label, re.I) if last else None
        if (last < 2 or counter != [last, self._sequential_total] or
                not self._answered_navigation(previous) or not unanswered or
                self._confirmation() is not None or self._result() is not None):
            raise BrowserError("本轮已有解锁题目，前面题目的保存状态未知。请停在最后一个已解锁且未作答的题目后重试。")
        return last - 1

    def resume_assessment_steps(self):
        """优先读取页面缓存，不切回旧题；缓存不足时把完整复核延迟到最后。"""
        nav = self._nav()
        if self._sequential_total is None or len(nav) <= 1:
            return []
        if self._saved_steps:
            raise BrowserError("本轮恢复记录已经存在，已暂停，未重复恢复。")
        count = self._saved_prefix_size(nav)
        saved = {}
        for step in range(1, count + 1):
            self._check_stopped()
            record = self._cached_saved_step(step)
            if record is None:
                saved = {}
                break
            saved[step] = record
        current_widgets = self._go_question(count + 1)
        if self._saved_prefix_size(self._nav()) != count:
            raise BrowserError("已保存题目范围在恢复时发生变化，已暂停。")
        # answered 只代表已选择答案，不能把当前题的该标签当作已保存。
        # 旧题的保存由后续题已经解锁证明；当前题仍须未提交且保存入口就绪。
        controls = [button for button in self._frame().locator('.secure-toolbar-container .submit-button').all()
                    if button.is_visible()]
        current_states = [widget.evaluate(_WIDGET_JS) for widget in current_widgets]
        if (any(raw['submitted'] or raw['disabled'] for raw in current_states) or
                len(controls) != 1 or
                controls[0].get_attribute('aria-busy') == 'true' or
                self._page_requests_pending([self._frame()])):
            raise BrowserError("当前题的保存状态未知或已进入保存，已暂停，未重复保存。")
        self._saved_steps = saved
        self._deferred_prefix_count = 0 if saved else count
        self._step_attempted.update(range(1, count+1))
        self._bindings = {}
        self._quiz = None
        return [(quiz, dict(answers)) for quiz, _, answers in saved.values()]

    def deferred_assessment_steps(self):
        return self._deferred_prefix_count

    @staticmethod
    def _saved_answers(quiz, bindings, actual):
        from .answers import validate_answers
        answers, issues = validate_answers(quiz, actual)
        if quiz.structural_issues() or issues:
            raise BrowserError("已保存题目的答案或作答标记不完整，已暂停，未覆盖旧答案。")
        matched = {}
        for number, binding in bindings.items():
            if binding.category is not None:
                used = matched.setdefault(binding.widget_id, set())
                if answers[number] in used:
                    raise BrowserError("已保存题目存在重复配对，已暂停，未覆盖旧答案。")
                used.add(answers[number])
        return answers

    def _cached_saved_step(self, number):
        # 一次 DOM 查询定位语义题号，避免每道旧题逐个跨进程扫描所有组件。
        candidates = self._frame().evaluate('number=>{'+_DOM_HELPERS+r'''
          return deep(document).filter(isQuestion).filter(el=>{
              const numbers=deep(el).filter(e=>e.matches('.component__title-inner'))
                .map(e=>text(e).match(/(?:问题|Question)\s*(\d+)\s*$/i)).filter(Boolean).map(m=>Number(m[1]));
              return numbers.length>0&&numbers.every(n=>n===number);
            }).map(el=>({tag:el.tagName.toLowerCase(),id:el.getAttribute('modelid')}));
        }''',number)
        if len(candidates) != 1:
            return None
        item = candidates[0]
        widget = self._frame().locator(item['tag']+'[sgpulse-type="component"]'+self._selector(item['id']))
        if widget.count() != 1:
            return None
        raw = self._read_widget_ready(widget)
        # 不为历史图片题切屏截图；需要时在最终复核阶段一次补齐。
        if raw is None or raw['images']:
            return None
        questions, bindings = [], {}
        self._extract_widgets(number,[widget],questions,bindings)
        quiz = Quiz(len(questions),dict(Counter(q.kind.value for q in questions)),questions)
        actual = {n:self._proofs[binding][0] for n,binding in bindings.items()}
        try:
            answers = self._saved_answers(quiz,bindings,actual)
        except BrowserError:
            return None
        return quiz, bindings, answers

    def _read_assessment_step(self, number):
        questions, bindings = [], {}
        self._extract_widgets(number, self._go_question(number), questions, bindings)
        self._bindings = bindings
        self._native_total = self._sequential_total
        self._quiz = Quiz(len(questions), dict(Counter(q.kind.value for q in questions)), questions)
        return self._quiz

    def extract_assessment_step(self, number):
        self._nav()
        if self._sequential_total is None or not 1 <= number <= self._sequential_total:
            raise BrowserError("逐题考试的题号或总数未知，已暂停。")
        if number != self._deferred_prefix_count + len(self._saved_steps) + 1:
            raise BrowserError("逐题考试的保存顺序不连续，已暂停。")
        return self._read_assessment_step(number)

    def save_assessment_step(self, number, answers):
        from .answers import answers_match, validate_answers
        if self.dry_run:
            raise BrowserError("测试模式不会保存当前题或解锁后续题目。")
        if number in self._step_attempted or number != self._deferred_prefix_count + len(self._saved_steps) + 1:
            raise BrowserError("本题已尝试保存或顺序不连续，结果未知时不能重复点击。")
        if self._quiz is None or validate_answers(self._quiz, answers)[1]:
            raise BrowserError("本题有效答案不足，未保存或解锁下一题。")
        if not answers_match(self.selected_answers(), answers)[0]:
            raise BrowserError("本题填写复核失败，未保存或解锁下一题。")
        if self._step_counter() != [number, self._sequential_total]:
            raise BrowserError("当前题号或总数已变化，已暂停，未保存。")
        button = self._frame().locator('.secure-toolbar-container .submit-button')
        self._wait(lambda: any(b.is_visible() and b.is_enabled() and
                   'is-disabled' not in (b.get_attribute('class') or '') for b in button.all()),
                   "本题保存按钮尚未启用，已暂停。")
        self._step_attempted.add(number)
        self._click(button)
        def advanced():
            nav = self._nav_snapshot()
            if number < self._sequential_total:
                return number + 1 in nav
            return self._confirmation() is not None or any(b.is_visible() for b in self._final_navigation().all())
        self._wait(advanced, "本题保存后未核实下一题或最终提交入口，已暂停，勿重复保存。")
        self._saved_steps[number] = (self._quiz, dict(self._bindings), dict(answers))

    def finish_assessment_steps(self):
        if self._deferred_prefix_count:
            self._restore_deferred_prefix()
        if self._sequential_total is None or set(self._saved_steps) != set(range(1, self._sequential_total + 1)):
            raise BrowserError("逐题考试尚未全部保存，已禁止最终提交。")
        if len(self._nav()) != self._sequential_total:
            raise BrowserError("逐题考试最终导航尚未完整，已禁止提交。")
        questions, bindings = [], {}
        for step in range(1, self._sequential_total + 1):
            quiz, saved, _ = self._saved_steps[step]
            for q in quiz.questions:
                number = len(questions) + 1
                questions.append(replace(q, number=number))
                bindings[number] = saved[q.number]
        self._bindings = bindings
        self._quiz = Quiz(len(questions), dict(Counter(q.kind.value for q in questions)), questions)
        return self._quiz

    def _restore_deferred_prefix(self):
        count = self._deferred_prefix_count
        total = self._sequential_total
        if total is None or set(self._saved_steps) != set(range(count + 1, total + 1)):
            raise BrowserError("逐题考试尚未全部保存，已禁止最终复核和提交。")
        if len(self._nav()) != total or self._result() is not None:
            raise BrowserError("最终确认页的作答状态未知，未补读旧题或重复提交。")
        confirm = self._confirmation()
        if confirm is None:
            self._click(self._final_navigation())
            confirm = self._wait(self._confirmation, "保存最后一题后未加载最终确认页，已暂停。")
        if confirm.is_checked() or not self._answered_navigation(self._nav()):
            raise BrowserError("最终确认页的作答状态未知，未补读旧题或重复提交。")
        self._check_unanswered()
        restored = {}
        for step in range(1,count+1):
            self._check_stopped()
            record = self._cached_saved_step(step)
            if record is None:
                quiz = self._read_assessment_step(step)
                answers = self._saved_answers(quiz,self._bindings,self.selected_answers())
                record = quiz,dict(self._bindings),answers
            restored[step] = record
        self._click(self._final_navigation())
        self._wait(self._confirmation,"完整复核后未返回最终确认页，已暂停。")
        if not self._answered_navigation(self._nav()):
            raise BrowserError("已保存题目的作答标记在最终复核时变化，已暂停。")
        self._check_unanswered()
        self._saved_steps.update(restored)
        self._deferred_prefix_count = 0

    def completed_assessment_answers(self):
        answers = {}
        for step in range(1,self._sequential_total+1):
            quiz,_,saved = self._saved_steps[step]
            for question in quiz.questions:
                answers[len(answers)+1] = saved[question.number]
        return answers

    def _go_question(self, number: int):
        frame = self._frame()
        # 自动解锁会同时更新标题和组件，必须在同一次 DOM 读取中取上一题快照。
        # 否则两次读取之间切题，可能把目标题组件误记为旧组件，永久等待它变化。
        previous = frame.evaluate('() => {' + _DOM_HELPERS + r'''
          const nodes=deep(document).filter(inViewport);
          const widgets=nodes.filter(isQuestion);
          return {titles:nodes.filter(e=>e.matches('.block__title-inner,.component__title-inner')).map(text),
            ids:widgets.map(e=>e.getAttribute('modelid')),
            componentNumbers:widgets.map(el=>deep(el).filter(e=>e.matches('.component__title-inner')&&visible(e))
              .map(e=>text(e).match(/(?:问题|Question)\s*(\d+)\s*$/i)).filter(Boolean).map(m=>Number(m[1])))};
        }''')
        previous_titles = previous['titles']
        current_numbers = previous['componentNumbers']
        own_current = (len(current_numbers) == 1 and bool(current_numbers[0]) and
                       all(n == number for n in current_numbers[0]))
        already_current = own_current or any(re.search(
            rf'(?:问题|Question)\s*{number}\s*$', t, re.I) for t in previous_titles)
        old_ids = set(previous['ids'])
        if not already_current:
            self._click(self._nav()[number])
        frame = self._frame()
        def ready():
            # 以本题标题确认分页完成，不把上一题的题干当作目标题。
            title = frame.locator(_QUESTION_TITLES).filter(has_text=re.compile(rf'(?:问题|Question)\s*{number}\s*$', re.I))
            title_in_view = any(t.evaluate('el => {' + _DOM_HELPERS + 'return inViewport(el);}') for t in title.all())
            widgets = [w for w in frame.locator(_QUESTION_COMPONENT).all() if w.is_visible()
                       and w.evaluate("el => {" + _DOM_HELPERS +
                                      "return inViewport(el);}")]
            # 组件自带题号时必须与目标一致，不能只凭已更新的外层标题读取旧题。
            widgets = [w for w in widgets if w.evaluate("(el, number) => {" + _DOM_HELPERS + r"""
              const titles=deep(el).filter(e=>e.matches('.component__title-inner') && visible(e));
              const numbers=titles.map(e=>text(e).match(/(?:问题|Question)\s*(\d+)\s*$/i))
                .filter(Boolean).map(m=>Number(m[1]));
              return !numbers.length || numbers.every(n=>n===number);
            }""", number)]
            if not title_in_view:
                # 题目组件本身仍在当前视口，组件自带的题号可以在上方视口之外。
                widgets = [w for w in widgets if w.evaluate('(el,number)=>{'+_DOM_HELPERS+r'''
                  const numbers=deep(el).filter(e=>e.matches('.component__title-inner')&&visible(e))
                    .map(e=>text(e).match(/(?:问题|Question)\s*(\d+)\s*$/i)).filter(Boolean).map(m=>Number(m[1]));
                  return numbers.length>0&&numbers.every(n=>n===number);
                }''',number)]
            if not already_current and widgets and all(w.get_attribute('modelid') in old_ids for w in widgets):
                return None
            return widgets if len(widgets) == 1 else None
        return self._wait(ready, f"第 {number} 题加载未完成，已暂停，未跳过该题。")

    def _widgets(self):
        return [w for w in self._scope().locator(_QUESTION_COMPONENT).all() if w.is_visible()]

    def extract_assessment(self) -> Quiz:
        questions = []
        bindings = {}
        nav = self._nav()
        if self._sequential_total is not None and len(nav) < self._sequential_total:
            raise BrowserError("当前为逐题解锁考试，必须逐题保存并核实全部题目，不能仅提取导航中的一部分。")
        if nav:
            for number in sorted(nav):
                # Locator 会随分页改变，必须在本题停留时读取。
                self._extract_widgets(number, self._go_question(number), questions, bindings)
        else:
            widgets = self._widgets()
            if not widgets:
                raise BrowserError("当前节点没有可识别的网页题目。")
            for number, widget in enumerate(widgets, 1):
                widget.scroll_into_view_if_needed()
                self._extract_widgets(number, [widget], questions, bindings)
        self._bindings = bindings
        self._native_total = len(nav) if nav else None
        self._quiz = Quiz(len(questions), dict(Counter(q.kind.value for q in questions)), questions)
        return self._quiz

    def _extract_widgets(self, source_number, widgets, questions, bindings):
        for widget in widgets:
            self._check_stopped()
            first_number = len(questions) + 1
            raw = self._wait(lambda: self._read_widget_ready(widget), "题干、图片或选项未加载完整，已暂停。")
            screenshot = ""
            if raw["images"]:
                screenshot = base64.b64encode(widget.screenshot(timeout=5000)).decode("ascii")
            if raw["tag"] == "OBJECT-MATCHING-VIEW":
                if not raw["categories"] or not raw["matches"] or len(raw["matches"]) > 26:
                    raise BrowserError("配对题的类别或选项未提取完整。")
                options = tuple(item["text"] for item in raw["matches"])
                if len(set(options)) != len(options):
                    raise BrowserError("配对题选项文字重复，不能可靠复核。")
                categories = tuple(item['text'] for item in raw['categories'])
                category_ids = tuple(item['id'] for item in raw['categories'])
                group_identity = ('matching', *sorted(categories), *raw['resources'])
                # 将一组配对分解为逐类别的单选；仍由适配器检查整组一一对应。
                for category, item in enumerate(raw["categories"]):
                    number = len(questions) + 1
                    stem = f'{raw["stem"]}\n配对类别：{item["text"]}。为此类别选择对应选项；同组类别不能使用重复选项。'
                    context = f'仅为类别“{item["text"]}”选择右侧对应选项。选项顺序：' + str(dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", options)))
                    identity = group_identity
                    if categories.count(item['text']) > 1 and item['id'] is not None:
                        identity += (raw['id'], item['id'])
                    questions.append(Question(number, QuestionType.SINGLE, stem,
                        [Option(chr(65+i), text) for i, text in enumerate(options)],
                        has_images=bool(raw["images"]), screenshot_b64=screenshot, image_input=True,
                        visual_context=context, memory_identity=identity))
                    bindings[number] = _Binding(source_number, raw["id"], category, options, raw["stem"],
                                                categories, category_ids)
            elif raw["tag"] == "MATCHING-VIEW":
                self._extract_dropdowns(source_number, raw, screenshot, questions, bindings)
            else:
                options = tuple(o["text"] for o in raw["options"])
                number = len(questions) + 1
                kind = QuestionType.MULTIPLE if all(o["multiple"] for o in raw["options"]) else QuestionType.SINGLE
                questions.append(Question(number, kind, raw["stem"],
                    [Option(chr(65+i), text) for i, text in enumerate(options)],
                    has_images=bool(raw["images"]), screenshot_b64=screenshot, image_input=True,
                    visual_context="截图选项按以下字母映射作答：" + str(dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", options))),
                    required_choices=self._required_choices(raw["stem"]) if kind == QuestionType.MULTIPLE else 1,
                    memory_identity=tuple(raw['resources'])))
                bindings[number] = _Binding(source_number, raw["id"], None, options, raw["stem"])
            # 提取时已经读到真实选中状态；未发生变化时无需再切题读取一遍。
            for number in range(first_number, len(questions) + 1):
                binding = bindings[number]
                self._proofs[binding] = (self._selected(raw, binding), tuple(raw['verification']))

    @staticmethod
    def _dropdown_structure(raw):
        rows = raw['dropdowns']
        if not rows or any(row is None or not row['label'] or
                           not 2 <= len(row['options']) <= 26 or
                           len(set(row['options'])) != len(row['options']) or '' in row['options']
                           for row in rows):
            return ()
        return tuple((row['label'], tuple(row['options'])) for row in rows)

    def _extract_dropdowns(self, source_number, raw, screenshot, questions, bindings):
        rows = self._dropdown_structure(raw)
        labels = tuple(label for label, _ in rows)
        identity = ('dropdown-matching', *sorted(labels), *raw['resources'])
        for row, (label, options) in enumerate(rows):
            number = len(questions) + 1
            stem = f'{raw["stem"]}\n当前项目：{label}。为此项目选择一个选项；每个项目独立选择，选项可以重复使用。'
            questions.append(Question(
                number, QuestionType.SINGLE, stem,
                [Option(chr(65+i), choice) for i, choice in enumerate(options)],
                has_images=bool(raw["images"]), screenshot_b64=screenshot, image_input=True,
                visual_context="为当前项目选择一个下拉选项，按提供的选项字母作答。",
                required_choices=1, memory_identity=identity,
            ))
            bindings[number] = _Binding(
                source_number, raw['id'], None, options, raw['stem'], dropdown_row=row, dropdown_rows=rows,
            )

    def _read_widget_ready(self,widget):
        self._guard_frames.add(self._frame())
        raw = widget.evaluate(selection_guard.guarded_reader(_WIDGET_JS),self._guard_key)
        if not raw["stem"] or not raw["imagesReady"]:
            return None
        if raw["tag"] == "MCQ-VIEW" and (len(raw["options"]) < 2 or len(raw["options"]) > 26 or any(not o["text"] for o in raw["options"])):
            return None
        if raw["options"] and any(o["declaredCount"] and o["declaredCount"] != len(raw["options"]) for o in raw["options"]):
            return None
        if raw["options"] and len({o["multiple"] for o in raw["options"]}) != 1:
            return None
        if raw["tag"] == "OBJECT-MATCHING-VIEW" and (
                not raw["categories"] or not raw["matches"] or
                any(not c["text"] or not c["letter"] for c in raw["categories"]) or
                any(not o["text"] for o in raw["matches"])):
            return None
        if raw["tag"] == "MATCHING-VIEW" and not self._dropdown_structure(raw):
            return None
        return raw

    @staticmethod
    def _required_choices(stem):
        words = {"一":1,"两":2,"二":2,"三":3,"四":4,"五":5,"六":6,"two":2,"three":3,"four":4,"five":5}
        match = re.search(r"选择\s*([一两二三四五六\d]+)\s*项|choose\s+(two|three|four|five|\d+)", stem, re.I)
        if not match:
            return None
        value = (match[1] or match[2]).lower()
        return int(value) if value.isdigit() else words[value]

    def _binding_widget(self, binding, *, capture=False):
        nav = self._nav()
        if binding.dropdown_row is not None:
            tag = 'matching-view'
        else:
            tag = 'object-matching-view' if binding.category is not None else 'mcq-view'
        selector = tag + _COMPONENT + self._selector(binding.widget_id)
        widget = self._frame().locator(selector)
        current = widget.count() == 1 and widget.evaluate('el=>{'+_DOM_HELPERS+'return inViewport(el);}')
        if nav and current:
            self._wait(lambda: self._frame().evaluate('()=>{'+_DOM_HELPERS+r'''
              return deep(document).filter(e=>isQuestion(e)&&inViewport(e)).length===1;
            }'''),"当前视口题目组件不唯一，已暂停，未填写或提交。")
        if nav and not current:
            self._go_question(binding.source_number)
            widget = self._frame().locator(selector)
        if widget.count() != 1 or not widget.is_visible():
            raise BrowserError("题目组件已变化，已暂停。")
        if nav and not widget.evaluate('(el,number)=>{'+_DOM_HELPERS+r'''
          const numbers=deep(el).filter(e=>e.matches('.component__title-inner')&&visible(e))
            .map(e=>text(e).match(/(?:问题|Question)\s*(\d+)\s*$/i)).filter(Boolean).map(m=>Number(m[1]));
          return !numbers.length||numbers.every(n=>n===number);
        }''',binding.source_number):
            raise BrowserError("题目组件的题号已变化，已暂停。")
        if capture:
            self._guard_frames.add(self._frame())
            raw = widget.evaluate(selection_guard.guarded_reader(_WIDGET_JS),self._guard_key)
        else:
            raw = widget.evaluate(_WIDGET_JS)
        self._check_binding_structure(raw, binding)
        return widget, raw

    def _check_binding_structure(self, raw, binding):
        if binding.dropdown_row is not None:
            if raw['stem'] != binding.stem or self._dropdown_structure(raw) != binding.dropdown_rows:
                raise BrowserError("下拉匹配题的题干、行或选项已变化，已禁止填写和提交。")
            return
        texts = tuple(o["text"] for o in (raw["matches"] if binding.category is not None else raw["options"]))
        if raw["stem"] != binding.stem or texts != binding.option_texts:
            raise BrowserError("题干或选项已变化，已禁止填写和提交。")
        if binding.category is not None:
            categories = tuple(c['text'] for c in raw['categories'])
            category_ids = tuple(c['id'] for c in raw['categories'])
            if categories != binding.categories or category_ids != binding.category_ids:
                raise BrowserError("配对类别已变化，已禁止填写和提交。")

    def fill_answers(self, answers):
        if set(answers) != set(self._bindings):
            raise BrowserError("答案数量与网页题目不一致。")
        self._check_inline_structure()
        for binding in self._bindings.values():
            self._proofs.pop(binding,None)
        # 在任何鼠标写操作前校验整组配对，不依赖模型输出的正确性。
        pair_answers = {}
        for number, binding in self._bindings.items():
            answer = answers[number]
            if not answer or any(not 'A' <= char <= chr(64 + len(binding.option_texts)) for char in answer):
                raise BrowserError("网页答案存在不合法的选项。")
            if binding.dropdown_row is not None and len(answer) != 1:
                raise BrowserError("下拉匹配题的每一行必须选择一个选项。")
            if binding.category is not None:
                used_letters = pair_answers.setdefault(binding.widget_id, set())
                if len(answer) != 1 or answer in used_letters:
                    raise BrowserError("配对答案重复或不合法，未填写和提交。")
                used_letters.add(answer)
        used = {}
        for number, binding in self._bindings.items():
            self._check_stopped()
            widget, raw = self._binding_widget(binding)
            answer = answers[number]
            if binding.dropdown_row is not None:
                self._fill_dropdown(widget, raw, binding, answer)
            elif binding.category is not None:
                if len(answer) != 1 or answer in used.setdefault(binding.widget_id, set()):
                    raise BrowserError("配对答案重复或不合法，未提交。")
                used[binding.widget_id].add(answer)
                index = ord(answer) - 65
                self._click(widget.locator('.objectMatching-category-item').nth(binding.category))
                self._click(widget.locator('.objectMatching-option-item').nth(index))
            else:
                for index, option in enumerate(raw["options"]):
                    self._check_stopped()
                    desired = chr(65+index) in answer
                    # radio 由选择目标自动取消旧选项；点击旧选项会把答案改回去。
                    if (option["multiple"] and option["selected"] != desired) or (
                            not option["multiple"] and desired and not option["selected"]):
                        self._click(widget.locator('.mcq__item').nth(index))

    def _fill_dropdown(self, widget, raw, binding, answer):
        row = widget.locator(_DROPDOWN_ROW).nth(binding.dropdown_row)
        before = raw['dropdowns'][binding.dropdown_row]
        target = ord(answer) - 65
        if before['selected'] != target:
            if not before['open']:
                self._click(row.locator(_DROPDOWN_BUTTON))
            option = row.locator(_DROPDOWN_OPTION).nth(target).locator('.dropdown__item-inner')
            self._wait(option.is_visible, "当前行的下拉菜单未展开，已暂停。")
            self._check_binding_structure(widget.evaluate(_WIDGET_JS), binding)
            self._click(option)
        elif before['open']:
            self._click(row.locator(_DROPDOWN_BUTTON))
        else:
            return

        def selection_updated():
            raw = widget.evaluate(_WIDGET_JS)
            self._check_binding_structure(raw, binding)
            current = raw['dropdowns'][binding.dropdown_row]
            return current['selected'] == target and not current['open']

        self._wait(selection_updated, "下拉匹配题的选中状态未更新，已暂停，未保存本题。")

    @staticmethod
    def _selected(raw, binding):
        if binding.dropdown_row is not None:
            index = raw['dropdowns'][binding.dropdown_row]['selected']
            return chr(65 + index) if index >= 0 else ''
        if binding.category is None:
            return ''.join(chr(65+i) for i, option in enumerate(raw['options']) if option['selected'])
        category_letter = raw['categories'][binding.category]['letter']
        return ''.join(chr(65+i) for i, option in enumerate(raw['matches'])
                       if option['selectedCategory'] == category_letter)

    def selected_answers(self):
        self._check_stopped()
        self._check_inline_structure()
        proofs=[self._proofs.get(binding) for binding in self._bindings.values()]
        if proofs and all(p is not None for p in proofs) and self._frame().evaluate(selection_guard.VALIDATE,
                {'key':self._guard_key,'stamps':[[binding.widget_id,*proof[1]]
                 for binding,proof in zip(self._bindings.values(),proofs)]}):
            return {number:self._proofs[binding][0] for number,binding in self._bindings.items()}
        result = {}
        raw_by_widget = {}
        for number, binding in self._bindings.items():
            self._check_stopped()
            key=(binding.source_number,binding.widget_id)
            if key not in raw_by_widget:
                _,raw_by_widget[key]=self._binding_widget(binding,capture=True)
            raw=raw_by_widget[key]
            result[number] = self._selected(raw, binding)
            self._proofs[binding]=(result[number],tuple(raw['verification']))
        return result

    def _check_inline_structure(self):
        nav=self._nav()
        if nav:
            if self._native_total is not None and self._sequential_total is None and len(nav)!=self._native_total:
                raise BrowserError('本轮题目数量已变化，已禁止提交旧的答案汇总。')
            return
        widgets = self._widgets()
        ids = [widget.get_attribute('modelid') for widget in widgets]
        expected = {binding.widget_id for binding in self._bindings.values()}
        if len(ids) != len(set(ids)) or set(ids) != expected:
            raise BrowserError("练习组件数量已变化，页面可能仍在加载，已禁止填写和提交。")
        if self._page_requests_pending([self._frame()]):
            raise BrowserError("练习页面仍有未完成的加载请求，已暂停，未提交。")

    def _result(self):
        return self._frame().evaluate(_RESULT_JS)

    def submit_assessment(self, answers):
        from .answers import answers_match
        self._check_course_relaunch()
        if self._submission_attempted:
            raise BrowserError("本轮已尝试最终提交，结果未知时不能重复点击。")
        if self._result() is not None:
            raise BrowserError("本轮提交前已有成绩显示，无法区分旧结果，已暂停。")
        if self._sequential_total is not None:
            if set(self._saved_steps) != set(range(1, self._sequential_total + 1)):
                raise BrowserError("逐题考试尚未全部保存，已禁止最终提交。")
            expected = [bindings[q.number] for step in sorted(self._saved_steps)
                        for quiz, bindings, _ in [self._saved_steps[step]] for q in quiz.questions]
            if list(self._bindings.values()) != expected:
                raise BrowserError("逐题考试整卷汇总不完整，已禁止最终提交。")
            if (self._quiz is None or self._quiz.structural_issues() or
                    self._saved_answers(self._quiz, self._bindings, answers) != answers or
                    self.completed_assessment_answers() != answers):
                raise BrowserError("逐题保存记录与整卷答案不一致，已禁止最终提交。")
        elif not answers_match(self.selected_answers(), answers)[0]:
            raise BrowserError("提交前填写复核失败。")
        if not self._nav():
            seen = set()
            for binding in self._bindings.values():
                if binding.widget_id in seen:
                    continue
                seen.add(binding.widget_id)
                widget, raw = self._binding_widget(binding)
                if raw["submitted"]:
                    raise BrowserError("该练习已有提交状态，无法区分本轮结果，已暂停。")
                self._click(widget.get_by_role("button", name=re.compile(
                    r"^(?:提交|提交答案|检查|检查答案|Submit|Check|Check Answer)$", re.I)))
                self._wait(lambda: self._practice_mark(binding), "该练习提交后未读取到本轮逐题判定，已暂停。")
            marks = [self._practice_mark(binding) for binding in self._unique_bindings()]
            self._last_score = 100.0 * sum(mark == "correct" for mark in marks) / len(marks)
            return AssessmentReport(self._last_score)
        frame = self._frame()
        if self._confirmation() is None:
            self._click(self._final_navigation())
        confirm = self._wait(self._confirmation, "提交确认页面未加载完整。")
        self._check_unanswered()
        if self._sequential_total is not None:
            nav = self._nav()
            if len(nav) != self._sequential_total or not self._answered_navigation(nav):
                raise BrowserError("提交页未确认整卷已作答，已禁止最终提交。")
        self._check_stopped()
        confirm.check(timeout=4000)
        self._check_stopped()
        if not confirm.is_checked():
            raise BrowserError("网页确认框尚未勾选，已禁止最终提交。")
        submit = frame.locator('.adaptive-assessment-submit')
        self._wait(lambda: any(b.is_visible() and b.is_enabled() and
                   not {'disabled','is-disabled'} & set((b.get_attribute('class') or '').split())
                   for b in submit.all()), "最终提交按钮尚不可用。")
        self._submission_attempted = True
        self._click(submit)  # 每轮只点击一次，结果未知时不能盲目重试。
        self._last_score = self._wait(self._result, "提交后未读取到本轮成绩，请人工确认，勿重复提交。")
        return AssessmentReport(self._last_score)

    @staticmethod
    def _review_pair_answers(raw):
        """将已公布的解析表映射到当前配对选项。"""
        categories = raw['categories']
        labels = [category['text'] for category in categories]
        options = [option['text'] for option in raw['matches']]
        rows = raw['pairRows']
        if (not rows or Counter(label for label, _ in rows) != Counter(labels) or
                len(set(options)) != len(options) or len({choice for _, choice in rows}) != len(rows) or
                any(choice not in options for _, choice in rows)):
            return {}

        choices = {label: choice for label, choice in rows if labels.count(label) == 1}
        answers = {i: chr(65 + options.index(choices[label])) for i, label in enumerate(labels) if label in choices}
        if len(answers) == len(categories):
            return answers

        # 播放器按原始类别序号输出解析表，作答界面可以打乱显示顺序。
        # 重名行只在序号完整且所有类别文字均与表格原始顺序吻合时采用。
        indices = [category['index'] for category in categories]
        if (not answers or set(indices) != {str(i) for i in range(len(categories))} or
                any(category['id'] != index or rows[int(index)][0] != category['text']
                    for category, index in zip(categories, indices))):
            return answers
        return {i: chr(65 + options.index(rows[int(index)][1])) for i, index in enumerate(indices)}

    @staticmethod
    def _review_dropdown_answers(raw):
        table = raw['tableRows']
        if len(table) < 2 or any(tag != 'TH' for tag, _ in table[0]):
            return {}
        headings = [label for _, label in table[0]]
        if not all(headings) or len(headings) != len(set(headings)):
            return {}
        rows = raw['dropdowns']
        labels = [row['label'] for row in rows]
        if len(labels) != len(set(labels)):
            return {}
        by_label = {}
        for cells in table[1:]:
            if len(cells) != len(headings) or any(tag != 'TD' for tag, _ in cells):
                return {}
            for (_, label), choice in zip(cells, headings):
                if not label:
                    continue
                if label in by_label or label not in labels:
                    return {}
                by_label[label] = choice
        if set(by_label) != set(labels) or any(by_label[row['label']] not in row['options'] for row in rows):
            return {}
        return {i: chr(65 + row['options'].index(by_label[row['label']])) for i, row in enumerate(rows)}

    def review_assessment(self):
        if self._last_score is None:
            raise BrowserError("本轮成绩未知，未打开错题重做。")
        return self._read_assessment_review(self._open_review())

    def _read_assessment_review(self, use_result_navigation):
        wrong, correct, uncertain, ungraded = [], [], [], []
        feedback = {}
        published = {}
        grades={}
        for number, binding in self._bindings.items():
            key=(binding.source_number,binding.widget_id)
            if key not in grades:
                widget, raw = self._binding_widget(binding)
                def graded():
                    raw=widget.evaluate(_WIDGET_JS)
                    self._check_binding_structure(raw, binding)
                    return raw if raw['correct'] or raw['wrong'] else None
                if not use_result_navigation:
                    raw = self._wait(graded,'未读取到本轮逐题判定，已暂停，未重做。')
                if binding.dropdown_row is not None:
                    published_rows = self._review_dropdown_answers(raw)
                else:
                    published_rows = self._review_pair_answers(raw) if binding.category is not None else {}
                grades[key] = (raw, published_rows)
            raw, pair_answers = grades[key]
            if not raw['correct'] and not raw['wrong']:
                ungraded.append(number)
                continue
            if raw["wrong"]:
                dropdown_answer = pair_answers.get(binding.dropdown_row) if binding.dropdown_row is not None else None
                if dropdown_answer and dropdown_answer == self._selected(raw, binding):
                    correct.append(number)
                    continue
                detail = raw["feedback"]
                if raw["correctTexts"]:
                    detail += " 页面解析标记的正确选项内容：" + "；".join(raw["correctTexts"])
                if not detail:
                    detail = ("页面明确标记本配对组未全部正确，不能推断每个类别都错。"
                              if binding.category is not None or binding.dropdown_row is not None
                              else "页面明确标记本题错误，但没有展示文字解析。")
                wrong.append(number)
                feedback[number] = detail
                if binding.category is not None:
                    uncertain.append(number)
                    if binding.category in pair_answers:
                        published[number] = pair_answers[binding.category]
                elif binding.dropdown_row is not None:
                    if dropdown_answer:
                        published[number] = dropdown_answer
                    else:
                        uncertain.append(number)
                elif raw['correctTexts'] and len(set(binding.option_texts)) == len(binding.option_texts):
                    question = self._quiz.questions[number - 1]
                    marked = set(raw['correctTexts'])
                    complete_count = question.required_choices == len(marked)
                    classified = all(option['correct'] != option['incorrect'] for option in raw['options'])
                    if marked <= set(binding.option_texts) and (complete_count or (
                            question.kind == QuestionType.MULTIPLE and question.required_choices is None and classified)):
                        published[number] = ''.join(chr(65+i) for i, label in enumerate(binding.option_texts) if label in marked)
            else:
                correct.append(number)
        return AssessmentReport(self._last_score, tuple(wrong), feedback, reviewed=True,
                                correct_numbers=tuple(correct),uncertain_wrong_numbers=tuple(uncertain),
                                correct_answers=published, ungraded_numbers=tuple(ungraded))

    def restart_assessment(self):
        self._check_course_relaunch()
        if not self._nav():
            for binding in self._unique_bindings():
                widget, _ = self._binding_widget(binding)
                self._click(widget.get_by_role("button", name=re.compile(
                    r"^(?:重置|重试|再次尝试|Reset|Retry|Try again)$", re.I)))
                self._wait(lambda: not widget.evaluate(_WIDGET_JS)["submitted"], "练习重置状态未确认，已暂停。")
            self._bindings = {}
            self._quiz = None
            self._last_score = None
            self._proofs.clear()
            self._deferred_prefix_count = 0
            return
        self._click(self._final_navigation())
        self._wait(lambda: self._last_score is not None and self._result() == self._last_score,
                   "未回到本轮成绩页，已暂停，未重置。")
        self._click(self._result_reset())
        self._wait(lambda: self._result() is None, "重做后旧成绩未消失，已暂停。")
        self._bindings = {}
        self._quiz = None
        self.open_assessment()

    def _unique_bindings(self):
        return list({b.widget_id: b for b in self._bindings.values()}.values())

    def _practice_mark(self, binding):
        _, raw = self._binding_widget(binding)
        return "correct" if raw["correct"] else ("wrong" if raw["wrong"] else None)

    def next_node(self) -> bool:
        self._check_stopped()
        current = self.current_node()
        if self._last_score == 100 and current.kind == NodeKind.ASSESSMENT:
            self._wait(lambda: self._completion_status(current) == 'completed',
                       "成绩已确认，但目录完成标记尚未更新，已停在当前节点。")
        tasks = self._tasks() if not self._nav() else []
        if self._cursor + 1 < len(tasks):
            next_cursor = self._cursor + 1
            article = self._article(tasks[next_cursor]["id"])
            self._check_stopped()
            article.scroll_into_view_if_needed(timeout=4000)
            self._check_stopped()
            self._cursor = next_cursor
            return True
        old_url = self._frame().url
        # 子节点已完成不代表整个小节已完成，目录可能还有延迟加载的任务。
        def section_completed():
            return self._exam_directory()['status'] == 'completed'
        self._wait(section_completed, "当前小节未全部完成或完成标记尚未更新，已暂停，未翻页。")
        next_button = self._require_page().locator('#course-content button[class*="moduleNavBtn"][class*="next--"]')
        if not next_button.count():
            final = self._require_page().evaluate(r"""item => {
              const clean=s=>(s||'').replace(/\s+/g,' ').trim();
              const siblings=Array.from(document.querySelectorAll('#course-outline button[id^="submodule-button"]')).filter(e=>e.id===item.id);
              const active=siblings.filter(e=>{
                const name=e.querySelector('[class*="subModuleName--"][title]')||e.querySelector('[title]');
                return clean(name?.getAttribute('title'))===item.title;
              });
              if(active.length!==1) return false;
              const id=item.id.replace('submodule-button-','node-button-');
              const headers=Array.from(document.querySelectorAll('button[id^="node-button-"]'));
              return headers.length>0 && headers.at(-1).id===id && siblings.at(-1)===active[0];
            }""", self._exam_directory())
            if not final:
                raise BrowserError("尚未确认下一页按钮或课程终点，已暂停，未把缺少按钮当作全部完成。")
            return False
        self._click(next_button)
        self._wait(lambda: self._frame().url != old_url, "下一小节加载超时，已暂停，未跳过该节。")
        self._cursor = 0
        return True
