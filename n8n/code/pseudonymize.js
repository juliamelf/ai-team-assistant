// Замена имён сотрудников на метки перед GigaChat и обратная подстановка в ответ.
// aliasMap — app.prompt_context(...).alias_map: [{ alias, id }], длинные формы первыми (сотрудники в составе на дату встречи).
// Регистр не важен, ё = е; заменяется только слово целиком. Местоимения и «Еле» в карту не входят.
function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
function pseudonymize(text, aliasMap) {
  let out = String(text || '');
  const found = new Set();
  for (const { alias, id } of aliasMap || []) {
    const body = escapeRe(alias).replace(/[её]/gi, '[её]');
    const re = new RegExp(`(^|[^\\p{L}\\p{N}\\[])${body}(?![\\p{L}\\p{N}])`, 'giu');
    out = out.replace(re, (m, pre) => { found.add(id); return `${pre}[${id}]`; });
  }
  return { text: out, ids: [...found] };
}
// Обратно: [E02] → «Павел Смирнов» (для сообщения руководителю); names = { E02: 'Павел Смирнов', ... }
function depseudonymize(text, names) {
  return String(text || '').replace(/\[([EM]\d{2})\]/g, (m, id) => names[id] || m);
}