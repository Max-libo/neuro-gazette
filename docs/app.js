/* ===== Нейрогазета — main app ===== */

const SITE_URL = 'https://нейрогазета.рф/';

const SECTION_ORDER = ['models', 'platforms', 'industry', 'content', 'art', 'hype'];

const SECTION_LABELS = {
  models: 'Модели',
  platforms: 'Платформы',
  industry: 'Индустрия',
  content: 'Нейроконтент',
  art: 'Искусство',
  hype: 'Желтуха',
};

/* Те же цвета, что --sec-* в style.css — нужны для canvas-карточки */
const SECTION_COLORS = {
  models: '#1a5276',
  platforms: '#145a32',
  industry: '#4a235a',
  content: '#8a5a00',
  art: '#7b2d26',
  hype: '#c0392b',
};

const SENTIMENT_LABELS = {
  positive: 'позитив',
  negative: 'негатив',
  neutral: 'нейтрально',
  rumor: 'слух',
};

/* ── Состояние выпуска ── */
const state = {
  issue: null,          // текущий выпуск целиком
  news: [],             // новости без changelog-карточки
  hero: null,
  changelog: null,
  hidden: new Set(),    // выключенные рубрики
  prev: undefined,      // дата предыдущего выпуска (undefined — индекс ещё не загружен, null — нет)
  next: undefined,      // дата следующего выпуска
  latest: null,         // дата самого свежего выпуска
  cols: 3,
};

/* ===== Утилиты ===== */

function formatDate(dateStr) {
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('ru-RU', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
  });
}

/* «4 нояб» — для стрелок */
function formatShortDate(dateStr) {
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }).replace('.', '');
}

/* «5 ноября 2026» — для карточки */
function formatCardDate(dateStr) {
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }).replace(/\s*г\.?$/, '');
}

function plural(n, one, few, many) {
  const n10 = n % 10, n100 = n % 100;
  if (n10 === 1 && n100 !== 11) return one;
  if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return few;
  return many;
}

function esc(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function sectionOf(item) {
  return SECTION_LABELS[item.section] ? item.section : 'other';
}

function sectionBadge(section) {
  const label = SECTION_LABELS[section] || section;
  return `<span class="section-badge section-badge--${esc(section)}">${esc(label)}</span>`;
}

/* Практическая строка для рубрик «Нейроконтент» и «Искусство»: даты выставки, срок скидки, лимиты */
function buildDetailsHtml(item) {
  if (!item.details) return '';
  return `<p class="art-details">${esc(item.details)}</p>`;
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
      'bfl.ai': 'Black Forest Labs', 'midjourney.com': 'Midjourney',
      'recraft.ai': 'Recraft', 'krea.ai': 'Krea', 'ideogram.ai': 'Ideogram',
      'leonardo.ai': 'Leonardo', 'blog.adobe.com': 'Adobe',
      'news.artnet.com': 'Artnet', 'hyperallergic.com': 'Hyperallergic',
      'theartnewspaper.com': 'The Art Newspaper', 'theartnewspaper.ru': 'The Art Newspaper Russia',
      'artguide.com': 'Artguide',
    };
    return NAMES[host] || host.split('.').slice(-2, -1)[0];
  } catch { return 'источник'; }
}

/* Ссылка на новость — её страница-превью /n/<id>.html (собирается при деплое,
   scripts/news_pages.py): в мессенджерах подтягивается заголовок новости,
   а читателя страница сразу перекидывает на новость в выпуске. */
function newsLink(id) {
  if (/^[A-Za-z0-9_-]{1,120}$/.test(id)) return `${SITE_URL}n/${id}.html`;
  const date = state.issue && state.issue.date;
  return `${SITE_URL}${date ? `?date=${date}` : ''}#${encodeURIComponent(id)}`;
}

/* ===== Мета, источники, связанные ===== */

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
  const n = related.length;
  return `<details class="related-block"><summary class="related-summary">ещё ${n} ${plural(n, 'новость', 'новости', 'новостей')} по теме</summary><ul class="related-list">${links}</ul></details>`;
}

