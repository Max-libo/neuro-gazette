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
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function sectionBadge(section) {
  const label = SECTION_LABELS[section] || section;
  return `<span class="section-badge section-badge--${esc(section)}">${esc(label)}</span>`;
}

function domainName(url) {
  try {
    let host = new URL(url).hostname.replace(/^www\./, '');
    // Известные домены → красивые названия
    const NAMES = {
      'openai.com': 'OpenAI', 'anthropic.com': 'Anthropic',
      'deepmind.google': 'DeepMind', 'ai.meta.com': 'Meta AI',
      'blogs.nvidia.com': 'NVIDIA', 'techcrunch.com': 'TechCrunch',
      'theverge.com': 'The Verge', 'theguardian.com': 'The Guardian',
      'wired.com': 'Wired', 'the-decoder.com': 'The Decoder',
      'habr.com': 'Habr', 'vc.ru': 'VC.ru',
      'venturebeat.com': 'VentureBeat', 'zdnet.com': 'ZDNet',
      'aibusiness.com': 'AI Business', 'infoq.com': 'InfoQ',
      'marktechpost.com': 'MarkTechPost', 'lumalabs.ai': 'Luma AI',
      'stability.ai': 'Stability AI', 'mistral.ai': 'Mistral',
      'blog.google': 'Google', 'blogs.microsoft.com': 'Microsoft',
      'spectrum.ieee.org': 'IEEE Spectrum', 'technologyreview.com': 'MIT Tech Review',
    };
    return NAMES[host] || host.split('.').slice(-2, -1)[0];
  } catch { return 'источник'; }
}

function buildSourcesHtml(sources) {
  if (!sources || sources.length === 0) return '';
  const badges = sources.map(s =>
    `<a class="source-badge source-type-${esc(s.type)}" href="${esc(s.url)}" target="_blank" rel="noopener" title="${esc(s.title)}">${esc(domainName(s.url))}</a>`
  ).join('');
  return `<div class="sources-list">${badges}</div>`;
}

function buildRelatedHtml(related) {
  if (!related || related.length === 0) return '';
  const links = related.map(r =>
    `<li><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a></li>`
  ).join('');
  return `<details class="related-block" onclick="event.stopPropagation()"><summary class="related-summary">ещё ${related.length} ${related.length === 1 ? 'новость' : related.length < 5 ? 'новости' : 'новостей'} по теме</summary><ul class="related-list">${links}</ul></details>`;
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
  html += buildRelatedHtml(item.related);
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

/* ── REGULAR (importance ≥ 5) ── в колонке, тело раскрывается по клику */
function buildRegularHtml(item) {
  return `
    <article class="col-article col-article--regular" id="${esc(item.id)}" onclick="toggleExpand(this)">
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

/* ── COMPACT (importance < 5) ── тот же кегль, подзаголовок и тело под катом */
function buildCompactHtml(item) {
  return `
    <article class="col-article col-article--compact" id="${esc(item.id)}" onclick="toggleExpand(this)">
      ${sectionBadge(item.section)}
      <h3 class="art-headline art-headline--large">${esc(item.headline)}</h3>
      <div class="art-body art-body--collapse">
        <p class="art-sub">${esc(item.subheadline)}</p>
        ${esc(item.body)}
        ${buildMetaHtml(item)}
      </div>
      <span class="expand-hint">подробнее ↓</span>
    </article>`;
}

function toggleExpand(el) {
  el.classList.toggle('expanded');
}

/* ── CHANGELOG (закреплённая карточка внизу, без плашки) ── */
function buildChangelogHtml(item) {
  return `
    <div class="changelog-zone">
      <article class="art-changelog">
        <div class="changelog-content">
          <span class="changelog-headline">${esc(item.headline)}</span>
          ${item.body ? `<span class="changelog-body">${esc(item.body)}</span>` : ''}
        </div>
      </article>
    </div>`;
}

function renderIssue(issue) {
  document.getElementById('issue-date').textContent = formatDate(issue.date);
  document.title = `Нейрогазета — ${issue.date}`;

  // Сортируем: hero → regular → compact → changelog
  const TIER_ORDER = { hero: 0, regular: 1, compact: 2, changelog: 99 };
  const all = [...(issue.news || [])].sort((a, b) =>
    (TIER_ORDER[a.tier] ?? 1) - (TIER_ORDER[b.tier] ?? 1) || b.importance - a.importance
  );

  // Выделяем changelog-карточку
  const changelog = all.find(n => n.section === 'changelog' || n.tier === 'changelog');
  const newsItems  = all.filter(n => n !== changelog);

  if (newsItems.length === 0 && !changelog) {
    document.getElementById('newspaper').innerHTML =
      '<div class="loading-state">Нет новостей в этом выпуске.</div>';
    return;
  }

  // Ровно одна hero-новость
  const hero   = newsItems.find(n => n.tier === 'hero') || newsItems[0];
  const inCols = newsItems.filter(n => n !== hero);

  let html = '';

  if (hero) {
    html += '<div class="heroes-zone">';
    html += buildHeroHtml(hero);
    html += '</div>';
  }

  // Все остальные — в трёх фиксированных колонках
  if (inCols.length) {
    const NUM_COLS = 3;
    const cols = Array.from({ length: NUM_COLS }, () => []);
    inCols.forEach((item, i) => cols[i % NUM_COLS].push(item));

    html += '<div class="col-wrap">';
    cols.forEach(colItems => {
      html += '<div class="col">';
      colItems.forEach(item => {
        if (item.tier === 'compact') html += buildCompactHtml(item);
        else                         html += buildRegularHtml(item);
      });
      html += '</div>';
    });
    html += '</div>';
  }

  // Changelog-карточка в самом конце
  if (changelog) {
    html += buildChangelogHtml(changelog);
    // Обновляем версию в футере
    const version = changelog.version || (changelog.headline.match(/v[\d.]+/) || [])[0];
    if (version) {
      const el = document.getElementById('footer-title');
      if (el) el.textContent = `Нейрогазета ${version}`;
    }
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
      `<div class="error-state">Не удалось загрузить выпуск.<br><small>${esc(err.message)}</small></div>`;
  }
}

const params = new URLSearchParams(location.search);
loadIssue(params.get('date'));
