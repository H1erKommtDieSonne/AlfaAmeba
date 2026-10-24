// Компоненты Alpine. Сборки нет: ядро лежит рядом (core/vendor/alpine.min.js) и подключается
// в base.html ПОСЛЕ этого файла — к его запуску обработчик alpine:init уже должен висеть.

function enhanceInvalidFields(root) {
  if (!root) return;
  root.querySelectorAll(".form-error").forEach((error) => {
    if (!error.textContent.trim()) return;
    const container = error.closest("label, fieldset, .form-section") || root;
    const fields = container.querySelectorAll("input:not([type=hidden]), select, textarea");
    fields.forEach((field) => {
      field.setAttribute("aria-invalid", "true");
      if (!error.id) return;
      const describedBy = (field.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
      if (!describedBy.includes(error.id)) {
        field.setAttribute("aria-describedby", [...describedBy, error.id].join(" "));
      }
    });
  });
}

function errorFieldId(error) {
  const container = error.closest("label, fieldset, .form-section");
  const field = container?.querySelector("input:not([type=hidden]), select, textarea");
  return field?.id || "";
}

document.addEventListener("alpine:init", () => {
  Alpine.data("needsAdvisor", () => ({
    advice: { items: [], checklist: [], message: "" },
    adviceLoading: false,
    adviceError: "",
    needValue: "",
    requestNumber: 0,
    requestKey: "",
    controller: null,
    fileTick: 0,
    formElement: null,
    init() {
      this.formElement = this.$el;
      enhanceInvalidFields(this.formElement);
      this.refreshAdvice();
    },
    syncAdviceState() {
      this.$dispatch("advice-state", {
        loading: this.adviceLoading,
        error: this.adviceError,
        selectionMatches: Boolean(this.advice.selection_matches),
        itemsCount: this.advice.items?.length || 0,
      });
    },
    async refreshAdvice(force = false) {
      this.fileTick += 1;
      const form = this.formElement;
      const params = new URLSearchParams();
      for (const name of ["need", "gap_kind", "debt_kind", "collateral", "amount", "term_months", "product"]) {
        const value = new FormData(form).get(name);
        if (value !== null) params.set(name, value);
      }
      this.needValue = params.get("need");
      const key = params.toString();
      if (!force && key === this.requestKey && !this.adviceError) return;

      const serial = ++this.requestNumber;
      this.requestKey = key;
      if (this.controller) this.controller.abort();
      const controller = new AbortController();
      this.controller = controller;
      this.adviceLoading = true;
      this.adviceError = "";
      this.syncAdviceState();
      try {
        const response = await fetch(`${form.dataset.adviceUrl}?${params}`, {
          headers: { Accept: "application/json" },
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("Не удалось обновить подбор. Проверьте сумму и срок и повторите попытку.");
        const result = await response.json();
        if (serial === this.requestNumber) this.advice = result;
      } catch (error) {
        if (error.name === "AbortError") return;
        if (serial === this.requestNumber) {
          this.adviceError = error.message;
          this.advice.items = [];
        }
      } finally {
        if (serial === this.requestNumber) {
          this.adviceLoading = false;
          this.syncAdviceState();
        }
      }
    },
    handleControlChange(event) {
      const tag = event.target.tagName;
      const type = event.target.type;
      if (tag === "SELECT" || ["file", "radio", "checkbox"].includes(type)) this.refreshAdvice();
    },
    chooseProduct(code) {
      const field = [...this.formElement.querySelectorAll('input[name="product"]')].find(input => input.value === code);
      if (field) { field.checked = true; field.dispatchEvent(new Event("change", { bubbles: true })); }
    },
    requires(code) { return this.advice.checklist.some(item => item.code === code); },
    hasFiles(code) { return this.fileTick >= 0 && (this.formElement.elements[`document_${code}`]?.files?.length || 0) > 0; },
    hasServerError(code) {
      const section = this.formElement.querySelector(`[data-document-code="${code}"]`);
      return Boolean(section?.querySelector(".form-error")?.textContent.trim());
    },
    uploadStatus(code, existing) {
      return this.hasFiles(code) ? "Файл выбран, будет загружен при отправке" : existing ? "Уже загружено, ожидает проверки" : "Не загружено";
    },
  }));

  Alpine.data("applicationWizard", (initialStep = 1, initialProduct = "") => ({
    step: Number(initialStep) || 1,
    product: String(initialProduct || ""),
    showHint: true,
    submitting: false,
    errorSummary: [],
    adviceState: { loading: false, error: "", selectionMatches: false, itemsCount: 0 },
    guardMessage: "",
    titles: {
      1: "Расскажите о компании",
      2: "Выберите условия кредита",
      3: "Приложите документы",
      4: "Проверьте заявку",
    },
    descriptions: {
      1: "Укажите реквизиты компании, от имени которой подаётся заявка.",
      2: "Выберите продукт, сумму, срок и опишите цель финансирования.",
      3: "Приложите комплект по выбранному продукту.",
      4: "Проверьте основные параметры перед окончательной отправкой в банк.",
    },
    init() {
      enhanceInvalidFields(this.$refs.form);
      this.errorSummary = this.collectServerErrors();
      if (this.errorSummary.length) {
        this.step = Math.min(...this.errorSummary.map((error) => error.step));
        this.$nextTick(() => this.$refs.errorSummary?.focus());
      }
    },
    collectServerErrors() {
      const form = this.$refs.form;
      if (!form) return [];
      const seen = new Set();
      const errors = [];
      const add = (element, fallbackStep) => {
        const text = element.textContent.trim();
        if (!text) return;
        const section = element.closest("[data-form-step]");
        const step = section ? Number(section.dataset.formStep) : fallbackStep;
        const key = `${step}:${text}`;
        if (seen.has(key)) return;
        seen.add(key);
        errors.push({ step, text, fieldId: errorFieldId(element), title: this.titles[step] || "Проверьте данные" });
      };
      form.querySelectorAll("[data-form-step] .form-error").forEach((error) => add(error, this.step));
      form.querySelectorAll(".error-box").forEach((error) => {
        const step = /документ|файл|МБ/i.test(error.textContent) ? 3 : 1;
        add(error, step);
      });
      return errors;
    },
    onAdviceState(detail) {
      if (!detail) return;
      this.adviceState = detail;
      if (!detail.loading && !detail.error && detail.selectionMatches) this.guardMessage = "";
    },
    adviceGuardReason() {
      const state = this.adviceState;
      if (state.loading) return "Дождитесь обновления подбора.";
      if (state.error) return "Подбор не загрузился. Повторите подбор или обновите страницу.";
      if (!state.selectionMatches) return "Выберите доступный продукт из подбора, чтобы продолжить.";
      return "";
    },
    canLeaveStep(step) {
      if (step !== 2) return true;
      const reason = this.adviceGuardReason();
      this.guardMessage = reason;
      return !reason;
    },
    validateStep(step) {
      const section = this.$refs.form.querySelector(`[data-form-step="${step}"]`);
      if (!section) return true;
      const fields = [...section.querySelectorAll("input, select, textarea")];
      const invalid = fields.find((field) => !field.checkValidity());
      if (!invalid) return true;
      // Шаг уже может быть скрыт (финальная отправка): сначала даём Alpine показать
      // секцию через caller, затем сообщаем браузеру о невалидном поле.
      this.$nextTick(() => {
        invalid.reportValidity();
        invalid.focus({ preventScroll: true });
        invalid.scrollIntoView({ behavior: "smooth", block: "center" });
      });
      return false;
    },
    goTo(target) {
      target = Number(target);
      if (target < this.step) {
        this.step = target;
        this.focusStage();
        return;
      }
      if (target >= 4 && !this.canLeaveStep(2)) {
        if (this.step < 2) this.step = 2;
        this.focusStage();
        return;
      }
      while (this.step < target) {
        if (this.step <= 3 && !this.validateStep(this.step)) return;
        if (!this.canLeaveStep(this.step)) return;
        this.step += 1;
      }
      this.focusStage();
    },
    next() {
      this.goTo(Math.min(4, this.step + 1));
    },
    previous() {
      this.goTo(Math.max(1, this.step - 1));
    },
    focusError(error) {
      this.step = error.step;
      this.$nextTick(() => {
        const field = error.fieldId && document.getElementById(error.fieldId);
        if (!field) {
          this.$refs.errorSummary?.focus();
          return;
        }
        field.focus({ preventScroll: true });
        field.scrollIntoView({ behavior: "smooth", block: "center" });
      });
    },
    focusStage() {
      this.$nextTick(() => {
        this.$refs.stageHeading?.focus({ preventScroll: true });
        this.$refs.stageHeading?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    },
    fieldValue(id, suffix = "") {
      const value = document.getElementById(id)?.value?.trim();
      return value ? `${value}${suffix}` : "Не указано";
    },
    productName() {
      const selected = this.$refs.form.querySelector('input[name="product"]:checked');
      return selected?.closest("label")?.querySelector("strong")?.textContent?.trim() || "Не выбран";
    },
    documentSummary() {
      const count = [...this.$refs.form.querySelectorAll('input[type="file"]')].reduce((total, field) => total + (field.files?.length || 0), 0);
      return count ? `${count} файл${count === 1 ? "" : count < 5 ? "а" : "ов"}` : "Файлы не выбраны";
    },
    handleSubmit(event) {
      if (this.submitting) {
        event.preventDefault();
        return;
      }
      if (this.step < 4) {
        event.preventDefault();
        this.next();
        return;
      }
      if (!this.canLeaveStep(2)) {
        event.preventDefault();
        this.step = 2;
        this.focusStage();
        return;
      }
      for (let current = 1; current <= 3; current += 1) {
        if (!this.validateStep(current)) {
          event.preventDefault();
          this.step = current;
          return;
        }
      }
      this.submitting = true;
    },
  }));
});
