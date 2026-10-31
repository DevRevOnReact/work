import { api } from "@/lib/api";
import type { Company } from "@/lib/types";
import type { WorkspaceState } from "@/hooks/use-workspace";

export function CompanyContacts({company, state: s}: {company: Company; state: WorkspaceState}) {
  return <details><summary>Контакты для рекомендации · {company.contacts.length}</summary>
    {company.contacts.map(lead => <div className="resume-row" key={lead.id}><div><strong>{lead.name}</strong>
      <small>{lead.location} {lead.notes}</small></div><div className="button-row"><a href={lead.profile_url} target="_blank" rel="noreferrer">Профиль ↗</a>
        <button className="text-button danger" disabled={s.pending} onClick={() => void s.action(() => api(`referral-leads/${lead.id}`, "DELETE"), "Контакт удалён")}>Удалить</button></div></div>)}
    <form onSubmit={e => {e.preventDefault(); const form = e.currentTarget; const body = Object.fromEntries(new FormData(form));
      void s.action(async () => {await api("referral-leads", "POST", {...body, company: company.name}); form.reset();}, "Контакт сохранён");
    }}><fieldset disabled={s.pending}><div className="form-grid two">
      <label>Имя<input name="name" required maxLength={200}/></label>
      <label>Профиль LinkedIn<input name="profile_url" type="url" required maxLength={1000} placeholder="https://www.linkedin.com/in/…"/></label>
      <label>Местоположение<input name="location" maxLength={200}/></label>
      <label>Заметка<input name="notes" maxLength={2000}/></label>
    </div><button className="button">Сохранить контакт</button></fieldset></form>
  </details>;
}