function buildMetaHtml(item) {
  let html = '<div class="news-meta">';
  if (item.unconfirmed) html += '<span class="unconfirmed-badge">не подтверждено</span>';
  if (item.tags && item.tags.sentiment && item.tags.sentiment !== 'neutral') {
    html += `<span class="sentiment-tag sentiment--${esc(item.tags.sentiment)}">${esc(SENTIMENT_LABELS[item.tags.sentiment] || item.tags.sentiment)}</span>`;
  }
  html += buildSourcesHtml(item.sources);
  html += `<button type="button" class="share-btn" data-share="${esc(item.id)}">↗ поделиться</button>`;
  html += '</div>';
  if (item.duplicate_note) {
    html += `<div class="duplicate-note">Дополнение: ${esc(item.duplicate_note)}</div>`;
  }
  html += buildRelatedHtml(item.related);
  return html;
}

/* ===== Карточки ===== */

/* ── HERO ── полная ширина над полоской навигации, всегда раскрыт */
function buildHeroHtml(item) {
  return `
    <article class="art-hero" id="${esc(item.id)}">
      ${sectionBadge(item.section)}
      <h2 class="art-headline art-headline--hero">${esc(item.headline)}</h2>
      <p class="art-sub">${esc(item.subheadline)}</p>
      ${buildDetailsHtml(item)}
      <div class="art-body art-body--cols">${esc(item.body)}</div>
      ${buildMetaHtml(item)}
    </article>`;
}

/* ── ВАЖНОЕ (regular) ── карточка с подзаголовком, тело раскрывается по клику */
function buildRegularHtml(item) {
  return `
    <article class="col-article col-article--regular" id="${esc(item.id)}" data-expandable>
      ${sectionBadge(item.section)}
      <h3 class="art-headline art-headline--large">${esc(item.headline)}</h3>
      <p class="art-sub">${esc(item.subheadline)}</p>
      ${buildDetailsHtml(item)}
      <div class="art-body art-body--collapse">
        ${esc(item.body)}
        ${buildMetaHtml(item)}
      </div>
      <span class="expand-hint">читать далее ↓</span>
    </article>`;
}

