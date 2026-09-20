/* ════════════════════════════════════════════════════════════════════
   JARVIS · NOVA — toolbox: the palette answers arithmetic and units
   Typing "12*(3+2)" or "70 kg lb" into the command palette shows the
   answer as the first row, computed right here with no model and no
   network. The evaluator is a hand-rolled tokenizer and shunting-yard -
   never eval - and it refuses anything that does not look like pure
   arithmetic, so ordinary command queries are left alone.
   ════════════════════════════════════════════════════════════════════ */
"use strict";

/* Unit tables, one map per dimension, each value the factor to the
   dimension's base unit. Turkish spellings sit beside the symbols. */
const TOOLBOX_DIMENSIONS = [
  ["uzunluk", { m: 1, km: 1000, cm: 0.01, mm: 0.001, mi: 1609.344, mil: 1609.344, ft: 0.3048, in: 0.0254, "inç": 0.0254, yd: 0.9144 }],
  ["kütle", { kg: 1, g: 0.001, mg: 0.000001, lb: 0.45359237, oz: 0.028349523125, ton: 1000 }],
  ["hacim", { l: 1, lt: 1, ml: 0.001, dl: 0.1, gal: 3.785411784 }],
  ["basınç", { pa: 1, kpa: 1000, bar: 100000, atm: 101325, mmhg: 133.322387415, psi: 6894.757293168 }],
  ["hız", { "m/s": 1, "km/h": 1 / 3.6, "km/sa": 1 / 3.6, mph: 0.44704 }],
  ["veri", { b: 1, kb: 1024, mb: 1048576, gb: 1073741824, tb: 1099511627776 }],
  ["süre", { s: 1, sn: 1, dk: 60, min: 60, h: 3600, sa: 3600 }],
  ["enerji", { j: 1, kj: 1000, cal: 4.184, kcal: 4184 }],
];
const TOOLBOX_TEMPERATURE = new Set(["c", "f", "k", "°c", "°f"]);

/* Six significant digits, Turkish decimal comma, no exponent forms. */
function toolboxFormat(value) {
  if (!Number.isFinite(value)) return null;
  if (Math.abs(value) >= 1e12 || (value !== 0 && Math.abs(value) < 1e-9)) return null;
  return String(Number(value.toPrecision(6))).replace(".", ",");
}

function toolboxUnit(name) {
  const key = String(name || "").trim().toLocaleLowerCase("tr");
  for (const [dimension, table] of TOOLBOX_DIMENSIONS) {
    if (key in table) return { dimension, key, factor: table[key] };
  }
  if (TOOLBOX_TEMPERATURE.has(key)) return { dimension: "sıcaklık", key: key.replace("°", ""), factor: null };
  return null;
}

function toolboxConvert(amount, fromName, toName) {
  const from = toolboxUnit(fromName);
  const to = toolboxUnit(toName);
  if (!from || !to || from.dimension !== to.dimension || from.key === to.key) return null;
  if (from.dimension === "sıcaklık") {
    const celsius = from.key === "c" ? amount : from.key === "f" ? (amount - 32) / 1.8 : amount - 273.15;
    return to.key === "c" ? celsius : to.key === "f" ? celsius * 1.8 + 32 : celsius + 273.15;
  }
  return (amount * from.factor) / to.factor;
}

/* ── arithmetic: tokenizer + shunting-yard, degrees for trig ──────── */

const TOOLBOX_FUNCTIONS = {
  sqrt: (x) => Math.sqrt(x),
  abs: (x) => Math.abs(x),
  round: (x) => Math.round(x),
  ln: (x) => Math.log(x),
  log: (x) => Math.log10(x),
  sin: (x) => Math.sin((x * Math.PI) / 180),
  cos: (x) => Math.cos((x * Math.PI) / 180),
  tan: (x) => Math.tan((x * Math.PI) / 180),
};
const TOOLBOX_CONSTANTS = { pi: Math.PI, e: Math.E };
const TOOLBOX_OPERATORS = { "+": [1, "l"], "-": [1, "l"], "*": [2, "l"], "/": [2, "l"], "^": [3, "r"] };

