// Компоненты Alpine. Сборки нет: ядро лежит рядом (core/vendor/alpine.min.js) и подключается
// в base.html ПОСЛЕ этого файла — к его запуску обработчик alpine:init уже должен висеть.

const FILE_LIMITS = {
  maxFiles: 5,
  maxFileSize: 10 * 1024 * 1024,
  maxTotalSize: 50 * 1024 * 1024,
  defaultAccept: [".pdf", ".png", ".jpg", ".jpeg", ".xlsx", ".csv", ".docx"],
};

function dataNumber(element, name, fallback) {
  const value = Number(element?.dataset?.[name]);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function formatFileSize(bytes) {
  const size = Number(bytes) || 0;
  if (size < 1024) return `${size} Б`;
  if (size < 1024 * 1024) {
    return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(size / 1024)} КБ`;
  }
  return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(size / 1024 / 1024)} МБ`;
}

function formatAmount(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return "Не указано";
  const amount = Number(raw.replace(/\s+/g, "").replace(",", "."));
  if (!Number.isFinite(amount)) return `${raw} ₽`;
  return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(amount)} ₽`;
}

function formatFileCount(count) {
  if (!count) return "Файлы не выбраны";
  const suffix = count === 1 ? "" : count >= 2 && count <= 4 ? "а" : "ов";
  return `${count} файл${suffix}`;
}

function fileExtension(name) {
  const match = /\.([^.]+)$/.exec(String(name || "").trim());
  return match ? `.${match[1].toLowerCase()}` : "";
}

function readDataFiles(elements) {
  return [...elements].map((element) => ({
    id: element.dataset?.documentId || element.dataset?.id || "",
    name: element.dataset?.name || element.textContent?.trim() || "Документ",
    size: Number(element.dataset?.size) || 0,
  }));
}

function readFreshFiles(input) {
  return [...(input?.files || [])].map((file, index) => ({
    name: file.name || `Файл ${index + 1}`,
    size: Number(file.size) || 0,
    file,
  }));
}

function acceptExtensions(form) {
  const accept = form?.querySelector?.('input[type="file"]')?.getAttribute?.("accept")
    || form?.dataset?.accept
    || FILE_LIMITS.defaultAccept.join(",");
  return accept.split(",").map((item) => item.trim().toLowerCase()).filter(Boolean);
}

// Единый расчёт пакета для живой подсказки и проверки перед отправкой.
// Учитывает: лимиты сервера, retained-файлы, замену typed-пункта и disabled-поля.
function collectDocumentStats(form) {
  const maxFiles = dataNumber(form, "maxFiles", FILE_LIMITS.maxFiles);
  const maxFileSize = dataNumber(form, "maxFileSize", FILE_LIMITS.maxFileSize);
  const maxTotalSize = dataNumber(form, "maxTotalSize", FILE_LIMITS.maxTotalSize);
  const allowed = new Set(acceptExtensions(form));
  const groups = [];
  const errors = [];
  let count = 0;
  let totalSize = 0;
  const removedIds = new Set(
    [...(form?.querySelectorAll?.('input[name="remove_document_ids"]:checked') || [])].map((input) => input.value)
  );

  const addFile = (file) => {
    count += 1;
    totalSize += file.size;
    const extension = fileExtension(file.name);
    if (!extension) {
      errors.push(`Файл «${file.name}»: не удалось определить формат, выберите файл с поддерживаемым расширением.`);
    } else if (extension === ".xls") {
      errors.push(`Файл «${file.name}»: формат .xls не поддерживается, сохраните его как .xlsx.`);
    } else if (!allowed.has(extension)) {
      errors.push(`Файл «${file.name}»: формат не поддерживается.`);
    }
    if (!file.size) errors.push(`Файл «${file.name}» пуст.`);
    if (file.size > maxFileSize) {
      errors.push(`Файл «${file.name}» больше ${formatFileSize(maxFileSize)}.`);
    }
  };

  form?.querySelectorAll?.("[data-document-code]").forEach((group) => {
    const input = group.querySelector?.('input[type="file"]');
    if (!input || input.disabled) return;
    const code = group.dataset?.documentCode || "";
    const label = group.querySelector?.("legend")?.textContent?.trim()
      || group.dataset?.documentLabel
      || "Документ";
    const retained = readDataFiles(group.querySelectorAll?.("[data-retained-file]") || [])
      .filter((file) => !file.id || !removedIds.has(file.id));
    const fresh = readFreshFiles(input);
    fresh.forEach(addFile);
    const countedRetained = fresh.length ? [] : retained;
    countedRetained.forEach(addFile);
    groups.push({ code, label, fresh, retained, replaced: fresh.length > 0 });
  });

  const generic = form?.querySelector?.("[data-document-generic]");
  if (generic) {
    const input = generic.querySelector?.('input[type="file"]');
    const label = generic.querySelector?.("legend")?.textContent?.trim()
      || generic.dataset?.documentLabel
      || "Дополнительные документы";
    const retained = readDataFiles(generic.querySelectorAll?.("[data-retained-file]") || [])
      .filter((file) => !file.id || !removedIds.has(file.id));
    const fresh = readFreshFiles(input);
    fresh.forEach(addFile);
    retained.forEach(addFile);
    groups.push({ code: "", label, fresh, retained, replaced: false });
  }

  if (count > maxFiles) {
    errors.unshift(`В одной версии можно передать не более ${maxFiles} документов с учётом уже приложенных.`);
  }
  if (totalSize > maxTotalSize) {
    errors.push(`Общий размер документов больше ${formatFileSize(maxTotalSize)}.`);
  }

  return { count, totalSize, errors, groups };
}

function errorTargets(error, root) {
  const name = error.dataset?.errorFor;
  if (name) return [...root.querySelectorAll(`[name="${name}"]`)];
  const idMatch = /^(.*)_error$/.exec(error.id || "");
  if (idMatch) {
    const byId = root.querySelector(`[id="${idMatch[1]}"]`);
    if (byId) return [byId];
  }
  const label = error.closest("label");
  if (label) {
    const field = label.querySelector("input:not([type=hidden]), select, textarea");
    if (field) return [field];
  }
  const group = error.closest("[data-document-code], [data-document-generic]");
  if (group) {
    const field = group.querySelector('input[type="file"]');
    if (field) return [field];
  }
  return [];
}

function enhanceInvalidFields(root) {
  if (!root) return;
  root.querySelectorAll(".form-error").forEach((error) => {
    if (!error.textContent.trim()) return;
    errorTargets(error, root).forEach((field) => {
      field.setAttribute("aria-invalid", "true");
      if (!error.id) return;
      const describedBy = (field.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
      if (!describedBy.includes(error.id)) {
        field.setAttribute("aria-describedby", [...describedBy, error.id].join(" "));
      }
    });
  });
}

function enhanceFieldDescriptions(root) {
  if (!root) return;
  root.querySelectorAll(".form-field").forEach((label) => {
    const field = label.querySelector("input:not([type=hidden]), select, textarea");
    if (!field) return;
    const ids = [...label.querySelectorAll(".form-help[id], .form-error[id]")].map((node) => node.id);
    if (!ids.length) return;
    const current = (field.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
    field.setAttribute("aria-describedby", [...new Set([...current, ...ids])].join(" "));
  });
  root.querySelectorAll("[data-document-code], [data-document-generic]").forEach((group) => {
    const field = group.querySelector('input[type="file"]');
    if (!field) return;
    const ids = [...group.querySelectorAll("[id]")].filter((node) => /help|status|error|existing|loss|notice/.test(node.id)).map((node) => node.id);
    if (!ids.length) return;
    const current = (field.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
    field.setAttribute("aria-describedby", [...new Set([...current, ...ids])].join(" "));
  });
}

function errorFieldId(error) {
  if (error.dataset?.errorFor) {
    return error.ownerDocument?.querySelector(`[name="${error.dataset.errorFor}"]`)?.id || "";
  }
  const idMatch = /^(.*)_error$/.exec(error.id || "");
  if (idMatch) return idMatch[1];
  const label = error.closest("label");
  const field = label?.querySelector("input:not([type=hidden]), select, textarea");
  return field?.id || "";
}

document.addEventListener("alpine:init", () => {
  Alpine.data("needsAdvisor", () => ({
    advice: { items: [], checklist: [], message: "" },
    adviceLoading: false,
    adviceError: "",
    needValue: "",
    selectedProduct: "",
    requestNumber: 0,
    requestKey: "",
    controller: null,
    fileTick: 0,
    fileNotice: "",
    // У revision-формы нет applicationWizard, но имена не должны перекрывать
    // submitting/guardMessage внешнего мастера на странице создания.
    revisionGuardMessage: "",
    revisionSubmitting: false,
    formElement: null,
    init() {
      this.formElement = this.$el;
      enhanceInvalidFields(this.formElement);
      enhanceFieldDescriptions(this.formElement);
      this.selectedProduct = String(new FormData(this.formElement).get("product") || "");
      this.lockIdentityFields();
      const productSelect = this.formElement.querySelector('select[name="product"]');
      if (productSelect) {
        productSelect.classList.add("form-control");
        if (!productSelect.getAttribute("aria-label")) {
          productSelect.setAttribute("aria-label", "Кредитный продукт");
        }
      }
      this.refreshAdvice();
      this.$nextTick?.(() => this.syncProductOptions());
    },
    lockIdentityFields() {
      if (!this.formElement.dataset.readonlyIdentity) return;
      ["inn", "organization_name"].forEach((name) => {
        const field = this.formElement.elements?.[name] || this.formElement.querySelector(`[name="${name}"]`);
        if (!field) return;
        field.readOnly = true;
        field.setAttribute("aria-readonly", "true");
      });
    },
    syncAdviceState() {
      this.$dispatch("advice-state", {
        loading: this.adviceLoading,
        error: this.adviceError,
        selectionMatches: Boolean(this.advice.selection_matches),
        itemsCount: this.advice.items?.length || 0,
        items: (this.advice.items || []).map((item) => ({
          code: item.code,
          name: item.name,
          available: Boolean(item.available),
          limitation: item.limitation || "",
        })),
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
      this.selectedProduct = params.get("product") || "";
      const key = params.toString();
      if (!force && key === this.requestKey && !this.adviceError) return;

      const serial = ++this.requestNumber;
      this.requestKey = key;
      if (this.controller) this.controller.abort();
      const controller = new AbortController();
      this.controller = controller;
      this.adviceLoading = true;
      this.adviceError = "";
      this.revisionGuardMessage = "";
      this.syncAdviceState();
      try {
        const response = await fetch(`${form.dataset.adviceUrl}?${params}`, {
          headers: { Accept: "application/json" },
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("Не удалось обновить подбор. Проверьте сумму и срок и повторите попытку.");
        const result = await response.json();
        if (serial === this.requestNumber) {
          this.advice = result;
          this.$nextTick?.(() => this.syncProductOptions());
        }
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
      this.selectedProduct = String(new FormData(this.formElement).get("product") || "");
      if (tag === "SELECT" || ["file", "radio", "checkbox"].includes(type)) {
        if (type === "file") this.fileNotice = "";
        this.refreshAdvice();
      }
    },
    chooseProduct(code) {
      const radio = [...this.formElement.querySelectorAll('input[name="product"]')].find((input) => input.value === code);
      if (radio) {
        radio.checked = true;
        radio.dispatchEvent(new Event("change", { bubbles: true }));
        return;
      }
      const select = this.formElement.querySelector('select[name="product"]');
      if (!select) return;
      const option = [...select.options].find((item) => item.value === code);
      if (!option || option.disabled) return;
      select.value = code;
      this.selectedProduct = code;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      select.focus();
    },
    requires(code) { return this.advice.checklist.some((item) => item.code === code); },
    hasFiles(code) {
      return this.fileTick >= 0 && (this.formElement.elements?.[`document_${code}`]?.files?.length || 0) > 0;
    },
    hasServerError(code) {
      const section = this.formElement.querySelector(`[data-document-code="${code}"]`);
      return Boolean(section?.querySelector(".form-error")?.textContent.trim());
    },
    uploadStatus(code, existing) {
      if (this.hasFiles(code)) return "Файлы выбраны, будут загружены при отправке";
      return existing ? "Уже приложено, ожидает проверки" : "Не загружено";
    },
    hasRetained(code) {
      const group = this.formElement.querySelector(`[data-document-code="${code}"]`);
      if (!group) return false;
      const removedIds = new Set(
        [...this.formElement.querySelectorAll('input[name="remove_document_ids"]:checked')].map((input) => input.value)
      );
      return [...group.querySelectorAll("[data-retained-file]")].some((row) => {
        const id = row.dataset?.documentId || "";
        return !id || !removedIds.has(id);
      });
    },
    syncTypedDocumentField(code) {
      const group = this.formElement.querySelector(`[data-document-code="${code}"]`);
      const input = group?.querySelector('input[type="file"]');
      if (!input) return;
      const relevant = this.requires(code);
      // Иррелевантный пункт всегда отключён от отправки и подсчёта, даже если
      // сервер вернул по нему ошибку: ошибку показываем, input не учитываем.
      input.disabled = !relevant;
      input.required = relevant && !this.hasRetained(code);
      group.classList.toggle("is-inactive", input.disabled);
      group.setAttribute("data-active", input.disabled ? "false" : "true");
    },
    productUnavailable(code) {
      if (this.adviceLoading || this.adviceError || !this.advice.items?.length) return false;
      const item = this.advice.items.find((candidate) => candidate.code === code);
      return !item || !item.available;
    },
    syncProductOptions() {
      const select = this.formElement.querySelector('select[name="product"]');
      if (!select || this.adviceLoading || !this.advice.items?.length) return;
      for (const option of [...select.options]) {
        if (!option.value) continue;
        option.disabled = this.productUnavailable(option.value) && option.value !== select.value;
      }
    },
    selectionReason() {
      if (this.adviceLoading) return "Дождитесь обновления подбора.";
      if (this.adviceError) return "Подбор не загрузился. Повторите попытку.";
      const item = this.advice.items.find((candidate) => candidate.code === this.selectedProduct);
      if (!this.selectedProduct) return "Выберите доступный продукт из подбора.";
      if (!item) return "Этот продукт не входит в предварительный подбор. Выберите доступный вариант.";
      if (!item.available) return item.limitation || "Подача по этому продукту сейчас недоступна.";
      return "";
    },
    isSelected(code) { return Boolean(code) && this.selectedProduct === code; },
    selectedFiles(code) {
      this.fileTick;
      const input = this.formElement.elements?.[code ? `document_${code}` : "documents"];
      return [...(input?.files || [])].map((file, index) => ({
        name: file.name || `Файл ${index + 1}`,
        size: formatFileSize(file.size),
        index,
        key: `${file.name}:${file.size}:${index}`,
      }));
    },
    removeFile(code, index) {
      const input = this.formElement.elements?.[code ? `document_${code}` : "documents"];
      if (!input) return;
      let clearedOnly = false;
      try {
        if (typeof DataTransfer === "undefined" || !input.files) throw new Error("no DataTransfer");
        const transfer = new DataTransfer();
        [...input.files].forEach((file, current) => {
          if (current !== index) transfer.items.add(file);
        });
        input.files = transfer.files;
      } catch (_) {
        input.value = "";
        clearedOnly = true;
      }
      this.fileTick += 1;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      if (clearedOnly) {
        this.fileNotice = "Браузер не позволяет удалить один файл из выбора. Поле очищено — выберите файлы заново.";
      }
    },
    packageStats() {
      this.fileTick;
      this.advice.checklist;
      return collectDocumentStats(this.formElement);
    },
    packageSummary() {
      const stats = this.packageStats();
      return `${formatFileCount(stats.count)} · ${formatFileSize(stats.totalSize)} из 50 МБ`;
    },
    validateDocuments() {
      const stats = this.packageStats();
      return stats.errors;
    },
    guardSubmit(event) {
      if (this.adviceLoading || this.adviceError || !this.advice.selection_matches) {
        event.preventDefault();
        this.revisionGuardMessage = this.selectionReason() || "Выберите доступный продукт из подбора, чтобы продолжить.";
        return;
      }
      const errors = this.validateDocuments();
      if (errors.length) {
        event.preventDefault();
        this.revisionGuardMessage = errors[0];
        this.$el?.querySelector?.(".upload-summary")?.scrollIntoView?.({ behavior: "smooth", block: "center" });
        return;
      }
      this.revisionGuardMessage = "";
      this.revisionSubmitting = true;
    },
  }));

  Alpine.data("applicationWizard", (initialStep = 1, initialProduct = "") => ({
    step: Number(initialStep) || 1,
    product: String(initialProduct || ""),
    showHint: true,
    submitting: false,
    errorSummary: [],
    documentErrors: [],
    adviceState: { loading: false, error: "", selectionMatches: false, itemsCount: 0, items: [] },
    guardMessage: "",
    review: {
      organization_name: "Не указано",
      inn: "Не указано",
      product: "Не выбран",
      amount: "Не указано",
      term: "Не указано",
      purpose: "Не указано",
      filesSummary: "Файлы не выбраны",
      fileGroups: [],
    },
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
      enhanceFieldDescriptions(this.$refs.form);
      this.errorSummary = this.collectServerErrors();
      if (this.errorSummary.length) {
        this.step = Math.min(...this.errorSummary.map((error) => error.step));
        this.$nextTick(() => this.$refs.errorSummary?.focus());
      }
      this.$nextTick(() => this.syncSummary());
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
      this.$nextTick?.(() => this.syncSummary());
    },
    productUnavailable(code) {
      const state = this.adviceState;
      if (state.loading || state.error || !state.items?.length) return false;
      const item = state.items.find((candidate) => candidate.code === code);
      return !item || !item.available;
    },
    productUnavailableReason(code) {
      if (this.adviceState.loading) return "Дождитесь обновления подбора.";
      if (this.adviceState.error) return "Подбор не загрузился. Повторите попытку.";
      const item = this.adviceState.items.find((candidate) => candidate.code === code);
      if (!item) return "Этот продукт не входит в предварительный подбор; выберите доступный вариант.";
      return item.limitation || "Подача по этому продукту сейчас недоступна.";
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
      // Irrelevant typed file inputs stay in the DOM but disabled: they must not block navigation.
      const invalid = fields.find((field) => !field.disabled && !field.checkValidity());
      if (!invalid) return true;
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
        this.syncSummary();
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
      this.syncSummary();
      this.focusStage();
    },
    next() { this.goTo(Math.min(4, this.step + 1)); },
    previous() { this.goTo(Math.max(1, this.step - 1)); },
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
    syncSummary() {
      const form = this.$refs.form;
      if (!form) return;
      const value = (name) => String(form.elements?.namedItem?.(name)?.value ?? form.querySelector?.(`[name="${name}"]`)?.value ?? "").trim();
      const radio = form.querySelector?.('input[name="product"]:checked');
      const select = form.querySelector?.('select[name="product"]');
      const product = radio?.closest?.("label")?.querySelector?.("strong")?.textContent?.trim()
        || select?.selectedOptions?.[0]?.textContent?.trim()
        || "Не выбран";
      const stats = collectDocumentStats(form);
      const purpose = value("purpose");
      this.review = {
        organization_name: value("organization_name") || "Не указано",
        inn: value("inn") || "Не указано",
        product,
        amount: formatAmount(value("amount")),
        term: value("term_months") ? `${value("term_months")} мес.` : "Не указано",
        purpose: purpose || "Не указано",
        filesSummary: stats.count ? `${formatFileCount(stats.count)} · ${formatFileSize(stats.totalSize)}` : "Файлы не выбраны",
        fileGroups: stats.groups
          .filter((group) => group.fresh.length || group.retained.length)
          .map((group) => ({
            code: group.code || "generic",
            label: group.label,
            names: [...group.retained.map((file) => file.name), ...group.fresh.map((file) => file.name)].join(", "),
            replaced: group.replaced,
          })),
      };
    },
    validateDocuments() {
      const stats = collectDocumentStats(this.$refs.form);
      this.documentErrors = stats.errors;
      return !stats.errors.length;
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
      if (!this.validateDocuments()) {
        event.preventDefault();
        this.step = 3;
        this.$nextTick(() => {
          this.$refs.documentErrors?.focus();
          this.$refs.documentErrors?.scrollIntoView({ behavior: "smooth", block: "center" });
        });
        return;
      }
      this.submitting = true;
    },
  }));
});

document.addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-href]");
  if (!row || event.target.closest("a, button, input, select, textarea, label")) return;
  if (window.getSelection()?.toString()) return;
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.button === 1) {
    window.open(row.dataset.href, "_blank", "noopener");
    return;
  }
  window.location.href = row.dataset.href;
});
