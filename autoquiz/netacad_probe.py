"""NetAcad 只读 DOM 诊断。不读取课程模型、答案键、Cookie 或存储。"""
from __future__ import annotations


# 选择器来自当前公开播放器的 DOM 模板和实际辅助功能树。
# Web Components 内部需要遍历开放的 shadowRoot；普通 querySelectorAll 无法穿透。
STRUCTURE_JS = r"""
() => {
  const roots = [document];
  const all = [];
  for (let i = 0; i < roots.length; i++) {
    for (const el of roots[i].querySelectorAll('*')) {
      all.push(el);
      if (el.shadowRoot) roots.push(el.shadowRoot);
    }
  }
  const visible = el => {
    for (let cur = el; cur; cur = cur.parentElement || cur.getRootNode()?.host) {
      if (cur.hidden || cur.getAttribute('aria-hidden') === 'true') return false;
      const style = getComputedStyle(cur);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
    }
    return el.getClientRects().length > 0;
  };
  const count = selector => all.filter(el => el.matches(selector) && visible(el)).length;
  const text = el => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  const controls = all.filter(el => el.matches('button, [role="button"], input[type="checkbox"]') && visible(el));
  const knownLabels = /^(?:开始|提交|提交页面|结果页面|查看解析|审查评估|重置|重试|重新参加|Start|Submit|Review Assessment|Reset)$/i;
  const items = all.filter(el => el.matches('[role="treeitem"]') && visible(el));
  const states = {completed: 0, pending: 0, unknown: 0};
  for (const el of items) {
    const icons = Array.from(el.querySelectorAll('img, [role="img"]')).map(icon =>
      icon.getAttribute('alt') || icon.getAttribute('aria-label') || '').join(' ');
    if (/\bcompleted\b/i.test(icons)) states.completed++;
    else if (/\b(?:start|in progress)\b/i.test(icons)) states.pending++;
    else states.unknown++;
  }
  return {
    shadow_roots: roots.length - 1,
    visible_tree_items: items.length,
    directory_states: states,
    visible_question_components: count('.mcq__inner, .objectMatching__inner, .matching__inner, .gmcq__inner'),
    visible_choice_controls: count('[role="radio"], [role="checkbox"]'),
    visible_matching_categories: count('.objectMatching-category-item'),
    visible_matching_options: count('.objectMatching-option-item'),
    visible_confirmation: count('#confirm-exam'),
    visible_result_components: count('.assessmentResults__inner'),
    controls: controls.map(el => ({
      label: el.getAttribute('aria-label') || text(el),
      enabled: !el.disabled && el.getAttribute('aria-disabled') !== 'true' &&
        !el.classList.contains('is-disabled') && !el.classList.contains('disabled'),
    })).filter(item => knownLabels.test(item.label)),
    // 仅输出播放器组件的类名，不保存题干、答案、网址参数和账号信息。
    component_classes: [...new Set(all.filter(el => visible(el) &&
      el.classList.contains('component__inner')).flatMap(el =>
      Array.from(el.classList).filter(name => /^[A-Za-z]+__inner$/.test(name))))].sort(),
  };
}
"""