function toolboxTokens(expression) {
  const tokens = [];
  let index = 0;
  const source = expression.replace(/(\d),(\d)/g, "$1.$2");
  while (index < source.length) {
    const char = source[index];
    if (/\s/.test(char)) { index += 1; continue; }
    if (/[\d.]/.test(char)) {
      let number = "";
      while (index < source.length && /[\d.]/.test(source[index])) { number += source[index]; index += 1; }
      if ((number.match(/\./g) || []).length > 1) return null;
      tokens.push({ type: "number", value: Number(number) });
      continue;
    }
    if (/[a-z]/i.test(char)) {
      let word = "";
      while (index < source.length && /[a-z]/i.test(source[index])) { word += source[index]; index += 1; }
      word = word.toLowerCase();
      if (word in TOOLBOX_FUNCTIONS) tokens.push({ type: "function", value: word });
      else if (word in TOOLBOX_CONSTANTS) tokens.push({ type: "number", value: TOOLBOX_CONSTANTS[word] });
      else return null;
      continue;
    }
    if (char in TOOLBOX_OPERATORS) {
      // A leading minus, or one right after an opening brace or operator,
      // negates rather than subtracts.
      const previous = tokens[tokens.length - 1];
      if (char === "-" && (!previous || previous.type === "operator" || previous.value === "(")) {
        tokens.push({ type: "number", value: 0 });
      }
      tokens.push({ type: "operator", value: char });
      index += 1;
      continue;
    }
    if (char === "(" || char === ")") { tokens.push({ type: "paren", value: char }); index += 1; continue; }
    return null;
  }
  return tokens;
}

function toolboxEvaluate(expression) {
  const tokens = toolboxTokens(expression);
  if (!tokens || !tokens.length) return null;
  const output = [];
  const stack = [];
  for (const token of tokens) {
    if (token.type === "number") output.push(token.value);
    else if (token.type === "function") stack.push(token);
    else if (token.type === "operator") {
      const [precedence, associativity] = TOOLBOX_OPERATORS[token.value];
      while (stack.length) {
        const top = stack[stack.length - 1];
        if (top.type === "function") { output.push(stack.pop()); continue; }
        if (top.type !== "operator") break;
        const [topPrecedence] = TOOLBOX_OPERATORS[top.value];
        if (topPrecedence > precedence || (topPrecedence === precedence && associativity === "l")) output.push(stack.pop());
        else break;
      }
      stack.push(token);
    } else if (token.value === "(") stack.push(token);
    else {
      let matched = false;
      while (stack.length) {
        const top = stack.pop();
        if (top.value === "(") { matched = true; break; }
        output.push(top);
      }
      if (!matched) return null;
      if (stack.length && stack[stack.length - 1].type === "function") output.push(stack.pop());
    }
  }
  while (stack.length) {
    const top = stack.pop();
    if (top.value === "(") return null;
    output.push(top);
  }
  const values = [];
  for (const item of output) {
    if (typeof item === "number") { values.push(item); continue; }
    if (item.type === "function") {
      if (!values.length) return null;
      values.push(TOOLBOX_FUNCTIONS[item.value](values.pop()));
      continue;
    }
    const right = values.pop();
    const left = values.pop();
    if (right === undefined || left === undefined) return null;
    if (item.value === "+") values.push(left + right);
    else if (item.value === "-") values.push(left - right);
    else if (item.value === "*") values.push(left * right);
    else if (item.value === "/") values.push(left / right);
    else values.push(Math.pow(left, right));
  }
  if (values.length !== 1 || !Number.isFinite(values[0])) return null;
  return values[0];
}

/* The palette's one entry point: an answer object, or null to stay out
   of the way. The guard errs toward null - a query that might be a
   command is never treated as arithmetic. */
function paletteMath(query) {
  const raw = String(query || "").trim();
  if (!raw || raw.length > 80 || !/\d/.test(raw)) return null;
  const conversion = raw.match(/^(-?\d+(?:[.,]\d+)?)\s*([a-zçğıöşü°/]+)\s+(?:to\s+|in\s+|->\s*|=\s*|kaç\s+)?([a-zçğıöşü°/]+)\s*$/i);
  if (conversion) {
    const amount = Number(conversion[1].replace(",", "."));
    const value = toolboxConvert(amount, conversion[2], conversion[3]);
    const formatted = value === null ? null : toolboxFormat(value);
    if (formatted !== null) {
      const from = String(conversion[2]).toLocaleLowerCase("tr");
      const to = String(conversion[3]).toLocaleLowerCase("tr");
      return { kind: "convert", value, display: `${conversion[1]} ${from} = ${formatted} ${to}` };
    }
    return null;
  }
  if (!/[+\-*/^]|sqrt|log|ln|sin|cos|tan|abs|round/.test(raw)) return null;
  const value = toolboxEvaluate(raw);
  const formatted = value === null ? null : toolboxFormat(value);
  if (formatted === null) return null;
  return { kind: "math", value, display: `${raw} = ${formatted}` };
}
