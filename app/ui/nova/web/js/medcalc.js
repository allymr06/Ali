/* ════════════════════════════════════════════════════════════════════
   JARVIS · NOVA — Tıp Akademisi: clinical calculators
   The classic bedside formulas, computed on the page with no model and
   no network, each card naming the formula it applies. Education only:
   the banner says so, and nothing here interprets a result beyond the
   arithmetic - the formula's answer, its unit, and at most the formula's
   own published reference range.
   ════════════════════════════════════════════════════════════════════ */
"use strict";

/* Every calculator: id, title, the formula as it is written in the
   textbooks, inputs, and a pure compute() over the parsed values. A
   compute that is missing an input returns null; one that refuses (a
   formula's own validity bound) returns { warn }. */
const MEDCALC = [
  {
    id: "bmi", title: "Vücut kitle indeksi (VKİ)", formula: "VKİ = kilo / boy² — kg/m²",
    inputs: [["weight", "Kilo", "kg"], ["height", "Boy", "cm"]], digits: 1, unit: "kg/m²",
    compute: ({ weight, height }) => (weight > 0 && height > 0 ? weight / Math.pow(height / 100, 2) : null),
    note: "DSÖ erişkin aralıkları: <18,5 zayıf · 18,5–24,9 normal · 25–29,9 fazla kilolu · ≥30 obez.",
  },
  {
    id: "bsa", title: "Vücut yüzey alanı (Mosteller)", formula: "VYA = √(boy × kilo / 3600)",
    inputs: [["height", "Boy", "cm"], ["weight", "Kilo", "kg"]], digits: 2, unit: "m²",
    compute: ({ height, weight }) => (height > 0 && weight > 0 ? Math.sqrt((height * weight) / 3600) : null),
  },
  {
    id: "ibw", title: "İdeal vücut ağırlığı (Devine)", formula: "Erkek 50 + 2,3×(boy(inç)−60) · Kadın 45,5 + 2,3×(boy(inç)−60)",
    inputs: [["height", "Boy", "cm"], ["sex", "Cinsiyet", ["male:Erkek", "female:Kadın"]]], digits: 1, unit: "kg",
    compute: ({ height, sex }) => {
      if (!(height > 0) || !sex) return null;
      const inches = height / 2.54;
      return (sex === "male" ? 50 : 45.5) + 2.3 * (inches - 60);
    },
  },
  {
    id: "crcl", title: "Kreatinin klirensi (Cockcroft-Gault)", formula: "KrKl = ((140−yaş) × kilo) / (72 × Cr) · kadında ×0,85",
    inputs: [["age", "Yaş", "yıl"], ["weight", "Kilo", "kg"], ["creatinine", "Kreatinin", "mg/dL"], ["sex", "Cinsiyet", ["male:Erkek", "female:Kadın"]]],
    digits: 1, unit: "mL/dk",
    compute: ({ age, weight, creatinine, sex }) => {
      if (!(age > 0) || !(weight > 0) || !(creatinine > 0) || !sex) return null;
      return (((140 - age) * weight) / (72 * creatinine)) * (sex === "female" ? 0.85 : 1);
    },
  },
  {
    id: "aniongap", title: "Anyon açığı", formula: "AA = Na − (Cl + HCO₃)",
    inputs: [["sodium", "Na", "mEq/L"], ["chloride", "Cl", "mEq/L"], ["bicarbonate", "HCO₃", "mEq/L"]], digits: 0, unit: "mEq/L",
    compute: ({ sodium, chloride, bicarbonate }) =>
      (sodium > 0 && chloride > 0 && bicarbonate > 0 ? sodium - (chloride + bicarbonate) : null),
    note: "Formülün yayınlanmış referans aralığı 8–12 mEq/L'dir.",
  },
  {
    id: "corrca", title: "Düzeltilmiş kalsiyum", formula: "Ca_düz = Ca + 0,8 × (4 − albümin)",
    inputs: [["calcium", "Kalsiyum", "mg/dL"], ["albumin", "Albümin", "g/dL"]], digits: 1, unit: "mg/dL",
    compute: ({ calcium, albumin }) => (calcium > 0 && albumin > 0 ? calcium + 0.8 * (4 - albumin) : null),
  },
  {
    id: "corrna", title: "Düzeltilmiş sodyum (hiperglisemi)", formula: "Na_düz = Na + 1,6 × ((glukoz − 100) / 100)",
    inputs: [["sodium", "Na", "mEq/L"], ["glucose", "Glukoz", "mg/dL"]], digits: 1, unit: "mEq/L",
    compute: ({ sodium, glucose }) => (sodium > 0 && glucose > 0 ? sodium + 1.6 * ((glucose - 100) / 100) : null),
  },
  {
    id: "ldl", title: "LDL (Friedewald)", formula: "LDL = Toplam kolesterol − HDL − TG/5 — mg/dL",
    inputs: [["total", "Toplam kolesterol", "mg/dL"], ["hdl", "HDL", "mg/dL"], ["tg", "Trigliserid", "mg/dL"]], digits: 0, unit: "mg/dL",
    compute: ({ total, hdl, tg }) => {
      if (!(total > 0) || !(hdl > 0) || !(tg > 0)) return null;
      if (tg >= 400) return { warn: "Friedewald formülü TG ≥ 400 mg/dL iken geçerli değildir." };
      return total - hdl - tg / 5;
    },
  },
  {
    id: "map", title: "Ortalama arter basıncı (OAB)", formula: "OAB = (SKB + 2 × DKB) / 3",
    inputs: [["systolic", "Sistolik", "mmHg"], ["diastolic", "Diyastolik", "mmHg"]], digits: 0, unit: "mmHg",
    compute: ({ systolic, diastolic }) => (systolic > 0 && diastolic > 0 ? (systolic + 2 * diastolic) / 3 : null),
  },
  {
    id: "osm", title: "Serum ozmolalitesi (hesaplanan)", formula: "Ozm = 2×Na + glukoz/18 + BUN/2,8",
    inputs: [["sodium", "Na", "mEq/L"], ["glucose", "Glukoz", "mg/dL"], ["bun", "BUN", "mg/dL"]], digits: 0, unit: "mOsm/kg",
    compute: ({ sodium, glucose, bun }) =>
      (sodium > 0 && glucose > 0 && bun > 0 ? 2 * sodium + glucose / 18 + bun / 2.8 : null),
  },
  {
    id: "maxhr", title: "Tahmini maksimum kalp hızı", formula: "220 − yaş",
    inputs: [["age", "Yaş", "yıl"]], digits: 0, unit: "atım/dk",
    compute: ({ age }) => (age > 0 && age < 130 ? 220 - age : null),
  },
  {
    id: "units", title: "Laboratuvar birim çevirici", formula: "Glukoz ÷18,016 · Kolesterol ÷38,67 · Trigliserid ÷88,57 · Kreatinin ×88,4",
    inputs: [["value", "Değer", "sayı"], ["what", "Ölçüt", [
      "glucose:Glukoz mg/dL → mmol/L", "glucose_r:Glukoz mmol/L → mg/dL",
      "chol:Kolesterol mg/dL → mmol/L", "chol_r:Kolesterol mmol/L → mg/dL",
      "tg:Trigliserid mg/dL → mmol/L", "tg_r:Trigliserid mmol/L → mg/dL",
      "cr:Kreatinin mg/dL → µmol/L", "cr_r:Kreatinin µmol/L → mg/dL",
    ]]],
    digits: 2, unit: "",
    compute: ({ value, what }) => {
      if (!(value > 0) || !what) return null;
      const factors = { glucose: 1 / 18.016, chol: 1 / 38.67, tg: 1 / 88.57, cr: 88.4 };
      const reverse = what.endsWith("_r");
      const factor = factors[reverse ? what.slice(0, -2) : what];
      if (!factor) return null;
      return reverse ? value / factor : value * factor;
    },
  },
];

