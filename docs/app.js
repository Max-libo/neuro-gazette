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

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function buildSourcesHtml(sources) {
  if (!sources || sources.length === 0) return '';
  const links = sources.map(s =>
    `<a class="source-link source-type-${escapeHtml(s.type)}" href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.title)}</a>`
  ).join('');
  return `<div class="sources-list">${links}</div>`;
}

function buildTagsHtml(tags) {
  if (!tags) return '';
  const parts = [];
  if (tags.entities) tags.entities.forEach(e => parts.push(`<span class="tag">${escapeHtml(e)}</span>`));
  if (tags.sentiment) parts.push(`<span class="tag">${escapeHtml(SENTIMENT_LABELS[tags.sentiment] || tags.sentiment)}</span>`);
  if (tags.event) parts.push(`<span class="tag">${escapeHtml(EVENT_LABELS[tags.event] || tags.event)}</span>`);
  return parts.length ? `<div class="tag-list">${parts.join('')}</div>` : '';
}

function buildMetaHtml(item) {
  let html = '<div class="news-meta">';
  html += `<span class="importance-badge">важность: ${item.importance}/10</span>`;
  if (item.unconfirmed) html += '<span class="unconfirmed-badge">не подтверждено</span>';
  html += buildTagsHtml(item.tags);
  html += buildSourcesHtml(item.sources);
  html += '</div>';
  if (item.duplicate_note) {
    html += `<div class="duplicate-note">Дополнение: ${escapeHtml(item.duplicate_note)}</div>`;
  }
  return html;
}

function buildHeroHtml(item) {
  return `
    <article class="news-hero" id="${escapeHtml(item.id)}">
      <h2 class="news-headline">${escapeHtml(item.headline)}</h2>
      <p class="news-subheadline">${escapeHtml(item.subheadline)}</p>
      <div class="news-body">${escapeHtml(item.body)}</div>
      ${buildMetaHtml(item)}
    </article>`;
}

function buildFeaturedHtml(item) {
  return `
    <article class="news-featured" id="${escapeHtml(item.id)}" onclick="toggleExpand(this)">
      <h3 class="news-headline">${escapeHtml(item.headline)}</h3>
      <p class="news-subheadline">${escapeHtml(item.subheadline)}</p>
      <span class="expand-hint">↓ читать далее</span>
      <div class="news-body-collapse">
        <p>${escapeHtml(item.body)}</p>
        ${buildMetaHtml(item)}
      </div>
    </article>`;
}

function buildBriefHtml(item) {
  return `
    <article class="news-brief" id="${escapeHtml(item.id)}" onclick="toggleExpand(this)">
      <span class="brief-importance">${item.importance}</span>
      <div class="brief-content">
        <h4 class="news-headline">${escapeHtml(item.headline)}</h4>
        <p class="news-subheadline">${escapeHtml(item.subheadline)}</p>
        <span class="expand-hint">↓ читать далее</span>
        <div class="news-body-collapse">
          <p>${escapeHtml(item.body)}</p>
          ${buildMetaHtml(item)}
        </div>
      </div>
    </article>`;
}

function toggleExpand(el) {
  el.classList.toggle('expanded');
}

function renderIssue(issue) {
  const el = document.getElementById('newspaper');

  // Date
  document.getElementById('issue-date').textContent = formatDate(issue.date);
  document.title = `Нейрогазета — ${issue.date}`;

  const sections = ['models', 'platforms', 'industry', 'hype'];
  let html = '';

  sections.forEach(sec => {
    const items = (issue.news || [])
      .filter(n => n.section === sec)
      .sort((a, b) => b.importance - a.importance);

    if (items.length === 0) return;

    html += `
      <section class="section-group" id="${sec}">
        <div class="section-header">
          <span class="section-label">${SECTION_LABELS[sec] || sec}</span>
          <div class="section-rule"></div>
        </div>`;

    const heroes = items.filter(n => n.importance >= 9);
    const featured = items.filter(n => n.importance >= 6 && n.importance < 9);
    const briefs = items.filter(n => n.importance < 6);

    heroes.forEach(item => { html += buildHeroHtml(item); });

    if (featured.length) {
      html += '<div class="news-grid">';
      featured.forEach(item => { html += buildFeaturedHtml(item); });
      html += '</div>';
    }

    if (briefs.length) {
      html += '<div class="news-briefs">';
      briefs.forEach(item => { html += buildBriefHtml(item); });
      html += '</div>';
    }

    html += '</section>';
  });

  el.innerHTML = html || '<div class="loading-state">Нет новостей в этом выпуске.</div>';
}

async function loadIssue(dateStr) {
  const url = dateStr
    ? `data/${dateStr}.json`
    : 'data/latest.json';

  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const issue = await res.json();
    renderIssue(issue);
  } catch (err) {
    document.getElementById('newspaper').innerHTML =
      `<div class="error-state">Не удалось загрузить выпуск.<br><small>${err.message}</small></div>`;
  }
}

// Read ?date= param
const params = new URLSearchParams(location.search);
loadIssue(params.get('date'));
