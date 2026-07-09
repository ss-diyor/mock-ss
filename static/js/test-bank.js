const sectionLabels = {
  listening: "Listening",
  reading: "Reading",
  writing: "Writing",
  speaking: "Speaking",
};

async function loadTests() {
  const container = document.querySelector("#testsGrid");

  container.innerHTML = `
    <div class="empty-state">Testlar yuklanmoqda...</div>
  `;

  const response = await fetch("/api/tests");

  if (!response.ok) {
    throw new Error("Test-bank olinmadi.");
  }

  const data = await response.json();

  container.innerHTML = data.tests.map(renderTestCard).join("");
}

function renderTestCard(test) {
  const statusClass =
    test.status === "ready"
      ? "success"
      : test.status === "planned"
      ? "warning"
      : "muted";

  const sectionButtons = test.sections
    .map((section) => {
      const isPlanned = test.status === "planned";
      const href = isPlanned ? "#" : section.route;
      const lockedClass = isPlanned ? "locked" : "";

      return `
        <a 
          class="section-link ${lockedClass}" 
          href="${href}" 
          onclick="${
            isPlanned
              ? "return false;"
              : `saveSelectedTest('${test.id}', '${section.key}')`
          }"
        >
          <span>
            <strong>${sectionLabels[section.key] || section.title}</strong>
            <small>
              ${section.duration_minutes} daqiqa · ${section.questions} savol
            </small>
          </span>
          <span>${isPlanned ? "Soon" : "Open"}</span>
        </a>
      `;
    })
    .join("");

  return `
    <article class="card test-card">
      <div>
        <span class="badge ${statusClass}">${test.status}</span>
        <h3 style="margin-top:12px">${test.title}</h3>
        <p>${test.description}</p>
      </div>

      <div class="section-buttons">
        ${sectionButtons}
      </div>
    </article>
  `;
}

function saveSelectedTest(testId, section) {
  localStorage.setItem("ielts_mock_selected_test", testId);
  localStorage.setItem("ielts_mock_selected_section", section);
  return true;
}

window.addEventListener("DOMContentLoaded", () => {
  loadTests().catch((error) => {
    document.querySelector("#testsGrid").innerHTML = `
      <div class="empty-state">${error.message}</div>
    `;
  });
});
