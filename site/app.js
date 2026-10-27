const SEARCH_FIELDS = [
  "description",
  "manufacturer",
  "mpn",
  "package",
];

const state = {
  rows: [],
  filtered: [],
  search: "",
  manufacturer: "",
  package: "",
  sortKey: "mpn",
  sortDir: "asc",
};

const searchInput = document.getElementById("search");
const manufacturerSelect = document.getElementById("filter-manufacturer");
const packageSelect = document.getElementById("filter-package");
const tbody = document.getElementById("parts-body");
const count = document.getElementById("result-count");
const summary = document.getElementById("summary");
const footerSummary = document.getElementById("footer-summary");

function compareTies(a, b) {
  const part = String(a.part_id).localeCompare(String(b.part_id), undefined, {
    sensitivity: "base",
  });
  if (part !== 0) return part;
  return String(a.mpn).localeCompare(String(b.mpn), undefined, {
    sensitivity: "base",
  });
}

function compareRows(a, b) {
  const key = state.sortKey;
  const direction = state.sortDir === "asc" ? 1 : -1;
  const av = a[key];
  const bv = b[key];

  if (av == null && bv == null) return compareTies(a, b);
  if (av == null) return 1;
  if (bv == null) return -1;

  const result = String(av).localeCompare(String(bv), undefined, {
    sensitivity: "base",
    numeric: true,
  });
  if (result !== 0) return result * direction;
  return compareTies(a, b);
}

function rowMatches(row) {
  if (state.manufacturer && row.manufacturer !== state.manufacturer) {
    return false;
  }
  if (state.package && row.package !== state.package) {
    return false;
  }
  if (state.search) {
    const haystack = SEARCH_FIELDS.map((field) => row[field] ?? "")
      .join(" ")
      .toLocaleLowerCase();
    if (!haystack.includes(state.search)) return false;
  }
  return true;
}

function uniqueValues(field) {
  return [...new Set(state.rows.map((row) => row[field]).filter(Boolean))]
    .map((value) => String(value))
    .sort((a, b) =>
      String(a).localeCompare(String(b), undefined, {
        sensitivity: "base",
        numeric: true,
      }),
    );
}

function populateFilters() {
  for (const [select, field, label] of [
    [manufacturerSelect, "manufacturer", "manufacturers"],
    [packageSelect, "package", "packages"],
  ]) {
    const all = document.createElement("option");
    all.value = "";
    all.textContent = `All ${label}`;
    const options = uniqueValues(field).map((value) => {
      const option = document.createElement("option");
      option.value = String(value);
      option.textContent = String(value);
      return option;
    });
    select.replaceChildren(all, ...options);
  }
}

function updateSummary() {
  const partCount = new Set(state.rows.map((row) => row.part_id)).size;
  const text = `${partCount} parts · ${state.rows.length} MPNs`;
  summary.textContent = text;
  footerSummary.textContent = text;
}

function updateCount() {
  count.textContent = `Showing ${state.filtered.length} of ${state.rows.length} MPNs`;
}

function updateSortIndicators() {
  for (const th of document.querySelectorAll("th[data-key]")) {
    th.setAttribute(
      "aria-sort",
      th.dataset.key === state.sortKey
        ? state.sortDir === "asc"
          ? "ascending"
          : "descending"
        : "none",
    );
  }
}

function cell(text, className) {
  const td = document.createElement("td");
  if (className) td.className = className;
  td.textContent = text;
  return td;
}

function datasheetCell(value, mpn) {
  const td = document.createElement("td");
  if (!value) {
    td.textContent = "—";
    return td;
  }
  const link = document.createElement("a");
  link.href = value;
  link.textContent = "Datasheet";
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.setAttribute("aria-label", `Datasheet for ${mpn} (opens in a new tab)`);
  td.append(link);
  return td;
}

function render() {
  state.filtered = state.rows.filter(rowMatches).sort(compareRows);
  tbody.replaceChildren();

  if (state.filtered.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.className = "empty";
    td.textContent = "No matching parts";
    tr.append(td);
    tbody.append(tr);
  } else {
    const fragment = document.createDocumentFragment();
    for (const row of state.filtered) {
      const tr = document.createElement("tr");
      tr.append(
        cell(row.mpn, "mono"),
        cell(row.description),
        cell(row.manufacturer || "—"),
        cell(row.package || "—"),
        datasheetCell(row.datasheet, row.mpn),
      );
      fragment.append(tr);
    }
    tbody.append(fragment);
  }

  updateCount();
  updateSortIndicators();
}

function showError(error) {
  tbody.replaceChildren();
  const tr = document.createElement("tr");
  const td = document.createElement("td");
  td.colSpan = 5;
  td.className = "error";
  td.textContent = `Could not load parts.json (${error.message}). `;
  const link = document.createElement("a");
  link.href = "https://github.com/sid115/kicad-libs/blob/master/parts.yaml";
  link.textContent = "View parts.yaml";
  td.append(link);
  tr.append(td);
  tbody.append(tr);
  count.textContent = "Parts could not be loaded";
  summary.textContent = "Parts could not be loaded";
  footerSummary.textContent = "Parts could not be loaded";
}

searchInput.addEventListener("input", () => {
  state.search = searchInput.value.trim().toLocaleLowerCase();
  render();
});

manufacturerSelect.addEventListener("change", () => {
  state.manufacturer = manufacturerSelect.value;
  render();
});

packageSelect.addEventListener("change", () => {
  state.package = packageSelect.value;
  render();
});

for (const button of document.querySelectorAll(".sort")) {
  button.addEventListener("click", () => {
    const key = button.closest("th").dataset.key;
    if (state.sortKey === key) {
      state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
    } else {
      state.sortKey = key;
      state.sortDir = "asc";
    }
    render();
  });
}

async function init() {
  try {
    const response = await fetch("parts.json", { cache: "no-cache" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!Array.isArray(data)) throw new Error("unexpected data format");
    state.rows = data;
    populateFilters();
    updateSummary();
    render();
  } catch (error) {
    showError(error);
  }
}

init();
