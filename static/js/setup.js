// First-day setup hints shown before a tap rather than after it (Oct 23 review): the company
// details the pay app PDF prints. node --test tests/js

// The company details the PDF's From block prints under the name (payapp_pdf.py), each with its
// box on Settings, Company. Blank ones are a warning only: the pay app can still be sent.
export const COMPANY_DETAILS = [
  { key: "address", words: "address", focus: "#co-address" },
  { key: "phone", words: "phone", focus: "#co-phone" },
  { key: "license", words: "licence number", focus: "#co-license" },
];

export function companyGaps(company) {
  const co = company || {};
  return COMPANY_DETAILS.filter(d => !String(co[d.key] || "").trim());
}

function listWords(words) {
  return words.length < 3 ? words.join(" and ") : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

export function companyGapsText(gaps) {
  if (!gaps.length) return "";
  if (gaps.length === 1 && gaps[0].key === "address") return "Your company address is blank on this PDF.";
  return `Your company ${listWords(gaps.map(g => g.words))} ${gaps.length === 1 ? "is" : "are"} blank on this PDF.`;
}

// "Before you send": the warning, with Company details opening the first blank box. The
// no-address class stays while the address is one of them (tests/e2e/smoke.mjs looks for it).
export function companyGapsHtml(company) {
  const gaps = companyGaps(company);
  if (!gaps.length) return "";
  const cls = gaps.some(g => g.key === "address") ? "notice company-gaps no-address" : "notice company-gaps";
  return `<div class="${cls}">${companyGapsText(gaps)}
          <button class="btn small secondary" data-action="onb-go" data-view="company" data-focus="${gaps[0].focus}">Company details</button></div>`;
}

