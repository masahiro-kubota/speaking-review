const unitSelect = document.getElementById("unit-select");
const lessonSelect = document.getElementById("lesson-select");
const loadButton = document.getElementById("load-button");
const audioPlayer = document.getElementById("audio-player");
const audioFileName = document.getElementById("audio-file-name");
const itemCountLabel = document.getElementById("item-count-label");
const itemCount = document.getElementById("item-count");
const studentSpeakers = document.getElementById("student-speakers");
const teacherSpeakers = document.getElementById("teacher-speakers");
const reviewSummary = document.getElementById("review-summary");
const turnList = document.getElementById("turn-list");

let speechData = null;
let currentStopAt = null;
let currentItemId = null;

function currentUnit() {
  return unitSelect.value || "turn";
}

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
  if (!speechData) {
    audioFileName.textContent = "-";
    itemCount.textContent = "0";
    itemCountLabel.textContent = currentUnit() === "utterance" ? "Utterances" : "Turns";
    studentSpeakers.textContent = "-";
    teacherSpeakers.textContent = "-";
    reviewSummary.textContent = "-";
    return;
  }

  audioFileName.textContent = speechData.source_file_name;
  itemCountLabel.textContent = speechData.unit_type === "utterance" ? "Utterances" : "Turns";
  itemCount.textContent = String(speechData.item_count || 0);
  studentSpeakers.textContent = formatSpeakerList(speechData.student_speakers);
  teacherSpeakers.textContent = formatSpeakerList(speechData.teacher_speakers);
  if (speechData.unit_type === "turn") {
    reviewSummary.textContent = "Utterance review only";
    return;
  }
  const summary = speechData.review_summary || {};
  if (!summary.available) {
    reviewSummary.textContent = "No review file";
    return;
  }
  reviewSummary.textContent = `${summary.reviewed_count || 0} reviewed / ${summary.skipped_count || 0} skipped`;
}

function escapeIssueCategory(value) {
  return escapeHtml(String(value || "").replaceAll("_", " "));
}

function renderReviewSection(item) {
  if (item.unit_type === "turn") {
    return `
      <section class="review-block review-pending">
        <div class="review-header">
          <p class="section-label">Review</p>
          <span class="status-badge status-pending">Utterance Only</span>
        </div>
        <p class="section-text">添削は utterance 単位のみです。Utterance に切り替えて確認してください。</p>
      </section>
    `;
  }

  const status = item.review_status || "not_reviewed";
  if (status === "not_reviewed") {
    return `
      <section class="review-block review-pending">
        <div class="review-header">
          <p class="section-label">Review</p>
          <span class="status-badge status-pending">Not Reviewed</span>
        </div>
        <p class="section-text">この utterance にはまだ添削結果がありません。</p>
      </section>
    `;
  }

  if (status === "skipped") {
    return `
      <section class="review-block review-skipped">
        <div class="review-header">
          <p class="section-label">Review</p>
          <span class="status-badge status-skipped">Skipped</span>
        </div>
        <p class="section-text">短すぎるため添削をスキップしました。理由: ${escapeHtml(item.skip_reason || "-")}</p>
      </section>
    `;
  }

  if (status === "error") {
    return `
      <section class="review-block review-error">
        <div class="review-header">
          <p class="section-label">Review</p>
          <span class="status-badge status-error">Error</span>
        </div>
        <p class="section-text">${escapeHtml(item.error || "添削中にエラーが発生しました。")}</p>
      </section>
    `;
  }

  const issues = Array.isArray(item.issues)
    ? item.issues
        .map(
          (issue) => `
            <li class="issue-item">
              <p class="issue-meta">${escapeIssueCategory(issue.category)}</p>
              <p class="issue-line"><strong>Original:</strong> ${escapeHtml(issue.original || "-")}</p>
              <p class="issue-line"><strong>Suggestion:</strong> ${escapeHtml(issue.suggestion || "-")}</p>
              <p class="issue-line">${escapeHtml(issue.explanation_ja || "-")}</p>
            </li>
          `,
        )
        .join("")
    : "";

  return `
    <section class="review-block review-complete">
      <div class="review-header">
        <p class="section-label">Review</p>
        <span class="status-badge status-reviewed">Reviewed</span>
      </div>
      <div class="review-columns">
        <section class="review-card">
          <p class="section-label">Corrected</p>
          <p class="section-text">${escapeHtml(item.corrected_text || "-")}</p>
        </section>
        <section class="review-card">
          <p class="section-label">More Natural</p>
          <p class="section-text">${escapeHtml(item.natural_text || "-")}</p>
        </section>
      </div>
      <section class="review-card feedback-card">
        <p class="section-label">Feedback</p>
        <p class="section-text">${escapeHtml(item.overall_feedback_ja || "-")}</p>
      </section>
      <section class="review-card issues-card">
        <p class="section-label">Issues</p>
        ${
          issues
            ? `<ul class="issue-list">${issues}</ul>`
            : `<p class="section-text">大きな修正点はありません。</p>`
        }
      </section>
    </section>
  `;
}

