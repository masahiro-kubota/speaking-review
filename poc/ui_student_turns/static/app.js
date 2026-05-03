const turnsSelect = document.getElementById("turns-select");
const loadButton = document.getElementById("load-button");
const audioPlayer = document.getElementById("audio-player");
const audioFileName = document.getElementById("audio-file-name");
const turnCount = document.getElementById("turn-count");
const studentSpeakers = document.getElementById("student-speakers");
const teacherSpeakers = document.getElementById("teacher-speakers");
const turnList = document.getElementById("turn-list");

let turnsData = null;
let currentStopAt = null;
let currentTurnId = null;

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

function formatSpeakerList(values) {
  if (!Array.isArray(values) || values.length === 0) {
    return "-";
  }
  return values.join(", ");
}

function updateSummary() {
  if (!turnsData) {
    audioFileName.textContent = "-";
    turnCount.textContent = "0";
    studentSpeakers.textContent = "-";
    teacherSpeakers.textContent = "-";
    return;
  }

  audioFileName.textContent = turnsData.source_file_name;
  turnCount.textContent = String(turnsData.turn_count || 0);
  studentSpeakers.textContent = formatSpeakerList(turnsData.student_speakers);
  teacherSpeakers.textContent = formatSpeakerList(turnsData.teacher_speakers);
}

function renderTurns() {
  if (!turnsData || turnsData.turns.length === 0) {
    turnList.innerHTML = `<article class="empty-card">表示できる student turn がありません。</article>`;
    updateSummary();
    return;
  }

  const cards = turnsData.turns
    .map((turn) => {
      const isPlaying = turn.turn_id === currentTurnId;
      const promptText = turn.prev_teacher_text || "Prompt がありません。";
      const promptAvailable = Boolean(turn.prev_teacher_text && turn.prev_teacher_start !== null);
      return `
        <article class="turn-card ${isPlaying ? "card-playing" : ""}" data-turn-id="${turn.turn_id}">
          <div class="turn-card-header">
            <div>
              <p class="turn-label">${escapeHtml(turn.turn_id)}</p>
              <h2>${formatSeconds(turn.start)} - ${formatSeconds(turn.end)}</h2>
            </div>
            <div class="turn-meta">
              <span>${turn.duration_seconds.toFixed(2)}s</span>
              <span>Labels: ${escapeHtml(formatSpeakerList(turn.speaker_labels))}</span>
            </div>
          </div>
          <div class="turn-actions">
            <button type="button" data-action="play-student" data-turn-id="${turn.turn_id}">Play Student</button>
            <button type="button" data-action="play-prompt" data-turn-id="${turn.turn_id}" ${promptAvailable ? "" : "disabled"}>Play Prompt + Student</button>
          </div>
          <div class="turn-sections">
            <section class="turn-section prompt-section">
              <p class="section-label">Prompt</p>
              <p class="section-text">${escapeHtml(promptText)}</p>
            </section>
            <section class="turn-section student-section">
              <p class="section-label">Student</p>
              <p class="section-text">${escapeHtml(turn.text || "-")}</p>
            </section>
          </div>
        </article>
      `;
    })
    .join("");

  turnList.innerHTML = cards;
  updateSummary();
}

function findTurn(turnId) {
  if (!turnsData) return null;
  return turnsData.turns.find((turn) => turn.turn_id === turnId) || null;
}

function setCurrentTurn(turnId) {
  currentTurnId = turnId;
  renderTurns();
}

function startPlayback(startAt, endAt, turnId) {
  currentStopAt = endAt;
  setCurrentTurn(turnId);

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

function playTurn(turnId, withPrompt) {
  const turn = findTurn(turnId);
  if (!turn) return;

  const startAt = withPrompt && turn.prev_teacher_start !== null ? turn.prev_teacher_start : turn.start;
  startPlayback(startAt, turn.end, turn.turn_id);
}

async function loadStudentTurns(name) {
  const response = await fetch(`/api/student-turns?name=${encodeURIComponent(name)}`);
  if (!response.ok) {
    turnList.innerHTML = `<article class="empty-card">Student turns を読み込めませんでした。</article>`;
    return;
  }

  turnsData = await response.json();
  audioPlayer.src = `/api/audio?name=${encodeURIComponent(turnsData.name)}`;
  currentStopAt = null;
  currentTurnId = null;
  renderTurns();

  const url = new URL(window.location.href);
  url.searchParams.set("name", turnsData.name);
  window.history.replaceState({}, "", url);
}

async function loadStudentTurnList() {
  const response = await fetch("/api/student-turn-files");
  const payload = await response.json();
  const items = payload.items || [];

  if (items.length === 0) {
    turnsSelect.innerHTML = `<option value="">No student turns found</option>`;
    turnList.innerHTML = `<article class="empty-card">poc/output に student_turns.json がありません。</article>`;
    return;
  }

  turnsSelect.innerHTML = items
    .map((item) => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)}</option>`)
    .join("");

  const url = new URL(window.location.href);
  const selectedName = url.searchParams.get("name");
  const initialName = items.some((item) => item.name === selectedName) ? selectedName : items[0].name;
  turnsSelect.value = initialName;
  await loadStudentTurns(initialName);
}

loadButton.addEventListener("click", async () => {
  if (!turnsSelect.value) return;
  await loadStudentTurns(turnsSelect.value);
});

turnList.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLButtonElement)) return;

  const turnId = target.dataset.turnId;
  const action = target.dataset.action;
  if (!turnId || !action) return;

  if (action === "play-student") {
    playTurn(turnId, false);
    return;
  }

  if (action === "play-prompt") {
    playTurn(turnId, true);
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

loadStudentTurnList().catch(() => {
  turnList.innerHTML = `<article class="empty-card">初期化に失敗しました。</article>`;
});
