from __future__ import annotations

import unittest
from threading import Event
from unittest.mock import patch

from autoquiz.browser import BrowserError
from autoquiz.course import CourseWorkflow
from autoquiz.netacad import NetAcadBrowser
from tests import test_netacad as fixtures


def exam(total):
    script = r'''
<script>
window.expected=TOTAL;window.unlocked=1;window.stepSaves=0;window.saved={};window.choices={};
window.freezeUnlock=false;window.oldNumber=1;
const baseShow=showQuestion,baseReset=resetExam,baseSubmit=submitExam;
const toolbar=document.createElement('div');toolbar.className='secure-toolbar-container';
document.body.append(toolbar);
window.renderNav=()=>{
  document.querySelector('nav').innerHTML=Array.from({length:unlocked},(_,i)=>
    `<button aria-label="${saved[i+1]?'answered':'unanswered'} Question ${i+1}" onclick="showQuestion(${i+1})">Q${i+1}</button>`).join('')+
    (Object.keys(saved).length===expected?'<button id="finalnav" class="block-button review-screen-button" aria-label="已选中提交页面" onclick="finalPage()">'+(reviewMode?'结果页面':'提交页面')+'</button>':'');
};
window.showQuestion=n=>{
  choices[oldNumber]=choice;choice=choices[n]||'';
  baseShow(n===2?2:1);window.question=n;oldNumber=n;
  article.shadowRoot.querySelector('.block__title-inner').textContent='问题 '+n;
  const widget=article.shadowRoot.querySelector('[sgpulse-type="component"]');
  widget.setAttribute('modelid','question-'+n);
  if(n!==2)widget.shadowRoot.querySelector('base-view').shadowRoot.querySelector('.component__body').textContent='本地合成单选题 '+n;
  toolbar.style.display=reviewMode?'none':'block';
  toolbar.innerHTML=`<div class="question-label">${n} 的 ${expected} 问题</div>
    <button class="submit-button" aria-label="submit" onclick="saveStep()">提交本题</button>`;
  const update=()=>{toolbar.querySelector('button').disabled=n===2?pairs.some(p=>!p):!choice;};
  widget.shadowRoot.querySelectorAll('.mcq__item,.objectMatching-option-item').forEach(e=>e.addEventListener('click',update));
  update();renderNav();
};
window.saveStep=()=>{
  if(toolbar.querySelector('button').disabled)throw Error('incomplete');
  stepSaves++;saved[question]=true;choices[question]=choice;
  if(freezeUnlock)return;
  if(question<expected){unlocked++;renderNav();setTimeout(()=>showQuestion(unlocked),120);}
  else{renderNav();toolbar.style.display='none';finalPage();}
};
window.resetExam=()=>{if(window.nextTotal)expected=nextTotal;
  document.querySelector('p').textContent='考试包括 '+expected+' 问题';
  unlocked=1;saved={};choices={};oldNumber=1;choice='';baseReset();};
window.submitExam=()=>{if(forceScore===100||forceScore===null)parent.document.querySelector('#course-outline img').alt='completed';baseSubmit();toolbar.style.display='none';};
document.querySelector('p').textContent='考试包括 '+expected+' 问题';showQuestion(1);
</script>'''.replace('TOTAL', str(total))
    return fixtures.EXAM + script


class Solver:
    def __init__(self):
        self.calls=[]

    def solve(self, quiz):
        self.calls.append(quiz)
        return {q.number:'A' if '类别甲' in q.stem else 'B' for q in quiz.questions}


class SequentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.manager=sync_playwright().start()
        try:
            cls.browser=cls.manager.chromium.launch(channel='msedge',headless=True)
        except Exception:
            cls.manager.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.manager.stop()

    def setUp(self):
        self.total=5
        self.page=self.browser.new_page(viewport={'width':1280,'height':1000})
        self.page.route('https://www.netacad.com/**', lambda route:route.fulfill(
            body=exam(self.total) if '/authoring-resources/' in route.request.url else fixtures.OUTER,
            content_type='text/html'))
        self.page.goto('https://www.netacad.com/zh/launch?id=synthetic')
        self.frame=self.page.frames[1]
        self.frame.wait_for_function('typeof saveStep === "function"')
        self.page.locator('#course-outline').evaluate("el=>el.insertAdjacentHTML('afterbegin','<button id=\"node-button-exam\"></button>')")
        self.adapter=NetAcadBrowser(dry_run=False)
        self.adapter.page=self.page

    def tearDown(self):
        self.page.close()

    def load(self,total):
        self.total=total
        self.frame.goto('https://www.netacad.com/authoring-resources/index.html?synthetic='+str(total))
        self.frame.wait_for_function('typeof saveStep === "function"')
        self.adapter=NetAcadBrowser(dry_run=False);self.adapter.page=self.page

    def test_variable_totals_unlock_all_questions_then_submit_once(self):
        for total in (1,3,5):
            with self.subTest(total=total):
                self.load(total)
                solver=Solver();flow=CourseWorkflow(self.adapter,solver,dry_run=False)
                stats=flow.run()
                self.assertEqual(flow.last_error,'')
                self.assertEqual(stats.submitted,1)
                self.assertEqual(len(solver.calls),total)
                self.assertEqual(self.frame.evaluate('stepSaves'),total)
                self.assertEqual(self.frame.evaluate('submitClicks'),1)
                self.assertEqual(len(self.adapter._saved_steps),total)

    def test_dry_run_previews_one_question_without_unlocking_or_submitting(self):
        self.adapter.dry_run=True
        solver=Solver();flow=CourseWorkflow(self.adapter,solver)
        stats=flow.run()
        self.assertEqual(flow.last_error,'')
        self.assertEqual(stats.tested,1)
        self.assertEqual(len(solver.calls),1)
        self.assertEqual(self.frame.evaluate('[stepSaves,unlocked,submitClicks]'),[0,1,0])

    def test_saved_answers_submit_without_returning_to_previously_saved_questions(self):
        self.load(3)
        original = self.adapter.selected_answers

        def selected():
            self.assertLess(self.frame.evaluate('stepSaves'), 3)
            return original()

        with patch.object(self.adapter, 'selected_answers', side_effect=selected), patch.object(
                self.adapter, '_go_question', wraps=self.adapter._go_question) as navigate:
            flow = CourseWorkflow(self.adapter, Solver(), dry_run=False)
            stats = flow.run()
        self.assertEqual(flow.last_error, '')
        self.assertEqual(stats.submitted, 1)
        self.assertEqual([call.args[0] for call in navigate.call_args_list], [1,2,3])
        self.assertEqual(self.frame.evaluate('[stepSaves,submitClicks]'), [3,1])
        self.assertTrue(self.frame.locator('#confirm-exam').is_checked())

    def test_incomplete_answer_never_saves_or_unlocks(self):
        solver=Solver();solver.solve=lambda quiz:{}
        flow=CourseWorkflow(self.adapter,solver,dry_run=False);flow.run()
        self.assertIn('有效答案不足',flow.last_error)
        self.assertEqual(self.frame.evaluate('[stepSaves,unlocked,submitClicks]'),[0,1,0])

    def test_direct_final_submission_of_only_current_question_is_blocked(self):
        self.adapter.open_assessment()
        self.adapter.extract_assessment_step(1);self.adapter.fill_answers({1:'B'})
        with self.assertRaisesRegex(BrowserError,'尚未全部保存'):
            self.adapter.submit_assessment({1:'B'})
        self.assertEqual(self.frame.evaluate('[stepSaves,submitClicks]'),[0,0])

    def test_current_mcq_can_fill_verify_and_save_after_heading_scrolls_out_of_view(self):
        self.load(1)
        self.adapter.open_assessment()
        self.adapter.extract_assessment_step(1)
        self.frame.evaluate("""() => {
          window.questionNavClicks=0;
          document.querySelector('nav').addEventListener('click',event=>{
            if(event.target.closest('button[aria-label]'))questionNavClicks++;
          });
          document.body.style.minHeight='2200px';
          toolbar.style.cssText='position:fixed;top:0;right:0;z-index:10';
          const widget=article.shadowRoot.querySelector('mcq-view');
          widget.shadowRoot.querySelector('.component__widget').style.paddingTop='1000px';
          window.scrollTo(0,900);
        }""")
        self.assertLessEqual(self.frame.locator('.block__title-inner').evaluate(
            'el=>el.getBoundingClientRect().bottom'),0)
        self.assertTrue(self.frame.locator('mcq-view').evaluate(
            'el=>{const r=el.getBoundingClientRect();return r.top<innerHeight&&r.bottom>0;}'))
        with patch('autoquiz.netacad._WAIT',0.6):
            self.adapter.fill_answers({1:'B'})
            self.assertEqual(self.adapter.selected_answers(),{1:'B'})
            self.adapter.save_assessment_step(1,{1:'B'})
        self.assertEqual(self.frame.evaluate('[questionNavClicks,stepSaves,submitClicks]'),[0,1,0])
        self.assertEqual(set(self.adapter._saved_steps),{1})

    def test_matching_first_category_scroll_does_not_navigate_before_remaining_pairs_or_save(self):
        self.load(2)
        self.adapter.open_assessment()
        self.adapter.extract_assessment_step(1)
        self.adapter.fill_answers({1:'B'})
        self.adapter.save_assessment_step(1,{1:'B'})
        self.adapter.extract_assessment_step(2)
        saves_before=self.frame.evaluate('stepSaves')
        self.frame.evaluate("""() => {
          window.questionNavClicks=0;window.categoryScrollHidHeading=false;
          document.querySelector('nav').addEventListener('click',event=>{
            if(event.target.closest('button[aria-label]'))questionNavClicks++;
          });
          document.body.style.minHeight='2200px';
          toolbar.style.cssText='position:fixed;top:0;right:0;z-index:10';
          const widget=article.shadowRoot.querySelector('object-matching-view');
          widget.shadowRoot.querySelector('.component__widget').style.paddingTop='1000px';
          widget.shadowRoot.querySelector('.objectMatching-category-item').addEventListener('click',()=>{
            categoryScrollHidHeading=article.shadowRoot.querySelector('.block__title-inner')
              .getBoundingClientRect().bottom<=0;
          },{once:true});
          window.scrollTo(0,0);
        }""")
        self.assertGreater(self.frame.locator('.block__title-inner').evaluate(
            'el=>el.getBoundingClientRect().bottom'),0)
        answers={1:'A',2:'B'}
        with patch('autoquiz.netacad._WAIT',0.6):
            self.adapter.fill_answers(answers)
            self.assertTrue(self.frame.evaluate('categoryScrollHidHeading'))
            self.assertEqual(self.adapter.selected_answers(),answers)
            self.adapter.save_assessment_step(2,answers)
        self.assertEqual(self.frame.evaluate('[questionNavClicks,stepSaves,submitClicks]'),
                         [0,saves_before+1,0])
        self.assertEqual(set(self.adapter._saved_steps),{1,2})

    def test_dropdown_rows_are_verified_then_saved_as_one_exam_question(self):
        self.load(2)
        self.frame.evaluate(fixtures.DROPDOWN_WIDGET)
        self.frame.evaluate("""() => {
          document.addEventListener('fixture-dropdown-selected', () => {
            toolbar.querySelector('button').disabled = Object.keys(dropdownSelections).length !== 3;
          });
        }""")
        self.adapter.open_assessment()
        self.adapter.extract_assessment_step(1)
        self.adapter.fill_answers({1:'B'})
        self.adapter.save_assessment_step(1,{1:'B'})
        quiz = self.adapter.extract_assessment_step(2)
        self.assertEqual(quiz.total, 3)
        answers = {1:'B', 2:'B', 3:'A'}
        self.adapter.fill_answers(answers)
        self.adapter.save_assessment_step(2, answers)
        self.assertEqual(self.adapter.finish_assessment_steps().total, 4)
        self.assertEqual(self.adapter.completed_assessment_answers(), {1:'B', 2:'B', 3:'B', 4:'A'})
        self.assertEqual(self.frame.evaluate('[stepSaves, dropdownClicks, submitClicks]'), [2,3,0])

    def prepare_pending_submission(self):
        self.load(2)
        self.adapter.open_assessment()
        for step in (1,2):
            quiz=self.adapter.extract_assessment_step(step)
            answers=Solver().solve(quiz)
            self.adapter.fill_answers(answers)
            self.adapter.save_assessment_step(step,answers)
        self.assertTrue(self.frame.locator('#confirm-exam').is_visible())

    def test_pending_final_page_resumes_existing_answers_and_auto_confirms_without_ai(self):
        self.prepare_pending_submission()
        before=self.frame.evaluate('choiceClicks')
        fresh=NetAcadBrowser(dry_run=False);fresh.page=self.page
        flow=CourseWorkflow(fresh,None,dry_run=False)
        stats=flow.run()
        self.assertEqual(flow.last_error,'')
        self.assertEqual(stats.submitted,1)
        self.assertEqual(self.frame.evaluate('[stepSaves,submitClicks,choiceClicks]'),[2,1,before])
        self.assertTrue(self.frame.locator('#confirm-exam').is_checked())
        with self.assertRaisesRegex(BrowserError,'已尝试最终提交'):
            fresh.submit_assessment({1:'B',2:'A',3:'B'})
        self.assertEqual(self.frame.evaluate('submitClicks'),1)

    def test_pending_final_page_in_dry_run_never_checks_or_submits(self):
        self.prepare_pending_submission()
        fresh=NetAcadBrowser(dry_run=True);fresh.page=self.page
        flow=CourseWorkflow(fresh,None)
        stats=flow.run()
        self.assertEqual(flow.last_error,'')
        self.assertEqual(stats.tested,1)
        self.assertEqual(self.frame.evaluate('[stepSaves,submitClicks]'),[2,0])
        self.assertFalse(self.frame.locator('#confirm-exam').is_checked())

    def test_pending_final_recovery_rechecks_answered_labels_after_leaving_selected_question(self):
        self.prepare_pending_submission()
        self.frame.evaluate("""() => {
          const originalShow=showQuestion,originalFinal=finalPage;
          window.showQuestion=n=>{originalShow(n);
            const active=document.querySelectorAll('nav button[aria-label]')[n-1];
            active.setAttribute('aria-label','已选中 Question '+n);
          };
          window.finalPage=()=>{originalFinal();renderNav();};
        }""")
        fresh=NetAcadBrowser(dry_run=False);fresh.page=self.page
        flow=CourseWorkflow(fresh,None,dry_run=False)
        self.assertEqual(flow.run().submitted,1)
        self.assertEqual(flow.last_error,'')
        self.assertEqual(self.frame.evaluate('[stepSaves,submitClicks]'),[2,1])

    def test_pending_final_page_rejects_unanswered_or_unknown_submission_state(self):
        self.prepare_pending_submission()
        fresh=NetAcadBrowser(dry_run=False);fresh.page=self.page
        self.frame.locator('#final').evaluate("el=>el.insertAdjacentHTML('beforeend','<div class=\"unanswered-numbers\">Q2</div>')")
        with self.assertRaisesRegex(BrowserError,'未解答'):
            fresh.resume_assessment()
        self.frame.locator('.unanswered-numbers').evaluate('el=>el.remove()')
        self.frame.locator('nav button[aria-label]').first.evaluate("el=>el.setAttribute('aria-label','unanswered Question 1')")
        with self.assertRaisesRegex(BrowserError,'已保存作答状态不完整'):
            fresh.resume_assessment()
        self.frame.locator('#confirm-exam').check()
        with self.assertRaisesRegex(BrowserError,'此前是否尝试过'):
            fresh.resume_assessment()
        self.assertEqual(self.frame.evaluate('submitClicks'),0)

    def test_final_form_itself_acknowledges_last_save_without_navigation_button(self):
        self.load(1)
        self.frame.evaluate("""() => {const original=saveStep;window.saveStep=()=>{
          original();document.getElementById('finalnav')?.remove();};}""")
        self.adapter.open_assessment()
        self.adapter.extract_assessment_step(1);self.adapter.fill_answers({1:'B'})
        with patch('autoquiz.netacad._WAIT',0.5):
            self.adapter.save_assessment_step(1,{1:'B'})
        self.assertEqual(set(self.adapter._saved_steps),{1})
        self.assertTrue(self.frame.locator('#confirm-exam').is_visible())
        self.assertEqual(self.frame.evaluate('submitClicks'),0)

    def test_unknown_final_result_never_repeats_final_submission(self):
        self.prepare_pending_submission()
        fresh=NetAcadBrowser(dry_run=False);fresh.page=self.page
        _,answers=fresh.resume_assessment()
        self.frame.evaluate('() => {window.submitExam=()=>{window.submitClicks++;};}')
        with patch('autoquiz.netacad._WAIT',0.15),self.assertRaisesRegex(BrowserError,'未读取到本轮成绩'):
            fresh.submit_assessment(answers)
        with self.assertRaisesRegex(BrowserError,'已尝试最终提交'):
            fresh.submit_assessment(answers)
        self.assertEqual(self.frame.evaluate('submitClicks'),1)

    def test_failed_unlock_is_not_retried_or_finally_submitted(self):
        self.frame.evaluate('freezeUnlock=true')
        self.adapter.open_assessment()
        self.adapter.extract_assessment_step(1);self.adapter.fill_answers({1:'B'})
        with patch('autoquiz.netacad._WAIT',0.15),self.assertRaisesRegex(BrowserError,'保存后未核实'):
            self.adapter.save_assessment_step(1,{1:'B'})
        with self.assertRaisesRegex(BrowserError,'已尝试保存'):
            self.adapter.save_assessment_step(1,{1:'B'})
        self.assertEqual(self.frame.evaluate('[stepSaves,unlocked,submitClicks]'),[1,1,0])

    def test_stop_after_saved_question_prevents_next_ai_fill_or_save(self):
        event=Event();self.adapter.stop_event=event
        save=self.adapter.save_assessment_step
        def stop_after_save(*args):
            save(*args);event.set()
        solver=Solver();flow=CourseWorkflow(self.adapter,solver,dry_run=False,stop_event=event)
        with patch.object(self.adapter,'save_assessment_step',side_effect=stop_after_save):flow.run()
        self.assertEqual(flow.last_error,'')
        self.assertEqual(len(solver.calls),1)
        self.assertEqual(self.frame.evaluate('[stepSaves,submitClicks]'),[1,0])

    def test_current_counter_wins_over_other_offscreen_exam_instructions(self):
        self.frame.locator('p').first.evaluate("el=>el.textContent='考试包括 33 问题'")
        self.assertEqual(self.adapter.assessment_steps(),5)
        self.assertEqual(set(self.adapter._nav()),{1})

    def test_total_changes_mid_attempt_pause_before_more_saves(self):
        self.adapter.assessment_steps()
        self.frame.evaluate("expected=6;showQuestion(1)")
        with self.assertRaisesRegex(BrowserError,'总题数已变化'):
            self.adapter.assessment_steps()
        self.assertEqual(self.frame.evaluate('[stepSaves,submitClicks]'),[0,0])

    def test_already_unlocked_questions_with_unknown_saved_state_pause(self):
        self.frame.evaluate('unlocked=2;renderNav()')
        solver=Solver();flow=CourseWorkflow(self.adapter,solver,dry_run=False);flow.run()
        self.assertIn('保存状态未知',flow.last_error)
        self.assertEqual(len(solver.calls),0)
        self.assertEqual(self.frame.evaluate('[stepSaves,submitClicks]'),[0,0])

    def prepare_saved_prefix(self, count=2):
        self.load(5)
        self.adapter.open_assessment()
        for step in range(1,count+1):
            quiz=self.adapter.extract_assessment_step(step)
            answers=Solver().solve(quiz)
            self.adapter.fill_answers(answers)
            self.adapter.save_assessment_step(step,answers)
        self.adapter._go_question(count+1)
        fresh=NetAcadBrowser(dry_run=False);fresh.page=self.page
        return fresh

    def test_saved_prefix_resumes_remaining_questions_without_resaving_old_answers(self):
        fresh=self.prepare_saved_prefix()
        before=self.frame.evaluate('choiceClicks')
        solver=Solver();flow=CourseWorkflow(fresh,solver,dry_run=False)
        stats=flow.run()
        self.assertEqual(flow.last_error,'')
        self.assertEqual(stats.submitted,1)
        self.assertEqual(len(solver.calls),3)
        self.assertTrue(all('单选题 '+str(n) in quiz.questions[0].stem
                            for n,quiz in enumerate(solver.calls,3)))
        self.assertEqual(self.frame.evaluate('[stepSaves,submitClicks,round,choiceClicks]'),[5,1,0,before+3])
        self.assertEqual(set(fresh._saved_steps),{1,2,3,4,5})

    def test_saved_prefix_dry_run_only_previews_first_unsaved_question(self):
        fresh=self.prepare_saved_prefix()
        fresh.dry_run=True
        solver=Solver();flow=CourseWorkflow(fresh,solver,dry_run=True)
        self.assertEqual(flow.run().tested,1)
        self.assertEqual(flow.last_error,'')
        self.assertEqual(len(solver.calls),1)
        self.assertIn('单选题 3',solver.calls[0].questions[0].stem)
        self.assertEqual(self.frame.evaluate('[stepSaves,submitClicks,round,question]'),[2,0,0,3])

    def test_locally_answered_current_question_does_not_count_as_already_saved(self):
        fresh=self.prepare_saved_prefix()
        self.frame.evaluate("""() => {
          choice='A';choices[3]='A';
          const original=renderNav;
          window.renderNav=()=>{original();
            document.querySelectorAll('nav button[aria-label]')[2].setAttribute('aria-label',
              (question===3?'已选中':'answered')+' Question 3');
          };showQuestion(3);
        }""")
        solver=Solver();flow=CourseWorkflow(fresh,solver,dry_run=False)
        self.assertEqual(flow.run().submitted,1)
        self.assertEqual(flow.last_error,'')
        self.assertEqual(len(solver.calls),3)
        self.assertEqual(self.frame.evaluate('[stepSaves,submitClicks]'),[5,1])

    def test_saved_prefix_accepts_selected_accessible_label_on_current_and_saved_questions(self):
        fresh=self.prepare_saved_prefix()
        self.frame.evaluate("""() => {
          const original=renderNav;
          window.renderNav=()=>{original();
            const buttons=document.querySelectorAll('nav button[aria-label]');
            const active=buttons[question-1];
            if(active)active.setAttribute('aria-label','已选中 '+active.getAttribute('aria-label').replace(/^(?:unanswered|answered) /,''));
          };renderNav();
        }""")
        self.assertEqual(fresh.assessment_steps(),5)
        restored=fresh.resume_assessment_steps()
        self.assertEqual(restored,[])
        self.assertEqual(fresh.deferred_assessment_steps(),2)
        self.assertEqual(self.frame.evaluate('[stepSaves,submitClicks,question]'),[2,0,3])

    def test_incomplete_existing_answer_blocks_submission_after_deferred_review(self):
        fresh=self.prepare_saved_prefix()
        self.frame.evaluate("pairs=['A','']")
        solver=Solver();flow=CourseWorkflow(fresh,solver,dry_run=False)
        self.assertEqual(flow.run().errors,1)
        self.assertIn('已保存题目',flow.last_error)
        self.assertEqual(len(solver.calls),3)
        self.assertEqual(self.frame.evaluate('[stepSaves,submitClicks,round]'),[5,0,0])

    def test_selected_label_hiding_saved_last_question_never_causes_duplicate_save(self):
        fresh=self.prepare_saved_prefix()
        self.frame.evaluate("""() => {
          saved[3]=true;choices[3]='B';
          const original=renderNav,originalShow=showQuestion;
          window.renderNav=()=>{original();
            const active=document.querySelectorAll('nav button[aria-label]')[question-1];
            if(active)active.setAttribute('aria-label','已选中 '+active.getAttribute('aria-label').replace(/^(?:unanswered|answered) /,''));
          };
          window.showQuestion=n=>{originalShow(n);if(n===3){
            article.shadowRoot.querySelector('mcq-view').shadowRoot.querySelector('.component__widget').classList.add('is-submitted');
            toolbar.querySelector('button').disabled=true;
          }};showQuestion(3);
        }""")
        solver=Solver();flow=CourseWorkflow(fresh,solver,dry_run=False)
        self.assertEqual(flow.run().errors,1)
        self.assertIn('保存状态未知',flow.last_error)
        self.assertEqual(solver.calls,[])
        self.assertEqual(self.frame.evaluate('[stepSaves,submitClicks]'),[2,0])

    def test_stop_during_saved_prefix_recovery_prevents_new_answers_and_saves(self):
        fresh=self.prepare_saved_prefix()
        event=Event();fresh.stop_event=event
        solver=Solver()
        original=fresh._cached_saved_step
        def stop_after_read(number):
            quiz=original(number)
            event.set()
            return quiz
        with patch.object(fresh,'_cached_saved_step',side_effect=stop_after_read):
            flow=CourseWorkflow(fresh,solver,dry_run=False,stop_event=event)
            self.assertEqual(flow.run().errors,0)
        self.assertEqual(solver.calls,[])
        self.assertEqual(self.frame.evaluate('[stepSaves,submitClicks,round]'),[2,0,0])

    def test_offscreen_start_of_current_exam_is_never_clicked(self):
        self.frame.evaluate("""() => {
          window.startClicks=0;const button=document.createElement('button');button.textContent='开始';
          button.style.cssText='position:absolute;top:5000px';button.onclick=()=>startClicks++;
          document.body.append(button);
        }""")
        self.adapter.open_assessment()
        self.assertEqual(self.frame.evaluate('startClicks'),0)
        self.assertEqual(self.adapter.assessment_steps(),5)

    def test_wrong_first_round_restarts_and_resolves_every_question(self):
        self.load(3)
        self.frame.evaluate("""() => {
          nextTotal=2;
          const original=submitExam;
          window.submitExam=()=>{forceScore=round===0?66:100;original();};
        }""")
        class RetrySolver(Solver):
            def solve(inner,quiz):
                result=super().solve(quiz)
                if self.frame.evaluate('round')==0 and self.frame.evaluate('question')==1:result={1:'A'}
                return result
        solver=RetrySolver();flow=CourseWorkflow(self.adapter,solver,dry_run=False)
        stats=flow.run()
        self.assertEqual(flow.last_error,'')
        self.assertEqual(stats.submitted,1)
        self.assertEqual(len(solver.calls),3)
        self.assertEqual(self.frame.evaluate('[stepSaves,submitClicks,round]'),[5,2,1])
        self.assertEqual(self.frame.evaluate('choices[1]'),'B')
