from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from autoquiz.answers import normalize_answer
from autoquiz.course import AssessmentReport
from autoquiz.kimi import DeepSeekSolver
from autoquiz.models import Option
from autoquiz.parser import parse_quiz
from autoquiz.retry_memory import RetryMemory
from tests.test_core import make_raw


class RetryMemoryTests(unittest.TestCase):
    def setUp(self):
        self.quiz=parse_quiz(make_raw())
        self.memory=RetryMemory()

    def test_wrong_choices_accumulate_by_text_after_letter_and_number_shuffle(self):
        self.quiz.questions[0].options.append(Option('C','丙'))
        self.memory.learn(self.quiz,{1:'A',2:'AC',3:'A'},AssessmentReport(60,(1,),{},True))
        self.quiz.questions[0].options=[Option('A','丙'),Option('B','甲'),Option('C','乙')]
        prepared,_=self.memory.prepare(self.quiz)
        self.assertEqual(prepared.questions[0].excluded_answers,('B',))
        self.memory.learn(self.quiz,{1:'C',2:'AC',3:'A'},AssessmentReport(60,(1,),{},True))
        prepared,_=self.memory.prepare(self.quiz)
        self.assertEqual(set(prepared.questions[0].excluded_answers),{'B','C'})
        self.assertIsNone(normalize_answer(prepared.questions[0],'B'))

    def test_multiple_choice_only_bans_the_entire_wrong_combination(self):
        self.memory.learn(self.quiz,{1:'A',2:'AC',3:'A'},AssessmentReport(60,(2,),{},True))
        q=self.memory.prepare(self.quiz)[0].questions[1]
        self.assertEqual(q.excluded_answers,('AC',))
        self.assertIsNone(normalize_answer(q,'CA'))
        self.assertEqual(normalize_answer(q,'AB'),'AB')

    def test_only_explicitly_correct_answers_are_reused_with_current_letters(self):
        self.memory.learn(self.quiz,{1:'A',2:'AC',3:'A'},AssessmentReport(60,(2,),{},True,correct_numbers=(1,)))
        self.quiz.questions[0].options=[Option('A','乙'),Option('B','甲')]
        _,fixed=self.memory.prepare(self.quiz)
        self.assertEqual(fixed,{1:'B'})

    def test_wrong_matching_group_does_not_ban_every_category_selection(self):
        self.memory.learn(self.quiz,{1:'A',2:'AC',3:'A'},AssessmentReport(60,(1,),{},True,uncertain_wrong_numbers=(1,)))
        q=self.memory.prepare(self.quiz)[0].questions[0]
        self.assertEqual(q.excluded_answers,())
        self.assertIn('配对组',q.retry_context)

    def test_duplicate_or_different_image_questions_do_not_share_negative_answers(self):
        self.quiz.questions[0].screenshot_b64='YWJj'
        self.memory.learn(self.quiz,{1:'A',2:'AC',3:'A'},AssessmentReport(60,(1,),{},True))
        self.quiz.questions[0].screenshot_b64='ZGlmZmVyZW50'
        self.assertEqual(self.memory.prepare(self.quiz)[0].questions[0].excluded_answers,())

    def test_api_repeated_known_wrong_answer_is_retried_once_and_rejected(self):
        self.memory.learn(self.quiz,{1:'A',2:'AC',3:'A'},AssessmentReport(60,(1,),{},True))
        quiz,_=self.memory.prepare(self.quiz)
        solver=DeepSeekSolver(api_key='synthetic')
        with patch.object(solver,'_request',side_effect=[{1:'A',2:'AC',3:'A'},{1:'B'}]) as request:
            self.assertEqual(solver.solve(quiz)[1],'B')
            self.assertEqual(request.call_count,2)
        with patch.object(solver,'_request',side_effect=[{1:'A',2:'AC',3:'A'},{1:'A'}]):
            self.assertNotIn(1,solver.solve(quiz))
        self.assertIn('excluded_answers',str(solver._message_content(quiz.questions)))
