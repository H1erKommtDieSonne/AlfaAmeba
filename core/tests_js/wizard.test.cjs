"use strict";

// Regression harness for core/static/core/js/app.js. No packages: plain Node CJS
// with minimal Alpine/DOM/fetch stubs, so the real component factories are exercised.
const assert = require("node:assert/strict");
const path = require("node:path");

const registry = {};
let alpineInit = null;
const byId = new Map();

global.document = {
  addEventListener(type, callback) {
    if (type === "alpine:init") alpineInit = callback;
  },
  getElementById(id) {
    return byId.get(id) || null;
  },
};

global.Alpine = {
  data(name, factory) {
    registry[name] = factory;
  },
};

global.FormData = class FormDataStub {
  constructor(form) {
    this.form = form;
  }
  get(name) {
    return Object.prototype.hasOwnProperty.call(this.form.fields, name) ? this.form.fields[name] : null;
  }
};

require(path.join(__dirname, "..", "static", "core", "js", "app.js"));
if (typeof alpineInit !== "function") throw new Error("alpine:init handler was not registered");
alpineInit();

function makeTick() {
  const queue = [];
  return {
    $nextTick(callback) {
      queue.push(callback);
    },
    flush() {
      while (queue.length) queue.shift()();
    },
  };
}

function makeField({ valid = true, id = "field" } = {}) {
  const calls = [];
  return {
    id,
    calls,
    checkValidity: () => valid,
    reportValidity() {
      calls.push("report");
    },
    focus() {
      calls.push("focus");
    },
    scrollIntoView() {
      calls.push("scroll");
    },
    setAttribute() {},
    getAttribute() {
      return null;
    },
  };
}

function makeSection(step, fields = []) {
  return {
    dataset: { formStep: String(step) },
    querySelectorAll: () => fields,
  };
}

