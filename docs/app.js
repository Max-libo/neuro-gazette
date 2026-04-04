/* ===== Нейрогазета — main app ===== */

const SECTION_LABELS = {
  models: 'Модели',
  platforms: 'Платформы',
  industry: 'Индустрия',
  hype: 'Желтуха',
};

const SENTIMENT_LABELS = {
  positive: 'позитив',
  negative: 'негатив',
  neutral: 'нейтрально',
  rumor: 'слух',
};

const EVENT_LABELS = {
  release: 'релиз',
  update: 'обновление',
  shutdown: 'закрытие',
  investment: 'инвестиции',
  regulation: 'регуляция',
  leak: 'утечка',
};

function formatDate(dateStr) {
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('ru-RU', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
  });
}

function esc(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function sectionBadge(section) {
  const label = SECTION_LABELS[section] || section;
  return `<span class="section-badge section-badge--${esc(section)}">${esc(label)}</span>`;
}

function buildSourcesHtml(sources) {
  if (!sources || sources.length === 0) return '';
  const links = sources.map(s =>
    `<a class="source-link source-type-${esc(s.type)}" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>`
  ).join('');
  return `<div class="sources-list">${links}</div>`;
}

function buildMetaHtml(item) {
  let html = '<div class="news-meta">';
  if (item.unconfirmed) html += '<span class="unconfirmed-badge">не подтверждено</span>';
  if (item.tags && item.tags.sentiment && item.tags.sentiment !== 'neutral') {
    html += `<span class="sentiment-tag sentiment--${esc(item.tags.sentiment)}">${esc(SENTIMENT_LABELS[item.tags.sentiment] || item.tags.sentiment)}</span>`;
  }
  html += buildSourcesHtml(item.sources);
  html += '</div>';
  if (item.duplicate_note) {
    html += `<div class="duplicate-note">Дополнение: ${esc(item.duplicate_note)}</div>`;
  }
  return html;
}

/* ── HERO (importance 9–10) ── полная ширина над колонками */
function buildHeroHtml(item) {
  return `
    <article class="art-hero" id="${esc(item.id)}">
      ${sectionBadge(item.section)}
      <h2 class="art-headline art-headline--hero">${esc(item.headline)}</h2>
      <p class="art-sub">${esc(item.subheadline)}</p>
      <div class="art-body art-body--cols">${esc(item.body)}</div>
      ${buildMetaHtml(item)}
    </article>`;
}

/* ── LARGE (importance 7–8) ── в колонке, крупный заголовок + тело */
function buildLargeHtml(item) {
  return `
    <article class="col-article col-article--large" id="${esc(item.id)}" onclick="toggleExpand(this)">
      ${sectionBadge(item.section)}
      <h3 class="art-headline art-headline--large">${esc(item.headline)}</h3>
      <p class="art-sub">${esc(item.subheadline)}</p>
      <div class="art-body art-body--collapse">
        ${esc(item.body)}
        ${buildMetaHtml(item)}
      </div>
      <span class="expand-hint">читать далее ↓</span>
    </article>`;
}

/* ── MEDIUM (importance 5–6) ── в колонке, средний заголовок */
function buildMediumHtml(item) {
  return `
    <article class="col-article col-article--medium" id="${esc(item.id)}" onclick="toggleExpand(this)">
      ${sectionBadge(item.section)}
      <h4 class="art-headline art-headline--medium">${esc(item.headline)}</h4>
      <p class="art-sub art-sub--small">${esc(item.subheadline)}</p>
      <div class="art-body art-body--collapse">
        ${esc(item.body)}
        ${buildMetaHtml(item)}
      </div>
      <span class="expand-hint">читать далее ↓</span>
    </article>`;
}

/* ── BRIEF (importance 1–4) ── размер как medium, без раскрытия */
function buildBriefHtml(item) {
  return `
    <article class="col-article col-article--brief" id="${esc(item.id)}">
      ${sectionBadge(item.section)}
      <h5 class="art-headline art-headline--medium">${esc(item.headline)}</h5>
      <p class="art-sub art-sub--small">${esc(item.subheadline)}</p>
    </article>`;
}

function toggleExpand(el) {
  el.classList.toggle('expanded');
}

function renderIssue(issue) {
  document.getElementById('issue-date').textContent = formatDate(issue.date);
  document.title = `Нейрогазета — ${issue.date}`;

  // Сортируем всё глобально по важности — рубрики перемешаны
  const all = [...(issue.news || [])].sort((a, b) => b.importance - a.importance);

  if (all.length === 0) {
    document.getElementById('newspaper').innerHTML =
      '<div class="loading-state">Нет новостей в этом выпуске.</div>';
    return;
  }

  const heroes  = all.filter(n => n.importance >= 9);
  const inCols  = all.filter(n => n.importance < 9);

  let html = '';

  // Герои — на всю ширину
  if (heroes.length) {
    html += '<div class="heroes-zone">';
    heroes.forEach(item => { html += buildHeroHtml(item); });
    html += '</div>';
  }

  // Все остальные — в трёх фиксированных колонках.
  // Статьи распределяются round-robin при рендере и больше не мигрируют.
  if (inCols.length) {
    const NUM_COLS = 3;
    const cols = Array.from({ length: NUM_COLS }, () => []);
    inCols.forEach((item, i) => cols[i % NUM_COLS].push(item));

    html += '<div class="col-wrap">';
    cols.forEach(colItems => {
      html += '<div class="col">';
      colItems.forEach(item => {
        if (item.importance >= 7)      html += buildLargeHtml(item);
        else if (item.importance >= 5) html += buildMediumHtml(item);
        else                           html += buildBriefHtml(item);
      });
      html += '</div>';
    });
    html += '</div>';
  }

  document.getElementById('newspaper').innerHTML = html;
}

async function loadIssue(dateStr) {
  const url = dateStr ? `data/${dateStr}.json` : 'data/latest.json';
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    renderIssue(await res.json());
  } catch (err) {
    document.getElementById('newspaper').innerHTML =
      `<div class="error-state">Не удалось загрузить выпуск.<br><small>${err.message}</small></div>`;
  }
}

const params = new URLSearchParams(location.search);
loadIssue(params.get('date'));
