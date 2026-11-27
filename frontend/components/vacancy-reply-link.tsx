"use client";
type Vacancy = { title: string; company: string; location: string; description?: string };
export function VacancyReplyLink({job, telegram = false}: {job: Vacancy; telegram?: boolean}) {
  return <a className="button" href={telegram ? "/telegram?view=assistant" : "/?view=assistant"}
    onClick={() => {
      const context = [job.title, job.company, job.location, job.description].filter(Boolean).join("\n");
      try { sessionStorage.setItem("jobpilot-vacancy", context.slice(0, 3000)); }
      catch { /* The assistant can still be opened and filled in manually. */ }
    }}>Подготовить сообщение</a>;
}