/* ── КОРОТКО (compact и продолжения) ── строка: рубрика, заголовок, details; остальное под катом */
function buildBriefHtml(item) {
  const sec = sectionOf(item);
  const isCont = !!item.duplicate_note;
  return `
    <article class="brief brief--${esc(sec)}${isCont ? ' brief--continuation' : ''}" id="${esc(item.id)}" data-expandable>
      <span class="brief-section">${esc(SECTION_LABELS[sec] || 'Разное')}${isCont ? ' · продолжение' : ''}</span>
      <h3 class="brief-headline">${esc(item.headline)}</h3>
      ${buildDetailsHtml(item)}
      <div class="art-body art-body--collapse">
        ${item.subheadline ? `<p class="art-sub">${esc(item.subheadline)}</p>` : ''}
        ${item.body ? esc(item.body) : ''}
        ${buildMetaHtml(item)}
      </div>
    </article>`;
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

/* ===== Полоска навигации ===== */

function arrowHtml(dir, date) {
  const isPrev = dir === 'prev';
  const sign = isPrev ? '‹' : '›';
  const cls = `issue-nav-arrow issue-nav-arrow--${dir}`;
  if (!date) {
    const label = isPrev ? 'Это первый выпуск' : 'Это свежий выпуск';
    return `<span class="${cls} is-disabled" title="${label}" aria-hidden="true">${sign}</span>`;
  }
  const href = (!isPrev && date === state.latest) ? './' : `?date=${date}`;
  const label = isPrev ? `Предыдущий выпуск, ${formatShortDate(date)}` : `Следующий выпуск, ${formatShortDate(date)}`;
  const text = isPrev
    ? `${sign}<span class="issue-nav-date">${esc(formatShortDate(date))}</span>`
    : `<span class="issue-nav-date">${esc(formatShortDate(date))}</span>${sign}`;
  return `<a class="${cls}" href="${href}" aria-label="${esc(label)}">${text}</a>`;
}

function buildNavHtml() {
  const counts = {};
  state.news.forEach(n => { const s = sectionOf(n); counts[s] = (counts[s] || 0) + 1; });

  const chips = SECTION_ORDER.map(sec => {
    const n = counts[sec] || 0;
    const off = state.hidden.has(sec);
    if (!n) {
      return `<span class="sec-chip sec-chip--${sec} is-empty" title="В этом выпуске нет">${SECTION_LABELS[sec]}<span class="sec-chip-count">0</span></span>`;
    }
    return `<button type="button" class="sec-chip sec-chip--${sec}${off ? ' is-off' : ''}" data-section-toggle="${sec}" aria-pressed="${off ? 'false' : 'true'}">${SECTION_LABELS[sec]}<span class="sec-chip-count">${n}</span></button>`;
  }).join('');

  const total = state.news.length;
  const shown = state.news.filter(n => n === state.hero || !state.hidden.has(sectionOf(n))).length;
  const totalHtml = shown === total
    ? `<span class="issue-nav-total-num">${total}</span> ${plural(total, 'новость', 'новости', 'новостей')}`
    : `показано <span class="issue-nav-total-num">${shown}</span> из ${total} · <button type="button" class="issue-nav-reset" data-filter-reset>сбросить</button>`;

  const withArrows = state.prev !== undefined;
  return `
    ${withArrows ? arrowHtml('prev', state.prev) : '<span class="issue-nav-arrow is-placeholder"></span>'}
    <div class="issue-nav-sections" role="group" aria-label="Рубрики">${chips}</div>
    <div class="issue-nav-total">${totalHtml}</div>
    ${withArrows ? arrowHtml('next', state.next) : '<span class="issue-nav-arrow is-placeholder"></span>'}`;
}

function renderNav() {
  const nav = document.getElementById('issue-nav');
  if (nav) nav.innerHTML = buildNavHtml();
}

/* ===== Раскладка «Важное + Коротко» ===== */

function columnCount() {
  if (window.matchMedia('(max-width: 768px)').matches) return 1;
  if (window.matchMedia('(max-width: 900px)').matches) return 2;
  return 3;
}

/* Жадная раскладка: каждая следующая карточка — в самую короткую колонку.
   Высоту прикидываем по длине текста, который виден в свёрнутом виде. */
function distributeColumns(items, n, weightFn) {
  const cols = Array.from({ length: n }, () => ({ h: 0, items: [] }));
  items.forEach(item => {
    let min = cols[0];
    for (const c of cols) if (c.h < min.h) min = c;
    min.items.push(item);
    min.h += weightFn(item);
  });
  return cols.map(c => c.items);
}

const weightRegular = n => 90 + (n.headline || '').length * 1.2 + (n.subheadline || '').length * 0.75 + (n.details ? 40 : 0);
const weightBrief   = n => 40 + (n.headline || '').length * 0.9 + (n.details ? 30 : 0);

function bySectionThenImportance(a, b) {
  return (b.importance || 0) - (a.importance || 0)
    || SECTION_ORDER.indexOf(a.section) - SECTION_ORDER.indexOf(b.section);
}

function renderBody() {
  const body = document.getElementById('issue-body');
  if (!body) return;
  state.cols = columnCount();

  const visible = state.news.filter(n => n !== state.hero && !state.hidden.has(sectionOf(n)));

  // Выбраны одна-две рубрики — новостей мало, «Коротко» не выделяем: всё идёт в «Важное»
  const { present, active } = sectionsState();
  const narrow = active.length < present.length && active.length <= 2;

  const isBrief = n => !narrow && (n.tier === 'compact' || n.duplicate_note);
  const important = visible.filter(n => !isBrief(n)).sort(bySectionThenImportance);
  const briefs    = visible.filter(isBrief).sort(bySectionThenImportance);

  if (!important.length && !briefs.length) {
    const allOff = state.hidden.size > 0;
    body.innerHTML = allOff
      ? `<div class="filter-empty">Все рубрики с новостями выключены · <button type="button" class="issue-nav-reset" data-filter-reset>показать все</button></div>`
      : '';
    return;
  }

  let html = '';
  const grid = `grid-template-columns: repeat(${state.cols}, minmax(0, 1fr))`;

  if (important.length) {
    const cols = distributeColumns(important, state.cols, weightRegular);
    html += `<div class="col-wrap" style="${grid}">`;
    cols.forEach(items => {
      html += '<div class="col">' + items.map(buildRegularHtml).join('') + '</div>';
    });
    html += '</div>';
  }

  if (briefs.length) {
    const cols = distributeColumns(briefs, state.cols, weightBrief);
    html += `
      <section class="briefs" aria-label="Коротко">
        <h2 class="briefs-title">Коротко <span class="briefs-count">${briefs.length}</span></h2>
        <div class="briefs-cols" style="${grid}">
          ${cols.map(items => '<div class="briefs-col">' + items.map(buildBriefHtml).join('') + '</div>').join('')}
        </div>
      </section>`;
  }

  body.innerHTML = html;
}

/* ===== Рендер выпуска ===== */

function renderIssue(issue) {
  document.getElementById('issue-date').textContent = formatDate(issue.date);
  document.title = `Нейрогазета — ${issue.date}`;

  const all = [...(issue.news || [])];
  const changelog = all.find(n => n.section === 'changelog' || n.tier === 'changelog');
  const news = all.filter(n => n !== changelog);

  state.issue = issue;
  state.news = news;
  state.changelog = changelog || null;
  state.hero = news.find(n => n.tier === 'hero')
    || [...news].sort((a, b) => (b.importance || 0) - (a.importance || 0))[0]
    || null;

  if (news.length === 0 && !changelog) {
    document.getElementById('newspaper').innerHTML =
      '<div class="loading-state">Нет новостей в этом выпуске.</div>';
    return;
  }

  let html = '';
  if (state.hero) {
    html += '<div class="heroes-zone">' + buildHeroHtml(state.hero) + '</div>';
  }
  html += '<nav class="issue-nav" id="issue-nav" aria-label="Навигация по выпуску"></nav>';
  html += '<div id="issue-body"></div>';
  if (changelog) html += buildChangelogHtml(changelog);

  document.getElementById('newspaper').innerHTML = html;
  renderNav();
  renderBody();

  // Версия в футере из changelog-карточки
  if (changelog) {
    const version = changelog.version || ((changelog.headline || '').match(/v[\d.]+/) || [])[0];
    if (version) {
      const el = document.getElementById('footer-title');
      if (el) el.textContent = `Нейрогазета ${version}`;
    }
  }
}

/* Соседние выпуски из data/index.json */
function applyNeighbours(index, date) {
  const dates = ((index && index.issues) || []).map(i => i.date).filter(Boolean).sort();
  if (!dates.length) return;
  state.latest = dates[dates.length - 1] > date ? dates[dates.length - 1] : date;
  state.prev = [...dates].reverse().find(d => d < date) || null;
  state.next = dates.find(d => d > date) || null;
}

/* ===== Взаимодействие ===== */

function toggleExpand(el) {
  el.classList.toggle('expanded');
}

/* Рубрики, в которых в этом выпуске есть новости, и какие из них сейчас включены */
function sectionsState() {
  const present = SECTION_ORDER.filter(s => state.news.some(n => sectionOf(n) === s));
  const active = present.filter(s => !state.hidden.has(s));
  return { present, active };
}

/* Логика фильтра:
   - включены все → клик оставляет только эту рубрику;
   - выключают единственную включённую → снова включаются все;
   - в остальных случаях клик просто включает/выключает рубрику.
   «Все» — это рубрики, в которых в этом выпуске есть новости. */
function toggleSection(sec) {
  const { present, active } = sectionsState();

  if (active.length === present.length) {
    present.forEach(s => { if (s !== sec) state.hidden.add(s); });
  } else if (active.length === 1 && active[0] === sec) {
    state.hidden.clear();
  } else if (state.hidden.has(sec)) {
    state.hidden.delete(sec);
  } else {
    state.hidden.add(sec);
  }
  renderNav();
  renderBody();
}

function resetFilter() {
  state.hidden.clear();
  renderNav();
  renderBody();
}

document.addEventListener('click', e => {
  const t = e.target;

  const toggle = t.closest('[data-section-toggle]');
  if (toggle) { toggleSection(toggle.dataset.sectionToggle); return; }

  if (t.closest('[data-filter-reset]')) { resetFilter(); return; }

  const share = t.closest('[data-share]');
  if (share) { e.preventDefault(); openShare(share.dataset.share); return; }

  if (t.closest('[data-share-close]')) { closeShare(); return; }
  if (t.closest('[data-share-save]')) { saveShareImage(); return; }
  if (t.closest('[data-share-native]')) { nativeShare(); return; }

  // Раскрытие карточки — по клику мимо ссылок, кнопок и «ещё по теме», и не при выделении текста
  const card = t.closest('[data-expandable]');
  if (card && !t.closest('a, button, details, summary')) {
    if (String(window.getSelection && window.getSelection()).length) return;
    toggleExpand(card);
  }
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && document.querySelector('.share-modal')) closeShare();
});

