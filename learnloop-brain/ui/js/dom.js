// Safe DOM building: text is ALWAYS set with textContent, never parsed as HTML.
// Study material and model output are untrusted – this prevents script injection (XSS).
export function h(tag, attrs = {}, ...children) {
  return build(document.createElement(tag), attrs, children);
}

// The same for SVG elements (icons, emblems, the knowledge map). Attributes only, never markup.
const SVG_NS = "http://www.w3.org/2000/svg";
export function s(tag, attrs = {}, ...children) {
  return build(document.createElementNS(SVG_NS, tag), attrs, children);
}

// "C#" or "F#" inside Hebrew text: an invisible left-to-right mark after the "#" keeps it from
// being displayed as "#C" in a right-to-left line. Display only; the data is unchanged.
export function bidiText(text) {
  return String(text).replace(/([A-Za-z])#(?![\u200e\w])/g, "$1#\u200e");
}

function build(el, attrs, children) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "class") el.setAttribute("class", v);
    else if (k === "style" && typeof v === "object") for (const [p, val] of Object.entries(v)) el.style.setProperty(p, val);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(bidiText(c)));
  }
  return el;
}
