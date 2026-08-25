/**
 * 설문조사 양식 편집기
 * - 편집 탭: 항목(질문)과 답변(보기)을 추가/수정/삭제/정렬
 * - 미리보기 탭: 실제 응답 화면. 제출하면 필수 검증 후 응답 결과를 보여준다.
 * 상태는 localStorage 에 저장되어 새로고침해도 유지된다.
 */

const STORAGE_KEY = 'survey-form:v1';
const CHOICE_TYPES = ['radio', 'checkbox', 'select'];
const el = {
  title: document.getElementById('survey-title'),
  description: document.getElementById('survey-description'),
  questionList: document.getElementById('question-list'),
  panelEdit: document.getElementById('panel-edit'),
  panelPreview: document.getElementById('panel-preview'),
  previewTitle: document.getElementById('preview-title'),
  previewDesc: document.getElementById('preview-description'),
  previewQuestions: document.getElementById('preview-questions'),
  form: document.getElementById('survey-form'),
  answerSheet: document.getElementById('answer-sheet'),
  answerList: document.getElementById('answer-list'),
  toast: document.getElementById('toast'),
  tplQuestion: document.getElementById('tpl-question'),
  tplOption: document.getElementById('tpl-option'),
};

let survey = load() || {
  title: '새 설문지',
  description: '',
  questions: [createQuestion({ label: '이 설문을 어떻게 알게 되셨나요?', type: 'radio' })],
};

/* ── 상태 헬퍼 ─────────────────────────────────────────────── */

function uid() {
  return Math.random().toString(36).slice(2, 10);
}

function createQuestion(overrides = {}) {
  const question = {
    id: uid(),
    label: '',
    type: 'short',
    required: false,
    options: [],
    ...overrides,
  };
  if (isChoice(question.type) && question.options.length === 0) {
    question.options = [createOption()];
  }
  return question;
}

function createOption(text = '') {
  return { id: uid(), text };
}

function isChoice(type) {
  return CHOICE_TYPES.includes(type);
}

function findQuestion(id) {
  return survey.questions.find((q) => q.id === id);
}

function save() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(survey));
  } catch (err) {
    /* 저장 공간이 없거나 차단된 경우엔 메모리 상태로만 동작한다. */
  }
}

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (!data || !Array.isArray(data.questions)) return null;
    return data;
  } catch (err) {
    return null;
  }
}

/* ── 편집 화면 렌더링 ───────────────────────────────────────── */

function renderEditor() {
  el.title.value = survey.title;
  el.description.value = survey.description;
  el.questionList.textContent = '';

  if (survey.questions.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = '아직 항목이 없습니다. 아래 “항목 추가”를 눌러 첫 질문을 만들어 보세요.';
    el.questionList.append(empty);
    return;
  }

  survey.questions.forEach((question, index) => {
    el.questionList.append(renderQuestionCard(question, index));
  });
}

function renderQuestionCard(question, index) {
  const node = el.tplQuestion.content.firstElementChild.cloneNode(true);
  node.dataset.questionId = question.id;

  const label = node.querySelector('[data-field="label"]');
  label.value = question.label;
  label.placeholder = `질문 ${index + 1}`;

  node.querySelector('[data-field="type"]').value = question.type;
  node.querySelector('[data-field="required"]').checked = question.required;

  const optionBox = node.querySelector('[data-role="options"]');
  const addOptionBtn = node.querySelector('[data-action="add-option"]');

  if (isChoice(question.type)) {
    question.options.forEach((option, optionIndex) => {
      optionBox.append(renderOptionRow(question, option, optionIndex));
    });
  } else {
    optionBox.remove();
    addOptionBtn.remove();
  }

  return node;
}

function renderOptionRow(question, option, index) {
  const row = el.tplOption.content.firstElementChild.cloneNode(true);
  row.dataset.optionId = option.id;

  const marker = row.querySelector('[data-role="marker"]');
  marker.classList.add(`option__marker--${question.type}`);
  if (question.type === 'select') marker.textContent = `${index + 1}.`;

  const input = row.querySelector('[data-field="text"]');
  input.value = option.text;
  input.placeholder = `답변 ${index + 1}`;

  // 답변이 하나뿐이면 삭제 버튼을 감춰 빈 객관식이 만들어지지 않게 한다.
  if (question.options.length <= 1) {
    row.querySelector('[data-action="remove-option"]').remove();
  }

  return row;
}

/* ── 편집 이벤트 ───────────────────────────────────────────── */