/* Перераскладка колонок при смене ширины */
['(max-width: 768px)', '(max-width: 900px)'].forEach(q => {
  const mq = window.matchMedia(q);
  const onChange = () => { if (state.issue && columnCount() !== state.cols) renderBody(); };
  if (mq.addEventListener) mq.addEventListener('change', onChange);
  else if (mq.addListener) mq.addListener(onChange);
});

/* ===== Глубокая ссылка #id ===== */

function handleDeepLink() {
  const id = decodeURIComponent((location.hash || '').slice(1));
  if (!id) return;
  let el = document.getElementById(id);
  if (!el) {
    // Новость может быть скрыта фильтром — включаем её рубрику
    const item = state.news.find(n => n.id === id);
    if (!item) return;
    state.hidden.delete(sectionOf(item));
    renderNav();
    renderBody();
    el = document.getElementById(id);
    if (!el) return;
  }
  if (el.hasAttribute('data-expandable')) el.classList.add('expanded');
  el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  el.classList.remove('is-target');
  void el.offsetWidth;
  el.classList.add('is-target');
  setTimeout(() => el.classList.remove('is-target'), 1800);
}

window.addEventListener('hashchange', handleDeepLink);

/* ===== Тост ===== */

let toastTimer;
function showToast(text, ms = 1000) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = text;
  el.classList.add('is-visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('is-visible'), ms);
}

