let CONFIG = { channels: [] };

const v = document.getElementById('v');
const startSlider = document.getElementById('startSlider');
const endSlider = document.getElementById('endSlider');
const trackSel = document.getElementById('trackSel');
const playhead = document.getElementById('playhead');
const timesEl = document.getElementById('times');
const playOverlay = document.getElementById('playOverlay');
const volumeSlider = document.getElementById('volumeSlider');
const volumeIcon = document.getElementById('volumeIcon');
const videoOptions = document.getElementById('videoOptions');
const hostRow = document.getElementById('hostRow');
const discordPostRow = document.getElementById('discordPostRow');
const channelRow = document.getElementById('channelRow');
const channelSelect = document.getElementById('channelSelect');
const titleInput = document.getElementById('titleInput');
const openFileBtn = document.getElementById('openFileBtn');
const openFolderBtn = document.getElementById('openFolderBtn');

let duration = 0;

function fmt(s) {
  if (!isFinite(s)) return '0:00.0';
  const m = Math.floor(s / 60);
  const sec = (s - m * 60).toFixed(1).padStart(4, '0');
  return m + ':' + sec;
}

function startTime() { return (startSlider.value / 1000) * duration; }
function endTime() { return (endSlider.value / 1000) * duration; }

function updateTimes() {
  timesEl.textContent = `start ${fmt(startTime())}  ·  end ${fmt(endTime())}  ·  duration ${fmt(duration)}  ·  selection: ${fmt(endTime() - startTime())}`;
  const s = (startSlider.value / 1000) * 100;
  const e = (endSlider.value / 1000) * 100;
  trackSel.style.left = s + '%';
  trackSel.style.width = Math.max(0, e - s) + '%';
  startSlider.classList.toggle('at-edge', parseInt(startSlider.value) === 0);
  endSlider.classList.toggle('at-edge', parseInt(endSlider.value) === 1000);
  updatePlayhead();
}

function updatePlayhead() {
  const pct = duration ? (v.currentTime / duration) * 100 : 0;
  playhead.style.left = pct + '%';
}

function updateVisibility() {
  const type = document.querySelector('input[name=type]:checked').value;
  videoOptions.style.display = (type === 'video') ? 'block' : 'none';
  const dest = document.querySelector('input[name=dest]:checked').value;
  hostRow.style.display = (type === 'video' && dest === 'upload') ? 'flex' : 'none';
  const showDiscordPost = type === 'video' && dest === 'upload' && CONFIG.channels.length > 0;
  discordPostRow.style.display = showDiscordPost ? 'flex' : 'none';
  const postToDiscord = document.querySelector('input[name=postToDiscord]:checked').value === 'yes';
  channelRow.style.display = (showDiscordPost && postToDiscord) ? 'flex' : 'none';
}

document.querySelectorAll('input[name=type]').forEach(r => r.addEventListener('change', updateVisibility));
document.querySelectorAll('input[name=dest]').forEach(r => r.addEventListener('change', updateVisibility));
document.querySelectorAll('input[name=postToDiscord]').forEach(r => r.addEventListener('change', updateVisibility));
updateVisibility();

v.addEventListener('loadedmetadata', () => {
  duration = v.duration;
  updateTimes();
  updatePlayhead();
});

v.addEventListener('seeked', updatePlayhead);

startSlider.addEventListener('input', () => {
  if (parseInt(startSlider.value) >= parseInt(endSlider.value)) {
    startSlider.value = Math.max(0, parseInt(endSlider.value) - 1);
  }
  v.currentTime = startTime();
  updateTimes();
});

endSlider.addEventListener('input', () => {
  if (parseInt(endSlider.value) <= parseInt(startSlider.value)) {
    endSlider.value = Math.min(1000, parseInt(startSlider.value) + 1);
  }
  v.currentTime = endTime();
  updateTimes();
});

function playFromCorrectPosition() {
  if (v.currentTime >= endTime() - 0.01) {
    v.currentTime = startTime();
  }
  v.play();
}

playOverlay.addEventListener('click', playFromCorrectPosition);
v.addEventListener('click', () => {
  if (!v.paused) { v.pause(); }
});

v.addEventListener('play', () => { playOverlay.classList.add('hidden'); });
v.addEventListener('pause', () => { playOverlay.classList.remove('hidden'); });

openFileBtn.addEventListener('click', () => { fetch(CONFIG.openFileUrl); });
openFolderBtn.addEventListener('click', () => { fetch(CONFIG.openFolderUrl); });

v.volume = parseFloat(volumeSlider.value);
volumeSlider.addEventListener('input', () => {
  v.volume = parseFloat(volumeSlider.value);
  volumeIcon.textContent = v.volume === 0 ? '🔇' : (v.volume < 0.5 ? '🔉' : '🔊');
});

v.addEventListener('timeupdate', () => {
  if (v.currentTime >= endTime() && !v.paused) {
    v.pause();
    v.currentTime = endTime();
  }
  updatePlayhead();
});

document.getElementById('confirmBtn').addEventListener('click', async () => {
  const type = document.querySelector('input[name=type]:checked').value;
  const body = { start: startTime(), end: endTime(), duration: duration, canceled: false, type: type };
  if (type === 'video') {
    body.title = titleInput.value.trim();
    body.destination = document.querySelector('input[name=dest]:checked').value;
    if (body.destination === 'upload') {
      body.videoHost = document.querySelector('input[name=videoHost]:checked').value;
      if (CONFIG.channels.length > 0) {
        body.postToDiscord = document.querySelector('input[name=postToDiscord]:checked').value === 'yes';
        if (body.postToDiscord) {
          body.discordChannel = channelSelect.value;
        }
      }
    }
  }
  await fetch(CONFIG.confirmUrl, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
  if (CONFIG.queueUrl) { window.location.href = CONFIG.queueUrl; }
  else { document.body.innerHTML = '<h1>Confirmed -- you can close this tab.</h1>'; }
});

document.getElementById('cancelBtn').addEventListener('click', async () => {
  await fetch(CONFIG.confirmUrl, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({canceled: true}) });
  if (CONFIG.queueUrl) { window.location.href = CONFIG.queueUrl; }
  else { document.body.innerHTML = '<h1>Canceled -- you can close this tab.</h1>'; }
});

// Fetch this page's per-item config (filename, video URL, confirm URL,
// Discord channels, etc.) -- resolved relative to wherever this picker
// page itself is served from, so the same static picker.html/picker.js
// work unmodified whether served at "/" (single-shot flow) or "/item/<id>"
// (queue flow). NOT `new URL('config.json', location.href)` -- standard
// relative-URL resolution drops the last path segment when there's no
// trailing slash, so from "/item/5" that resolves to "/item/config.json",
// not "/item/5/config.json".
(async () => {
  const configUrl = location.pathname.replace(/\/?$/, '/') + 'config.json';
  const res = await fetch(configUrl);
  CONFIG = await res.json();

  document.querySelector('.filename').textContent = CONFIG.filename;
  v.src = CONFIG.videoUrl;

  CONFIG.channels.forEach(name => {
    const opt = document.createElement('option');
    opt.value = name; opt.textContent = name;
    channelSelect.appendChild(opt);
  });
  updateVisibility();
})();