el.title.addEventListener('input', () => {
  survey.title = el.title.value;
  save();
});

el.description.addEventListener('input', () => {
  survey.description = el.description.value;
  save();
});

// 텍스트 입력은 다시 그리지 않고 상태만 갱신한다(포커스 유지).
el.questionList.addEventListener('input', (event) => {
  const field = event.target.dataset.field;
  const card = event.target.closest('[data-question-id]');
  if (!field || !card) return;

  const question = findQuestion(card.dataset.questionId);
  if (!question) return;

  if (field === 'label') {
    question.label = event.target.value;
  } else if (field === 'text') {
    const optionId = event.target.closest('[data-option-id]').dataset.optionId;
    const option = question.options.find((o) => o.id === optionId);
    if (option) option.text = event.target.value;
  }
  save();
});

el.questionList.addEventListener('change', (event) => {
  const field = event.target.dataset.field;
  const card = event.target.closest('[data-question-id]');
  if (!field || !card) return;

  const question = findQuestion(card.dataset.questionId);
  if (!question) return;

  if (field === 'type') {
    question.type = event.target.value;
    if (isChoice(question.type) && question.options.length === 0) {
      question.options = [createOption()];
    }
    save();
    renderEditor();
  } else if (field === 'required') {
    question.required = event.target.checked;
    save();
  }
});

el.questionList.addEventListener('click', (event) => {
  const button = event.target.closest('[data-action]');
  if (!button) return;

  const card = button.closest('[data-question-id]');
  const question = findQuestion(card.dataset.questionId);
  if (!question) return;

  const index = survey.questions.indexOf(question);

  switch (button.dataset.action) {
    case 'add-option':
      question.options.push(createOption());
      break;
    case 'remove-option': {
      const optionId = button.closest('[data-option-id]').dataset.optionId;
      question.options = question.options.filter((o) => o.id !== optionId);
      break;
    }
    case 'remove-question':
      survey.questions.splice(index, 1);
      break;
    case 'duplicate':
      survey.questions.splice(index + 1, 0, {
        ...structuredClone(question),
        id: uid(),
        options: question.options.map((o) => ({ ...o, id: uid() })),
      });
      break;
    case 'move-up':
      if (index > 0) {
        survey.questions.splice(index - 1, 0, survey.questions.splice(index, 1)[0]);
      }
      break;
    case 'move-down':
      if (index < survey.questions.length - 1) {
        survey.questions.splice(index + 1, 0, survey.questions.splice(index, 1)[0]);
      }
      break;
    default:
      return;
  }

  save();
  renderEditor();
});

document.querySelector('[data-action="add-question"]').addEventListener('click', () => {
  survey.questions.push(createQuestion());
  save();
  renderEditor();
  const cards = el.questionList.querySelectorAll('[data-question-id]');
  const last = cards[cards.length - 1];
  if (last) last.querySelector('[data-field="label"]').focus();
});

/* ── 미리보기 렌더링 ───────────────────────────────────────── */

function renderPreview() {
  el.previewTitle.textContent = survey.title || '제목 없는 설문지';
  el.previewDesc.textContent = survey.description;
  el.previewDesc.classList.toggle('is-hidden', !survey.description);
  el.previewQuestions.textContent = '';
  el.answerSheet.classList.add('is-hidden');
  el.form.classList.remove('is-hidden');

  if (survey.questions.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = '표시할 항목이 없습니다. 편집 탭에서 항목을 추가해 주세요.';
    el.previewQuestions.append(empty);
    return;
  }

  survey.questions.forEach((question, index) => {
    el.previewQuestions.append(renderField(question, index));
  });
}

function renderField(question, index) {
  const wrap = document.createElement('div');
  wrap.className = 'field';
  wrap.dataset.questionId = question.id;

  const label = document.createElement('label');
  label.className = 'field__label';
  label.textContent = question.label || `질문 ${index + 1}`;
  if (question.required) {
    const mark = document.createElement('span');
    mark.className = 'field__required';
    mark.textContent = '*';
    label.append(mark);
  }
  wrap.append(label);

  const name = `q_${question.id}`;

  if (question.type === 'short' || question.type === 'long') {
    const control = document.createElement(question.type === 'long' ? 'textarea' : 'input');
    control.className = 'field__control';
    control.name = name;
    if (question.type === 'long') control.rows = 4;
    else control.type = 'text';
    control.placeholder = '답변을 입력하세요';
    label.htmlFor = control.id = name;
    wrap.append(control);
  } else if (question.type === 'select') {
    const select = document.createElement('select');
    select.className = 'field__control';
    select.name = name;
    label.htmlFor = select.id = name;
    select.append(new Option('선택하세요', ''));
    question.options.forEach((option, i) => {
      const text = option.text || `답변 ${i + 1}`;
      select.append(new Option(text, text));
    });
    wrap.append(select);
  } else {
    question.options.forEach((option, i) => {
      const text = option.text || `답변 ${i + 1}`;
      const row = document.createElement('label');
      row.className = 'choice';

      const input = document.createElement('input');
      input.type = question.type;
      input.name = name;
      input.value = text;

      const span = document.createElement('span');
      span.textContent = text;

      row.append(input, span);
      wrap.append(row);
    });
  }

  return wrap;
}