async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* пробуем запасной способ */ }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch { return false; }
}

/* ===== Шеринг: попап 9:16 ===== */

const share = { item: null, blob: null, url: null, objectUrl: null };

async function openShare(id) {
  const item = state.news.find(n => n.id === id);
  if (!item) return;

  const link = newsLink(id);
  share.item = item;
  share.url = link;
  share.blob = null;

  copyText(link).then(ok =>
    showToast(ok ? 'Ссылка на новость скопирована' : 'Не удалось скопировать ссылку', ok ? 1000 : 1800)
  );

  closeShare(true);
  const modal = document.createElement('div');
  modal.className = 'share-modal';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-label', 'Поделиться новостью');
  modal.innerHTML = `
    <div class="share-modal-backdrop" data-share-close></div>
    <div class="share-modal-panel">
      <button type="button" class="share-modal-close" data-share-close aria-label="Закрыть">×</button>
      <div class="share-card-frame">
        <div class="share-card-loading">Готовлю карточку…</div>
      </div>
      <div class="share-actions">
        <button type="button" class="btn" data-share-save disabled>Сохранить</button>
        <button type="button" class="btn btn-primary" data-share-native disabled>Поделиться</button>
      </div>
      <div class="share-fallback" hidden>
        <span>Отправить ссылку:</span>
        <a href="https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(item.headline || '')}" target="_blank" rel="noopener">Telegram</a>
        <a href="https://vk.com/share.php?url=${encodeURIComponent(link)}&title=${encodeURIComponent(item.headline || '')}" target="_blank" rel="noopener">ВКонтакте</a>
      </div>
    </div>`;
  document.body.appendChild(modal);
  document.body.classList.add('has-modal');
  modal.querySelector('.share-modal-close').focus({ preventScroll: true });

  try {
    const canvas = await drawShareCard(item, state.issue.date);
    const blob = await new Promise(res => canvas.toBlob(res, 'image/png'));
    if (share.item !== item || !document.body.contains(modal)) return;
    share.blob = blob;
    if (share.objectUrl) URL.revokeObjectURL(share.objectUrl);
    share.objectUrl = URL.createObjectURL(blob);
    modal.querySelector('.share-card-frame').innerHTML =
      `<img class="share-card-img" src="${share.objectUrl}" alt="${esc(item.headline)}" />`;
    modal.querySelectorAll('[data-share-save], [data-share-native]').forEach(b => { b.disabled = false; });
  } catch (err) {
    modal.querySelector('.share-card-frame').innerHTML =
      `<div class="share-card-loading">Не получилось нарисовать карточку.<br><small>${esc(err.message)}</small></div>`;
    modal.querySelector('[data-share-native]').disabled = false;
  }
}

