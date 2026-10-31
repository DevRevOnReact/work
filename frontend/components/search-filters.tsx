import type { Filters } from "@/lib/types";
import { activeFilters, emptyFilters } from "@/lib/search-filters";

export function SearchFilters({value, onChange}: {value: Filters; onChange: (value: Filters) => void}) {
  function set(key: keyof Filters, next: string | number) { onChange({...value, [key]: next}); }
  function text(key: keyof Filters, label: string, placeholder = "", maxLength = 200) {
    return <label>{label}<input value={value[key]} maxLength={maxLength}
      onChange={e => set(key, e.target.value)} placeholder={placeholder}/></label>;
  }
  const active = activeFilters(value);
  return <>
    <fieldset><legend>Что и где ищем</legend><div className="form-grid three">
      {text("title", "Должности · любая из перечисленных", "Frontend, Fullstack")}
      {text("location", "Города и страны · любой вариант", "Europe, Armenia, Remote")}
      {text("company", "Компании · любой вариант", "GitLab, JetBrains")}
    </div><small>Варианты разделяются запятыми. Разные фильтры применяются вместе.</small></fieldset>
    <fieldset><legend>Условия работы</legend>
      <div className="segmented" role="group" aria-label="Формат работы">
        {[["", "Любой формат"], ["remote", "Удалённо"], ["hybrid", "Гибрид"], ["onsite", "В офисе"]].map(([key, label]) =>
          <button className="button" type="button" key={key} aria-pressed={value.work_mode === key}
            onClick={() => set("work_mode", key)}>{label}</button>)}
      </div>
      <div className="form-grid three">
        <label>Дата публикации<select value={[0,1,3,7,14,30].includes(value.posted_days) ? value.posted_days : "custom"}
          onChange={e => {if(e.target.value !== "custom") set("posted_days", Number(e.target.value));}}>
          <option value={0}>За всё время</option><option value={1}>За 24 часа</option><option value={3}>За 3 дня</option>
          <option value={7}>За неделю</option><option value={14}>За 2 недели</option><option value={30}>За месяц</option>
          <option value="custom" disabled>Свой период: {value.posted_days} дней</option>
        </select></label>
        <label>Зарплата<select value={value.salary} onChange={e => set("salary", e.target.value)}>
          <option value="">Любая</option><option value="yes">Только с зарплатой</option><option value="no">Без указанной зарплаты</option>
        </select></label>
        <label>Сортировка<select value={value.sort} onChange={e => set("sort", e.target.value)}>
          <option value="score">По соответствию резюме</option><option value="newest">Сначала новые</option><option value="company">По компании</option>
        </select></label>
      </div>
    </fieldset>
    <fieldset><legend>Навыки и соответствие</legend><div className="form-grid three">
      {text("skills", "Навыки через запятую", "React, TypeScript, Python", 300)}
      <label>Совпадение навыков<select value={value.skills_mode} onChange={e => set("skills_mode", e.target.value)}>
        <option value="any">Хотя бы один навык</option><option value="all">Все перечисленные навыки</option></select></label>
      <label>Минимальная оценка: {value.min_score}/100<input type="range" min={0} max={100} step={1}
        value={value.min_score} onChange={e => set("min_score", Number(e.target.value))}/></label>
    </div></fieldset>
    <details className="advanced-filters"><summary>Все параметры · ключевые слова, исключения, допуск и источники</summary>
      <div className="form-grid three">
        {text("keywords", "Ключевые слова через пробел", "cloud payments", 300)}
        <label>Совпадение ключевых слов<select value={value.keywords_mode} onChange={e => set("keywords_mode", e.target.value)}>
          <option value="all">Все слова</option><option value="any">Хотя бы одно слово</option></select></label>
        {text("exclude", "Исключить фразы через запятую", "intern, unpaid", 300)}
        {text("seniority", "Уровень в названии", "Senior", 100)}
        {text("employment_type", "Тип занятости в тексте", "Full-time", 100)}
        {text("salary_text", "Текст зарплаты", "USD, EUR — одна фраза")}
        <label>Платформа<select value={value.provider} onChange={e => set("provider", e.target.value)}>
          <option value="">Все источники</option><option value="greenhouse">Greenhouse</option><option value="ashby">Ashby</option><option value="lever">Lever</option>
          <option value="remotive">Remotive · удалённые</option><option value="arbeitnow">Arbeitnow · Европа</option></select></label>
        <label>Допуск по условиям<select value={value.eligibility} onChange={e => set("eligibility", e.target.value)}>
          <option value="">Любой, включая неизвестный</option><option value="eligible">Подходит</option><option value="unknown">Нужно уточнить</option><option value="rejected">Не подходит</option></select></label>
        <label>Свой период в днях<input type="number" min={0} max={3650} value={value.posted_days}
          onChange={e => set("posted_days", Number(e.target.value))}/></label>
        <label>Точная минимальная оценка<input type="number" min={0} max={100} value={value.min_score}
          onChange={e => set("min_score", Number(e.target.value))}/></label>
      </div>
      <p className="hint">При выборе периода вакансии без даты исключаются. Неизвестный формат не считается офисным.
        Зарплата и занятость проверяются по тексту работодателя; валюты не пересчитываются.</p>
    </details>
    <div className="filter-summary" aria-live="polite">
      <div className="button-row"><strong>Активные фильтры: {active.length}</strong>
        <button className="text-button" type="button" onClick={() => onChange({...emptyFilters})}>Сбросить фильтры</button></div>
      <div className="tags">{active.map(item => <button className="filter-chip" type="button" key={item.key}
        aria-label={`Убрать ${item.label}`} onClick={() => set(item.key, emptyFilters[item.key])}>{item.label} ×</button>)}</div>
    </div>
  </>;
}
