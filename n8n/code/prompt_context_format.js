// n8n Code node «Собрать контекст для промпта» (Run Once for All Items)
// Перед ним — узел Postgres «Справочники: контекст» (Execute Query):
//   SELECT app.prompt_context($1::date) AS ctx      Query Parameters: {{ $json.meeting_date }}
// Выход: context_text (блок для промпта), alias_map (псевдонимизация, x05), manager_id, ctx (всё остальное).
// 13.11.2026: после миграции 007 навыки — словарь тем (ctx.topics) вместо 8 колонок демо-матрицы (ctx.skills).
// 14.11.2026: блок «Открытые задачи» (ctx.open_tasks) — для task_updates. В v6 context_text проходит
//             псевдонимизацию вместе с транскриптом: в формулировках задач могут быть имена.

const MEETING_NODE = 'Дата встречи';   // узел с полем meeting_date (YYYY-MM-DD) — переименуйте под свой workflow

let ctx = $input.first().json.ctx;
if (typeof ctx === 'string') ctx = JSON.parse(ctx);
if (!ctx || !ctx.team) throw new Error('Пустой контекст: проверьте узел «Справочники: контекст» и дату встречи');
if (!Array.isArray(ctx.topics) || !ctx.topics.length) throw new Error('В контексте нет словаря тем: примените миграцию 007_schema_1_0');

const day = (d) => (d ? `${d.ru} ${d.weekday}${d.is_working ? '' : ' (нерабочий)'}` : 'нет данных');
const week = (w) => (w || []).map(day).join('; ');
const sprint = (s) => (s ? `${s.sprint_id}: ${s.start.ru}–${s.end.ru}` : 'нет данных');

const teamLines = ctx.team.map((e) =>
  `${e.employee_id} | ${e.full_name} | ${(e.aliases || []).filter((a) => a !== 'я').join(', ')} | ${e.position}` +
  (e.is_manager ? ' | автор записи: «я», «мне», «сам/сама» — это он(а)' : ''));
const dueText = (d) => {
  if (!d || !d.type) return 'срок не указан';
  if (d.type === 'date') return `до ${d.value}`;
  if (d.type === 'range') return `${d.value}–${d.value_to}`;
  if (d.type === 'from') return `с ${d.value}`;
  return d.said || (d.type === 'none' ? 'срок не назван' : d.type);
};
const taskLines = (ctx.open_tasks || []).map((t) =>
  `${t.task_id} | ${t.what} | ${(t.owner_ids || []).join(', ') || 'не назван'} | ${dueText(t.due)}` +
  ` | ${t.status === 'in_progress' ? 'в работе' : 'открыта'} | встреча ${t.meeting_date}`);
const topicLines = ctx.topics.map((t) => `${t.skill_id} | ${t.name} | ${(t.examples || []).join(', ')}`);

const context_text = [
  '### СОСТАВ КОМАНДЫ (исполнители — только из этого списка, указывай employee_id)',
  'id | ФИО | как могут назвать | должность',
  ...teamLines,
  'Имя, которого нет в списке (HR, аналитик, другая команда), не превращай в сотрудника: запиши в unresolved.unknown_names.',
  '',
  '### ТЕМЫ НАВЫКОВ (skill_id — только из этого списка)',
  'skill_id | тема | как называют в речи',
  ...topicLines,
  `Не темы навыков, записывай в observations: ${(ctx.observation_terms || []).join(', ') || '—'}.`,
  `Не считать навыком: ${(ctx.ignore_terms || []).map((t) => `${t.alias} — ${t.note}`).join('; ') || '—'}.`,
  `Направления развития — это не навыки: ${(ctx.development_paths || []).map((d) => `${d.name} (${(d.examples || []).join(', ')})`).join('; ') || '—'}. ` +
    'Если сотрудник хочет развиваться в таком направлении, запиши это в signals с type = goal, а не в skills.',
  '',
  '### ОТКРЫТЫЕ ЗАДАЧИ С ПРОШЛЫХ ВСТРЕЧ (task_id — только из этого списка)',
  'task_id | что | исполнители | срок | статус | поставлена',
  ...(taskLines.length ? taskLines : ['— открытых задач нет']),
  'Если в записи сказано, что задача из списка начата, выполнена, отменена или у неё новый срок, запиши это в task_updates ' +
    '(status: in_progress, done, cancelled, rescheduled; due — только при rescheduled). Новую задачу с тем же смыслом в tasks не добавляй. ' +
    'Если о прошлой задаче говорят, но в списке её нет или подходят несколько, поставь task_id: null.',
  '',
  '### ДАТЫ (сроки считай от даты встречи; бери даты только из этого блока)',
  `Дата встречи: ${day(ctx.meeting)}`,
  `Эта неделя: ${week(ctx.weeks?.[0])}`,
  `Следующая неделя: ${week(ctx.weeks?.[1])}`,
  `Неделя после следующей: ${week(ctx.weeks?.[2])}`,
  `Рабочие дни после встречи: ${(ctx.plus_working_days || []).map((d) => `+${d.n}: ${day(d)}`).join('; ')}`,
  `Конец месяца: ${day(ctx.month_end)}; конец следующего месяца: ${day(ctx.next_month_end)}`,
  `Текущий спринт: ${sprint(ctx.current_sprint)}; следующий спринт: ${sprint(ctx.next_sprint)}`,
  ctx.special_days?.length
    ? `Праздники и сокращённые дни в ближайшие 6 недель: ${ctx.special_days.map((d) => `${d.ru} ${d.weekday} — ${d.name}`).join('; ')}`
    : 'Праздников в ближайшие 6 недель нет.',
].join('\n');

const meeting = $(MEETING_NODE).first().json;
return [{ json: { ...meeting, manager_id: ctx.manager_id, context_text, alias_map: ctx.alias_map, ctx } }];
