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
    querySelectorAll: () => [],
  };
}

function makeAdvisor(form) {
  const advisor = registry.needsAdvisor();
  advisor.formElement = form;
  advisor.$dispatch = () => {};
  return advisor;
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
