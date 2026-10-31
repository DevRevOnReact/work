import { useState } from "react";
import { emptyFilters } from "@/lib/search-filters";
import type { Resume, SavedSearch, SearchInput } from "@/lib/types";
import { SearchFilters } from "./search-filters";

export function SearchEditor({selected, resumes, pending, onSave, onCancel}: {
  selected: SavedSearch | null; resumes: Resume[]; pending: boolean;
  onSave: (body: SearchInput) => Promise<void>; onCancel: () => void;
}) {
  const [name, setName] = useState(selected?.name || "");
  const [resume, setResume] = useState(selected?.resume_id || resumes[0]?.id || "");
  const [enabled, setEnabled] = useState(selected?.enabled ?? true);
  const [filters, setFilters] = useState({...emptyFilters, ...selected?.filters});
  return <form className="panel padded search-editor" onSubmit={e => {
    e.preventDefault(); void onSave({name, resume_id: resume, enabled, filters});
  }}><h2>{selected ? "Настройки поиска" : "Новый поиск"}</h2>
    <fieldset disabled={pending}>
      <div className="form-grid two">
        <label>Название поиска<input required maxLength={120} value={name} onChange={e => setName(e.target.value)} placeholder="Frontend · удалённо"/></label>
        <label>Резюме<select required value={resume} onChange={e => setResume(e.target.value)}>
          <option value="">Выберите резюме</option>{resumes.map(r => <option key={r.id} value={r.id}>{r.filename}</option>)}</select></label>
      </div>
      <SearchFilters value={filters} onChange={setFilters}/>
      <label className="check-label"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)}/>
        Обновлять каждые 3 часа · только вакансии за сегодня по Москве</label>
      <div className="button-row"><button className="button primary" disabled={!resume || !name.trim()}>{pending ? "Сохраняем…" : "Сохранить настройки"}</button>
        <button className="button" type="button" onClick={onCancel}>Отмена</button></div>
      <p className="hint">После изменения фильтров запустите поиск, чтобы получить новые результаты. Общие условия профиля учитываются при оценке.</p>
    </fieldset>
  </form>;
}