function makeWizardForm(sections) {
  return {
    querySelector(selector) {
      const match = /data-form-step="(\d+)"/.exec(selector);
      if (match) return sections[match[1]] || null;
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
}

function makeWizard(step, sections) {
  const wizard = registry.applicationWizard(step, "working-capital");
  const tick = makeTick();
  wizard.$refs = {
    form: makeWizardForm(sections),
    stageHeading: { focus() {}, scrollIntoView() {} },
    errorSummary: { focus() {} },
  };
  wizard.$nextTick = tick.$nextTick;
  wizard.$dispatch = () => {};
  wizard.adviceState = { loading: false, error: "", selectionMatches: true, itemsCount: 1 };
  wizard._tick = tick;
  return wizard;
}

function makeAdviceForm(fields) {
  return {
    fields,
    dataset: { adviceUrl: "/financing-advice/" },
    elements: {},
    querySelector: () => null,
    querySelectorAll: () => [],
  };
}

function makeAdvisor(form) {
  const advisor = registry.needsAdvisor();
  advisor.formElement = form;
  advisor.$dispatch = () => {};
  advisor.$nextTick = (callback) => callback();
  return advisor;
}

function makeFile(name, size) {
  return { name, size };
}

function makeRetained(name, size, id = name) {
  return { dataset: { documentId: id, name, size: String(size) }, textContent: name };
}

function makeFileGroup({ code = "statement", label = "Выписка", fresh = [], retained = [], disabled = false } = {}) {
  const input = {
    type: "file",
    disabled,
    files: fresh,
    getAttribute() { return ".pdf,.png,.jpg,.jpeg,.xlsx,.csv,.docx"; },
    dispatchEvent() {},
  };
  return {
    dataset: { documentCode: code },
    input,
    classList: { toggle() {} },
    setAttribute() {},
    querySelector(selector) {
      if (selector === 'input[type="file"]') return input;
      if (selector === "legend") return { textContent: label };
      return null;
    },
    querySelectorAll(selector) {
      return selector === "[data-retained-file]" ? retained : [];
    },
  };
}

function makeDocumentForm({ groups = [], generic = null, removed = [] } = {}) {
  const form = {
    dataset: {
      adviceUrl: "/financing-advice/",
      maxFiles: "5",
      maxFileSize: String(10 * 1024 * 1024),
      maxTotalSize: String(50 * 1024 * 1024),
      accept: ".pdf,.png,.jpg,.jpeg,.xlsx,.csv,.docx",
    },
    elements: {},
    querySelectorAll(selector) {
      if (selector === "[data-document-code]") return groups;
      if (selector === 'input[name="remove_document_ids"]:checked') return removed;
      return [];
    },
    querySelector(selector) {
      if (selector === "[data-document-generic]") return generic;
      if (selector === 'input[type="file"]') return groups[0]?.input || generic?.input || null;
      const codeMatch = /^\[data-document-code="(.+)"\]$/.exec(selector);
      if (codeMatch) return groups.find((group) => group.dataset.documentCode === codeMatch[1]) || null;
      return null;
    },
  };
  return form;
}

function makeSummaryForm({ groups = [] } = {}) {
  const fields = {
    organization_name: { value: "ООО Север" },
    inn: { value: "7707083893" },
    amount: { value: "1500000.00" },
    term_months: { value: "12" },
    purpose: { value: "Закупка товара" },
  };
  let productName = "Оборотный кредит";
  const radio = {
    closest() { return { querySelector() { return { textContent: productName }; } }; },
    setProductName(value) { productName = value; },
  };
  return {
    dataset: {},
    elements: { namedItem: (name) => fields[name] || null },
    fields,
    radio,
    querySelector(selector) {
      if (selector === 'input[name="product"]:checked') return radio;
      if (selector === 'select[name="product"]') return null;
      if (selector === 'input[type="file"]') return groups[0]?.input || null;
      return null;
    },
    querySelectorAll(selector) {
      return selector === "[data-document-code]" ? groups : [];
    },
  };
}

const tests = [];
function test(name, fn) {
  tests.push({ name, fn });
}

test("final submit reveals hidden invalid step before report/focus", () => {
  const invalid = makeField({ valid: false, id: "id_amount" });
  const wizard = makeWizard(4, {
    1: makeSection(1, []),
    2: makeSection(2, [invalid]),
    3: makeSection(3, []),
  });
  let prevented = false;
  wizard.handleSubmit({ preventDefault: () => { prevented = true; } });

  assert.equal(prevented, true, "submit must be prevented");
  assert.equal(wizard.step, 2, "invalid step must be revealed first");
  assert.deepEqual(invalid.calls, [], "no report/focus while section is still hidden");
  wizard._tick.flush();
  assert.deepEqual(invalid.calls, ["report", "focus", "scroll"], "after tick native validation runs");
});

test("advice guard blocks progress and submit until selection matches", () => {
  const wizard = makeWizard(2, { 1: makeSection(1), 2: makeSection(2), 3: makeSection(3) });
  wizard.adviceState = { loading: true, error: "", selectionMatches: false, itemsCount: 0 };
  wizard.goTo(4);
  assert.equal(wizard.step, 2, "step 4 must not open while advice is loading");
  assert.ok(wizard.guardMessage.length > 0, "guard must explain the block");

  let prevented = false;
  wizard.handleSubmit({ preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(wizard.step, 2);

  wizard.adviceState = { loading: false, error: "", selectionMatches: true, itemsCount: 1 };
  wizard.goTo(4);
  assert.equal(wizard.step, 4, "valid advice lets the wizard advance");
});

test("double submit is ignored while pending", () => {
  const wizard = makeWizard(4, { 1: makeSection(1), 2: makeSection(2), 3: makeSection(3) });
  let prevented = false;
  wizard.handleSubmit({ preventDefault: () => { prevented = true; } });
  assert.equal(wizard.submitting, true);
  assert.equal(prevented, false, "first valid submit is allowed");
  wizard.handleSubmit({ preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true, "second submit must be prevented");
});

test("advice dedupes identical params and keeps newest result", async () => {
  const form = makeAdviceForm({ need: "working" });
  const advisor = makeAdvisor(form);
  let calls = 0;
  global.fetch = async () => {
    calls += 1;
    return { ok: true, json: async () => ({ items: [], checklist: [], message: "" }) };
  };
  await advisor.refreshAdvice();
  await advisor.refreshAdvice();
  assert.equal(calls, 1, "identical params must not refetch");

  calls = 0;
  let resolvers = [];
  global.fetch = (url, options) =>
    new Promise((resolve, reject) => {
      calls += 1;
      options.signal.addEventListener("abort", () => {
        const error = new Error("aborted");
        error.name = "AbortError";
        reject(error);
      });
      resolvers.push((data) => resolve({ ok: true, json: async () => data }));
    });

  const first = advisor.refreshAdvice(true);
  const second = advisor.refreshAdvice(true);
  resolvers[1]({ items: [{ code: "new" }], checklist: [], message: "new" });
  await second;
  await first;
  assert.equal(advisor.advice.items[0].code, "new", "stale response must not overwrite newest");
});

test("advice error is actionable and retry refetches", async () => {
  const form = makeAdviceForm({ need: "working" });
  const advisor = makeAdvisor(form);
  let calls = 0;
  global.fetch = async () => {
    calls += 1;
    return { ok: false, json: async () => ({}) };
  };
  await advisor.refreshAdvice();
  assert.ok(advisor.adviceError.length > 0, "error must be stored");
  assert.deepEqual(advisor.advice.items, [], "stale items are cleared on error");

  global.fetch = async () => {
    calls += 1;
    return { ok: true, json: async () => ({ items: [{ code: "ok" }], checklist: [], message: "" }) };
  };
  await advisor.refreshAdvice(true);
  assert.equal(advisor.adviceError, "");
  assert.equal(advisor.advice.items[0].code, "ok");
  assert.equal(calls, 2);
});

test("summary is built from live form values, files and formatted amount", () => {
  const wizard = makeWizard(4, { 1: makeSection(1), 2: makeSection(2), 3: makeSection(3) });
  const form = makeSummaryForm({
    groups: [makeFileGroup({
      code: "statement",
      label: "Выписка",
      fresh: [makeFile("statement-1.pdf", 1024 * 1024), makeFile("statement-2.pdf", 2 * 1024 * 1024)],
    })],
  });
  wizard.$refs.form = form;
  wizard.syncSummary();

  assert.equal(wizard.review.inn, "7707083893");
  assert.equal(wizard.review.organization_name, "ООО Север");
  assert.equal(wizard.review.product, "Оборотный кредит");
  assert.equal(wizard.review.term, "12 мес.");
  assert.equal(wizard.review.purpose, "Закупка товара");
  assert.equal(wizard.review.amount.replace(/\s/g, " "), "1 500 000 ₽");
  assert.equal(wizard.review.filesSummary, "2 файла · 3 МБ");
  assert.equal(wizard.review.fileGroups[0].names, "statement-1.pdf, statement-2.pdf");

  form.fields.organization_name.value = "ООО Новое";
  form.fields.inn.value = "7801234567";
  form.fields.amount.value = "2500000";
  form.fields.purpose.value = "Новая цель";
  form.radio.setProductName("Инвестиционный кредит");
  wizard.syncSummary();
  assert.equal(wizard.review.organization_name, "ООО Новое");
  assert.equal(wizard.review.inn, "7801234567");
  assert.equal(wizard.review.product, "Инвестиционный кредит");
  assert.equal(wizard.review.amount.replace(/\s/g, " "), "2 500 000 ₽");
  assert.equal(wizard.review.purpose, "Новая цель");
  assert.equal(wizard.review.filesSummary, "2 файла · 3 МБ");
});

test("document stats count retained files, replacement and ignore disabled groups", () => {
  const statement = makeFileGroup({
    code: "statement",
    label: "Выписка",
    fresh: [makeFile("statement-1.pdf", 1024 * 1024), makeFile("statement-2.pdf", 2 * 1024 * 1024)],
    retained: [makeRetained("old-statement.pdf", 3 * 1024 * 1024)],
  });
  const estimate = makeFileGroup({
    code: "estimate",
    label: "Смета",
    fresh: [makeFile("stale.pdf", 9 * 1024 * 1024)],
    disabled: true,
  });
  const generic = makeFileGroup({
    code: "",
    label: "Дополнительные документы",
    fresh: [makeFile("annual.pdf", 4 * 1024 * 1024)],
    retained: [makeRetained("old-annual.pdf", 5 * 1024 * 1024)],
  });
  const form = makeDocumentForm({ groups: [statement, estimate], generic });
  const advisor = makeAdvisor(form);
  const stats = advisor.packageStats();

  assert.equal(stats.count, 4, "2 fresh replacing 1 retained + 1 generic fresh + 1 generic retained");
  assert.equal(stats.totalSize, 12 * 1024 * 1024);
  assert.deepEqual(stats.errors, []);
  assert.equal(stats.groups.find((group) => group.code === "statement").replaced, true);
  assert.equal(stats.groups.some((group) => group.code === "estimate"), false, "disabled irrelevant group is not submitted or counted");
});

test("document stats reject count, size, format and empty files", () => {
  const oversized = makeFile("big.pdf", 11 * 1024 * 1024);
  const invalid = makeFileGroup({
    code: "statement",
    fresh: [oversized, makeFile("old.xls", 10), makeFile("empty.pdf", 0), makeFile("without-extension", 10)],
  });
  const form = makeDocumentForm({ groups: [invalid] });
  const advisor = makeAdvisor(form);
  const stats = advisor.packageStats();

  assert.ok(stats.errors.some((message) => message.includes("10 МБ")));
  assert.ok(stats.errors.some((message) => message.includes(".xls")));
  assert.ok(stats.errors.some((message) => message.includes("пуст")));
  assert.ok(stats.errors.some((message) => message.includes("определить формат")));
  assert.equal(stats.count, 4);
});

test("checked remove_document_ids are excluded from retained stats and required state", () => {
  const retained = makeRetained("old-statement.pdf", 3 * 1024 * 1024, "doc-1");
  const statement = makeFileGroup({ code: "statement", label: "Выписка", retained: [retained] });
  const removed = [{ value: "doc-1" }];
  const form = makeDocumentForm({ groups: [statement], removed });
  const advisor = makeAdvisor(form);
  advisor.advice = { checklist: [{ code: "statement" }], items: [], selection_matches: true };

  advisor.syncTypedDocumentField("statement");
  assert.equal(statement.input.required, true, "removing the only retained typed file makes the field required");

  const stats = advisor.packageStats();
  assert.equal(stats.count, 0, "checked retained document is not carried into the new version");
  assert.equal(stats.groups[0].retained.length, 0);
  assert.deepEqual(stats.errors, []);
});

test("retained files validate extension and size like the server", () => {
  const statement = makeFileGroup({
    code: "statement",
    retained: [
      makeRetained("old-without-extension", 10, "doc-1"),
      makeRetained("old-oversize.pdf", 11 * 1024 * 1024, "doc-2"),
    ],
  });
  const advisor = makeAdvisor(makeDocumentForm({ groups: [statement] }));
  const stats = advisor.packageStats();
  assert.ok(stats.errors.some((message) => message.includes("определить формат")));
  assert.ok(stats.errors.some((message) => message.includes("10 МБ")));
});

test("irrelevant typed input stays disabled even with a server error and is not counted", () => {
  const input = { type: "file", disabled: false, required: true, files: [makeFile("stale.pdf", 1024)] };
  const group = {
    dataset: { documentCode: "estimate" },
    input,
    classList: { toggle() {} },
    setAttribute() {},
    querySelector(selector) {
      if (selector === 'input[type="file"]') return input;
      if (selector === "legend") return { textContent: "Смета" };
      if (selector === ".form-error") return { textContent: "Ошибка сервера" };
      return null;
    },
    querySelectorAll() { return []; },
  };
  const form = makeDocumentForm({ groups: [group] });
  const advisor = makeAdvisor(form);
  advisor.advice = { checklist: [{ code: "statement" }], items: [], selection_matches: true };
  advisor.syncTypedDocumentField("estimate");
  assert.equal(input.disabled, true);
  assert.equal(advisor.packageStats().count, 0);
});

test("needsAdvisor does not shadow wizard submitting and guardMessage", () => {
  const advisor = makeAdvisor(makeDocumentForm());
  assert.equal("submitting" in advisor, false);
  assert.equal("guardMessage" in advisor, false);
  assert.equal(advisor.revisionSubmitting, false);
  assert.equal(advisor.revisionGuardMessage, "");

  const wizard = makeWizard(4, { 1: makeSection(1), 2: makeSection(2), 3: makeSection(3) });
  let prevented = false;
  wizard.handleSubmit({ preventDefault: () => { prevented = true; } });
  assert.equal(prevented, false);
  assert.equal(wizard.submitting, true);
  assert.equal(advisor.revisionSubmitting, false, "outer submit state is not shadowed by the child");
});

test("removeFile uses DataTransfer and honestly clears when unsupported", () => {
  const input = {
    files: [makeFile("a.pdf", 1), makeFile("b.pdf", 2)],
    value: "C:\\fake",
    dispatchEvent() {},
  };
  const form = { elements: { document_statement: input }, querySelectorAll: () => [], querySelector: () => null, dataset: {} };
  const advisor = makeAdvisor(form);
  const originalTransfer = global.DataTransfer;
  global.DataTransfer = class {
    constructor() { this.items = { add: (file) => this.files.push(file) }; this.files = []; }
  };
  advisor.removeFile("statement", 0);
  assert.deepEqual(input.files.map((file) => file.name), ["b.pdf"]);
  assert.equal(advisor.fileNotice, "");

  global.DataTransfer = undefined;
  input.files = [makeFile("a.pdf", 1), makeFile("b.pdf", 2)];
  advisor.removeFile("statement", 1);
  assert.equal(input.value, "");
  assert.match(advisor.fileNotice, /очищено/);
  global.DataTransfer = originalTransfer;
});

test("chooseProduct works for radio and revision select", () => {
  const radio = { value: "working-capital", checked: false, dispatchEvent() {} };
  const select = {
    value: "investment",
    options: [
      { value: "working-capital", disabled: false },
      { value: "investment", disabled: false },
    ],
    focus() {},
    dispatchEvent() {},
  };
  const radioForm = {
    dataset: {},
    elements: {},
    querySelectorAll: (selector) => (selector === 'input[name="product"]' ? [radio] : []),
    querySelector: () => null,
  };
  const radioAdvisor = makeAdvisor(radioForm);
  radioAdvisor.chooseProduct("working-capital");
  assert.equal(radio.checked, true, "catalog radio is selected by recommendation");

  const selectForm = {
    dataset: {},
    elements: {},
    querySelectorAll: () => [],
    querySelector: (selector) => (selector === 'select[name="product"]' ? select : null),
  };
  const selectAdvisor = makeAdvisor(selectForm);
  selectAdvisor.chooseProduct("working-capital");
  assert.equal(select.value, "working-capital", "revision select is updated by recommendation");
});

test("revision guard blocks advice mismatch and client file errors", () => {
  const form = makeDocumentForm({ groups: [makeFileGroup({ fresh: [makeFile("big.pdf", 11 * 1024 * 1024)] })] });
  const advisor = makeAdvisor(form);
  advisor.advice = { items: [{ code: "statement", available: true }], checklist: [], selection_matches: true };
  let prevented = false;
  advisor.guardSubmit({ preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.match(advisor.revisionGuardMessage, /10 МБ/);
  assert.equal(advisor.revisionSubmitting, false);

  advisor.advice.selection_matches = false;
  prevented = false;
  advisor.guardSubmit({ preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.match(advisor.revisionGuardMessage, /доступн/);
});

test("revision guard blocks more than five fresh and retained documents", () => {
  const generic = makeFileGroup({
    code: "",
    label: "Дополнительные документы",
    fresh: [1, 2, 3, 4, 5, 6].map((index) => makeFile(`annual-${index}.pdf`, 1024)),
  });
  const advisor = makeAdvisor(makeDocumentForm({ generic }));
  advisor.advice = { items: [{ code: "statement", available: true }], checklist: [], selection_matches: true };
  let prevented = false;
  advisor.guardSubmit({ preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.match(advisor.revisionGuardMessage, /не более 5/);
  assert.equal(advisor.revisionSubmitting, false);
});

test("disabled irrelevant invalid field does not block step navigation", () => {
  const ignored = makeField({ valid: false, id: "id_document_estimate" });
  ignored.disabled = true;
  const required = makeField({ valid: true, id: "id_amount" });
  const wizard = makeWizard(2, {
    1: makeSection(1, []),
    2: makeSection(2, [ignored, required]),
    3: makeSection(3, []),
  });
  assert.equal(wizard.validateStep(2), true);
  wizard.goTo(3);
  assert.equal(wizard.step, 3);
});

(async () => {
  let failed = 0;
  for (const { name, fn } of tests) {
    try {
      await fn();
      console.log(`ok - ${name}`);
    } catch (error) {
      failed += 1;
      console.error(`not ok - ${name}`);
      console.error(error && error.stack ? error.stack : error);
    }
  }
  console.log(`${tests.length - failed}/${tests.length} passed`);
  if (failed) process.exitCode = 1;
})();
