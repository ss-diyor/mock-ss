const $ = (selector) => document.querySelector(selector);

const sectionLabels = {
  listening: "Listening",
  reading: "Reading",
  writing: "Writing",
  speaking: "Speaking",
};

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("uz-UZ");
}

function bandText(value) {
  return value === null || value === undefined ? "—" : Number(value).toFixed(1);
}

function showMessage(message, type = "info") {
  const box = $("#message-box");
  if (!box) return;
  if (!message) {
    box.style.display = "none";
    return;
  }
  box.className = `alert ${type}`;
  box.textContent = message;
  box.style.display = "block";
}

async function loadDashboard() {
  const token = localStorage.getItem("ielts_token");

  if (!token) {
    $("#auth-required").style.display = "block";
    $("#dash-content").style.display = "none";
    return;
  }

  $("#auth-required").style.display = "none";
  $("#dash-content").style.display = "block";
  showMessage("Dashboard yuklanmoqda...", "info");

  const response = await fetch("/api/dashboard", {
    headers: { Authorization: `Bearer ${token}` }
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || "Dashboard ma'lumotlari olinmadi.");
  }

  const data = await response.json();
  renderDashboard(data);
  showMessage(data.overview.recommendation, "success");
}

function renderDashboard(data) {
  const profileName =
    data.profile.full_name || data.profile.username || data.profile.email;

  $("#profileName").textContent = profileName;
  $("#profileEmail").textContent = data.profile.email;
  $("#overallBand").textContent = bandText(data.overview.overall_band);
  $("#totalAttempts").textContent = data.overview.total_attempts;
  $("#completedSections").textContent = `${data.overview.completed_sections}/4`;

  const statusBadge = $("#statusBadge");
  if (data.profile.email_verified) {
    statusBadge.textContent = "Verified";
    statusBadge.className = "badge success";
  } else {
    statusBadge.textContent = "Email tasdiqlanmagan";
    statusBadge.className = "badge warning";
  }

  const sectionGrid = $("#sectionGrid");

  sectionGrid.innerHTML = data.sections
    .map((item) => {
      const latest = item.latest;
      const score =
        latest && latest.score !== null && latest.score !== undefined
          ? `${latest.score}/${latest.total}`
          : "—";
      const band = latest ? bandText(latest.band) : "—";
      const status = latest ? "Topshirildi" : "Boshlanmagan";
      const badgeClass = latest ? "success" : "muted";

      return `
        <div class="section-card">
          <span class="badge ${badgeClass}" style="align-self:flex-start;">${status}</span>
          <div class="section-title">${sectionLabels[item.section] || item.section}</div>
          <div class="section-band">${band}</div>
          <div class="section-meta">So'nggi natija: ${score}</div>
          <div class="section-meta">Urinishlar: ${item.attempts}</div>
          <a href="/tests">Bo'limni ochish</a>
        </div>
      `;
    })
    .join("");

  const historyList = $("#historyList");

  if (!data.history.length) {
    historyList.innerHTML = `<div class="empty-state">Hali natija yo'q. Test-bankdan mock boshlang.</div>`;
    return;
  }

  historyList.innerHTML = data.history
    .map(
      (row) => `
        <div class="history-row">
          <span class="history-section">${sectionLabels[row.section] || row.section}</span>
          <span>${row.score === null || row.score === undefined ? "—" : `${row.score}/${row.total}`}</span>
          <span class="history-band">${bandText(row.band)}</span>
          <span class="history-date">${formatDate(row.submitted_at)}</span>
        </div>
      `
    )
    .join("");
}

window.addEventListener("DOMContentLoaded", () => {
  loadDashboard().catch((error) => showMessage(error.message, "danger"));
});
