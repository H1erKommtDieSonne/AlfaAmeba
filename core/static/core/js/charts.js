// Графики карточки сотрудника. Данные кладёт сервер через json_script (node_integration/financial.py),
// здесь только отрисовка. Цвета берём из CSS-переменных темы и перерисовываем графики
// при переключении светлой/тёмной темы — иначе подписи остались бы цвета прошлой темы.
(() => {
  const charts = [];

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  const palette = () => ({
    ink: css("--ink"),
    muted: css("--muted"),
    line: css("--line"),
    accent: css("--accent"),
    positive: css("--positive"),
    warning: css("--warning"),
    negative: css("--negative"),
    progress: css("--progress-ink"),
    neutral: css("--neutral-ink"),
    surface: css("--surface"),
  });

  // 23 100 000 → «23,1 млн»: на оси полные суммы не помещаются.
  const shortMoney = (value) => {
    if (value === null || value === undefined) return "—";
    const size = Math.abs(value);
    const format = (number, digits) => number.toLocaleString("ru-RU", { maximumFractionDigits: digits });
    if (size >= 1e9) return `${format(value / 1e9, 1)} млрд`;
    if (size >= 1e6) return `${format(value / 1e6, 1)} млн`;
    if (size >= 1e3) return `${format(value / 1e3, 0)} тыс.`;
    return format(value, 0);
  };
  const fullMoney = (value) => (value === null || value === undefined ? "нет данных" : `${shortMoney(value)} ₽`);

  const baseOptions = (colors) => ({
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { position: "bottom", labels: { color: colors.muted, boxWidth: 12, boxHeight: 12, usePointStyle: true } },
      tooltip: { callbacks: { label: (item) => `${item.dataset.label}: ${fullMoney(item.parsed.y ?? item.parsed)}` } },
    },
    scales: {
      x: { grid: { display: false }, ticks: { color: colors.muted }, border: { color: colors.line } },
      y: { grid: { color: colors.line }, ticks: { color: colors.muted, callback: shortMoney }, border: { display: false } },
    },
  });

  const builders = {
    // Поступления/платежи столбцами и остаток линией на второй оси: суммы разного масштаба.
    cashflow(data, colors) {
      const options = baseOptions(colors);
      options.scales.balance = {
        position: "right",
        grid: { display: false },
        ticks: { color: colors.muted, callback: shortMoney },
        border: { display: false },
      };
      // Неполный месяц подписываем, чтобы его обороты не читались как полноценные.
      const labels = data.labels.map((label, index) => (data.complete[index] ? label : `${label} (неполный)`));
      return {
        type: "bar",
        data: {
          labels,
          datasets: [
            { label: "Поступления от покупателей", data: data.receipts, backgroundColor: colors.positive, borderRadius: 3, order: 2 },
            { label: "Операционные платежи", data: data.operating, backgroundColor: colors.warning, borderRadius: 3, order: 2 },
            { label: "Обслуживание долга", data: data.debt, backgroundColor: colors.negative, borderRadius: 3, order: 2 },
            {
              label: "Остаток на конец месяца", data: data.closing, type: "line", yAxisID: "balance", order: 1,
              borderColor: colors.accent, backgroundColor: colors.accent, pointRadius: 3, tension: 0.25,
            },
          ],
        },
        options,
      };
    },

    // Сценарии остатка от последнего фактического месяца; линия нуля показывает разрыв.
    forecast(data, colors) {
      const tones = { base: colors.accent, cautious: colors.warning, stress: colors.negative };
      const options = baseOptions(colors);
      options.scales.y.grid.color = (context) => (context.tick.value === 0 ? colors.negative : colors.line);
      return {
        type: "line",
        data: {
          labels: data.labels,
          datasets: data.scenarios.map((scenario) => ({
            label: scenario.label,
            data: scenario.values,
            borderColor: tones[scenario.name] || colors.neutral,
            backgroundColor: tones[scenario.name] || colors.neutral,
            pointRadius: 3,
            tension: 0.2,
            // Первая точка — факт, дальше — расчёт: пунктир от неё.
            segment: { borderDash: () => [6, 4] },
          })),
        },
        options,
      };
    },

    concentration(data, colors) {
      const tones = [colors.accent, colors.progress, colors.positive, colors.warning, colors.negative, colors.neutral];
      const total = data.values.reduce((sum, value) => sum + value, 0);
      return {
        type: "doughnut",
        data: {
          labels: data.labels,
          datasets: [{ data: data.values, backgroundColor: tones, borderColor: colors.surface, borderWidth: 2 }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          cutout: "62%",
          plugins: {
            legend: { position: "right", labels: { color: colors.muted, boxWidth: 12, boxHeight: 12, usePointStyle: true } },
            tooltip: {
              callbacks: {
                label: (item) => `${item.label}: ${fullMoney(item.parsed)} · ${Math.round((item.parsed / total) * 100)} %`,
              },
            },
          },
        },
      };
    },
  };

  function render() {
    charts.splice(0).forEach((chart) => chart.destroy());
    const colors = palette();
    document.querySelectorAll("canvas[data-chart]").forEach((canvas) => {
      const source = document.getElementById(canvas.dataset.source);
      const build = builders[canvas.dataset.chart];
      if (!source || !build || typeof window.Chart === "undefined") return;
      const data = JSON.parse(source.textContent)?.[canvas.dataset.chart];
      if (data) charts.push(new window.Chart(canvas, build(data, colors)));
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    render();
    new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  });
})();
