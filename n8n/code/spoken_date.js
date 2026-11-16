// Дата встречи из речи: ищется только во фразе про встречу в начале записи.
// «Встреча с Павлом четырнадцатого сентября», «14 сентября была встреча», «Вчера встречалась с Марией»,
// «Встреча в понедельник с Еленой». Сроки задач («до пятницы», «к двадцать пятому») не считаются.
function detectSpokenDate(text, msgUnix) {
  const ref = DateTime.fromSeconds(Number(msgUnix), { zone: 'Europe/Moscow' }).startOf('day');
  const MEET = /встреч|один на один|1[:\-]1|созвон|разговор|поговорил|обсудил|виделись/i;
  const sentences = String(text || '').split(/(?<=[.!?…])\s+/).slice(0, 3);
  const sent = sentences.find((s) => MEET.test(s));
  if (!sent) return '';
  const span = sent.split(/\s+/).slice(0, 25).join(' ').toLowerCase().replace(/ё/g, 'е');
  const NOT_MEETING = /(^|\s)(до|к|ко|по|с|со|после|через|на|от)\s+$/;   // «до пятницы», «к 25 сентября»
  const ok = (idx) => !NOT_MEETING.test(span.slice(Math.max(0, idx - 8), idx));

  // сегодня / вчера / позавчера
  let m = /(^|[^а-я])(позавчера|вчера|сегодня|вчерашн\w*|сегодняшн\w*)(?=[^а-я]|$)/.exec(span);
  if (m && ok(m.index + m[1].length)) {
    const w = m[2];
    const back = w.startsWith('позавчера') ? 2 : w.startsWith('вчера') ? 1 : 0;
    return ref.minus({ days: back }).toISODate();
  }

  // число + месяц: «14 сентября», «14-го сентября», «четырнадцатого сентября», «двадцать пятое сентября»
  const MONTHS = ['январ', 'феврал', 'март', 'апрел', 'ма[йя]', 'июн', 'июл', 'август', 'сентябр', 'октябр', 'ноябр', 'декабр'];
  const UNITS = { 'перв': 1, 'втор': 2, 'трет': 3, 'четверт': 4, 'пят': 5, 'шест': 6, 'седьм': 7, 'восьм': 8, 'девят': 9 };
  const TEENS = { 'десят': 10, 'одиннадцат': 11, 'двенадцат': 12, 'тринадцат': 13, 'четырнадцат': 14, 'пятнадцат': 15,
    'шестнадцат': 16, 'семнадцат': 17, 'восемнадцат': 18, 'девятнадцат': 19, 'двадцат': 20, 'тридцат': 30 };
  const ORD = '(?:(двадцать|тридцать)\\s+)?([а-я]+?)(?:ого|ое|ий|ой|ье|ьего|его)';
  const reMonth = new RegExp('(?:^|[^а-я0-9])(?:(\\d{1,2})(?:-?го|-?е)?|' + ORD + ')\\s+(' + MONTHS.join('|') + ')[а-я]*', 'g');
  let best = '';
  for (const mm of span.matchAll(reMonth)) {
    const start = mm.index + (mm[0].match(/^[^а-я0-9]/) ? 1 : 0);
    if (!ok(start)) continue;
    let day = 0;
    if (mm[1]) day = Number(mm[1]);
    else {
      const tens = mm[2] === 'двадцать' ? 20 : mm[2] === 'тридцать' ? 30 : 0;
      const stem = mm[3];
      const unit = Object.entries(UNITS).find(([k]) => stem === k || stem.startsWith(k) && stem.length <= k.length + 1);
      const teen = Object.entries(TEENS).find(([k]) => stem === k);
      if (tens && unit) day = tens + unit[1];
      else if (!tens && teen) day = teen[1];
      else if (!tens && unit) day = unit[1];
    }
    const month = MONTHS.findIndex((p) => new RegExp('^' + p).test(mm[4])) + 1;
    if (!day || !month) continue;
    let d = DateTime.fromObject({ year: ref.year, month, day }, { zone: 'Europe/Moscow' });
    if (!d.isValid) continue;
    if (d > ref) d = d.minus({ years: 1 });
    best = d.toISODate();
    break;
  }
  if (best) return best;

  // «14.09» цифрами
  m = /(?:^|[^\d])(\d{1,2})\.(\d{1,2})(?:\.(\d{2}|\d{4}))?(?!\d)/.exec(span);
  if (m && ok(m.index + 1)) {
    let d = DateTime.fromObject({ year: m[3] ? Number(m[3].length === 2 ? '20' + m[3] : m[3]) : ref.year, month: Number(m[2]), day: Number(m[1]) }, { zone: 'Europe/Moscow' });
    if (d.isValid) { if (!m[3] && d > ref) d = d.minus({ years: 1 }); if (d <= ref) return d.toISODate(); }
  }

  // день недели: «в понедельник», «в прошлый четверг» → ближайший такой день не позже сообщения
  const WD = { 'понедельник': 1, 'вторник': 2, 'среду': 3, 'четверг': 4, 'пятницу': 5, 'субботу': 6, 'воскресенье': 7 };
  m = /(^|\s)во?\s+(?:прошл\w+\s+)?(понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)(?=[^а-я]|$)/.exec(span);
  if (m) {
    const back = (ref.weekday - WD[m[2]] + 7) % 7;
    return ref.minus({ days: back }).toISODate();
  }
  return '';
}