function closeShare(silent) {
  const modal = document.querySelector('.share-modal');
  if (modal) modal.remove();
  document.body.classList.remove('has-modal');
  if (!silent && share.objectUrl) {
    URL.revokeObjectURL(share.objectUrl);
    share.objectUrl = null;
  }
}

function shareFileName() {
  const date = state.issue ? state.issue.date : 'issue';
  return `neurogazeta-${date}-${share.item ? share.item.id : 'news'}.png`;
}

function saveShareImage() {
  if (!share.objectUrl) return;
  const a = document.createElement('a');
  a.href = share.objectUrl;
  a.download = shareFileName();
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function nativeShare() {
  const item = share.item;
  if (!item) return;
  const title = item.headline || 'Нейрогазета';
  try {
    if (share.blob && navigator.canShare) {
      const file = new File([share.blob], shareFileName(), { type: 'image/png' });
      if (navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], title, text: `${title}\n${share.url}` });
        return;
      }
    }
    if (navigator.share) {
      await navigator.share({ title, url: share.url });
      return;
    }
  } catch (err) {
    if (err && err.name === 'AbortError') return; // пользователь закрыл меню
  }
  const fb = document.querySelector('.share-fallback');
  if (fb) fb.hidden = false;
}

/* ===== Canvas-карточка 1080×1920 ===== */

const CARD = {
  W: 1080, H: 1920,
  paper: '#f5f0e8', ink: '#1a1a18', inkMid: '#3d3d38', inkLight: '#6b6860', rule: '#c8c2b0', accent: '#c0392b',
  serif: '"Playfair Display", Georgia, serif',
  serifText: '"IBM Plex Serif", Georgia, serif',
  sans: '"IBM Plex Sans", system-ui, sans-serif',
  mono: '"IBM Plex Mono", "Courier New", monospace',
};

async function ensureCardFonts() {
  if (!document.fonts || !document.fonts.load) return;
  const sample = 'Нейрогазета AaЁё 0123';
  const loads = [
    `700 80px ${CARD.serif}`,
    `400 40px ${CARD.serifText}`,
    `400 36px ${CARD.sans}`,
    `500 30px ${CARD.mono}`,
  ].map(f => document.fonts.load(f, sample).catch(() => null));
  await Promise.race([Promise.all(loads), new Promise(r => setTimeout(r, 2500))]);
}

/* Перенос строк по словам; слишком длинное слово режется по буквам */
function wrapLines(ctx, text, maxWidth) {
  const words = String(text || '').replace(/\s+/g, ' ').trim().split(' ').filter(Boolean);
  const lines = [];
  let line = '';
  for (let word of words) {
    const candidate = line ? line + ' ' + word : word;
    if (ctx.measureText(candidate).width <= maxWidth) { line = candidate; continue; }
    if (line) lines.push(line);
    while (ctx.measureText(word).width > maxWidth) {
      let cut = word.length - 1;
      while (cut > 1 && ctx.measureText(word.slice(0, cut)).width > maxWidth) cut--;
      lines.push(word.slice(0, cut));
      word = word.slice(cut);
    }
    line = word;
  }
  if (line) lines.push(line);
  return lines;
}

/* Последняя строка с «…» в пределах ширины */
function ellipsize(ctx, line, maxWidth) {
  let words = line.split(' ');
  let out = words.join(' ') + '…';
  while (words.length > 1 && ctx.measureText(out).width > maxWidth) {
    words.pop();
    out = words.join(' ').replace(/[,;:—–-]$/, '') + '…';
  }
  return out;
}