/* ── 응답 수집 / 제출 ──────────────────────────────────────── */

function collectAnswer(question) {
  const name = `q_${question.id}`;
  if (question.type === 'checkbox') {
    return [...el.form.querySelectorAll(`[name="${name}"]:checked`)].map((i) => i.value);
  }
  if (question.type === 'radio') {
    const checked = el.form.querySelector(`[name="${name}"]:checked`);
    return checked ? checked.value : '';
  }
  const control = el.form.querySelector(`[name="${name}"]`);
  return control ? control.value.trim() : '';
}

function isEmpty(answer) {
  return Array.isArray(answer) ? answer.length === 0 : answer === '';
}

el.form.addEventListener('submit', (event) => {
  event.preventDefault();
  el.form.querySelectorAll('.field__error').forEach((node) => node.remove());

  const answers = [];
  let firstInvalid = null;

  survey.questions.forEach((question, index) => {
    const answer = collectAnswer(question);
    if (question.required && isEmpty(answer)) {
      const field = el.form.querySelector(`.field[data-question-id="${question.id}"]`);
      const error = document.createElement('p');
      error.className = 'field__error';
      error.textContent = '필수 항목입니다.';
      field.append(error);
      if (!firstInvalid) firstInvalid = field;
    }
    answers.push({ label: question.label || `질문 ${index + 1}`, answer });
  });

  if (firstInvalid) {
    firstInvalid.scrollIntoView({ behavior: 'smooth', block: 'center' });
    showToast('필수 항목을 입력해 주세요.');
    return;
  }

  el.answerList.textContent = '';
  answers.forEach(({ label, answer }) => {
    const dt = document.createElement('dt');
    dt.textContent = label;
    const dd = document.createElement('dd');
    const text = Array.isArray(answer) ? answer.join(', ') : answer;
    dd.textContent = text || '(응답 없음)';
    el.answerList.append(dt, dd);
  });

  el.form.classList.add('is-hidden');
  el.answerSheet.classList.remove('is-hidden');
  el.answerSheet.scrollIntoView({ behavior: 'smooth', block: 'start' });
});

el.answerSheet.querySelector('[data-action="answer-again"]').addEventListener('click', () => {
  el.form.reset();
  renderPreview();
});

/* ── 탭 전환 ───────────────────────────────────────────────── */

document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((t) => {
      const active = t === tab;
      t.classList.toggle('is-active', active);
      t.setAttribute('aria-selected', String(active));
    });
    const preview = tab.dataset.tab === 'preview';
    el.panelEdit.classList.toggle('is-hidden', preview);
    el.panelPreview.classList.toggle('is-hidden', !preview);
    if (preview) renderPreview();
  });
});

/* ── 상단 액션 ─────────────────────────────────────────────── */

document.querySelector('[data-action="export"]').addEventListener('click', async () => {
  const json = JSON.stringify(survey, null, 2);
  try {
    await navigator.clipboard.writeText(json);
    showToast('설문 JSON을 클립보드에 복사했습니다.');
  } catch (err) {
    showToast('복사에 실패했습니다. 콘솔에 JSON을 출력합니다.');
    console.log(json);
  }
});

document.querySelector('[data-action="reset"]').addEventListener('click', () => {
  if (!confirm('작성한 설문지를 모두 지우고 처음부터 시작할까요?')) return;
  survey = { title: '새 설문지', description: '', questions: [createQuestion()] };
  save();
  renderEditor();
  showToast('설문지를 초기화했습니다.');
});

let toastTimer;
function showToast(message) {
  el.toast.textContent = message;
  el.toast.classList.add('is-visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.toast.classList.remove('is-visible'), 2200);
}

renderEditor();
