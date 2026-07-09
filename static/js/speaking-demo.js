const prompts = [
  {
    part: "Part 1",
    title: "Introduction and interview",
    time: 240,
    questions: [
      "Do you work or study?",
      "What subject do you enjoy most?",
      "How often do you use English outside class?",
      "Do you prefer studying alone or with friends? Why?",
    ],
  },
  {
    part: "Part 2",
    title: "Cue card",
    time: 120,
    questions: [
      "Describe a useful website or app you use for studying.",
      "You should say: what it is, how often you use it, what you use it for, and explain why it is useful.",
    ],
  },
  {
    part: "Part 3",
    title: "Discussion",
    time: 300,
    questions: [
      "How has technology changed education?",
      "Should schools use more online tests?",
      "What are the disadvantages of learning only through digital platforms?",
    ],
  },
];

let current = 0;
let secondsLeft = prompts[0].time;
let timerId = null;

function renderPrompt() {
  const prompt = prompts[current];

  secondsLeft = prompt.time;

  document.querySelector("#partBadge").textContent = prompt.part;
  document.querySelector("#promptTitle").textContent = prompt.title;

  document.querySelector("#promptQuestions").innerHTML = prompt.questions
    .map((question) => `<li>${question}</li>`)
    .join("");

  updateTimer();
}

function updateTimer() {
  const minutes = String(Math.floor(secondsLeft / 60)).padStart(2, "0");
  const seconds = String(secondsLeft % 60).padStart(2, "0");

  document.querySelector("#timer").textContent = `${minutes}:${seconds}`;
}

function startTimer() {
  clearInterval(timerId);

  timerId = setInterval(() => {
    secondsLeft = Math.max(0, secondsLeft - 1);
    updateTimer();

    if (secondsLeft === 0) {
      clearInterval(timerId);
    }
  }, 1000);
}

function nextPrompt() {
  current = (current + 1) % prompts.length;

  clearInterval(timerId);
  renderPrompt();
}

function savePracticeNote() {
  const note = document.querySelector("#selfFeedback").value.trim();

  const data = JSON.parse(
    localStorage.getItem("ielts_mock_speaking_notes") || "[]"
  );

  data.unshift({
    test: localStorage.getItem("ielts_mock_selected_test") || "demo",
    part: prompts[current].part,
    note,
    saved_at: new Date().toISOString(),
  });

  localStorage.setItem(
    "ielts_mock_speaking_notes",
    JSON.stringify(data.slice(0, 20))
  );

  document.querySelector("#saveStatus").textContent =
    "Self-feedback saqlandi.";
}

window.addEventListener("DOMContentLoaded", () => {
  renderPrompt();

  document.querySelector("#startBtn").addEventListener("click", startTimer);
  document.querySelector("#nextBtn").addEventListener("click", nextPrompt);
  document.querySelector("#saveBtn").addEventListener("click", savePracticeNote);
});