function drawSpaced(ctx, text, x, y, spacing) {
  let cx = x;
  for (const ch of text) {
    ctx.fillText(ch, cx, y);
    cx += ctx.measureText(ch).width + spacing;
  }
  return cx - spacing - x;
}

function spacedWidth(ctx, text, spacing) {
  let w = 0;
  for (const ch of text) w += ctx.measureText(ch).width + spacing;
  return w - spacing;
}

/* Раскладка текстовых блоков при масштабе s. Возвращает блоки и суммарную высоту. */
function layoutCardText(ctx, item, width, s) {
  const blocks = [];
  const add = (key, font, lh, color, text, gapBefore) => {
    if (!text) return;
    ctx.font = font;
    blocks.push({ key, font, lh, color, gapBefore, lines: wrapLines(ctx, text, width - (key === 'details' ? 34 : 0)) });
  };
  const hs = Math.round(84 * s), ss = Math.round(42 * s), bs = Math.round(38 * s), ds = Math.round(30 * s);
  add('headline', `700 ${hs}px ${CARD.serif}`, Math.round(hs * 1.14), CARD.ink, item.headline, 0);
  add('sub', `400 ${ss}px ${CARD.serifText}`, Math.round(ss * 1.42), CARD.inkMid, item.subheadline, Math.round(36 * s));
  add('details', `500 ${ds}px ${CARD.mono}`, Math.round(ds * 1.5), CARD.inkMid, item.details, Math.round(32 * s));
  if (item.body) blocks.push({ key: 'rule', gapBefore: Math.round(44 * s), height: 2 });
  add('body', `400 ${bs}px ${CARD.sans}`, Math.round(bs * 1.55), CARD.inkMid, item.body, Math.round(44 * s));

  let total = 0;
  for (const b of blocks) {
    total += b.gapBefore;
    total += b.key === 'rule' ? b.height : b.lines.length * b.lh;
  }
  return { blocks, total };
}

