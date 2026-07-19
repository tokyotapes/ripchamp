function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// esc() alone doesn't escape quotes (not special in HTML text content) --
// needed wherever a dynamic value goes inside a "..."-delimited attribute
// (e.g. the upload URL in an href), so it can't break out of the attribute.
function escAttr(s) {
  return esc(s).replace(/"/g, '&quot;');
}

function keySet(arr) { return new Set(arr.map(String)); }

function truncateName(name, max = 40) {
  if (name.length <= max) return name;
  const keep = max - 3;
  const front = Math.ceil(keep / 2);
  const back = Math.floor(keep / 2);
  return name.slice(0, front) + '...' + name.slice(-back);
}

function colorizeStage(stage) {
  return esc(stage)
    .replace(/YouTube/g, '<span style="color:#FF0000">YouTube</span>')
    .replace(/Discord/g, '<span style="color:#5865F2">Discord</span>');
}

let seenKeys = { pending: new Set(), active: new Set(), history: new Set() };
let firstRefresh = true;
let dotsTimeline = null;
let lastActiveSignature = null;

async function refresh() {
  let data;
  try {
    const res = await fetch('/status.json');
    data = await res.json();
  } catch (e) {
    return;
  }


  const pendingKeys = keySet(data.pending.map(p => p.id));
  const activeKeys = keySet(data.active.map(a => a.id));
  const historyKeys = keySet(data.history.map(h => h.finished));

  const isNew = (key, prevKeys) => !firstRefresh && !prevKeys.has(String(key));

  const watcherReady = !!(data.watch_enabled && data.watcher && data.watcher.running);
  const watcherHint = watcherReady
    ? 'New videos in your watch folders should show up here automatically.'
    : 'Setup your watcher settings to automatically have new videos show up here.';

  const clearAllDisabled = data.pending.length === 0 ? ' disabled' : '';
  document.getElementById('pendingList').innerHTML =
    data.pending.map(p => `<li data-key="${p.id}" class="${isNew(p.id, seenKeys.pending) ? 'enter' : ''}"><span class="name">${esc(p.name)}</span><a class="button process-btn" href="/item/${p.id}">Process</a></li>`).join('')
    + `<li class="browse-item"><button class="button browse-btn" id="browseBtn">Browse for a file...</button><span class="name">${esc(watcherHint)}</span><button class="danger" id="clearAllBtn" style="margin-left:auto;"${clearAllDisabled}>Clear All</button></li>`;

  // Only touch the DOM (and restart the dots animation) when the active
  // list's ids/stages actually changed -- rebuilding it every 3s poll even
  // when nothing changed was resetting the dots loop's timeline mid-cycle,
  // making it look like it never completed a full 1-2-3 pass.
  const activeSignature = JSON.stringify(data.active.map(a => [a.id, a.stage]));
  if (activeSignature !== lastActiveSignature) {
    lastActiveSignature = activeSignature;

    document.getElementById('activeList').innerHTML = data.active.length
      ? data.active.map(a => `<li data-key="${a.id}" class="${isNew(a.id, seenKeys.active) ? 'enter' : ''}"><span class="name">${esc(a.name)}</span><span class="status-processing"><span class="stage-text">${colorizeStage(a.stage || 'processing')}<span class="dots"><span></span><span></span><span></span></span></span><button class="cancel-btn" data-id="${a.id}">Cancel</button></span></li>`).join('')
      : '<li class="empty">Nothing processing.</li>';

    if (dotsTimeline) { dotsTimeline.pause(); dotsTimeline = null; }
    if (typeof anime !== 'undefined' && data.active.length) {
      dotsTimeline = anime.timeline({ loop: true, easing: 'easeInOutSine' })
        .add({ targets: '#activeList .dots span:nth-child(1)', opacity: [0.25, 1, 0.25], duration: 400 })
        .add({ targets: '#activeList .dots span:nth-child(2)', opacity: [0.25, 1, 0.25], duration: 400 }, '-=200')
        .add({ targets: '#activeList .dots span:nth-child(3)', opacity: [0.25, 1, 0.25], duration: 400 }, '-=200');
    }
  }

  document.getElementById('historyList').innerHTML = data.history.length
    ? data.history.map(h => {
        const badge = h.destination === 'local' ? '<span class="dest-badge dest-local">Local</span>'
          : h.destination === 'upload' ? '<span class="dest-badge dest-upload">Upload</span>' : '';
        const isLocal = h.destination === 'local';
        const isUpload = h.destination === 'upload';
        const newValue = isLocal ? h.output_filename : (isUpload ? h.title : null);
        const canOpen = h.status === 'done' && ((isLocal && h.output_path) || (isUpload && h.upload_url));
        const renamed = newValue && newValue !== h.filename;
        // Whichever span represents the actual finished output is the one
        // that should open it -- the new name if renamed, otherwise the
        // original (e.g. a local crop with the same name, or upload titled
        // identically to the source file).
        const openTarget = renamed ? 'new' : 'old';

        function makeSpan(text, isOpenTarget, extraTitleParts) {
          const clickable = canOpen && isOpenTarget;
          const titleParts = (extraTitleParts || []).slice();
          if (clickable) titleParts.push(isUpload ? 'Click to open' : 'Click to open in Explorer');
          const titleAttr = titleParts.length ? ` title="${esc(titleParts.join(' — '))}"` : '';
          if (clickable && isUpload) {
            return `<a class="name name-link" href="${escAttr(h.upload_url)}" target="_blank" rel="noopener"${titleAttr}>${esc(text)}</a>`;
          }
          const cls = clickable ? 'name name-link' : 'name';
          const dataAttr = clickable ? ` data-finished="${h.finished}"` : '';
          return `<span class="${cls}"${dataAttr}${titleAttr}>${esc(text)}</span>`;
        }

        const truncated = h.filename.length > 40;
        const nameHtml = makeSpan(truncateName(h.filename), openTarget === 'old', truncated ? [h.filename] : []);
        const renameHtml = renamed
          ? `<span class="rename-arrow">&mdash;&mdash;&#10230;  </span>${makeSpan(newValue, true)}`
          : '';

        return `<li data-key="${h.finished}" class="${isNew(h.finished, seenKeys.history) ? 'enter' : ''}"><span class="name-wrap">${nameHtml}${renameHtml}${badge}</span><span class="status-${h.status}">${h.status}</span></li>`;
      }).join('')
    : '<li class="empty">No history yet.</li>';
  document.getElementById('clearHistoryBtn').disabled = data.history.length === 0;

  seenKeys = { pending: pendingKeys, active: activeKeys, history: historyKeys };

  if (typeof anime !== 'undefined') {
    const enteringItems = document.querySelectorAll('#pendingList li.enter, #activeList li.enter, #historyList li.enter');
    if (enteringItems.length) {
      anime({
        targets: enteringItems,
        opacity: [0, 1],
        translateY: [8, 0],
        duration: 320,
        easing: 'easeOutQuad',
        delay: anime.stagger(60),
        complete: (anim) => anim.animatables.forEach(a => a.target.classList.remove('enter')),
      });
    }
  }
  firstRefresh = false;

  const watcherEl = document.getElementById('watcherStatus');
  if (data.watcher && data.watcher.running) {
    const folderName = data.watcher.watch_folder_name || 'unknown folder';
    watcherEl.innerHTML = `<span class="dot on"></span>Watching "${esc(folderName)}"`;
    watcherEl.title = data.watcher.watch_path || '';
  } else {
    watcherEl.innerHTML = `<span class="dot off"></span>Watcher not running`;
    watcherEl.title = '';
  }

  const clipDirEl = document.getElementById('clipDirStatus');
  if (data.clip_directory) {
    clipDirEl.textContent = `Clips Directory: ${data.clip_directory_name || data.clip_directory}`;
    clipDirEl.title = data.clip_directory;
  } else {
    clipDirEl.textContent = 'Clips Directory: Default';
    clipDirEl.title = 'Local (non-upload) clips and mp3s are saved in the same folder as the original file.';
  }
}

document.addEventListener('click', (e) => {
  const btn = e.target.closest('.button, button.button, .cancel-btn');
  if (!btn || btn.disabled || typeof anime === 'undefined') return;
  anime({ targets: btn, scale: [1, 0.94, 1], duration: 220, easing: 'easeOutQuad' });
});

document.addEventListener('mouseover', (e) => {
  const btn = e.target.closest('.button, button.button, .cancel-btn');
  if (!btn || btn.disabled || btn._jiggling || typeof anime === 'undefined') return;
  btn._jiggling = true;
  anime({
    targets: btn,
    rotate: [0, -2, 2, -1.5, 1.5, 0],
    duration: 400,
    easing: 'easeInOutSine',
    complete: () => { btn._jiggling = false; },
  });
});

// "?" heading hints -- letters fade in one by one (adapted from
// https://tobiasahlin.com/moving-letters/#11, minus its sweeping line),
// played on hover instead of looping automatically, then a blinking "_"
// cursor (matching the one in the title) appears at the end.
function pulseAttention(el) {
  if (!el || typeof anime === 'undefined') return null;
  return anime({ targets: el, scale: [1, 1.06, 1], duration: 500, easing: 'easeInOutSine', loop: true });
}

function setupHintHeader(headerId, hintText, onEnter, onLeave) {
  const header = document.getElementById(headerId);
  if (!header) return;
  const lettersEl = header.querySelector('.hint-letters');
  const cursorEl = header.querySelector('.hint-cursor');
  lettersEl.innerHTML = hintText.replace(/\S/g, c => `<span class="letter">${c}</span>`);

  let timeline = null;

  function reset() {
    if (timeline) { timeline.pause(); timeline = null; }
    if (cursorEl) cursorEl.style.display = 'none';
    if (typeof anime === 'undefined') return;
    anime.set(lettersEl.querySelectorAll('.letter'), { opacity: 0 });
  }

  function play() {
    if (typeof anime === 'undefined') return;
    reset();
    timeline = anime.timeline({
      easing: 'easeOutExpo',
      complete: () => { if (cursorEl) cursorEl.style.display = 'inline-block'; },
    }).add({ targets: lettersEl.querySelectorAll('.letter'), opacity: [0, 1], duration: 400, delay: anime.stagger(18) });
    if (onEnter) onEnter();
  }

  reset();
  header.addEventListener('mouseenter', play);
  header.addEventListener('mouseleave', () => {
    if (onLeave) onLeave();
    if (timeline) { timeline.pause(); timeline = null; }
    if (cursorEl) cursorEl.style.display = 'none';
    if (typeof anime === 'undefined') return;
    anime({ targets: lettersEl.querySelectorAll('.letter'), opacity: 0, duration: 200, easing: 'easeOutQuad' });
  });
}

let watcherPulseAnim = null;
let browsePulseAnim = null;

setupHintHeader(
  'pendingHeader',
  'Not seeing a new clip? Check the watcher status above or Browse below.',
  () => {
    watcherPulseAnim = pulseAttention(document.getElementById('watcherStatus'));
    browsePulseAnim = pulseAttention(document.getElementById('browseBtn'));
  },
  () => {
    if (watcherPulseAnim) { watcherPulseAnim.pause(); watcherPulseAnim = null; }
    if (browsePulseAnim) { browsePulseAnim.pause(); browsePulseAnim = null; }
    if (typeof anime !== 'undefined') anime.set('#watcherStatus, #browseBtn', { scale: 1 });
  },
);

setupHintHeader(
  'activeHeader',
  'We are getting your clip ready. If you change your mind, click Cancel and try again.',
);

document.getElementById('pendingList').addEventListener('click', async (e) => {
  const btn = e.target.closest('#browseBtn');
  if (!btn) return;
  btn.disabled = true;
  try {
    const res = await fetch('/browse');
    const data = await res.json();
    if (data.paths && data.paths.length) {
      for (const path of data.paths) {
        await fetch('/add?path=' + encodeURIComponent(path));
      }
      refresh();
    }
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('pendingList').addEventListener('click', async (e) => {
  const btn = e.target.closest('#clearAllBtn');
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  try {
    await fetch('/clear-pending');
    refresh();
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('clearHistoryBtn').addEventListener('click', async () => {
  const btn = document.getElementById('clearHistoryBtn');
  btn.disabled = true;
  try {
    await fetch('/clear-history');
    refresh();
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('setupBtn').addEventListener('click', () => {
  window.location.href = '/setup';
});

document.getElementById('activeList').addEventListener('click', async (e) => {
  const btn = e.target.closest('.cancel-btn');
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = 'Canceling...';
  await fetch(`/item/${btn.dataset.id}/cancel-processing`, { method: 'POST' });
  refresh();
});

document.getElementById('historyList').addEventListener('click', async (e) => {
  const nameEl = e.target.closest('.name-link');
  // The upload case renders an actual <a target="_blank"> that opens the
  // uploaded link natively -- no data-finished attribute, so it's excluded
  // here rather than also firing a pointless /history-open-folder call.
  if (!nameEl || !nameEl.dataset.finished) return;
  await fetch('/history-open-folder?finished=' + encodeURIComponent(nameEl.dataset.finished));
});

refresh();
setInterval(refresh, 3000);
