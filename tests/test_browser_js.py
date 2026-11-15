from __future__ import annotations

import unittest
from threading import Event, Timer
from unittest.mock import patch

from autoquiz.browser import BrowserError, BrowserStopped, EdgeBrowser, _DIRECTORY_READ_JS, _EXTRACT_JS, _FILL_JS
from autoquiz.parser import parse_quiz


HTML = r"""
<html><body>
<div>章节测验 题量：3 一、单选题（共1题） 二、多选题（共1题） 三、判断题（共1题）</div>
<div class="questionLi" id="question1">
  <div class="mark_name">1【单选题】</div>
  <div class="questionText">下列哪项正确？</div>
  <ul>
    <li><input type="radio" name="q1"><span class="check_icon">A</span><span>甲</span></li>
    <li><input type="radio" name="q1"><span class="check_icon">B</span><span>乙</span></li>
  </ul>
</div>
<div class="questionLi" id="question2">
  <div class="mark_name">2【多选题】下列哪些正确？</div>
  <ul>
    <li><input type="checkbox" name="q2"><span class="check_icon">A</span><span>甲</span></li>
    <li><input type="checkbox" name="q2"><span class="check_icon">B</span><span>乙</span></li>
    <li><input type="checkbox" name="q2"><span class="check_icon">C</span><span>丙</span></li>
  </ul>
</div>
<div class="questionLi" id="question3">
  <div class="mark_name">3【判断题】这个说法正确吗？</div>
  <ul>
    <li><input type="radio" name="q3"><span class="check_icon">A</span><span>对</span></li>
    <li><input type="radio" name="q3"><span class="check_icon">B</span><span>错</span></li>
  </ul>
</div>
</body></html>
"""


DIRECTORY_HTML = r"""
<html><body>
<div id="pageState">2.1 当前正文</div>
<div class="posCatalog_level" style="display:block"><ul>
  <li><div class="posCatalog_select posCatalog_active" id="cur101">
    <span class="posCatalog_name" title="急性中毒（一）" onclick="selectChapter(101)">
      <em class="posCatalog_sbar">2.1</em> 急性中毒（一）
    </span>
    <span class="icon_Completed prevTips"><span class="prevHoverTips">已完成<i></i></span></span>
  </div></li>
  <li><div class="posCatalog_select" id="cur102">
    <span class="posCatalog_name" title="急性中毒（二）" onclick="selectChapter(102)">
      <em class="posCatalog_sbar">2.2</em> 急性中毒（二）
    </span>
    <input type="hidden" value="1" class="jobUnfinishCount">
    <span class="catalog_points_yi prevTips"><span class="prevHoverTips">1个待完成任务点<i></i></span>
      <span class="orangeNew">1</span></span>
  </div></li>
  <li><div class="posCatalog_select" id="cur103">
    <span class="posCatalog_name" title="急性中毒（三）" onclick="selectChapter(103)">
      <em class="posCatalog_sbar">2.3</em> 急性中毒（三）
    </span>
    <input type="hidden" value="1" class="jobUnfinishCount">
    <span class="catalog_points_yi prevTips"><span class="prevHoverTips">1个待完成任务点</span></span>
  </div></li>
</ul></div>
<script>
  window.selectChapter = chapterId => {
    document.querySelectorAll('.posCatalog_select').forEach(el => el.classList.remove('posCatalog_active'));
    document.getElementById('cur' + chapterId).classList.add('posCatalog_active');
    document.getElementById('pageState').textContent = '已切换到 ' + chapterId;
  };
</script>
</body></html>
"""


def make_quiz_tab_page(initial_content: str, tab_number: int) -> str:
    return f"""
    <html><body>
      <div id="mainid">
        <ul>
          <li title="章节测验" cardid="658655490" tabindex="0" role="option" id="dct{tab_number}"
              onclick="changeDisplayContent({tab_number},{tab_number},'705053966','265728801','155003801','');">
            <div class="prev_white"><span class="num">{tab_number}</span><span class="spanText">章节测验</span></div>
          </li>
        </ul>
        <div id="initial">{initial_content}</div>
        <div id="quizHost"></div>
      </div>
      <script>
        window.quizTabClicks = 0;
        window.changeDisplayContent = () => {{
          window.quizTabClicks += 1;
          setTimeout(() => {{
            document.getElementById('quizHost').innerHTML = `
              <div>章节测验 题量：1 一、单选题（共1题）</div>
              <div class="questionLi" id="question1">
                <div class="mark_name">1【单选题】测试题</div>
                <ul><li><input type="radio"><span class="check_icon">A</span>选项</li></ul>
              </div>`;
          }}, 300);
        }};
      </script>
    </body></html>
    """