async function drawShareCard(item, date) {
  await ensureCardFonts();
  const { W, H } = CARD;
  const canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');
  ctx.textBaseline = 'alphabetic';

  // Фон и рамка
  ctx.fillStyle = CARD.paper;
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = CARD.rule;
  ctx.lineWidth = 2;
  ctx.strokeRect(36, 36, W - 72, H - 72);

  // Левая полоса: «НЕЙРОГАЗЕТА.РФ» вертикально + толстая линейка
  const stripX = 150;
  ctx.fillStyle = CARD.ink;
  ctx.fillRect(stripX, 36, 8, H - 72);
  ctx.save();
  ctx.font = `500 34px ${CARD.mono}`;
  ctx.fillStyle = CARD.ink;
  const brand = 'НЕЙРОГАЗЕТА.РФ';
  const spacing = 14;
  const bw = spacedWidth(ctx, brand, spacing);
  ctx.translate(108, H - 120);
  ctx.rotate(-Math.PI / 2);
  drawSpaced(ctx, brand, 0, 0, spacing);
  ctx.restore();
  // Мелкая дата внизу полосы — тоже вертикально, над брендом
  ctx.save();
  ctx.font = `400 24px ${CARD.mono}`;
  ctx.fillStyle = CARD.inkLight;
  ctx.translate(108, H - 120 - bw - 60);
  ctx.rotate(-Math.PI / 2);
  drawSpaced(ctx, 'ЕЖЕДНЕВНОЕ ИЗДАНИЕ ОБ ИИ', 0, 0, 6);
  ctx.restore();

  const x0 = 210, x1 = W - 84, width = x1 - x0;

  // Верх: плашка рубрики и дата
  const sec = sectionOf(item);
  const secColor = SECTION_COLORS[sec] || CARD.ink;
  const secLabel = (SECTION_LABELS[sec] || 'Новость').toUpperCase();
  ctx.font = `500 28px ${CARD.mono}`;
  const lw = spacedWidth(ctx, secLabel, 4);
  ctx.strokeStyle = secColor;
  ctx.lineWidth = 3;
  ctx.strokeRect(x0, 104, lw + 40, 56);
  ctx.fillStyle = secColor;
  drawSpaced(ctx, secLabel, x0 + 20, 142, 4);

  ctx.font = `400 28px ${CARD.mono}`;
  ctx.fillStyle = CARD.inkLight;
  const dateText = formatCardDate(date).toUpperCase();
  const dw = spacedWidth(ctx, dateText, 3);
  drawSpaced(ctx, dateText, x1 - dw, 142, 3);

  ctx.fillStyle = CARD.ink;
  ctx.fillRect(x0, 196, width, 6);

  // Низ: источник и пометки
  const footerTop = H - 196;
  ctx.fillStyle = CARD.rule;
  ctx.fillRect(x0, footerTop, width, 2);
  const src = item.sources && item.sources[0] ? domainName(item.sources[0].url) : '';
  ctx.font = `400 28px ${CARD.mono}`;
  ctx.fillStyle = CARD.inkLight;
  if (src) ctx.fillText(`Источник: ${src}`, x0, footerTop + 64);
  if (item.unconfirmed) {
    ctx.font = `500 26px ${CARD.mono}`;
    const t = 'НЕ ПОДТВЕРЖДЕНО';
    const tw = spacedWidth(ctx, t, 3);
    ctx.strokeStyle = CARD.accent;
    ctx.lineWidth = 2;
    ctx.strokeRect(x1 - tw - 28, footerTop + 30, tw + 28, 48);
    ctx.fillStyle = CARD.accent;
    drawSpaced(ctx, t, x1 - tw - 14, footerTop + 63, 3);
  }

  // Текст: подбираем масштаб — короткие новости крупнее, длинные мельче, пока не влезет
  const top = 270, bottom = footerTop - 56, avail = bottom - top;
  const MIN_S = 30 / 38, MAX_S = 1.25;
  let s = MAX_S, lay = layoutCardText(ctx, item, width, s);
  while (lay.total > avail && s > MIN_S) {
    s = Math.max(MIN_S, s * 0.95);
    lay = layoutCardText(ctx, item, width, s);
  }

  // Всё ещё не влезло — режем основной текст по целым строкам
  canvas.dataset.truncated = lay.total > avail ? '1' : '';
  if (lay.total > avail) {
    const body = lay.blocks.find(b => b.key === 'body');
    if (body) {
      const overflow = lay.total - avail;
      const drop = Math.ceil(overflow / body.lh);
      const keep = Math.max(1, body.lines.length - drop);
      body.lines = body.lines.slice(0, keep);
      ctx.font = body.font;
      body.lines[keep - 1] = ellipsize(ctx, body.lines[keep - 1], width);
    }
  }

  let y = top;
  for (const b of lay.blocks) {
    y += b.gapBefore;
    if (b.key === 'rule') {
      ctx.fillStyle = CARD.rule;
      ctx.fillRect(x0, y, 140, b.height);
      y += b.height;
      continue;
    }
    ctx.font = b.font;
    ctx.fillStyle = b.color;
    const startY = y;
    const tx = b.key === 'details' ? x0 + 34 : x0;
    b.lines.forEach((line, i) => {
      // базовая линия ≈ 78% межстрочного
      ctx.fillText(line, tx, y + b.lh * 0.78 + (i ? 0 : 0));
      y += b.lh;
    });
    if (b.key === 'details') {
      ctx.fillStyle = secColor;
      ctx.fillRect(x0, startY + 4, 6, y - startY - 8);
    }
  }

  return canvas;
}

/* ===== Загрузка ===== */

async function loadIssue(dateStr) {
  const url = dateStr ? `data/${dateStr}.json` : 'data/latest.json';
  const indexPromise = fetch('data/index.json')
    .then(r => (r.ok ? r.json() : null))
    .catch(() => null);
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const issue = await res.json();
    renderIssue(issue);
    handleDeepLink();
    const index = await indexPromise;
    if (index) {
      applyNeighbours(index, issue.date);
      renderNav();
    }
  } catch (err) {
    document.getElementById('newspaper').innerHTML =
      `<div class="error-state">Не удалось загрузить выпуск.<br><small>${esc(err.message)}</small></div>`;
  }
}

const params = new URLSearchParams(location.search);
loadIssue(params.get('date'));
