import type { Filters } from "./types";

export const emptyFilters: Filters = {
  title: "", keywords: "", exclude: "", skills: "", company: "", location: "", salary_text: "",
  employment_type: "", seniority: "", work_mode: "", salary: "", provider: "", eligibility: "",
  min_score: 0, posted_days: 0, skills_mode: "all", keywords_mode: "all", sort: "score",
};
export const filterLabels: Partial<Record<keyof Filters, string>> = {
  title: "Должность", keywords: "Ключевые слова", exclude: "Исключения", skills: "Навыки",
  company: "Компания", location: "Регион", salary_text: "Текст зарплаты", employment_type: "Занятость",
  seniority: "Уровень", work_mode: "Формат", salary: "Зарплата", provider: "Платформа",
  eligibility: "Допуск", min_score: "Оценка от", posted_days: "Дней с публикации",
};
const values: Record<string, string> = {
  remote: "Удалённо", hybrid: "Гибрид", onsite: "В офисе", yes: "Указана", no: "Не указана",
  eligible: "Подходит", unknown: "Уточнить", rejected: "Не подходит",
};
export function activeFilters(filters: Filters) {
  return (Object.keys(filterLabels) as (keyof Filters)[])
    .filter(key => Boolean(typeof filters[key] === "string" ? String(filters[key]).trim() : filters[key]))
    .map(key => ({key, label: `${filterLabels[key]}: ${values[String(filters[key])] || filters[key]}`}));
}
export const busy = (status: string) => status === "running" || status === "queued";
export const statusLabel: Record<string, string> = {
  idle: "Готов к запуску", queued: "В очереди", running: "Выполняется", complete: "Обновлён", failed: "Ошибка обновления",
};
