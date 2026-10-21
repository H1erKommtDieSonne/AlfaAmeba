import Alpine from "alpinejs";

window.Alpine = Alpine;

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
    3: "Добавьте документы, которые модель должна извлечь и структурировать.",
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
    const count = document.getElementById("id_documents")?.files?.length || 0;
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

Alpine.start();

