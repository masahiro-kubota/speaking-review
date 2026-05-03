const transcriptSelect = document.getElementById("transcript-select");
const loadButton = document.getElementById("load-button");
const audioPlayer = document.getElementById("audio-player");
const audioFileName = document.getElementById("audio-file-name");
const segmentCount = document.getElementById("segment-count");
const durationSeconds = document.getElementById("duration-seconds");
const tableBody = document.getElementById("segment-table-body");

let transcriptData = null;
let currentStopAt = null;
let currentSegmentId = null;

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatSeconds(value) {
  const total = Number(value);
  const minutes = Math.floor(total / 60);
  const seconds = total - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${seconds.toFixed(3).padStart(6, "0")}`;
}

function updateSummary() {
  if (!transcriptData) {
    audioFileName.textContent = "-";
    segmentCount.textContent = "0";
    durationSeconds.textContent = "-";
    return;
  }

  audioFileName.textContent = transcriptData.source_file_name;
  segmentCount.textContent = String(transcriptData.segments.length);
  durationSeconds.textContent = formatSeconds(transcriptData.duration_seconds || 0);
}

function renderSegments() {
  if (!transcriptData || transcriptData.segments.length === 0) {
    tableBody.innerHTML = `<tr><td colspan="6" class="empty-row">表示できる segment がありません。</td></tr>`;
    updateSummary();
    return;
  }

  const rows = transcriptData.segments
    .map((segment) => {
      const isPlaying = segment.id === currentSegmentId;
      return `
        <tr data-segment-id="${segment.id}" class="${isPlaying ? "row-playing" : ""}">
          <td><button type="button" data-action="play" data-segment-id="${segment.id}">Play</button></td>
          <td><button type="button" data-action="play-context" data-segment-id="${segment.id}">Play +1s</button></td>
          <td class="time-cell">${formatSeconds(segment.absolute_start)}</td>
          <td class="time-cell">${formatSeconds(segment.absolute_end)}</td>
          <td class="speaker-cell">${escapeHtml(segment.speaker || "-")}</td>
          <td class="text-cell">${escapeHtml(segment.text || "-")}</td>
        </tr>
      `;
    })
    .join("");

  tableBody.innerHTML = rows;
  updateSummary();
}

function findSegment(segmentId) {
  if (!transcriptData) return null;
  return transcriptData.segments.find((segment) => segment.id === segmentId) || null;
}

function setCurrentSegment(segmentId) {
  currentSegmentId = segmentId;
  renderSegments();
}

function startPlayback(startAt, endAt, segmentId) {
  currentStopAt = endAt;
  setCurrentSegment(segmentId);

  const playNow = () => {
    audioPlayer.pause();
    audioPlayer.currentTime = startAt;
    window.setTimeout(() => {
      audioPlayer.play().catch(() => {});
    }, 40);
  };

  if (audioPlayer.readyState < 1) {
    audioPlayer.addEventListener("loadedmetadata", playNow, { once: true });
    audioPlayer.load();
    return;
  }

  playNow();
}

function playSegment(segmentId, withContext) {
  const segment = findSegment(segmentId);
  if (!segment) return;

  const contextPadding = withContext ? 1 : 0;
  const startAt = Math.max(0, segment.absolute_start - contextPadding);
  const endAt = segment.absolute_end + contextPadding;
  startPlayback(startAt, endAt, segment.id);
}

async function loadTranscript(name) {
  const response = await fetch(`/api/transcript?name=${encodeURIComponent(name)}`);
  if (!response.ok) {
    tableBody.innerHTML = `<tr><td colspan="6" class="empty-row">Transcript を読み込めませんでした。</td></tr>`;
    return;
  }

  transcriptData = await response.json();
  audioPlayer.src = `/api/audio?name=${encodeURIComponent(transcriptData.name)}`;
  currentStopAt = null;
  currentSegmentId = null;
  renderSegments();

  const url = new URL(window.location.href);
  url.searchParams.set("name", transcriptData.name);
  window.history.replaceState({}, "", url);
}

async function loadTranscriptList() {
  const response = await fetch("/api/transcripts");
  const payload = await response.json();
  const items = payload.items || [];

  if (items.length === 0) {
    transcriptSelect.innerHTML = `<option value="">No transcript found</option>`;
    tableBody.innerHTML = `<tr><td colspan="6" class="empty-row">poc/output に diarized transcript がありません。</td></tr>`;
    return;
  }

  transcriptSelect.innerHTML = items
    .map((item) => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)}</option>`)
    .join("");

  const url = new URL(window.location.href);
  const selectedName = url.searchParams.get("name");
  const initialName = items.some((item) => item.name === selectedName)
    ? selectedName
    : items[0].name;
  transcriptSelect.value = initialName;
  await loadTranscript(initialName);
}

loadButton.addEventListener("click", async () => {
  if (!transcriptSelect.value) return;
  await loadTranscript(transcriptSelect.value);
});

tableBody.addEventListener("click", async (event) => {
  const target = event.target;
  if (!(target instanceof HTMLButtonElement)) return;

  const segmentId = target.dataset.segmentId;
  const action = target.dataset.action;
  if (!segmentId || !action) return;

  if (action === "play") {
    playSegment(segmentId, false);
    return;
  }

  if (action === "play-context") {
    playSegment(segmentId, true);
    return;
  }
});

audioPlayer.addEventListener("timeupdate", () => {
  if (currentStopAt === null) return;
  if (audioPlayer.currentTime >= currentStopAt) {
    audioPlayer.pause();
    currentStopAt = null;
  }
});

audioPlayer.addEventListener("pause", () => {
  currentStopAt = null;
});

loadTranscriptList().catch(() => {
  tableBody.innerHTML = `<tr><td colspan="6" class="empty-row">初期化に失敗しました。</td></tr>`;
});