function renderItems() {
  if (!speechData || speechData.items.length === 0) {
    turnList.innerHTML = `<article class="empty-card">表示できる student ${currentUnit()} がありません。</article>`;
    updateSummary();
    return;
  }

  const cards = speechData.items
    .map((item) => {
      const isPlaying = item.id === currentItemId;
      const promptText = item.prev_teacher_text || "Prompt がありません。";
      const promptAvailable = Boolean(item.prev_teacher_text && item.prev_teacher_start !== null);
      const sourceUnitMeta =
        item.unit_type === "utterance"
          ? `<span>Source turns: ${item.source_unit_count}</span>`
          : "";
      return `
        <article class="turn-card ${isPlaying ? "card-playing" : ""}" data-item-id="${item.id}">
          <div class="turn-card-header">
            <div>
              <p class="turn-label">${escapeHtml(item.id)}</p>
              <h2>${formatSeconds(item.start)} - ${formatSeconds(item.end)}</h2>
            </div>
            <div class="turn-meta">
              <span>${item.duration_seconds.toFixed(2)}s</span>
              <span>Labels: ${escapeHtml(formatSpeakerList(item.speaker_labels))}</span>
              ${sourceUnitMeta}
            </div>
          </div>
          <div class="turn-actions">
            <button type="button" data-action="play-student" data-item-id="${item.id}">Play Student</button>
            <button type="button" data-action="play-prompt" data-item-id="${item.id}" ${promptAvailable ? "" : "disabled"}>Play Prompt + Student</button>
          </div>
          <div class="turn-sections">
            <section class="turn-section prompt-section">
              <p class="section-label">Prompt</p>
              <p class="section-text">${escapeHtml(promptText)}</p>
            </section>
            <section class="turn-section student-section">
              <p class="section-label">${item.unit_type === "utterance" ? "Utterance" : "Student"}</p>
              <p class="section-text">${escapeHtml(item.text || "-")}</p>
            </section>
          </div>
          ${renderReviewSection(item)}
        </article>
      `;
    })
    .join("");

  turnList.innerHTML = cards;
  updateSummary();
}

function findItem(itemId) {
  if (!speechData) return null;
  return speechData.items.find((item) => item.id === itemId) || null;
}

function setCurrentItem(itemId) {
  currentItemId = itemId;
  renderItems();
}

function startPlayback(startAt, endAt, itemId) {
  currentStopAt = endAt;
  setCurrentItem(itemId);

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

function playItem(itemId, withPrompt) {
  const item = findItem(itemId);
  if (!item) return;

  const promptStartAvailable =
    withPrompt &&
    item.prev_teacher_start !== null &&
    Number.isFinite(Number(item.prev_teacher_start)) &&
    Number(item.prev_teacher_start) < item.end;
  const startAt = promptStartAvailable ? Number(item.prev_teacher_start) : item.start;
  startPlayback(startAt, item.end, item.id);
}

async function loadStudentSpeech(lesson) {
  const unit = currentUnit();
  const response = await fetch(`/api/student-speech?unit=${encodeURIComponent(unit)}&lesson=${encodeURIComponent(lesson)}`);
  if (!response.ok) {
    turnList.innerHTML = `<article class="empty-card">Student ${unit} を読み込めませんでした。</article>`;
    return;
  }

  speechData = await response.json();
  audioPlayer.src = `/api/audio?unit=${encodeURIComponent(unit)}&lesson=${encodeURIComponent(speechData.name)}`;
  currentStopAt = null;
  currentItemId = null;
  renderItems();

  const url = new URL(window.location.href);
  url.searchParams.set("unit", unit);
  url.searchParams.set("lesson", speechData.name);
  window.history.replaceState({}, "", url);
}

async function loadStudentTurnList() {
  const unit = currentUnit();
  const response = await fetch(`/api/lessons?unit=${encodeURIComponent(unit)}`);
  const payload = await response.json();
  const items = payload.items || [];

  if (items.length === 0) {
    lessonSelect.innerHTML = `<option value="">No lesson found</option>`;
    turnList.innerHTML = `<article class="empty-card">poc/output/&lt;lesson&gt;/${unit === "utterance" ? "merged.student_utterances.json" : "merged.student_turns.json"} がありません。</article>`;
    speechData = null;
    updateSummary();
    return;
  }

  lessonSelect.innerHTML = items
    .map((item) => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)}</option>`)
    .join("");

  const url = new URL(window.location.href);
  const selectedName = url.searchParams.get("lesson");
  const initialName = items.some((item) => item.name === selectedName) ? selectedName : items[0].name;
  lessonSelect.value = initialName;
  await loadStudentSpeech(initialName);
}

loadButton.addEventListener("click", async () => {
  if (!lessonSelect.value) return;
  await loadStudentSpeech(lessonSelect.value);
});

unitSelect.addEventListener("change", async () => {
  await loadStudentTurnList();
});

turnList.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLButtonElement)) return;

  const turnId = target.dataset.itemId;
  const action = target.dataset.action;
  if (!turnId || !action) return;

  if (action === "play-student") {
    playItem(turnId, false);
    return;
  }

  if (action === "play-prompt") {
    playItem(turnId, true);
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

const initialUrl = new URL(window.location.href);
const initialUnit = initialUrl.searchParams.get("unit");
if (initialUnit === "turn" || initialUnit === "utterance") {
  unitSelect.value = initialUnit;
}

loadStudentTurnList().catch(() => {
  turnList.innerHTML = `<article class="empty-card">初期化に失敗しました。</article>`;
});
