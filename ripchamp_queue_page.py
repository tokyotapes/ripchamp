#!/usr/bin/env python3
"""
ripchamp_queue_page.py

The queue list page (HTML/CSS/JS) served by ripchamp_queue_server.py at
"/". Split into its own module so it can be hot-reloaded independently --
see _reload_if_changed() in ripchamp_queue_server.py.

Not meant to be run directly.
"""

QUEUE_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>RIPChamp Clip Queue</title>
<link rel="icon" href="/favicon.ico">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Workbench&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Poppins:ital,wght@0,100;0,200;0,300;0,400;0,500;0,600;0,700;0,800;0,900;1,100;1,200;1,300;1,400;1,500;1,600;1,700;1,800;1,900&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:ital,wght@0,100..800;1,100..800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://www.nerdfonts.com/assets/css/webfont.css">
<style>
  :root {
    --bg: #0b0c0f;
    --bg-elev: #15171c;
    --border: #262931;
    --text: #e7e9ee;
    --text-dim: #8b909c;
    --accent: #5b8cff;
    --accent-hover: #729bff;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; background: var(--bg); color: var(--text); }
  body {
    display: flex; justify-content: center;
    font-family: "Poppins", sans-serif;
    padding: 40px 24px;
  }
  .page { width: 100%; max-width: 1300px; }
  .brand { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 4px; }
  .brand-left { display: flex; flex-direction: column; gap: 4px; }
  .title-row { display: flex; align-items: center; gap: 14px; }
  .brand .logo { height: 48px; width: auto; flex-shrink: 0; }
  h1 { font-size: 46px; font-weight: 400; margin: 0; letter-spacing: 0.01em; }
  .brand-name {
    font-family: "JetBrains Mono", monospace;
    font-optical-sizing: auto;
    font-weight: 400;
    font-style: normal;
    font-variation-settings: "BLED" 0, "SCAN" 0;
    letter-spacing: 0.1rem;
  }
  .brand-name .champ-part { color: #8E54EE; }
  .brand-name .clip-part { color: #FF0000; }
  .brand-name .queue-part { color: #5865F2; }
  .cursor-blink { display: inline-block; animation: blink 1.6s ease-in-out infinite; }
  @keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0; }
  }
  .sub { color: var(--text-dim); font-size: 13px; margin: 0; }
  h2 {
    font-size: 14px; font-weight: 500; color: var(--text-dim);
    text-transform: uppercase; letter-spacing: 0.06em; margin: 0 0 12px;
    cursor: default;
  }
  .hint-icon {
    display: inline-flex; align-items: center; justify-content: center;
    width: 14px; height: 14px; margin-left: 8px; border-radius: 50%;
    border: 1px solid var(--text-dim); color: var(--text-dim);
    font-size: 10px; font-weight: 700; text-transform: none; vertical-align: middle;
  }
  .hint-wrapper { position: relative; display: inline-flex; align-items: center; height: 12px; margin-left: 10px; vertical-align: bottom; }
  .hint-letters {
    display: inline-block; font-size: 14px; font-weight: 400; text-transform: none;
    letter-spacing: normal; color: var(--text-dim); white-space: nowrap; padding-left: 10px;
    position: relative; top: -2px;
  }
  .hint-letters .letter { display: inline-block; opacity: 0; }
  .hint-cursor { display: none; margin-left: 2px; color: var(--text-dim); position: relative; top: -2px; }
  .card {
    background: var(--bg-elev);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 20px;
    margin-bottom: 20px;
    box-shadow: 5px 5px 0px -3px #8E54EE;
  }
  ul { list-style: none; padding: 0; margin: 0; font-family: "JetBrains Mono", monospace; }
  li {
    padding: 12px 14px; border: 1px solid var(--border); border-radius: 10px; margin-bottom: 8px;
    display: flex; justify-content: space-between; align-items: center; gap: 12px; background: #1a1c22;
  }
  li:last-child { margin-bottom: 0; }
  li.enter { opacity: 0; }
  li.empty { border-style: dashed; color: var(--text-dim); background: transparent; }
  a.button, button.button {
    background: var(--accent); color: #fff; text-decoration: none; padding: 7px 14px;
    border: none; border-radius: 8px; font-size: 13px; font-weight: 400; white-space: nowrap;
    cursor: pointer; transition: background 0.15s ease; font-family: inherit;
  }
  a.button:hover, button.button:hover { background: var(--accent-hover); }
  button.browse-btn { background: #8E54EE; white-space: nowrap; font-size: 11px; padding: 6px 12px; }
  button.browse-btn:hover { background: #9d6bf0; }
  button.browse-btn:disabled { opacity: 0.6; cursor: default; }
  .browse-col { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; margin-top: 0; }
  .browse-buttons { display: flex; flex-direction: column; gap: 8px; }
  .watcher-status { font-size: 12px; color: var(--text-dim); display: flex; align-items: center; gap: 6px; white-space: nowrap; }
  .watcher-status .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .watcher-status .dot.on { background: #4ade80; }
  .watcher-status .dot.off { background: #f87171; }
  button.cancel-btn {
    background: transparent; border: 1px solid var(--danger, #e5484d); color: #e5484d;
    padding: 5px 12px; border-radius: 6px; font-size: 12px; font-family: inherit;
    cursor: pointer; transition: background 0.15s ease;
  }
  button.cancel-btn:hover { background: rgba(229, 72, 77, 0.12); }
  button.cancel-btn:disabled { opacity: 0.5; cursor: default; }
  .name { overflow-wrap: anywhere; }
  .name-wrap { display: flex; align-items: center; gap: 8px; overflow-wrap: anywhere; min-width: 0; }
  .name-link { cursor: pointer; text-decoration: underline dotted; text-underline-offset: 3px; }
  .name-link:hover { color: var(--text); }
  .dest-badge {
    font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 4px;
    text-transform: uppercase; letter-spacing: 0.03em; white-space: nowrap; flex-shrink: 0;
  }
  .dest-badge.dest-local { background: rgba(142, 84, 238, 0.15); color: #8E54EE; }
  .dest-badge.dest-upload { background: rgba(88, 101, 242, 0.15); color: #5865F2; }
  .status-done { color: #4ade80; }
  .status-error { color: #f87171; }
  .status-canceled { color: var(--text-dim); }
  .status-processing { color: #facc15; display: flex; align-items: center; gap: 10px; }
  .dots { display: inline-flex; align-items: flex-end; vertical-align: text-bottom; gap: 3px; margin-left: 4px; position: relative; top: -3px; }
  .dots span { width: 4px; height: 4px; border-radius: 50%; background: #facc15; opacity: 0.25; }
</style>
</head>
<body>
<div class="page">
<div class="brand">
  <div class="brand-left">
    <div class="title-row">
      <img src="/logo2.png" alt="RIPChamp logo" class="logo">
      <h1 class="brand-name"><span class="rip-part">RIP</span><span class="champ-part">Champ</span>(<span class="clip-part">Clip</span>).<span class="queue-part">Queue</span><span class="cursor-blink">_</span></h1>
    </div>
    <div class="sub">Bookmark this page. New clips automatically show up here or browse for files manually.</div>
  </div>
  <div class="browse-col">
    <div class="browse-buttons">
      <button class="button browse-btn" id="browseBtn">Browse for a file...</button>
      <button class="button browse-btn" id="setClipDirBtn">Set Clip Directory</button>
    </div>
    <div class="watcher-status" id="watcherStatus"></div>
    <div class="watcher-status" id="clipDirStatus"></div>
  </div>
</div>

<div class="card">
  <h2 id="pendingHeader">Fresh Clips<span class="hint-icon">?</span><span class="hint-wrapper"><span class="hint-letters"></span><span class="hint-cursor cursor-blink">_</span></span></h2>
  <ul id="pendingList"></ul>
</div>

<div class="card">
  <h2 id="activeHeader">Let us cook<span class="hint-icon">?</span><span class="hint-wrapper"><span class="hint-letters"></span><span class="hint-cursor cursor-blink">_</span></span></h2>
  <ul id="activeList"></ul>
</div>

<div class="card">
  <h2>Clipped and shipped</h2>
  <ul id="historyList"></ul>
</div>
</div>

<script src="https://cdn.jsdelivr.net/npm/animejs@3.2.2/lib/anime.min.js"></script>
<script>
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function keySet(arr) { return new Set(arr.map(String)); }

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

  document.getElementById('pendingList').innerHTML = data.pending.length
    ? data.pending.map(p => `<li data-key="${p.id}" class="${isNew(p.id, seenKeys.pending) ? 'enter' : ''}"><span class="name">${esc(p.name)}</span><a class="button" href="/item/${p.id}">Process</a></li>`).join('')
    : '<li class="empty">Nothing waiting.</li>';

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
        const openable = h.status === 'done' && h.destination === 'local' && h.output_path;
        const nameClass = openable ? 'name name-link' : 'name';
        const nameAttrs = openable ? ` data-finished="${h.finished}" title="Click to open in Explorer"` : '';
        return `<li data-key="${h.finished}" class="${isNew(h.finished, seenKeys.history) ? 'enter' : ''}"><span class="name-wrap"><span class="${nameClass}"${nameAttrs}>${esc(h.filename)}</span>${badge}</span><span class="status-${h.status}">${h.status}</span></li>`;
      }).join('')
    : '<li class="empty">No history yet.</li>';

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
    rotate: [0, -4, 4, -3, 3, 0],
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
  'Not seeing a new clip? Check the watcher status or Browse above.',
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

document.getElementById('browseBtn').addEventListener('click', async () => {
  const btn = document.getElementById('browseBtn');
  btn.disabled = true;
  try {
    const res = await fetch('/browse');
    const data = await res.json();
    if (data.path) {
      await fetch('/add?path=' + encodeURIComponent(data.path));
      refresh();
    }
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('setClipDirBtn').addEventListener('click', async () => {
  const btn = document.getElementById('setClipDirBtn');
  btn.disabled = true;
  try {
    await fetch('/set-clip-directory');
    refresh();
  } finally {
    btn.disabled = false;
  }
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
  if (!nameEl) return;
  await fetch('/history-open-folder?finished=' + encodeURIComponent(nameEl.dataset.finished));
});

refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""
