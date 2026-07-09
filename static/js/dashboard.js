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

function saveEmail(email) {
  localStorage.setItem("ielts_mock_email", email.trim().toLowerCase());
}

function getSavedEmail() {
  return localStorage.getItem("ielts_mock_email") || "";
}

async function loadDashboard(email) {
  const cleanEmail = email.trim().toLowerCase();
  const token = localStorage.getItem("ielts_token");

  if (!cleanEmail || !cleanEmail.includes("@")) {
    showMessage("Iltimos, to'g'ri email kiriting.", "warning");
    return;
  }
  if (!token) {
    showMessage("Dashboard uchun avval profilingizga kiring.", "warning");
    return;
  }

  saveEmail(cleanEmail);
  showMessage("Dashboard yuklanmoqda...", "muted");

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

function showMessage(message, type = "muted") {
  const box = $("#message");
  if (!box) return;

  box.className = `badge ${type}`;
  box.textContent = message;
}

function renderDashboard(data) {
  const profileName =
    data.profile.full_name || data.profile.username || data.profile.email;

  $("#profileName").textContent = profileName;
  $("#profileEmail").textContent = data.profile.email;
  $("#overallBand").textContent = bandText(data.overview.overall_band);
  $("#totalAttempts").textContent = data.overview.total_attempts;
  $("#completedSections").textContent = `${data.overview.completed_sections}/4`;
  $("#verified").textContent = data.profile.email_verified ? "Verified" : "Demo";

  const sectionGrid = $("#sectionGrid");

  sectionGrid.innerHTML = data.sections
    .map((item) => {
      const latest = item.latest;
      const score =
        latest && latest.score !== null ? `${latest.score}/${latest.total}` : "—";
      const band = latest ? bandText(latest.band) : "—";
      const badge = latest ? "success" : "muted";
      const status = latest ? "Topshirildi" : "Boshlanmagan";

      return `
        <article class="card section-card">
          <span class="badge ${badge}">${status}</span>
          <h3>${sectionLabels[item.section] || item.section}</h3>
          <div class="band">${band}</div>
          <p>So'nggi natija: <strong>${score}</strong></p>
          <p>Urinishlar: <strong>${item.attempts}</strong></p>
          <a class="btn ghost" href="/tests">Bo'limni ochish</a>
        </article>
      `;
    })
    .join("");

  const historyBody = $("#historyBody");

  if (!data.history.length) {
    historyBody.innerHTML = `
      <tr>
        <td colspan="5">
          <div class="empty-state">
            Hali natija yo'q. Test-bankdan mock boshlang.
          </div>
        </td>
      </tr>
    `;
    return;
  }

  historyBody.innerHTML = data.history
    .map(
      (row) => `
        <tr>
          <td>${sectionLabels[row.section] || row.section}</td>
          <td>${row.score === null ? "—" : `${row.score}/${row.total}`}</td>
          <td>${bandText(row.band)}</td>
          <td>${formatDate(row.submitted_at)}</td>
          <td>${row.writing_feedback || "—"}</td>
        </tr>
      `
    )
    .join("");
}

window.addEventListener("DOMContentLoaded", () => {
  const input = $("#emailInput");

  input.value = getSavedEmail();

  $("#loadBtn").addEventListener("click", () => {
    loadDashboard(input.value).catch((error) =>
      showMessage(error.message, "warning")
    );
  });

  if (input.value) {
    loadDashboard(input.value).catch((error) =>
      showMessage(error.message, "warning")
    );
  }
});