def make_task_scan_page(with_quiz: bool = True) -> str:
    quiz_html = """
      <div>随堂练习 题量：1 一、单选题（共1题）</div>
      <div class="questionLi" id="question1">
        <div class="mark_name">1【单选题】扫描标签发现的测试题</div>
        <ul><li><input type="radio"><span class="check_icon">A</span>选项</li></ul>
      </div>
    """ if with_quiz else ""
    return f"""
    <html><body>
      <ul>
        <li title="视频" onclick="changeDisplayContent('video')">视频</li>
        <li title="资料" onclick="changeDisplayContent('material')">资料</li>
        <li title="随堂练习" onclick="changeDisplayContent('practice')">随堂练习</li>
        <li title="讨论" onclick="changeDisplayContent('discussion')">讨论</li>
      </ul>
      <div id="content">学习目标</div>
      <script>
        window.clickedTabs = [];
        window.changeDisplayContent = kind => {{
          window.clickedTabs.push(kind);
          setTimeout(() => {{
            const quiz = {quiz_html!r};
            document.getElementById('content').innerHTML =
              kind === 'practice' && quiz ? quiz : '<div>' + kind + ' 内容</div>';
          }}, 150);
        }};
      </script>
    </body></html>
    """


class BrowserJSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            from playwright.sync_api import sync_playwright
            cls.manager = sync_playwright().start()
            cls.browser = cls.manager.chromium.launch(channel="msedge", headless=True)
        except Exception as exc:
            if hasattr(cls, "manager"):
                cls.manager.stop()
            raise unittest.SkipTest(f"无法启动本机 Edge 进行 JS 集成测试：{exc}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls.manager.stop()

    def setUp(self) -> None:
        self.page = self.browser.new_page()
        self.page.set_content(HTML)

    def tearDown(self) -> None:
        self.page.close()

    def test_extract_real_dom(self) -> None:
        raw = self.page.evaluate(_EXTRACT_JS)
        quiz = parse_quiz(raw)
        self.assertEqual(quiz.total, 3)
        self.assertEqual(quiz.structural_issues(), [])
        self.assertEqual(quiz.questions[0].stem, "下列哪项正确？")
        self.assertEqual([option.letter for option in quiz.questions[1].options], ["A", "B", "C"])

    def test_fill_and_reextract_real_dom(self) -> None:
        result = self.page.evaluate(_FILL_JS, {"answers": {"1": "B", "2": "AC", "3": "B"}})
        self.assertEqual(result["missing"], [])
        raw = self.page.evaluate(_EXTRACT_JS)
        selected = {
            question["number"]: "".join(
                option["letter"] for option in question["options"] if option["selected"]
            )
            for question in raw["questions"]
        }
        self.assertEqual(selected, {1: "B", 2: "AC", 3: "B"})

    def test_question_screenshots_are_captured(self) -> None:
        from autoquiz.browser import EdgeBrowser

        edge = EdgeBrowser()
        edge.page = self.page
        raw = edge.extract_quiz()
        self.assertEqual(len(raw["questions"]), 3)
        self.assertTrue(all(question.get("screenshot_b64") for question in raw["questions"]))
        self.assertTrue(all(not question.get("screenshot_error") for question in raw["questions"]))

    def test_real_mouse_fill_and_selected_state_verification(self) -> None:
        from autoquiz.browser import EdgeBrowser

        edge = EdgeBrowser()
        edge.page = self.page
        edge.fill_answers({1: "B", 2: "AC", 3: "B"})
        self.assertEqual(edge.selected_answers(), {1: "B", 2: "AC", 3: "B"})

    def test_incomplete_video_task_dialog_chooses_next_section(self) -> None:
        from autoquiz.browser import EdgeBrowser

        self.page.set_content(r"""
          <div class="layui-layer" style="display:block;width:400px;height:200px">
            当前章节还有任务点未完成，是否去完成？
            <a id="study" href="#" style="display:inline-block;width:60px;height:30px">去学习</a>
            <a id="next" href="#" style="display:inline-block;width:60px;height:30px"
               onclick="window.nextChosen=true; return false;">下一节</a>
          </div>
        """)
        edge = EdgeBrowser()
        edge.page = self.page
        self.assertTrue(edge._click_incomplete_task_next())
        self.assertTrue(self.page.evaluate("window.nextChosen === true"))

    def test_selected_class_on_inner_icon_is_detected(self) -> None:
        from autoquiz.browser import EdgeBrowser

        self.page.locator("#question1 li").nth(1).locator(".check_icon").evaluate(
            "el => el.classList.add('check_answer')"
        )
        edge = EdgeBrowser()
        edge.page = self.page
        self.assertEqual(edge.selected_answers()[1], "B")

    def test_submit_closes_task_dialog_scrolls_confirms_and_waits_for_result(self) -> None:
        from autoquiz.browser import EdgeBrowser

        self.page.set_content(HTML + r"""
          <div class="popDiv maskDiv workpop" id="task" style="display:block;width:400px;height:200px">
            当前章节还有任务点未完成
            <a class="popClose fr" href="#" style="display:block;width:30px;height:30px"
               onclick="this.parentElement.style.display='none';window.taskClosed=true;return false;">×</a>
          </div>
          <button id="submit" style="display:block;width:100px;height:40px">提交</button>
          <div class="popDiv maskDiv workpop" id="confirm" style="display:none;width:400px;height:200px">
            <p class="popWord fs16 colorIn">确认提交？</p>
            <a id="confirmSubmit" class="jb_btn jb_btn_92 fr fs14" href="#"
               style="display:block;width:80px;height:40px">提交</a>
          </div>
          <script>
            document.getElementById('submit').onclick = () => {
              document.getElementById('confirm').style.display = 'block';
            };
            document.getElementById('confirmSubmit').onclick = () => {
              document.getElementById('confirm').style.display = 'none';
              document.body.insertAdjacentHTML('beforeend', '<div>我的答案：B</div>');
              return false;
            };
          </script>
        """)
        edge = EdgeBrowser()
        edge.page = self.page
        expected = {1: "B", 2: "AC", 3: "B"}
        edge.fill_answers(expected)
        edge.submit(expected)
        self.assertTrue(self.page.evaluate("window.taskClosed === true"))
        self.assertIn("我的答案", self.page.locator("body").inner_text())

    def test_open_quiz_from_learning_objectives_clicks_real_tab(self) -> None:
        from autoquiz.browser import EdgeBrowser

        self.page.set_content(make_quiz_tab_page("学习目标：掌握急救原则", 3))
        edge = EdgeBrowser()
        edge.page = self.page
        self.assertTrue(edge.open_quiz())
        self.assertEqual(self.page.evaluate("window.quizTabClicks"), 1)
        self.assertEqual(self.page.locator(".questionLi").count(), 1)

    def test_open_quiz_from_completed_video_does_not_treat_video_as_quiz(self) -> None:
        from autoquiz.browser import EdgeBrowser

        self.page.set_content(
            make_quiz_tab_page("<div>任务点已完成</div><video></video>", 2)
        )
        edge = EdgeBrowser()
        edge.page = self.page
        self.assertFalse(edge.is_quiz_completed())
        self.assertTrue(edge.open_quiz())
        self.assertEqual(self.page.evaluate("window.quizTabClicks"), 1)
        self.assertEqual(self.page.locator(".questionLi").count(), 1)

    def test_open_quiz_supports_tab_named_only_quiz(self) -> None:
        from autoquiz.browser import EdgeBrowser

        content = make_quiz_tab_page("学习目标", 4).replace(
            'title="章节测验"', 'title="测验"', 1
        )
        self.page.set_content(content)
        edge = EdgeBrowser()
        edge.page = self.page
        self.assertTrue(edge.open_quiz())
        self.assertEqual(self.page.evaluate("window.quizTabClicks"), 1)

    def test_open_quiz_scans_task_tabs_until_visible_questions_appear(self) -> None:
        from autoquiz.browser import EdgeBrowser

        self.page.set_content(make_task_scan_page(with_quiz=True))
        edge = EdgeBrowser()
        edge.page = self.page
        self.assertTrue(edge.open_quiz())
        self.assertEqual(
            self.page.evaluate("window.clickedTabs"),
            ["video", "material", "practice"],
        )
        self.assertIn("扫描标签发现的测试题", self.page.locator("#content").inner_text())

    def test_open_quiz_scans_all_tabs_and_returns_false_when_none_has_quiz(self) -> None:
        from autoquiz.browser import EdgeBrowser

        self.page.set_content(make_task_scan_page(with_quiz=False))
        edge = EdgeBrowser()
        edge.page = self.page
        self.assertFalse(edge.open_quiz())
        self.assertEqual(
            self.page.evaluate("window.clickedTabs"),
            ["video", "material", "practice", "discussion"],
        )

    def test_hidden_old_questions_do_not_count_as_current_quiz(self) -> None:
        from autoquiz.browser import EdgeBrowser

        self.page.set_content(
            make_task_scan_page(with_quiz=False)
            + '<div class="questionLi" style="display:none">隐藏旧题</div>'
        )
        edge = EdgeBrowser()
        edge.page = self.page
        self.assertIsNone(edge._quiz_frame())
        self.assertFalse(edge.open_quiz())

    def test_waits_for_all_questions_after_switching_chapters(self) -> None:
        self.page.set_content(DIRECTORY_HTML + HTML)
        self.page.evaluate(r"""() => {
          const select = window.selectChapter;
          window.selectChapter = id => {
            select(id);
            setTimeout(() => {
              const host = document.createElement('section');
              host.innerHTML = '<div>章节测验 题量：2 一、单选题（共2题）</div>';
              document.querySelectorAll('.questionLi').forEach(el => el.remove());
              [...document.body.children].find(el => el.innerText.startsWith('章节测验 题量：3')).remove();
              document.body.append(host);
              const question = n => `<div class="questionLi" id="question${n}">
                <div class="mark_name">${n}【单选题】新小节测试题${n}</div>
                <ul><li><span class="check_icon">A</span>选项</li></ul></div>`;
              host.insertAdjacentHTML('beforeend', question(1));
              setTimeout(() => host.insertAdjacentHTML('beforeend', question(2)), 500);
            }, 900);
          };
        }""")
        edge = EdgeBrowser()
        edge.page = self.page
        self.assertTrue(edge.next_unfinished_section())
        self.assertTrue(edge.open_quiz())
        quiz = parse_quiz(edge.extract_quiz(include_screenshots=False))
        self.assertEqual(quiz.structural_issues(), [])
        self.assertEqual([q.stem for q in quiz.questions], ['新小节测试题1', '新小节测试题2'])

    def test_pending_quiz_timeout_pauses_without_advancing(self) -> None:
        from autoquiz.workflow import QuizWorkflow

        self.page.set_content(DIRECTORY_HTML + r"""
          <li title="测验" onclick="changeDisplayContent()">2 测验</li>
          <div id="loading" hidden>加载中…</div>
          <script>window.changeDisplayContent = () => document.getElementById('loading').hidden = false;</script>
        """)
        self.page.evaluate("window.selectChapter(102)")
        edge = EdgeBrowser()
        edge.page = self.page
        with patch('autoquiz.browser._PAGE_WAIT_SECONDS', 0.9):
            workflow = QuizWorkflow(edge, solver=None)
            stats = workflow.run()
        self.assertIn('“测验”页签加载超时', workflow.last_error)
        self.assertEqual((stats.errors, stats.skipped), (1, 0))
        self.assertTrue(self.page.locator('#cur102').evaluate("el => el.classList.contains('posCatalog_active')"))

    def test_waits_for_quiz_request_when_tab_shows_placeholder_content(self) -> None:
        pending = []

        def serve(route) -> None:
            if route.request.url.endswith('/exercise'):
                pending.append(route)
            elif route.request.url.endswith('/release'):
                pending.pop().fulfill(status=200, content_type='text/html', body=HTML)
                route.fulfill(status=204)
            else:
                route.fulfill(status=200, content_type='text/html', body=r"""
                  <ul><li title="随堂练习" onclick="changeDisplayContent()">随堂练习</li></ul>
                  <main id="pane">章节正文</main>
                  <script>
                    window.changeDisplayContent = async () => {
                      document.querySelector('#pane').textContent = '练习说明';
                      setTimeout(() => fetch('/release'), 800);
                      const response = await fetch('/exercise');
                      document.querySelector('#pane').innerHTML = await response.text();
                    };
                  </script>
                """)

        self.page.route('http://course.test/**', serve)
        self.page.goto('http://course.test/studentstudy')
        edge = EdgeBrowser()
        edge.page = self.page
        with patch('autoquiz.browser._NON_QUIZ_STABLE_SECONDS', 0.2), patch('autoquiz.browser._POLL_SECONDS', 0.05):
            self.assertTrue(edge.open_quiz())
        self.assertEqual(len(edge.extract_quiz(include_screenshots=False)['questions']), 3)

    def test_quiz_tab_submits_from_outer_page_with_delayed_confirmation(self) -> None:
        self.page.set_content(r"""
          <li cardid="quiz" title="测验" onclick="changeDisplayContent()">2 测验</li>
          <iframe id="quiz" hidden></iframe>
          <input type="button" id="send" value="提交答案" disabled>
          <div role="dialog" id="confirm" hidden>
            <p>是否要提交测验？</p><button id="confirmSend">确定</button>
          </div>
          <script>
            const quiz = document.getElementById('quiz');
            const send = document.getElementById('send');
            window.sent = 0;
            window.confirmed = 0;
            window.changeDisplayContent = () => {
              quiz.hidden = false;
              quiz.srcdoc = window.questions;
              setTimeout(() => send.disabled = false, 2200);
            };
            send.onclick = () => {
              window.sent++;
              setTimeout(() => document.getElementById('confirm').hidden = false, 1400);
            };
            document.getElementById('confirmSend').onclick = () => {
              window.confirmed++;
              document.getElementById('confirm').hidden = true;
              setTimeout(() => quiz.contentDocument.body.insertAdjacentHTML('beforeend', '<p>我的答案：B</p>'), 600);
            };
          </script>
        """)
        self.page.evaluate("html => window.questions = html", HTML)
        edge = EdgeBrowser()
        edge.page = self.page
        self.assertTrue(edge.open_quiz())
        expected = {1: 'B', 2: 'AC', 3: 'B'}
        edge.fill_answers(expected)
        edge.submit(expected)
        self.assertEqual(self.page.evaluate('[window.sent, window.confirmed]'), [1, 1])

    def test_stop_during_confirmation_wait_prevents_confirming(self) -> None:
        self.page.set_content(HTML + r"""
          <button id="send" onclick="window.sent = (window.sent || 0) + 1;
              setTimeout(() => document.getElementById('confirm').hidden = false, 1400)">提交</button>
          <div role="dialog" id="confirm" hidden>确认提交？
            <button onclick="window.confirmed = true">提交</button>
          </div>
        """)
        stopped = Event()
        edge = EdgeBrowser(stop_event=stopped)
        edge.page = self.page
        expected = {1: 'B', 2: 'AC', 3: 'B'}
        edge.fill_answers(expected)
        timer = Timer(0.8, stopped.set)
        timer.start()
        try:
            with self.assertRaises(BrowserStopped):
                edge.submit(expected)
        finally:
            timer.cancel()
        self.assertEqual(self.page.evaluate('window.sent'), 1)
        self.page.locator('#confirm').wait_for(state='visible', timeout=3000)
        self.assertFalse(self.page.evaluate('Boolean(window.confirmed)'))

    def test_completed_result_with_timu_is_recognized_without_submit_button(self) -> None:
        from autoquiz.browser import EdgeBrowser

        self.page.set_content(r"""
          <div class="newTestTitle">章节测验 已完成</div>
          <div class="ceyan_name">章节测验 题量: 1</div>
          <div class="TiMu newTiMu" id="question402303735">
            1【单选题】已完成题目
            <ul><li><i>A、</i>选项</li></ul>
            <div>我的答案：A 20.0分</div>
          </div>
        """)
        edge = EdgeBrowser()
        edge.page = self.page
        self.assertTrue(edge.open_quiz())
        self.assertTrue(edge.is_quiz_completed())
        self.assertEqual(len(edge.extract_quiz(include_screenshots=False)["questions"]), 1)

    def test_directory_reads_actual_green_and_yellow_markers(self) -> None:
        from autoquiz.browser import EdgeBrowser

        self.page.set_content(DIRECTORY_HTML)
        raw = self.page.evaluate(_DIRECTORY_READ_JS)
        self.assertEqual(len(raw["items"]), 3)
        self.assertEqual(
            [item["status"] for item in raw["items"]],
            ["completed", "pending", "pending"],
        )
        self.assertEqual(raw["items"][0]["number"], "2.1")
        self.assertEqual(raw["items"][1]["chapter_id"], "102")
        self.assertTrue(raw["items"][0]["active"])

        edge = EdgeBrowser()
        edge.page = self.page
        self.assertEqual(edge.current_directory_status(), "completed")
        self.assertTrue(edge.is_quiz_completed())

    def test_directory_navigation_clicks_first_pending_after_active(self) -> None:
        from autoquiz.browser import EdgeBrowser

        self.page.set_content(DIRECTORY_HTML)
        edge = EdgeBrowser()
        edge.page = self.page
        self.assertTrue(edge.next_unfinished_section())
        self.assertEqual(edge.current_directory_status(), "pending")
        self.assertIn("已切换到 102", self.page.locator("#pageState").inner_text())

    def test_current_chapter_key_uses_active_directory_when_url_lags(self) -> None:
        from autoquiz.browser import EdgeBrowser

        self.page.route(
            "http://course.test/**",
            lambda route: route.fulfill(
                status=200, content_type="text/html", body=DIRECTORY_HTML
            ),
        )
        self.page.goto("http://course.test/studentstudy?chapterId=999&courseId=1")
        edge = EdgeBrowser()
        edge.page = self.page
        key = edge.current_chapter_key()
        self.assertIn("chapterId=101", key)
        self.assertNotIn("chapterId=999", key)
        self.assertIn("courseId=1", key)

    def test_directory_navigation_ends_when_only_green_items_remain(self) -> None:
        from autoquiz.browser import EdgeBrowser

        self.page.set_content(DIRECTORY_HTML)
        self.page.locator(".posCatalog_select").evaluate_all(
            """els => els.forEach(el => {
              el.querySelectorAll('.jobUnfinishCount, .catalog_points_yi').forEach(node => node.remove());
              if (!el.querySelector('.icon_Completed')) {
                el.insertAdjacentHTML('beforeend', '<span class="icon_Completed"></span>');
              }
            })"""
        )
        edge = EdgeBrowser()
        edge.page = self.page
        self.assertFalse(edge.next_unfinished_section())

    def test_submit_accepts_directory_green_without_result_text(self) -> None:
        from autoquiz.browser import EdgeBrowser

        self.page.set_content(HTML + r"""
          <div class="posCatalog_select posCatalog_active" id="cur201">
            <span class="posCatalog_name" title="当前小节"><em class="posCatalog_sbar">2.1</em></span>
            <input type="hidden" value="1" class="jobUnfinishCount">
            <span class="catalog_points_yi"><span class="prevHoverTips">1个待完成任务点</span></span>
          </div>
          <button id="submit" style="display:block;width:100px;height:40px">提交</button>
          <div class="popDiv maskDiv workpop" id="confirm" style="display:none;width:400px;height:200px">
            <p>确认提交？</p>
            <a id="confirmSubmit" href="#" style="display:block;width:80px;height:40px">提交</a>
          </div>
          <script>
            document.getElementById('submit').onclick = () => {
              document.getElementById('confirm').style.display = 'block';
            };
            document.getElementById('confirmSubmit').onclick = () => {
              document.getElementById('confirm').style.display = 'none';
              const current = document.getElementById('cur201');
              current.querySelectorAll('.jobUnfinishCount, .catalog_points_yi').forEach(el => el.remove());
              current.insertAdjacentHTML('beforeend', '<span class="icon_Completed">已完成</span>');
              return false;
            };
          </script>
        """)
        edge = EdgeBrowser()
        edge.page = self.page
        expected = {1: "B", 2: "AC", 3: "B"}
        edge.fill_answers(expected)
        edge.submit(expected)
        self.assertEqual(edge.current_directory_status(), "completed")
        self.assertNotIn("我的答案", self.page.locator("body").inner_text())


if __name__ == "__main__":
    unittest.main()
