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

// A figure's caption and plot, and the [plot element, figure] to draw.
function figureNodes(panel) {
  const plot = fitnessEl("div", undefined, "plot");
  return { nodes: [fitnessEl("p", panel.caption, "caption"), plot], toDraw: [plot, panel.figure] };
}

// Appends a panel to `container` and draws it. A panel is one figure ({title, caption, figure}),
// or a group of them under one heading ({title, panels}), each with its own title and caption.
// Resolves to the plot elements, in order. A panel that starts collapsed ({starts_collapsed}) is
// drawn when first opened, as a hidden plot can't be sized; it resolves to [].
function appendPanel(container, panel) {
  const body = [];
  const toDraw = [];
  if (panel.panels) {
    for (const member of panel.panels) {
      const figure = figureNodes(member);
      const section = fitnessEl("section", undefined, "subpanel");
      section.append(fitnessEl("h3", member.title), ...figure.nodes);
      body.push(section);
      toDraw.push(figure.toDraw);
    }
  } else {
    const figure = figureNodes(panel);
    body.push(...figure.nodes);
    toDraw.push(figure.toDraw);
  }
  const drawAll = () => {
    drawnFigures.push(...toDraw);
    return Promise.all(toDraw.map(([plot, figure]) => drawFigure(plot, figure)));
  };

  if (!panel.starts_collapsed) {
    const section = fitnessEl("section", undefined, "panel");
    section.append(fitnessEl("h2", panel.title), ...body);
    container.append(section);
    return drawAll();
  }
  const details = fitnessEl("details", undefined, "panel");
  const summary = fitnessEl("summary");
  summary.append(fitnessEl("h2", panel.title));
  details.append(summary, ...body);
  container.append(details);
  let drawn = false;
  details.addEventListener("toggle", () => {
    if (details.open) openPanelTitles.add(panel.title);
    else openPanelTitles.delete(panel.title);
    if (details.open && !drawn) {
      drawn = true;
      drawAll();
    }
  });
  details.open = openPanelTitles.has(panel.title);
  return Promise.resolve([]);
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

function gaitText(runningFrac) {
  if (runningFrac === 0) return "hiking";
  if (runningFrac === 1) return "running";
  return `${Math.round(runningFrac * 100)}% running`;
}

function standardConditionsText(standard) {
  return `${Math.round(standard.heart_rate_bpm)} bpm, ${fmtFt(standard.elevation_ft)}, ` +
         `${Math.round(standard.grade_frac * 100)}% grade, ${gaitText(standard.running_frac)}`;
}

// The model's slopes at the standard grade and gait, in the units they're easiest to read in.
function modelSlopesText(model) {
  return [
    `${fmtSigned(model.hr_slope_ft_per_hr_per_bpm, 1)} ft/hr per bpm`,
    `${fmtSigned(model.elevation_slope_ft_per_hr_per_ft * 1000)} per 1,000 ft`,
  ].join(" · ");
}

// Why an effort's comparison may be unreliable, or "" if it isn't.
function elevationWarning(effort) {
  const frac = effort.outside_model_elevations_frac;
  if (!frac) return "";
  return `${Math.round(frac * 100)}% of its climbing is at elevations the model it's compared with wasn't fit on, ` +
         "so the comparison extrapolates there.";
}
