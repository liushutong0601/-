from __future__ import annotations

import unittest
from threading import Event
from unittest.mock import patch

from autoquiz.browser import BrowserError, BrowserStopped
from autoquiz.course import CourseWorkflow
from autoquiz.netacad import NetAcadBrowser
from autoquiz.netacad_probe import STRUCTURE_JS


OUTER = """<meta charset="utf-8"><div id="course-outline"><button role="treeitem" id="submodule-button-exam"
aria-expanded="true"><img alt="in progress"><span title="合成考试">合成考试</span></button></div>
<iframe title="Course content" style="width:1100px;height:850px;border:0"
src="https://www.netacad.com/authoring-resources/index.html#synthetic"></iframe>"""

EXAM = r"""<!doctype html><meta charset="utf-8"><style>body{margin:0} button{padding:8px} .mcq__item{padding:12px}
article-view,mcq-view,object-matching-view{display:block} .assessmentResults__inner{padding:40px}</style>
<p>考试包括 2 问题</p><nav><button aria-label="unanswered Question 1" onclick="showQuestion(1)">Q1</button>
<button aria-label="unanswered Question 2" onclick="showQuestion(2)">Q2</button>
<button id="finalnav" class="block-button review-screen-button" aria-label="已选中提交页面" onclick="finalPage()"> 提交页面 </button></nav>
<article-view modelid="exam-article"></article-view>
<div id="final" style="display:none"><h2>提交我的评估</h2><label><input type="checkbox" id="confirm-exam"
onchange="document.querySelector('.adaptive-assessment-submit').classList.toggle('is-disabled',!this.checked)">是的，确认我的提交。</label>
<button class="adaptive-assessment-submit is-disabled" onclick="submitExam()">提交</button></div>
<div id="results" style="display:none" class="assessmentResults__inner"><p>你的得分是 <span id="score">0</span>%。</p>
<button onclick="review()">审查评估</button><button onclick="resetExam()">重置</button></div>
<div class="assessmentResults__inner" style="display:none">你的得分是 0%。旧成绩</div>
<script>
 window.question=1; window.choice=''; window.pairs=['','']; window.category=0;
 window.reviewMode=false;window.round=0;window.submitClicks=0;window.finalClicks=0;
 window.forceScore=null; window.choiceClicks=0;
 const article=document.querySelector('article-view'); article.attachShadow({mode:'open'});
 window.showQuestion = n => {
  window.question=n; document.getElementById('final').style.display='none';document.getElementById('results').style.display='none';
  article.style.display='block';article.shadowRoot.innerHTML=`<div class="block__title-inner">问题 ${n}</div>`;
  if(n===1){
    const el=document.createElement('mcq-view');el.setAttribute('sgpulse-type','component');el.setAttribute('modelid','mcq-one');
    article.shadowRoot.append(el); const s=el.attachShadow({mode:'open'});
    s.innerHTML=`<div class="component__inner mcq__inner"><base-view></base-view>
    <div class="component__widget ${reviewMode?'is-submitted '+(choice==='B'?'is-correct':'show-user-answer'):''}">
    <div class="mcq__item ${reviewMode?'is-incorrect':''}" role="radio" aria-checked="${choice==='A'}"><label class="mcq__item-text">甲</label></div>
    <div class="mcq__item ${reviewMode?'is-correct':''}" role="radio" aria-checked="${choice==='B'}"><label class="mcq__item-text">乙</label></div></div>
    ${reviewMode?'<div class="feedback">解析：区分甲与乙。</div>':''}</div>`;
    const base=s.querySelector('base-view');base.attachShadow({mode:'open'}).innerHTML='<div class="component__body">本地合成单选题</div>';
    s.querySelectorAll('.mcq__item').forEach((e,i)=>e.onclick=()=>{window.choiceClicks++;window.choice=String.fromCharCode(65+i);s.querySelectorAll('.mcq__item').forEach((o,j)=>o.setAttribute('aria-checked',i===j));});
  }else{
    const el=document.createElement('object-matching-view');el.setAttribute('sgpulse-type','component');el.setAttribute('modelid','matching-two');
    article.shadowRoot.append(el);const s=el.attachShadow({mode:'open'});
    s.innerHTML=`<div class="component__inner objectMatching__inner"><div class="component__body">将两类与选项配对。</div>
    <div class="component__widget ${reviewMode?'is-submitted is-correct':''}">
    <button class="objectMatching-category-item" data-id="10"><span class="category-item-text">类别甲</span><span class="category-item-number">A</span></button>
    <button class="objectMatching-category-item" data-id="20"><span class="category-item-text">类别乙</span><span class="category-item-number">B</span></button>
    <button class="objectMatching-option-item" data-id="101"><span class="category-item-text">选项一</span><span class="category-item-number">${pairs[0]}</span></button>
    <button class="objectMatching-option-item" data-id="202"><span class="category-item-text">选项二</span><span class="category-item-number">${pairs[1]}</span></button></div></div>`;
    s.querySelectorAll('.objectMatching-category-item').forEach((e,i)=>e.onclick=()=>window.category=i);
    s.querySelectorAll('.objectMatching-option-item').forEach((e,i)=>e.onclick=()=>{pairs=pairs.map(p=>p===String.fromCharCode(65+category)?'':p);pairs[i]=String.fromCharCode(65+category);s.querySelectorAll('.objectMatching-option-item .category-item-number').forEach((e,j)=>e.textContent=pairs[j]);});
  }
 };
 window.finalPage=()=>{window.finalClicks++;article.style.display='none';document.getElementById('final').style.display=reviewMode?'none':'block';document.getElementById('results').style.display=reviewMode?'block':'none';};
 window.submitExam=()=>{if(!document.getElementById('confirm-exam').checked)throw Error('unconfirmed');window.submitClicks++;
  document.getElementById('final').style.display='none';setTimeout(()=>{document.getElementById('score').textContent=forceScore??100;
  document.getElementById('results').style.display='block';document.getElementById('finalnav').textContent='结果页面';},150);};
 window.review=()=>{reviewMode=true;showQuestion(1);};
 window.resetExam=()=>{window.round++;choice='';pairs=['',''];reviewMode=false;document.getElementById('confirm-exam').checked=false;showQuestion(1);};
 showQuestion(1);
</script>"""

