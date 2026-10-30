// Calendar days and weeks, shared by the Trail Planning and Training Log tabs.

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const SATURDAY = 6;

// ---- Calendar days ----
// A day is a whole number counted from 1970-01-01. Dates are plain calendar dates with no time
// zone, so stepping by days is integer arithmetic and never trips over daylight saving.
const MS_PER_DAY = 86_400_000;
const dayFromDate = (year, month, date) => Date.UTC(year, month - 1, date) / MS_PER_DAY;  // month is 1–12
const dateOfDay = (day) => {
  const d = new Date(day * MS_PER_DAY);
  return { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, date: d.getUTCDate() };
};
const weekdayOfDay = (day) => new Date(day * MS_PER_DAY).getUTCDay();  // 0 is Sunday
// "2026-10-17" and back.
const dayOfIsoDate = (iso) => dayFromDate(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)), Number(iso.slice(8, 10)));
const isoDateOfDay = (day) => new Date(day * MS_PER_DAY).toISOString().slice(0, 10);
const today = (() => {
  const now = new Date();
  return dayFromDate(now.getFullYear(), now.getMonth() + 1, now.getDate());
})();
const firstDayOfWeek = (day, weekStartWeekday) => day - ((weekdayOfDay(day) - weekStartWeekday + 7) % 7);
const firstDayOfMonth = (year, month) => dayFromDate(year, month, 1);
const lastDayOfMonth = (year, month) => dayFromDate(year, month + 1, 1) - 1;

function fmtDayRange(firstDay, lastDay) {
  const first = dateOfDay(firstDay);
  const last = dateOfDay(lastDay);
  const thisYear = dateOfDay(today).year;
  const withYear = (d, text) => (d.year === thisYear ? text : `${text}, ${d.year}`);
  if (first.year !== last.year) {
    return `${withYear(first, `${MONTH_NAMES[first.month - 1]} ${first.date}`)} – ` +
           withYear(last, `${MONTH_NAMES[last.month - 1]} ${last.date}`);
  }
  const lastText = first.month === last.month ? `${last.date}` : `${MONTH_NAMES[last.month - 1]} ${last.date}`;
  return withYear(last, `${MONTH_NAMES[first.month - 1]} ${first.date} – ${lastText}`);
}

// ---- Weeks ----
// The weekday weeks start on is chosen on the Training Log and remembered in the browser.
const WEEK_START_STORAGE_KEY = "trainingLogWeekStartWeekday";

function loadWeekStartWeekday() {
  try {
    const stored = localStorage.getItem(WEEK_START_STORAGE_KEY);
    return stored === null ? SATURDAY : Number(stored);
  } catch {
    return SATURDAY;
  }
}

function saveWeekStartWeekday(weekday) {
  try {
    localStorage.setItem(WEEK_START_STORAGE_KEY, String(weekday));
  } catch {
    // Storage unavailable: the choice still holds until the page is reloaded.
  }
}

// The link to the Trail Planning tab for the week starting on `firstDay`.
const planningUrlForWeek = (firstDay) => `/trail-planning?week=${isoDateOfDay(firstDay)}`;