function medcalcResult(definition, values) {
  const outcome = definition.compute(values);
  if (outcome === null || outcome === undefined) return null;
  if (typeof outcome === "object") return outcome;
  if (!Number.isFinite(outcome)) return null;
  return { value: Number(outcome.toFixed(definition.digits)) };
}

/* ── render (DOM below this line; the formulas above stay pure) ───── */

const MedCalc = {
  render() {
    const host = $("#med-calc-grid");
    if (!host || host.dataset.built) return;
    host.dataset.built = "1";
    host.innerHTML = MEDCALC.map((definition) => `
      <div class="panel med-card med-calc" data-calc="${definition.id}">
        <div class="panel-title"><span class="kicker">${esc(definition.title)}</span></div>
        <p class="med-calc-formula">${esc(definition.formula)}</p>
        <div class="med-form-row">${definition.inputs.map(([key, label, unit]) => Array.isArray(unit)
          ? `<label class="med-field"><span>${esc(label)}</span><select data-input="${key}"><option value="">—</option>${unit.map((option) => {
              const [value, text] = option.split(":");
              return `<option value="${esc(value)}">${esc(text)}</option>`;
            }).join("")}</select></label>`
          : `<label class="med-field"><span>${esc(label)} (${esc(unit)})</span><input data-input="${key}" type="number" step="any" inputmode="decimal"></label>`).join("")}</div>
        <div class="med-calc-out" aria-live="polite">—</div>
      </div>`).join("");
    $$(".med-calc", host).forEach((card) => {
      const definition = MEDCALC.find((item) => item.id === card.dataset.calc);
      const update = () => {
        const values = {};
        $$("[data-input]", card).forEach((node) => {
          values[node.dataset.input] = node.tagName === "SELECT" ? node.value : Number(String(node.value).replace(",", "."));
        });
        const out = $(".med-calc-out", card);
        const result = medcalcResult(definition, values);
        if (!result) { out.textContent = "—"; out.classList.remove("warn"); return; }
        if (result.warn) { out.textContent = result.warn; out.classList.add("warn"); return; }
        out.classList.remove("warn");
        out.innerHTML = `<strong>${esc(String(result.value).replace(".", ","))}</strong>${definition.unit ? ` <span>${esc(definition.unit)}</span>` : ""}${definition.note ? `<small>${esc(definition.note)}</small>` : ""}`;
      };
      $$("[data-input]", card).forEach((node) => node.addEventListener("input", update));
    });
  },
};