# The closed menus and feedback live in separate, open shadow roots.
DROPDOWN_WIDGET = r"""() => {
  const original = showQuestion;
  window.dropdownSelections = {};
  window.dropdownClicks = 0;
  window.dropdownFeedbackIncomplete = false;
  window.showQuestion = n => {
    original(n);
    if (n !== 2) return;
    const old = article.shadowRoot.querySelector('object-matching-view');
    const widget = document.createElement('matching-view');
    widget.style.display = 'block';
    widget.setAttribute('sgpulse-type', 'component');
    widget.setAttribute('modelid', old.getAttribute('modelid'));
    old.replaceWith(widget);
    const root = widget.attachShadow({mode:'open'});
    const labels = round === 0 ? ['项目甲','项目乙','项目丙'] : ['项目乙','项目丙','项目甲'];
    const options = round === 0 ? ['红色','蓝色','绿色'] : ['绿色','红色','蓝色'];
    root.innerHTML = `<div class="component__title-inner">问题 2</div>
      <div class="component__body">在每行选择颜色，允许重复。</div>
      <div class="component__widget matching__widget ${reviewMode?'is-submitted show-user-answer':''}"></div>`;
    labels.forEach(label => {
      const row = document.createElement('matching-dropdown-view');
      row.className = 'matching__item_main';
      row.style.display = 'block';
      root.querySelector('.matching__widget').append(row);
      const s = row.attachShadow({mode:'open'});
      let selected = dropdownSelections[label] || '';
      s.innerHTML = `<div class="matching__item-title"><div class="matching__item-title_inner">${label}</div></div>
        <button class="dropdown__btn" aria-haspopup="listbox" aria-expanded="false">
          <span class="dropdown__inner">${selected || '请选择'}</span></button>
        <ul class="dropdown__list" role="listbox" hidden>${options.map((choice, i) =>
          `<li class="dropdown__item" role="option" aria-selected="${choice === selected}" data-index="${i}">
            <div class="dropdown__item-inner"><span class="sr-only">第 ${i+1} 项</span>${choice}</div>
          </li>`).join('')}</ul>`;
      const button = s.querySelector('button');
      const menu = s.querySelector('ul');
      button.onclick = () => {
        menu.hidden = !menu.hidden;
        button.setAttribute('aria-expanded', !menu.hidden);
      };
      s.querySelectorAll('.dropdown__item-inner').forEach((option, i) => option.onclick = () => {
        dropdownClicks++;
        selected = options[i];
        dropdownSelections[label] = selected;
        s.querySelector('.dropdown__inner').textContent = selected;
        s.querySelectorAll('[role="option"]').forEach((li, index) => li.setAttribute('aria-selected', index === i));
        menu.hidden = true;
        button.setAttribute('aria-expanded', false);
        document.dispatchEvent(new Event('fixture-dropdown-selected'));
      });
    });
    if (reviewMode) {
      const feedback = document.createElement('feedback-fixture');
      root.append(feedback);
      feedback.attachShadow({mode:'open'}).innerHTML = `<div class="component__feedback-content">
        <table class="table-feedback">
          <tr><th>蓝色</th><th>绿色</th><th>红色</th></tr>
          <tr><td>项目甲</td><td></td><td>项目丙</td></tr>
          ${dropdownFeedbackIncomplete?'':'<tr><td>项目乙</td><td></td><td></td></tr>'}
        </table></div>`;
    }
  };
}"""


class NetAcadBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.manager = sync_playwright().start()
        try:
            cls.browser = cls.manager.chromium.launch(channel="msedge", headless=True)
        except Exception:
            cls.manager.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.manager.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width":1280,"height":1000})
        self.page.route('https://www.netacad.com/**', lambda route:
                        route.fulfill(body=EXAM if '/authoring-resources/' in route.request.url else OUTER,content_type='text/html'))
        self.page.goto('https://www.netacad.com/zh/launch?id=synthetic')
        self.frame = self.page.frames[1]
        self.frame.wait_for_function("typeof showQuestion === 'function'")
        self.adapter = NetAcadBrowser(dry_run=False)
        self.adapter.page = self.page

    def tearDown(self):
        self.page.close()

    def test_reads_shadow_dom_and_expands_two_source_questions_into_three_answers(self):
        quiz = self.adapter.extract_assessment()
        self.assertEqual(quiz.structural_issues(), [])
        self.assertEqual(quiz.total, 3)
        self.assertEqual(quiz.questions[0].stem, "本地合成单选题")
        self.assertIn("类别乙", quiz.questions[2].stem)
        self.assertEqual([o.text for o in quiz.questions[2].options], ["选项一","选项二"])
        self.adapter.fill_answers({1:"B",2:"A",3:"B"})
        self.assertEqual(self.adapter.selected_answers(), {1:"B",2:"A",3:"B"})

    def test_dropdown_reads_closed_menus_and_allows_repeated_choices(self):
        self.frame.evaluate(DROPDOWN_WIDGET)
        self.frame.evaluate("dropdownSelections={'项目甲':'绿色'}")
        quiz = self.adapter.extract_assessment()
        self.assertEqual(quiz.structural_issues(), [])
        self.assertEqual(quiz.total, 4)
        self.assertIn('项目甲', quiz.questions[1].stem)
        self.assertEqual([o.text for o in quiz.questions[1].options], ['红色', '蓝色', '绿色'])
        self.assertEqual(self.adapter.selected_answers(), {1:'', 2:'C', 3:'', 4:''})
        answers = {1:'B', 2:'B', 3:'B', 4:'A'}
        self.adapter.fill_answers(answers)
        self.assertEqual(self.adapter.selected_answers(), answers)
        self.assertEqual(self.frame.evaluate('dropdownSelections'), {'项目甲':'蓝色', '项目乙':'蓝色', '项目丙':'红色'})

    def test_dropdown_blocks_partial_answers_and_changed_options(self):
        self.frame.evaluate(DROPDOWN_WIDGET)
        self.adapter.extract_assessment()
        with self.assertRaisesRegex(BrowserError, '每一行必须选择一个'):
            self.adapter.fill_answers({1:'B', 2:'AB', 3:'B', 4:'A'})
        self.frame.locator('matching-dropdown-view').last.locator('.dropdown__item-inner').last.evaluate(
            "el=>el.textContent='新选项'")
        with self.assertRaisesRegex(BrowserError, '题干、行或选项已变化'):
            self.adapter._binding_widget(self.adapter._bindings[2])
        self.assertEqual(self.frame.evaluate('[choiceClicks, dropdownClicks, submitClicks]'), [0,0,0])

    def test_dropdown_feedback_reuses_published_choices_after_row_and_option_reordering(self):
        self.page.locator('#course-outline').evaluate("el=>el.insertAdjacentHTML('afterbegin','<button id=node-button-exam></button>')")
        self.frame.evaluate(DROPDOWN_WIDGET)
        self.frame.evaluate("""() => {
          const originalSubmit = submitExam;
          const originalReset = resetExam;
          window.resetExam = () => {dropdownSelections={};originalReset();};
          window.submitExam = () => {
            const correct = dropdownSelections['项目甲']==='蓝色' &&
              dropdownSelections['项目乙']==='蓝色' && dropdownSelections['项目丙']==='红色';
            forceScore = correct ? 100 : 75;
            if (correct) parent.document.querySelector('#course-outline img').alt='completed';
            originalSubmit();
          };
        }""")
        calls = []

        def solve(quiz):
            calls.append(quiz.total)
            return {1:'B', 2:'B', 3:'A', 4:'A'}

        solver = type('Solver', (), {'solve':staticmethod(solve)})()
        with patch('autoquiz.netacad._WAIT', 3):
            workflow = CourseWorkflow(self.adapter, solver, dry_run=False)
            stats = workflow.run()
        self.assertEqual(workflow.last_error, '')
        self.assertEqual(stats.submitted, 1)
        self.assertEqual(calls, [4])
        self.assertEqual(self.frame.evaluate('[submitClicks,round,dropdownClicks]'), [2,1,6])

    def test_incomplete_dropdown_feedback_does_not_guess_which_row_was_wrong(self):
        self.frame.evaluate(DROPDOWN_WIDGET)
        self.frame.evaluate("""() => {
          reviewMode=true;choice='B';dropdownFeedbackIncomplete=true;
          dropdownSelections={'项目甲':'蓝色','项目乙':'红色','项目丙':'红色'};
          showQuestion(2);
        }""")
        self.adapter.extract_assessment()
        self.adapter._last_score = 75
        report = self.adapter.review_assessment()
        self.assertEqual(report.wrong_numbers, (2,3,4))
        self.assertEqual(report.uncertain_wrong_numbers, (2,3,4))
        self.assertEqual(report.correct_answers, {})

    def checkpoint_outline(self):
        self.page.locator('#course-outline').evaluate("""root=>root.innerHTML=`
          <button id="node-button-previous"></button>
          <div class="subModuleContainer--test"><button role="treeitem" id="submodule-button-previous">
            <img alt="completed"><div class="subModuleName--test" title="检查点考试">检查点考试</div></button></div>
          <div class="subModuleContainer--test"><button role="treeitem" aria-expanded="true" id="submodule-button-old-one">
            <img alt="completed"><div title="普通展开一">普通展开一</div></button></div>
          <div class="subModuleContainer--test"><button role="treeitem" aria-expanded="true" id="submodule-button-old-two">
            <img alt="in progress"><div title="普通展开二">普通展开二</div></button></div>
          <button id="node-button-checkpoint" aria-expanded="true">独立检查点</button>
          <div class="subModuleContainer--test active--selected" id="checkpoint-container">
            <button role="treeitem" id="submodule-button-checkpoint"><span title="100 Pts">100 Pts</span>
              <img alt="start"><img alt="assignment"><div class="subModuleName--test" title="检查点考试">检查点考试</div>
            </button>
          </div>`""")

    def test_checkpoint_without_expanded_attribute_uses_selected_container(self):
        self.checkpoint_outline()
        node=self.adapter.current_node()
        self.assertEqual(node.key,'netacad-exam:submodule-button-checkpoint:检查点考试')
        self.assertEqual(node.kind.value,'assessment')
        self.assertEqual(node.status,'pending')
        self.assertEqual(self.adapter._completion_status(node),'pending')
        self.assertEqual(self.frame.evaluate('submitClicks'),0)

    def test_two_selected_exam_containers_pause_without_starting_or_submitting(self):
        self.checkpoint_outline()
        self.page.locator('#submodule-button-previous').evaluate("el=>el.parentElement.classList.add('active--selected')")
        flow=CourseWorkflow(self.adapter,None,dry_run=False);flow.run()
        self.assertIn('无法唯一确定当前选中的考试目录项',flow.last_error)
        self.assertEqual(self.frame.evaluate('[choiceClicks,submitClicks]'),[0,0])

    def test_styled_directory_does_not_guess_current_item_from_expanded_sections(self):
        self.checkpoint_outline()
        self.page.locator('#checkpoint-container').evaluate("el=>el.classList.remove('active--selected')")
        with self.assertRaisesRegex(BrowserError,'当前选中的考试目录项'):
            self.adapter.current_node()

    def test_checkpoint_points_badge_is_not_a_completion_marker(self):
        self.checkpoint_outline()
        self.page.locator('#submodule-button-checkpoint img[alt="start"]').evaluate('el=>el.remove()')
        self.assertEqual(self.adapter.current_node().status,'unknown')

    def test_checkpoint_completed_state_and_course_end_use_its_own_identity(self):
        self.checkpoint_outline()
        self.page.locator('#submodule-button-checkpoint img[alt="start"]').evaluate("el=>el.alt='completed'")
        self.adapter._last_score=100
        with patch('autoquiz.netacad._WAIT',0.4):
            self.assertFalse(self.adapter.next_node())
        # 相同名称的另一个检查点不影响当前状态。
        self.assertEqual(self.adapter._completion_status(self.adapter.current_node()),'completed')

    def test_switching_selected_checkpoint_after_ai_cannot_fill_or_submit(self):
        self.checkpoint_outline()
        page=self.page
        class SwitchingSolver:
            def solve(self,quiz):
                page.locator('#checkpoint-container').evaluate("el=>el.classList.remove('active--selected')")
                page.locator('#submodule-button-previous').evaluate("el=>el.parentElement.classList.add('active--selected')")
                return {1:'B',2:'A',3:'B'}
        flow=CourseWorkflow(self.adapter,SwitchingSolver(),dry_run=False);flow.run()
        self.assertIn('课程节点已变化',flow.last_error)
        self.assertEqual(self.frame.evaluate('[choiceClicks,submitClicks]'),[0,0])

    def test_checkpoint_start_page_keeps_identity_after_33_question_navigation_loads(self):
        self.checkpoint_outline()
        self.frame.evaluate("""() => {
          document.querySelector('nav').style.display='none';article.style.display='none';
          document.querySelector('p').textContent='考试包括 33 问题';window.startClicks=0;
          const start=document.createElement('div');start.setAttribute('role','button');start.textContent='开始';
          start.onclick=()=>{startClicks++;start.remove();document.querySelector('nav').style.display='block';
            for(let n=3;n<=33;n++){const b=document.createElement('button');b.setAttribute('aria-label','Question '+n);
              b.textContent='Q'+n;document.querySelector('nav').append(b);}showQuestion(1);};
          document.body.append(start);
        }""")
        before=self.adapter.current_node()
        self.adapter.dry_run=True
        with self.assertRaisesRegex(BrowserError,'不会启动计时考试'):
            self.adapter.open_assessment()
        self.assertEqual(self.frame.evaluate('startClicks'),0)
        self.adapter.dry_run=False;self.adapter.open_assessment()
        self.assertEqual(self.adapter.current_node().key,before.key)
        self.assertEqual(len(self.adapter._nav()),33)
        self.assertEqual(self.frame.evaluate('startClicks'),1)
        self.assertEqual(self.frame.evaluate('submitClicks'),0)

    def test_submission_requires_checked_confirmation_ignores_hidden_old_score_and_clicks_once(self):
        self.adapter.extract_assessment()
        self.assertIsNone(self.adapter._result())
        answers={1:"B",2:"A",3:"B"}
        self.adapter.fill_answers(answers)
        report=self.adapter.submit_assessment(answers)
        self.assertEqual(report.score,100)
        self.assertEqual(self.frame.evaluate('submitClicks'),1)
        self.assertTrue(self.frame.locator('#confirm-exam').is_checked())

    def test_visible_existing_grade_blocks_new_ai_run_and_duplicate_submission(self):
        self.frame.evaluate("document.getElementById('results').style.display='block';document.getElementById('score').textContent=100;article.style.display='none'")
        with self.assertRaisesRegex(BrowserError,'100% 成绩'):
            self.adapter.open_assessment()
        self.assertEqual(self.frame.evaluate('submitClicks'),0)

    def test_review_reads_visible_wrong_mark_and_feedback_then_reset_clears_old_state(self):
        self.adapter.extract_assessment()
        answers={1:"A",2:"A",3:"B"}
        self.adapter.fill_answers(answers)
        self.frame.evaluate('forceScore=66')
        self.assertEqual(self.adapter.submit_assessment(answers).score,66)
        review=self.adapter.review_assessment()
        self.assertEqual(review.wrong_numbers,(1,))
        self.assertIn('乙',review.feedback[1])
        self.adapter.restart_assessment()
        self.assertEqual(self.frame.evaluate('round'),1)
        self.assertIsNone(self.adapter._last_score)
        self.assertFalse(self.adapter._bindings)

    def test_mismatched_fill_or_stopped_event_never_reaches_final_submission(self):
        self.adapter.extract_assessment()
        with self.assertRaisesRegex(BrowserError,'复核'):
            self.adapter.submit_assessment({1:"B",2:"A",3:"B"})
        self.assertEqual(self.frame.evaluate('submitClicks'),0)
        event=Event();event.set();self.adapter.stop_event=event
        with self.assertRaises(BrowserStopped):
            self.adapter.fill_answers({1:"B",2:"A",3:"B"})
        self.assertEqual(self.frame.evaluate('choiceClicks'),0)

    def test_shadow_diagnostic_filters_hidden_confirmation_and_score_without_changing_answers(self):
        result=self.frame.evaluate(STRUCTURE_JS)
        self.assertGreater(result['shadow_roots'],1)
        self.assertEqual(result['visible_choice_controls'],2)
        self.assertEqual(result['visible_confirmation'],0)
        self.assertEqual(result['visible_result_components'],0)
        self.assertEqual(self.frame.evaluate('choice'), '')
        self.assertNotIn('本地合成单选题',str(result))

    def test_course_dry_run_keeps_current_exam_and_never_submits_or_resets(self):
        class Solver:
            def solve(self,quiz):
                return {1:"B",2:"A",3:"B"}
        self.adapter.dry_run=True
        workflow=CourseWorkflow(self.adapter,Solver())
        stats=workflow.run()
        self.assertEqual(workflow.last_error,'')
        self.assertEqual(stats.tested,1)
        self.assertTrue(workflow.preview_ready)
        self.assertEqual(self.frame.evaluate('submitClicks'),0)
        self.assertEqual(self.frame.evaluate('round'),0)

    def test_slow_question_switch_does_not_reuse_previous_question(self):
        self.frame.evaluate("""() => {const original=showQuestion; window.showQuestion=n=>{
          if(n===2){document.querySelector('article-view').shadowRoot.querySelector('.block__title-inner').textContent='问题 2';
          setTimeout(()=>original(n),180);} else original(n);
        }}""")
        quiz=self.adapter.extract_assessment()
        self.assertEqual(quiz.total,3)
        self.assertIn('类别甲',quiz.questions[1].stem)
        self.assertEqual(self.frame.evaluate('submitClicks'),0)

    def test_component_titles_and_cached_offscreen_questions_can_be_revisited(self):
        for own_title in (True,False):
            with self.subTest(component_title=own_title):
                self.frame.evaluate("""ownTitle => {
                  article.shadowRoot.innerHTML=ownTitle?'':'<div class="block__title-inner">问题 1</div>';
                  for(let n=1;n<=2;n++){
                    const widget=document.createElement('mcq-view');widget.setAttribute('sgpulse-type','component');
                    widget.setAttribute('modelid','cached-'+n);widget.style.cssText='display:block;position:absolute;top:130px;width:500px;left:'+(n===1?'0px':'2200px');
                    article.shadowRoot.append(widget);const root=widget.attachShadow({mode:'open'});
                    root.innerHTML='<base-view></base-view><div class="component__widget">'+
                      '<div class="mcq__item" role="radio" aria-checked="false"><label class="mcq__item-text">甲</label></div>'+
                      '<div class="mcq__item" role="radio" aria-checked="false"><label class="mcq__item-text">乙</label></div></div>';
                    root.querySelector('base-view').attachShadow({mode:'open'}).innerHTML=
                      (ownTitle?'<div class="component__title-inner">不完整 问题 '+n+'</div>':'')+
                      '<div class="component__body">合成缓存题 '+n+'</div>';
                    root.querySelectorAll('.mcq__item').forEach((e,i)=>e.onclick=()=>{
                      window.choiceClicks++;root.querySelectorAll('.mcq__item').forEach((e,j)=>e.setAttribute('aria-checked',String(i===j)));
                    });
                  }
                  document.querySelectorAll('nav button[aria-label]').forEach((b,i)=>b.onclick=()=>{
                    article.shadowRoot.querySelectorAll('mcq-view').forEach((w,j)=>w.style.left=i===j?'0px':'2200px');
                    if(!ownTitle)article.shadowRoot.querySelector('.block__title-inner').textContent='问题 '+(i+1);
                  });
                }""",own_title)
                with patch('autoquiz.netacad._WAIT',0.6):
                    quiz=self.adapter.extract_assessment()
                    self.assertEqual(quiz.total,2)
                    self.assertEqual(quiz.structural_issues(),[])
                    self.adapter.fill_answers({1:'A',2:'B'})
                    self.assertEqual(self.adapter.selected_answers(),{1:'A',2:'B'})
                    with patch.object(self.adapter,'_binding_widget',wraps=self.adapter._binding_widget) as reread:
                        self.assertEqual(self.adapter.selected_answers(),{1:'A',2:'B'})
                        self.assertEqual(self.adapter.selected_answers(),{1:'A',2:'B'})
                        self.assertEqual(reread.call_count,0)
                    # 脚本或用户改变已选项后，凭据失效，不能用旧缓存直接提交。
                    self.frame.locator('mcq-view[modelid="cached-1"] .mcq__item').evaluate_all(
                        "items=>items.forEach((e,i)=>e.setAttribute('aria-checked',String(i===1)))")
                    with self.assertRaisesRegex(BrowserError,'填写复核失败'):
                        self.adapter.submit_assessment({1:'A',2:'B'})
                    self.frame.locator('mcq-view[modelid="cached-1"] .mcq__item').evaluate_all(
                        "items=>items.forEach((e,i)=>e.setAttribute('aria-checked',String(i===0)))")
                    self.assertEqual(self.adapter.selected_answers(),{1:'A',2:'B'})
                    self.frame.locator('mcq-view[modelid="cached-1"] .mcq__item').first.evaluate(
                        "el=>el.style.display='none'")
                    with self.assertRaisesRegex(BrowserError,'题干或选项已变化'):
                        self.adapter.submit_assessment({1:'A',2:'B'})
                self.assertEqual(self.frame.evaluate('submitClicks'),0)

    def test_review_accepts_concatenated_submitted_class_and_wrong_mark_without_explanation(self):
        self.frame.evaluate("""() => {
          const original=showQuestion;window.showQuestion=n=>{original(n);
            if(reviewMode&&n===1){const root=article.shadowRoot.querySelector('mcq-view').shadowRoot;
              root.querySelector('.component__widget').className='component__widget is-disabled is-complete is-submittedshow-user-answer';
              root.querySelector('.feedback')?.remove();}
          };
        }""")
        self.adapter.extract_assessment();answers={1:'A',2:'A',3:'B'}
        self.adapter.fill_answers(answers);self.frame.evaluate('forceScore=66')
        self.adapter.submit_assessment(answers)
        review=self.adapter.review_assessment()
        self.assertEqual(review.wrong_numbers,(1,))
        self.assertEqual(review.correct_numbers,(2,3))
        self.assertTrue(review.feedback[1])

    def test_outer_heading_cannot_override_conflicting_component_question_number(self):
        self.frame.evaluate("""() => {
          showQuestion(2);const widget=article.shadowRoot.querySelector('[sgpulse-type="component"]');
          const title=document.createElement('div');title.className='component__title-inner';
          title.textContent='不完整 问题 1';widget.shadowRoot.prepend(title);
        }""")
        with patch('autoquiz.netacad._WAIT',0.15),self.assertRaisesRegex(BrowserError,'第 2 题加载未完成'):
            self.adapter._go_question(2)
        self.assertEqual(self.frame.evaluate('submitClicks'),0)
        self.frame.locator('.component__title-inner').evaluate("el=>el.textContent='问题 2'")
        self.assertEqual(len(self.adapter._go_question(2)),1)

    def test_second_viewport_question_component_blocks_fill_before_any_click(self):
        self.adapter.extract_assessment()
        self.adapter._go_question(1)
        self.frame.evaluate("""() => {
          const extra=document.createElement('mcq-view');
          extra.setAttribute('sgpulse-type','component');
          extra.setAttribute('modelid','unexpected-extra');
          extra.textContent='额外题目组件';article.shadowRoot.append(extra);
        }""")
        self.assertTrue(self.frame.locator('mcq-view[modelid="unexpected-extra"]').evaluate(
            'el=>{const r=el.getBoundingClientRect();return r.top<innerHeight&&r.bottom>0;}'))
        with patch('autoquiz.netacad._WAIT',0.2),self.assertRaises(BrowserError):
            self.adapter.fill_answers({1:'B',2:'A',3:'B'})
        self.assertEqual(self.frame.evaluate('[choiceClicks,submitClicks]'),[0,0])

    def test_partial_navigation_does_not_extract_or_fill_an_incomplete_exam(self):
        self.frame.locator('p').first.evaluate("el=>el.textContent='考试包括 3 问题'")
        with patch('autoquiz.netacad._WAIT',0.15),self.assertRaisesRegex(BrowserError,'题数与已加载导航'):
            self.adapter.extract_assessment()
        self.assertEqual(self.frame.evaluate('choiceClicks'),0)

    def test_full_exam_waits_for_delayed_navigation_instead_of_skipping_question(self):
        self.frame.evaluate("""() => {
          document.querySelector('p').textContent='考试包括 3 问题';
          setTimeout(()=>{const button=document.createElement('button');button.setAttribute('aria-label','Question 3');
            button.textContent='Q3';document.querySelector('nav').append(button);},180);
        }""")
        self.assertEqual(set(self.adapter._nav()),{1,2,3})
        self.assertEqual(self.frame.evaluate('choiceClicks'),0)

    def test_duplicate_matching_answers_are_rejected_before_any_fill(self):
        self.adapter.extract_assessment()
        with self.assertRaisesRegex(BrowserError,'配对答案重复'):
            self.adapter.fill_answers({1:'B',2:'A',3:'A'})
        self.assertEqual(self.frame.evaluate('choiceClicks'),0)

    def test_changing_preselected_radio_to_earlier_option_keeps_target_selected(self):
        self.frame.evaluate("choice='B';showQuestion(1)")
        self.adapter.extract_assessment()
        self.adapter.fill_answers({1:'A',2:'A',3:'B'})
        self.assertEqual(self.adapter.selected_answers(), {1:'A',2:'A',3:'B'})
        self.assertEqual(self.frame.evaluate('choiceClicks'),1)

    def test_matching_category_change_blocks_fill_before_pair_clicks(self):
        self.adapter.extract_assessment()
        self.frame.locator('.objectMatching-category-item .category-item-text').first.evaluate(
            "el=>el.textContent='已变化类别'")
        with self.assertRaisesRegex(BrowserError,'配对类别已变化'):
            self.adapter._binding_widget(self.adapter._bindings[2])
        self.assertEqual(self.frame.evaluate('pairs'),['',''])

    def test_start_page_is_assessment_and_dry_run_never_starts_timed_exam(self):
        self.frame.evaluate("""() => {
          document.querySelector('nav').style.display='none';article.style.display='none';
          window.startClicks=0;const button=document.createElement('button');button.textContent='开始';
          button.onclick=()=>{startClicks++;button.remove();document.querySelector('nav').style.display='block';showQuestion(1);};
          document.body.append(button);
        }""")
        self.assertEqual(self.adapter.current_node().kind.value,'assessment')
        self.adapter.dry_run=True
        with self.assertRaisesRegex(BrowserError,'不会启动计时考试'):
            self.adapter.open_assessment()
        self.assertEqual(self.frame.evaluate('startClicks'),0)
        self.adapter.dry_run=False
        self.adapter.open_assessment()
        self.assertEqual(self.frame.evaluate('startClicks'),1)
        self.assertEqual(self.adapter.extract_assessment().total,3)

    def test_full_web_workflow_reviews_wrong_answer_and_redoes_entire_exam(self):
        self.page.locator('#course-outline').evaluate("""el=>{
          const header=document.createElement('button');header.id='node-button-exam';el.prepend(header);
        }""")
        self.frame.evaluate("""() => {
          const original=submitExam;
          window.submitExam=()=>{forceScore=round===0?66:100;
            if(round>0)parent.document.querySelector('#course-outline img').alt='completed';
            original();};
        }""")
        class Solver:
            def __init__(self):
                self.calls=[]
            def solve(self,quiz):
                self.calls.append(quiz)
                return {1:'A',2:'A',3:'B'}
        solver=Solver()
        workflow=CourseWorkflow(self.adapter,solver,dry_run=False)
        stats=workflow.run()
        self.assertEqual(workflow.last_error,'')
        self.assertEqual(stats.submitted,1)
        self.assertEqual([q.total for q in solver.calls],[3])
        self.assertEqual(self.frame.evaluate('choice'),'B')
        self.assertEqual(self.frame.evaluate('submitClicks'),2)
        self.assertEqual(self.frame.evaluate('round'),1)

    def test_image_question_captures_picture_and_sends_explicit_option_mapping(self):
        self.frame.evaluate("""() => {
          const original=showQuestion;
          window.showQuestion=n=>{original(n);if(n===1){
            const root=document.querySelector('article-view').shadowRoot.querySelector('mcq-view').shadowRoot.querySelector('base-view').shadowRoot;
            const img=document.createElement('img');img.src='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a+1sAAAAASUVORK5CYII=';
            img.width=50;img.height=50;root.querySelector('.component__body').append(img);
          }};showQuestion(1);
        }""")
        quiz=self.adapter.extract_assessment()
        from autoquiz.kimi import KimiSolver
        self.assertTrue(quiz.questions[0].has_images)
        self.assertTrue(quiz.questions[0].supported)
        content=KimiSolver._message_content(quiz.questions)
        self.assertIn('截图选项按以下字母映射',str(content))
        self.assertIn('data:image/png;base64,',str(content))

    def test_pt_link_and_published_matching_answers_allow_retry_without_another_api_call(self):
        self.page.locator('#course-outline').evaluate("""el=>{
          const header=document.createElement('button');header.id='node-button-exam';el.prepend(header);
        }""")
        self.frame.evaluate("""() => {
          const original=showQuestion;
          const originalSubmit=submitExam;
          const labels=['类别甲','相同描述','类别丙','相同描述'];
          const options=['选项甲','选项乙','选项丙','选项丁'];
          window.downloadClicks=0;
          window.showQuestion=n=>{
            original(n);
            if(n===1){
              const body=article.shadowRoot.querySelector('mcq-view').shadowRoot
                .querySelector('base-view').shadowRoot.querySelector('.component__body');
              const link=document.createElement('a');
              link.href='fixture.pka';link.textContent=' PT 活动';
              link.onclick=e=>{e.preventDefault();downloadClicks++;};body.append(link);
              return;
            }
            const root=article.shadowRoot.querySelector('object-matching-view').shadowRoot;
            const categoryOrder=round===0?[3,0,2,1]:[1,2,0,3];
            const optionOrder=round===0?[0,1,2,3]:[3,1,0,2];
            const letters='ABCD';
            root.querySelector('.component__inner').innerHTML=`<div class="component__body">配对说明</div>
              <div class="component__widget ${reviewMode?'is-submitted show-user-answer':''}">
              ${categoryOrder.map((id,i)=>`<button class="objectMatching-category-item" data-id="${id}" data-itemindex="${id}"><span class="category-item-text">${labels[id]}</span><span class="category-item-number">${letters[i]}</span></button>`).join('')}
              ${optionOrder.map((id,i)=>`<button class="objectMatching-option-item"><span class="category-item-text">${options[id]}</span><span class="category-item-number">${pairs[i]||''}</span></button>`).join('')}</div>
              ${reviewMode?`<div class="component__feedback"><div class="component__feedback-content"><table class="table-feedback">${labels.map((label,id)=>`<tr><td>${label}</td><td>${options[id]}</td></tr>`).join('')}</table></div></div>`:''}`;
            root.querySelectorAll('.objectMatching-category-item').forEach((e,i)=>e.onclick=()=>window.category=i);
            root.querySelectorAll('.objectMatching-option-item').forEach((e,i)=>e.onclick=()=>{
              const letter=letters[category];
              pairs[i]=letter;
              root.querySelectorAll('.objectMatching-option-item .category-item-number')
                .forEach((el,j)=>el.textContent=pairs[j]||'');
            });
          };
          window.submitExam=()=>{
            const allPairsCorrect=JSON.stringify(pairs)===JSON.stringify(['D','A','C','B']);
            forceScore=round===0?97:(allPairsCorrect&&choice==='B'?100:97);
            if(forceScore===100)parent.document.querySelector('#course-outline img').alt='completed';
            originalSubmit();
          };showQuestion(1);
        }""")
        calls = []

        def solve(quiz):
            calls.append(quiz.total)
            return {1:'B', 2:'A', 3:'B', 4:'C', 5:'D'}

        with patch('autoquiz.netacad._WAIT', 3):
            workflow = CourseWorkflow(self.adapter, type('Solver', (), {'solve':staticmethod(solve)})(), dry_run=False)
            stats = workflow.run()
        self.assertEqual(workflow.last_error, '')
        self.assertEqual(stats.submitted, 1)
        self.assertEqual(calls, [5])
        self.assertEqual(self.frame.evaluate('[downloadClicks,submitClicks,round,pairs]'),
                         [0,2,1,['D','A','C','B']])

    def test_existing_wrong_review_is_read_before_reset_and_only_wrong_answer_is_resolved(self):
        self.page.locator('#course-outline').evaluate("""el=>{
          const header=document.createElement('button');header.id='node-button-exam';el.prepend(header);
        }""")
        self.frame.evaluate("""() => {
          choice='A';pairs=['A','B'];forceScore=100;
          document.getElementById('score').textContent=66;
          document.getElementById('finalnav').textContent='结果页面';
          const originalShow=showQuestion;
          window.showQuestion=n=>{originalShow(n);if(n===1&&reviewMode){
            const root=article.shadowRoot.querySelector('mcq-view').shadowRoot;
            root.querySelector('.component__widget').className='component__widget is-submittedshow-user-answer';
            root.querySelector('.feedback').remove();
            root.querySelectorAll('.mcq__item').forEach(e=>e.classList.remove('is-correct'));
          }};
          const originalSubmit=submitExam;
          window.submitExam=()=>{parent.document.querySelector('#course-outline img').alt='completed';originalSubmit();};
          review();
        }""")
        class Solver:
            def __init__(self):
                self.calls=[]
            def solve(inner,quiz):
                inner.calls.append(quiz)
                self.assertEqual(self.frame.evaluate('round'),1)
                self.assertEqual(quiz.total,1)
                self.assertEqual(quiz.questions[0].excluded_answers,('A',))
                return {1:'B'}
        solver=Solver()
        workflow=CourseWorkflow(self.adapter,solver,dry_run=False)
        stats=workflow.run()
        self.assertEqual(workflow.last_error,'')
        self.assertEqual(stats.submitted,1)
        self.assertEqual(len(solver.calls),1)
        self.assertEqual(self.frame.evaluate('[choice,pairs,submitClicks,round]'),['B',['A','B'],1,1])

    def show_failed_lesson_result(self, *, hide_grades):
        self.page.locator('#course-outline').evaluate(
            "el=>el.insertAdjacentHTML('afterbegin','<button id=node-button-exam></button>')")
        self.frame.evaluate("""hideGrades => {
          const original = showQuestion;
          const originalSubmit = submitExam;
          const originalReset = resetExam;
          window.showQuestion = n => {
            original(n);
            if (!reviewMode) return;
            const widget = article.shadowRoot.querySelector('[sgpulse-type="component"]');
            const root = widget.shadowRoot;
            if (hideGrades) {
              root.querySelector('.component__widget').className = 'component__widget is-disabled';
            } else if (n===1) {
              root.querySelector('.mcq__item.is-correct')?.classList.remove('is-correct');
            }
            root.querySelector('.feedback')?.remove();
          };
          window.resetExam = () => {
            document.querySelector('p').hidden = false;
            document.getElementById('finalnav').textContent = '提交页面';
            originalReset();
          };
          window.submitExam = () => {
            forceScore = choice==='B' && pairs.join('')==='AB' ? 100 : 67;
            if (forceScore===100) parent.document.querySelector('#course-outline img').alt='completed';
            originalSubmit();
          };
          document.querySelector('#results button[onclick="review()"]').remove();
          document.querySelector('p').hidden = true;
          document.getElementById('finalnav').textContent = '结果页面';
          document.getElementById('score').textContent = 67;
          choice = hideGrades ? '' : 'A';
          pairs = hideGrades ? ['',''] : ['A','B'];
          reviewMode = true;
          finalPage();
        }""", hide_grades)

    def test_failed_lesson_without_review_button_reuses_correct_answers_and_searches_wrong_question(self):
        self.show_failed_lesson_result(hide_grades=False)
        requested = []

        def solve(quiz):
            requested.append([(q.number, q.excluded_answers) for q in quiz.questions])
            return {1:'B'}

        solver = type('Solver', (), {'solve':staticmethod(solve)})()
        with patch('autoquiz.netacad._WAIT', 3):
            workflow = CourseWorkflow(self.adapter, solver, dry_run=False)
            self.assertEqual(workflow.run().submitted, 1, workflow.last_error)
        self.assertEqual(requested, [[(1, ('A',))]])
        self.assertEqual(self.frame.evaluate('[round,submitClicks]'), [1,1])

    def test_failed_lesson_with_only_score_retries_without_guessing_old_answers(self):
        self.show_failed_lesson_result(hide_grades=True)
        requested = []

        def solve(quiz):
            requested.append([(q.number, q.excluded_answers) for q in quiz.questions])
            return {1:'B', 2:'A', 3:'B'}

        solver = type('Solver', (), {'solve':staticmethod(solve)})()
        with patch('autoquiz.netacad._WAIT', 3):
            workflow = CourseWorkflow(self.adapter, solver, dry_run=False)
            self.assertEqual(workflow.run().submitted, 1, workflow.last_error)
        self.assertEqual(requested, [[(1, ()), (2, ()), (3, ())]])
        self.assertEqual(self.frame.evaluate('[round,submitClicks]'), [1,1])

    def test_existing_full_score_does_not_call_ai_or_reset_or_submit(self):
        self.frame.evaluate("""() => {
          document.getElementById('results').style.display='block';
          document.getElementById('score').textContent=100;article.style.display='none';
        }""")
        workflow=CourseWorkflow(self.adapter,None,dry_run=True)
        stats=workflow.run()
        self.assertEqual(workflow.last_error,'')
        self.assertEqual(stats.already_completed,1)
        self.assertEqual(self.frame.evaluate('[submitClicks,round,choiceClicks]'),[0,0,0])

    def test_course_relaunch_notice_pauses_at_result_before_review_and_reset(self):
        self.frame.evaluate("""() => {
          document.getElementById('results').style.display='block';
          document.getElementById('score').textContent=97;
          article.style.display='none';
          const notice=document.createElement('notify-view');
          notice.attachShadow({mode:'open'}).innerHTML='<div>请重新启动课程以记录您的进度</div>';
          document.body.append(notice);
        }""")
        flow = CourseWorkflow(self.adapter, None, dry_run=False)
        self.assertEqual(flow.run().errors, 1)
        self.assertIn('要求重新启动课程', flow.last_error)
        self.assertEqual(self.frame.evaluate('[reviewMode,round,submitClicks]'), [False,0,0])

    def test_existing_wrong_review_dry_run_reads_but_never_resets_or_submits(self):
        self.frame.evaluate("""() => {
          choice='A';pairs=['A','B'];document.getElementById('score').textContent=66;review();
        }""")
        workflow=CourseWorkflow(self.adapter,None,dry_run=True)
        stats=workflow.run()
        self.assertEqual(workflow.last_error,'')
        self.assertEqual(stats.tested,1)
        self.assertEqual(self.frame.evaluate('[submitClicks,round,choiceClicks]'),[0,0,0])

    def test_inline_practice_has_its_own_checked_submission_and_verified_mark(self):
        self.frame.evaluate("""() => {
          document.querySelector('nav').remove();document.querySelector('p').remove();
          const article=document.querySelector('article-view');
          const title=document.createElement('div');title.className='article__title-inner';title.textContent='合成考试';article.shadowRoot.prepend(title);
          const root=article.shadowRoot.querySelector('mcq-view').shadowRoot;
          const button=document.createElement('button');button.textContent='检查答案';
          button.onclick=()=>{window.submitClicks++;root.querySelector('.component__widget').className='component__widget is-submitted is-correct';};
          root.append(button);
        }""")
        quiz=self.adapter.extract_assessment()
        self.assertEqual(quiz.total,1)
        self.adapter.fill_answers({1:'B'})
        self.assertEqual(self.adapter.submit_assessment({1:'B'}).score,100)
        self.assertEqual(self.frame.evaluate('submitClicks'),1)

    def test_late_inline_question_blocks_fill_and_submission(self):
        self.frame.evaluate("""() => {
          document.querySelector('nav').remove();document.querySelector('p').remove();
          const title=document.createElement('div');title.className='article__title-inner';
          title.textContent='合成考试';article.shadowRoot.prepend(title);
        }""")
        self.adapter.extract_assessment()
        self.frame.evaluate("""() => {
          const widget=document.createElement('mcq-view');widget.setAttribute('sgpulse-type','component');
          widget.setAttribute('modelid','late-question');widget.textContent='延迟加载的题目';article.shadowRoot.append(widget);
        }""")
        with self.assertRaisesRegex(BrowserError,'组件数量已变化'):
            self.adapter.fill_answers({1:'B'})
        with self.assertRaisesRegex(BrowserError,'组件数量已变化'):
            self.adapter.submit_assessment({1:'B'})
        self.assertEqual(self.frame.evaluate('choiceClicks'),0)
        self.assertEqual(self.frame.evaluate('submitClicks'),0)

    def test_completed_child_does_not_advance_an_unfinished_section(self):
        self.frame.evaluate("""() => {
          document.querySelector('nav').remove();document.querySelector('p').remove();
          article.shadowRoot.innerHTML='<div class="article__title-inner">合成阅读</div>';
        }""")
        self.page.evaluate("""() => {
          document.querySelector('#course-outline').insertAdjacentHTML('beforeend',
            '<button role="treeitem"><img alt="completed"><span title="合成阅读">合成阅读</span></button>');
          const content=document.createElement('div');content.id='course-content';
          content.innerHTML='<button class="moduleNavBtn next--test">下一小节</button>';document.body.append(content);
          window.nextClicks=0;content.querySelector('button').onclick=()=>nextClicks++;
        }""")
        workflow=CourseWorkflow(self.adapter,None,dry_run=False)
        with patch('autoquiz.netacad._WAIT',0.1):
            workflow.run()
        self.assertIn('当前小节未全部完成',workflow.last_error)
        self.assertEqual(self.page.evaluate('nextClicks'),0)

    def test_reading_navigation_and_scope_ignore_heading_with_same_model_id(self):
        self.frame.set_content('''<meta charset="utf-8"><style>article-view{display:block;min-height:600px}</style>
          <article-view modelid="read-one"></article-view><article-view modelid="read-two"></article-view>''')
        self.frame.evaluate("""() => {
          document.querySelectorAll('article-view').forEach((article,index)=>{
            const title=index===0?'合成阅读一':'合成阅读二';
            article.attachShadow({mode:'open'}).innerHTML=
              '<div class="article__title-inner">'+title+'</div>'+
              '<heading-view aria-hidden="true" modelid="'+article.getAttribute('modelid')+'">'+title+'</heading-view>';
          });
        }""")
        self.page.locator('#course-outline').evaluate("""el=>el.innerHTML=
          '<button role="treeitem"><img alt="completed"><span title="合成阅读一">合成阅读一</span></button>'+
          '<button role="treeitem"><img alt="in progress"><span title="合成阅读二">合成阅读二</span></button>'""")
        self.assertTrue(self.adapter.next_node())
        self.assertEqual(self.adapter._cursor,1)
        self.assertEqual(self.adapter._scope().evaluate('el=>el.tagName'),'ARTICLE-VIEW')
        self.assertEqual(self.adapter.current_node().status,'pending')
        # 真有两个同类组件时必须暂停，不能靠 first() 随便选一个。
        self.frame.evaluate("""() => {
          const duplicate=document.createElement('article-view');duplicate.setAttribute('modelid','read-two');
          duplicate.textContent='另一篇同标识文章';document.body.append(duplicate);
        }""")
        self.adapter._cursor=0
        with self.assertRaisesRegex(BrowserError,'节点定位'):
            self.adapter.next_node()
        self.assertEqual(self.adapter._cursor,0)

    def test_question_binding_ignores_heading_with_same_model_id(self):
        self.frame.evaluate("""() => {
          const original=showQuestion;
          window.showQuestion=n=>{original(n);
            const widget=article.shadowRoot.querySelector('[sgpulse-type="component"]');
            const heading=document.createElement('heading-view');heading.setAttribute('modelid',widget.getAttribute('modelid'));
            heading.setAttribute('aria-hidden','true');heading.setAttribute('sgpulse-type','component');
            heading.textContent='组件的隐藏标题';widget.shadowRoot.prepend(heading);
          };showQuestion(1);
        }""")
        self.adapter.extract_assessment()
        answers={1:'B',2:'A',3:'B'}
        self.adapter.fill_answers(answers)
        self.assertEqual(self.adapter.selected_answers(),answers)
        self.assertEqual(self.frame.evaluate('submitClicks'),0)


if __name__ == '__main__':
    unittest.main()
