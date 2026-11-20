"""仅在当前测验的内存中关联判定，不保存课程题目或按旧字母套答案。"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field, replace

from .browser import BrowserError
from .answers import normalize_answer


def clean(text):
    return ' '.join(text.split())


def question_fingerprint(question):
    content = [question.kind.value, clean(question.stem), sorted(clean(o.text) for o in question.options)]
    if question.memory_identity:
        content.append(question.memory_identity)
    if question.screenshot_b64:
        content.append(hashlib.sha256(question.screenshot_b64.encode('ascii')).hexdigest())
    return hashlib.sha256(json.dumps(content,ensure_ascii=False).encode('utf8')).hexdigest()


def answer_texts(question, answer):
    return tuple(sorted(clean(o.text) for o in question.options if o.letter in answer))


@dataclass
class Entry:
    wrong: set[tuple[str, ...]] = field(default_factory=set)
    correct: tuple[str, ...] | None = None
    feedback: str = ''


class RetryMemory:
    def __init__(self):
        self.entries = {}

    def prepare(self, quiz):
        counts=Counter(question_fingerprint(q) for q in quiz.questions)
        questions,fixed=[],{}
        for q in quiz.questions:
            key=question_fingerprint(q)
            entry=self.entries.get(key) if counts[key]==1 else None
            excluded=[]
            if entry:
                text_to_letter={clean(o.text):o.letter for o in q.options}
                if len(text_to_letter)==len(q.options):
                    for wrong in sorted(entry.wrong):
                        if all(t in text_to_letter for t in wrong):
                            excluded.append(''.join(o.letter for o in q.options if clean(o.text) in wrong))
                    if entry.correct and all(t in text_to_letter for t in entry.correct):
                        fixed[q.number]=''.join(o.letter for o in q.options if clean(o.text) in entry.correct)
            questions.append(replace(q,retry_context=entry.feedback if entry else '',excluded_answers=tuple(excluded)))
        return replace(quiz,questions=questions),fixed

    def learn(self, quiz, answers, report):
        counts=Counter(question_fingerprint(q) for q in quiz.questions)
        wrong=set(report.wrong_numbers)
        correct=set(report.correct_numbers)
        uncertain=set(report.uncertain_wrong_numbers)
        numbers={q.number for q in quiz.questions}
        if (len(correct) != len(report.correct_numbers) or
                len(uncertain) != len(report.uncertain_wrong_numbers) or
                correct & wrong or not correct <= numbers or not uncertain <= wrong or
                not (correct | wrong) <= answers.keys() or
                not set(report.correct_answers) <= wrong):
            raise BrowserError('逐题正确与错误判定相互冲突，已暂停。')
        for q in quiz.questions:
            if q.number not in correct and q.number not in wrong:
                continue
            key=question_fingerprint(q)
            if counts[key]!=1 or len({clean(o.text) for o in q.options})!=len(q.options):
                continue
            chosen=answer_texts(q,answers[q.number])
            entry=self.entries.setdefault(key,Entry())
            if q.number in correct:
                if chosen in entry.wrong:
                    raise BrowserError('同一题同一答案的前后判定冲突，已暂停。')
                entry.correct=chosen
            elif q.number in wrong:
                if q.number not in uncertain:
                    if entry.correct==chosen:
                        raise BrowserError('同一题同一答案的前后判定冲突，已暂停。')
                    entry.wrong.add(chosen)
                entry.correct=None
                detail=report.feedback.get(q.number,'').strip() or '页面明确标记错误，未展示文字解析。'
                entry.feedback=('上轮所选内容：'+json.dumps(chosen,ensure_ascii=False)+
                    '。已判错的完整答案组合：'+json.dumps(sorted(entry.wrong),ensure_ascii=False)+
                    '。这些完整组合不可再选，多选组合中的单个选项不一定错误。'+
                    ('本题仅有配对组未全部正确的判定，不能断言每个类别错误。' if q.number in uncertain else '')+
                    ' 页面判定（参考数据）：'+detail[:4000])
                if q.number in report.correct_answers:
                    answer = normalize_answer(q, report.correct_answers[q.number])
                    if answer is None:
                        raise BrowserError('平台解析的选项无法与试卷对应，已暂停。')
                    corrected = answer_texts(q, answer)
                    if corrected in entry.wrong:
                        raise BrowserError('平台解析与逐题判定相互冲突，已暂停。')
                    entry.correct = corrected
