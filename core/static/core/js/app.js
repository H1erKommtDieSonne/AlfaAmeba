// Компоненты Alpine. Сборки нет: ядро лежит рядом (core/vendor/alpine.min.js) и подключается
// в base.html ПОСЛЕ этого файла — к его запуску обработчик alpine:init уже должен висеть.
document.addEventListener("alpine:init", () => {
  Alpine.data("needsAdvisor", () => ({
    advice: { items: [], checklist: [], message: "" },
    adviceLoading: false,
    adviceError: "",
    needValue: "",
    requestNumber: 0,
    fileTick: 0,
    formElement: null,
    init() { this.formElement = this.$el; this.refreshAdvice(); },
    async refreshAdvice() {
      this.fileTick++;
      const form = this.formElement;
      const params = new URLSearchParams();
      for (const name of ["need", "gap_kind", "debt_kind", "collateral", "amount", "term_months", "product"]) {
        const value = new FormData(form).get(name);
        if (value !== null) params.set(name, value);
      }
      this.needValue = params.get("need");
      const serial = ++this.requestNumber;
      this.adviceLoading = true;
      this.adviceError = "";
      try {
        const response = await fetch(`${form.dataset.adviceUrl}?${params}`, { headers: { Accept: "application/json" } });
        if (!response.ok) throw new Error("Не удалось обновить подбор. Проверьте сумму и срок и повторите попытку.");
        const result = await response.json();
        if (serial === this.requestNumber) this.advice = result;
      } catch (error) {
        if (serial === this.requestNumber) {
          this.adviceError = error.message;
          this.advice.items = [];
        }
      } finally {
        if (serial === this.requestNumber) this.adviceLoading = false;
      }
    },
    chooseProduct(code) {
      const field = [...this.formElement.querySelectorAll('input[name="product"]')].find(input => input.value === code);
      if (field) { field.checked = true; field.dispatchEvent(new Event("change", { bubbles: true })); }
    },
    requires(code) { return this.advice.checklist.some(item => item.code === code); },
    hasFiles(code) { return this.fileTick >= 0 && (this.formElement.elements[`document_${code}`]?.files?.length || 0) > 0; },
    uploadStatus(code, existing) {
      return this.hasFiles(code) ? "Файл выбран, будет загружен при отправке" : existing ? "Уже загружено, ожидает проверки" : "Не загружено";
    },
  }));
  Alpine.data("applicationWizard", (initialStep = 1, initialProduct = "") => ({
    step: Number(initialStep) || 1,
    product: String(initialProduct || ""),
    showHint: true,
    submitting: false,
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
    validateStep(step) {
      const section = this.$refs.form.querySelector(`[data-form-step="${step}"]`);
      if (!section) return true;
      const fields = [...section.querySelectorAll("input, select, textarea")];
      const invalid = fields.find((field) => !field.checkValidity());
      if (!invalid) return true;
      invalid.reportValidity();
      invalid.focus({ preventScroll: true });
      invalid.scrollIntoView({ behavior: "smooth", block: "center" });
      return false;
    },
    goTo(target) {
      target = Number(target);
      if (target < this.step) {
        this.step = target;
        this.focusStage();
        return;
      }
      while (this.step < target) {
        if (this.step <= 3 && !this.validateStep(this.step)) return;
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
      if (this.step < 4) {
        event.preventDefault();
        this.next();
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
      window.setTimeout(() => { this.submitting = false; }, 15000);
    },
  }));
});
