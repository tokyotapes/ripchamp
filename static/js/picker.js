/*
RIPChamp
Copyright (C) 2026  NoOrg

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

// canUpload starts true (optimistic) so the pre-config-load synchronous
// updateVisibility() call at script init doesn't briefly flip the default
// selection to Local before the real config.json answer comes back.
let CONFIG = { channels: [], youtubeAvailable: false, streamableAvailable: false, canUpload: true };

const v = document.getElementById('v');
const startSlider = document.getElementById('startSlider');
const endSlider = document.getElementById('endSlider');
const trackSel = document.getElementById('trackSel');
const playhead = document.getElementById('playhead');
const timeStartEl = document.getElementById('timeStart');
const timeSelectionEl = document.getElementById('timeSelection');
const timeEndEl = document.getElementById('timeEnd');
const playOverlay = document.getElementById('playOverlay');
const volumeSlider = document.getElementById('volumeSlider');
const volumeIcon = document.getElementById('volumeIcon');
const localCard = document.getElementById('localCard');
const uploadCard = document.getElementById('uploadCard');
const localNameInput = document.getElementById('localNameInput');
const localAspectRow = document.getElementById('localAspectRow');
const uploadNameInput = document.getElementById('uploadNameInput');
const hostYoutubeOption = document.getElementById('hostYoutubeOption');
const hostStreamableOption = document.getElementById('hostStreamableOption');
const discordPostRow = document.getElementById('discordPostRow');
const channelRow = document.getElementById('channelRow');
const channelSelect = document.getElementById('channelSelect');
const createClipBtn = document.getElementById('createClipBtn');
const createClipCaption = document.getElementById('createClipCaption');
const uploadClipBtn = document.getElementById('uploadClipBtn');
const uploadClipCaption = document.getElementById('uploadClipCaption');
const openFileBtn = document.getElementById('openFileBtn');
const openFolderBtn = document.getElementById('openFolderBtn');

let selectedSide = 'upload';

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
  timeStartEl.textContent = `start ${fmt(startTime())}`;
  timeSelectionEl.textContent = `selection ${fmt(endTime() - startTime())}`;
  timeEndEl.textContent = `end ${fmt(endTime())}`;
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

// Mirrors ripchamp_picker.py's build_ripchamp_args default-naming logic
// (sanitized typed name if given, else "<stem>_1080p" for video / "<stem>"
// for audio) so the caption always matches what will actually be written.
function computeLocalOutputName() {
  const localType = document.querySelector('input[name=localType]:checked').value;
  const ext = localType === 'audio' ? '.mp3' : '.mp4';
  const typed = localNameInput.value.trim();
  const stem = (CONFIG.filename || '').replace(/\.[^/.]+$/, '');
  const base = typed || (localType === 'audio' ? stem : `${stem}_1080p`);
  return base + ext;
}

function updateCreateClipCaption() {
  const location = CONFIG.clipDirectoryName || 'the same folder as the original file';
  createClipCaption.replaceChildren(
    document.createTextNode('Create '),
    Object.assign(document.createElement('strong'), { textContent: computeLocalOutputName() }),
    document.createTextNode(' in '),
    Object.assign(document.createElement('strong'), { textContent: location }),
  );
}

const HOST_NAMES = { youtube: 'YouTube', streamable: 'Streamable' };

function updateUploadClipCaption(postToDiscord) {
  const host = document.querySelector('input[name=videoHost]:checked').value;
  const parts = [
    document.createTextNode('Upload video to '),
    Object.assign(document.createElement('strong'), { textContent: HOST_NAMES[host] || host }),
  ];
  if (postToDiscord) {
    parts.push(
      document.createTextNode(' and upload video to '),
      Object.assign(document.createElement('strong'), { textContent: 'Discord' }),
    );
  }
  uploadClipCaption.replaceChildren(...parts);
}

function updateVisibility() {
  // No upload host configured at all -- don't offer a choice, local is
  // the only option and stays selected/undimmed.
  uploadCard.style.display = CONFIG.canUpload ? 'block' : 'none';
  if (!CONFIG.canUpload) { selectedSide = 'local'; }

  localCard.classList.toggle('selected', selectedSide === 'local');
  uploadCard.classList.toggle('selected', selectedSide === 'upload');

  const localType = document.querySelector('input[name=localType]:checked').value;
  localAspectRow.style.display = (localType === 'video') ? 'flex' : 'none';
  updateCreateClipCaption();

  const showDiscordPost = CONFIG.channels.length > 0;
  discordPostRow.style.display = showDiscordPost ? 'flex' : 'none';
  const postToDiscord = document.querySelector('input[name=postToDiscord]:checked').value === 'yes';
  channelRow.style.display = (showDiscordPost && postToDiscord) ? 'flex' : 'none';
  updateUploadClipCaption(showDiscordPost && postToDiscord);
}

function selectSide(side) {
  selectedSide = side;
  updateVisibility();
}

localCard.addEventListener('click', () => selectSide('local'));
uploadCard.addEventListener('click', () => selectSide('upload'));
document.querySelectorAll('input[name=localType]').forEach(r => r.addEventListener('change', updateVisibility));
document.querySelectorAll('input[name=postToDiscord]').forEach(r => r.addEventListener('change', updateVisibility));
document.querySelectorAll('input[name=videoHost]').forEach(r => r.addEventListener('change', updateVisibility));
localNameInput.addEventListener('input', updateCreateClipCaption);
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

openFileBtn.addEventListener('click', (e) => { e.preventDefault(); fetch(CONFIG.openFileUrl); });
openFolderBtn.addEventListener('click', (e) => { e.preventDefault(); fetch(CONFIG.openFolderUrl); });

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

async function submitOutput() {
  const body = { start: startTime(), end: endTime(), duration: duration, canceled: false };
  if (selectedSide === 'local') {
    const localType = document.querySelector('input[name=localType]:checked').value;
    body.type = localType;
    if (localType === 'audio') {
      body.fileName = localNameInput.value.trim();
    } else {
      body.title = localNameInput.value.trim();
      body.aspect = document.querySelector('input[name=localAspect]:checked').value;
      body.destination = 'local';
    }
  } else {
    body.type = 'video';
    body.title = uploadNameInput.value.trim();
    body.aspect = document.querySelector('input[name=uploadAspect]:checked').value;
    body.destination = 'upload';
    body.videoHost = document.querySelector('input[name=videoHost]:checked').value;
    if (CONFIG.channels.length > 0) {
      body.postToDiscord = document.querySelector('input[name=postToDiscord]:checked').value === 'yes';
      if (body.postToDiscord) {
        body.discordChannel = channelSelect.value;
      }
    }
  }
  await fetch(CONFIG.confirmUrl, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
  if (CONFIG.queueUrl) { window.location.href = CONFIG.queueUrl; }
  else { document.body.innerHTML = '<h1>Confirmed -- you can close this tab.</h1>'; }
}

createClipBtn.addEventListener('click', () => {
  selectSide('local');
  submitOutput();
});

uploadClipBtn.addEventListener('click', () => {
  selectSide('upload');
  submitOutput();
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
  document.getElementById('previewProxyNotice').style.display = CONFIG.usingPreviewProxy ? 'block' : 'none';

  CONFIG.channels.forEach(name => {
    const opt = document.createElement('option');
    opt.value = name; opt.textContent = name;
    channelSelect.appendChild(opt);
  });

  // No upload host configured on the setup page -- don't offer the Upload
  // card at all, local-only is assumed (updateVisibility hides the card
  // and forces selectedSide back to 'local').
  CONFIG.canUpload = !!(CONFIG.youtubeAvailable || CONFIG.streamableAvailable);
  // Only offer hosts that are actually usable -- hide the other radio,
  // and if the default-checked one (YouTube) isn't available but
  // Streamable is, select that instead.
  if (!CONFIG.youtubeAvailable) {
    hostYoutubeOption.style.display = 'none';
  }
  if (!CONFIG.streamableAvailable) {
    hostStreamableOption.style.display = 'none';
  }
  if (!CONFIG.youtubeAvailable && CONFIG.streamableAvailable) {
    document.querySelector('input[name=videoHost][value=streamable]').checked = true;
  }

  updateVisibility();
})();
