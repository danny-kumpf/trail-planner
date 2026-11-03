// Shared by the Fitness tab and its effort pages: drawing the server's figures, and formatting.

const darkScheme = window.matchMedia("(prefers-color-scheme: dark)");
// Every figure drawn so far, as [element, {light, dark}], redrawn when the color scheme changes.
const drawnFigures = [];

function drawFigure(element, figure) {
  const dark = darkScheme.matches;
  // The dark figure carries only styling; its data comes from the light one.
  const data = dark ? figure.light.data.map((trace, i) => ({ ...trace, ...figure.dark.data[i] })) : figure.light.data;
  const layout = dark ? figure.dark.layout : figure.light.layout;
  return Plotly.react(element, data, layout, { responsive: true, displayModeBar: false });
}

darkScheme.addEventListener("change", () => {
  for (const [element, figure] of drawnFigures) drawFigure(element, figure);
});

function fitnessEl(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

// The titles of collapsible panels opened on this page, so they stay open when the page redraws
// them, e.g. after a refit.
const openPanelTitles = new Set();

// Appends a panel ({title, caption, figure, starts_collapsed}) to `container` and draws it; resolves
// to the plot element. A panel that starts collapsed is drawn when first opened, as a hidden plot
// can't be sized; it resolves to undefined.
function appendPanel(container, panel) {
  const plot = fitnessEl("div", undefined, "plot");
  const caption = fitnessEl("p", panel.caption, "caption");
  if (!panel.starts_collapsed) {
    const section = fitnessEl("section", undefined, "panel");
    section.append(fitnessEl("h2", panel.title), caption, plot);
    container.append(section);
    drawnFigures.push([plot, panel.figure]);
    return drawFigure(plot, panel.figure);
  }
  const details = fitnessEl("details", undefined, "panel");
  const summary = fitnessEl("summary");
  summary.append(fitnessEl("h2", panel.title));
  details.append(summary, caption, plot);
  container.append(details);
  let drawn = false;
  details.addEventListener("toggle", () => {
    if (details.open) openPanelTitles.add(panel.title);
    else openPanelTitles.delete(panel.title);
    if (details.open && !drawn) {
      drawn = true;
      drawnFigures.push([plot, panel.figure]);
      drawFigure(plot, panel.figure);
    }
  });
  details.open = openPanelTitles.has(panel.title);
  return Promise.resolve(undefined);
}

const fmtRate = (ftPerHr) => Math.round(ftPerHr).toLocaleString() + " ft/hr";
const fmtFt = (ft) => Math.round(ft).toLocaleString() + " ft";
const fmtSignedPct = (frac) => (frac >= 0 ? "+" : "−") + Math.abs(frac * 100).toFixed(1) + "%";
const fmtSigned = (value, digits = 0) =>
  (value >= 0 ? "+" : "−") + Math.abs(value).toLocaleString(undefined, { maximumFractionDigits: digits });
const fmtMinutes = (seconds) => {
  const minutes = Math.round(seconds / 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m` : `${minutes} min`;
};
const fmtDate = (isoDateTime) =>
  new Date(isoDateTime).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });

function standardConditionsText(standard) {
  return `${Math.round(standard.heart_rate_bpm)} bpm, ${fmtFt(standard.elevation_ft)}, ${Math.round(standard.grade_frac * 100)}% grade`;
}

// The model's slopes, in the units they're easiest to read in.
function modelSlopesText(model) {
  return [
    `${fmtSigned(model.hr_slope_ft_per_hr_per_bpm, 1)} ft/hr per bpm`,
    `${fmtSigned(model.elevation_slope_ft_per_hr_per_ft * 1000)} per 1,000 ft`,
    `${fmtSigned(model.grade_slope_ft_per_hr_per_frac / 100, 1)} per 1% grade`,
  ].join(" · ");
}

// Why an effort's comparison may be unreliable, or "" if it isn't.
function elevationWarning(effort) {
  const frac = effort.outside_model_elevations_frac;
  if (!frac) return "";
  return `${Math.round(frac * 100)}% of its climbing is at elevations the model it's compared with wasn't fit on, ` +
         "so the comparison extrapolates there.";
}
