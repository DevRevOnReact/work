"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Copy, FileText } from "lucide-react";
import { AssistantCapture } from "./assistant-capture";

type Resume = {id: string; filename: string; variant: string; reason?: string};
type Style = "brief" | "professional" | "warm";
type Draft = {id: string; question: string; instruction: string; vacancy: string; language: string; resume_id: string | null;
  status: "queued" | "working" | "ready" | "failed"; created_at: string;
  result: {resume?: Resume; variants?: Partial<Record<Style, string>>; facts?: Record<string, string>; evidence_ids?: string[]; note?: string; error?: string}};
type Overview = {drafts: Draft[]; resumes: Resume[]; media_enabled: boolean; notify_enabled: boolean};
const statusLabel = {queued:"В очереди", working:"Готовим ответ", ready:"Готово", failed:"Требует внимания"};

export function ReplyAssistant({telegram = false}: {telegram?: boolean}) {
  const base = telegram ? "/api/telegram-assistant" : "/api/backend/assistant";
  const [data, setData] = useState<Overview | null>(null);
  const [question, setQuestion] = useState("");
  const [vacancy, setVacancy] = useState("");
  const [instruction, setInstruction] = useState("");
  const [language, setLanguage] = useState("en");
  const [resume, setResume] = useState("");
  const [notify, setNotify] = useState(false);
  const [active, setActive] = useState("");
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState("");
  const [revision, setRevision] = useState(0);
  const [edited, setEdited] = useState<{key:string; text:string} | null>(null);
  const retry = useRef({signature:"", id:""});
  const form = useRef<HTMLTextAreaElement>(null);
  const selected = data?.drafts.find(d => d.id === active) || data?.drafts[0];
  const pending = data?.drafts.some(d => d.status === "working" || d.status === "queued");
  const key = selected?.id || "";
  const variants = selected?.result.variants;
  const answer = edited?.key === key ? edited.text : variants?.professional || variants?.brief || variants?.warm || "";

  const request = useCallback(async (path: string, options: RequestInit = {}) => {
    const headers = new Headers(options.headers);
    if (telegram) {
      const init = window.Telegram?.WebApp?.initData;
      if (!init) throw new Error("Откройте помощника кнопкой в личном чате с ботом.");
      headers.set("X-Telegram-Init-Data", init);
    }
    const response = await fetch(base + path, {...options, headers, cache:"no-store"});
    if (!response.ok) {
      const info = await response.json().catch(() => ({}));
      throw new Error(response.status === 401 ? "Войдите снова или откройте мини-приложение из своего бота."
        : typeof info.detail === "string" ? info.detail : "Запрос не выполнен. Проверьте соединение и попробуйте ещё раз.");
    }
    return response.json();
  }, [base, telegram]);

  useEffect(() => {
    window.Telegram?.WebApp?.ready(); window.Telegram?.WebApp?.expand();
    try {
      const context = sessionStorage.getItem("jobpilot-vacancy");
      if (context) {
        setVacancy(context.slice(0, 3000)); sessionStorage.removeItem("jobpilot-vacancy");
        setQuestion("Подготовь первое сообщение рекрутеру по этой вакансии.");
      }
    } catch { /* The form also accepts context manually. */ }
    if (!telegram) {
      if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
      try {
        const value = sessionStorage.getItem("jobpilot-share");
        if (value) { setQuestion(value.slice(0, 4000)); sessionStorage.removeItem("jobpilot-share"); }
      } catch { /* Clipboard and typing remain available. */ }
    }
  }, [telegram]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const next = await request("", {signal:controller.signal});
        if (!controller.signal.aborted) setData(next);
      } catch (e) {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "История недоступна.");
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, pending ? 2200 : 12000);
      }
    }
    void refresh();
    return () => {controller.abort(); clearTimeout(timer);};
  }, [request, pending, revision]);

  async function act(fn: () => Promise<void>) {
    setBusy(true); setError(""); setFeedback("");
    try {await fn();}
    catch (e) {setError(e instanceof Error ? e.message : "Не удалось выполнить действие.");}
    finally {setBusy(false);}
  }

  async function submit() {
    const body = {question:question.trim(), instruction:instruction.trim(), vacancy:vacancy.trim(), language, resume_id:resume || null, notify};
    const signature = JSON.stringify(body);
    if (retry.current.signature !== signature) retry.current = {signature, id:crypto.randomUUID()};
    const result = await request("", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({...body, id:retry.current.id})});
    retry.current.signature = "";
    setActive(result.id); setEdited(null); setRevision(n => n+1);
    setFeedback("Запрос принят. Ответ появится в истории, даже если закрыть страницу.");
  }

  return <div className="reply-assistant">
    {!telegram && <p><a href="/android">Установить помощник для Android</a></p>}
    <form className="panel padded" onSubmit={e => {e.preventDefault(); void act(submit);}}>
      <label>Сообщение рекрутера или описание вакансии
        <textarea ref={form} rows={5} value={question} maxLength={4000} required
          placeholder="Вставьте вопрос или перешлите его личному боту" onChange={e => setQuestion(e.target.value)}/>
      </label>
      {data?.media_enabled && <AssistantCapture disabled={busy} onError={setError}
        recognize={async (kind, blob) => (await request("/media/" + kind, {method:"POST",body:blob})).text}
        onText={(value, target) => {
          const update = (old:string) => [old.trim(),value].filter(Boolean).join("\n").slice(0,target === "question" ? 4000 : 800);
          (target === "question" ? setQuestion : setInstruction)(update);
        }}/>}
      <label>Пожелание к ответу
        <textarea rows={2} value={instruction} maxLength={800} onChange={e => setInstruction(e.target.value)}
          placeholder="Например, предложи созвон завтра и спроси про стек"/>
      </label>
      <details open={vacancy ? true : undefined}><summary>Контекст вакансии</summary>
        <label>Название и описание<textarea rows={3} maxLength={3000} value={vacancy} onChange={e => setVacancy(e.target.value)}/></label>
      </details>
      <div className="form-grid two">
        <label>Резюме<select value={resume} onChange={e => setResume(e.target.value)}>
          <option value="">Подобрать по сообщению</option>
          {data?.resumes.map(r => <option key={r.id} value={r.id}>{r.filename}</option>)}</select></label>
        <label>Язык ответа<select value={language} onChange={e => setLanguage(e.target.value)}>
          <option value="en">English</option>
        </select></label>
      </div>
      {data?.notify_enabled && <label className="check-label"><input type="checkbox" checked={notify}
        onChange={e => setNotify(e.target.checked)}/>Прислать готовый ответ мне в Telegram</label>}
      <button className="button primary" disabled={busy || !data || !question.trim()}>Подготовить ответ</button>
    </form>
    {error && <p role="alert" className="notice error">{error}</p>}
    {feedback && <p role="status" className="notice">{feedback}</p>}
    {selected && <section className="panel padded">
      <h2>{selected.status === "ready" ? "Готовый ответ" : statusLabel[selected.status]}</h2>
      <p className="original-question">{selected.question}</p>
      {selected.result.error && <p role="alert">{selected.result.error}</p>}
      {variants && <>
        {selected.result.note && <p>{selected.result.note}</p>}
        <label>Текст для отправки<textarea rows={5} value={answer}
          onChange={e => setEdited({key,text:e.target.value})}/></label>
        <div className="button-row">
          <button className="button primary" disabled={busy || !answer.trim()} onClick={() => void act(async () => {
            if (!navigator.clipboard) throw new Error("Выделите текст ответа и скопируйте через меню телефона.");
            await navigator.clipboard.writeText(answer); setFeedback("Ответ скопирован.");
          })}><Copy size={15}/> Скопировать</button>
          {selected.result.resume && (telegram
            ? <button className="button" disabled={busy} onClick={() => void act(async () => {
                await request("/"+selected.id+"/document", {method:"POST"}); setFeedback("Резюме отправлено вам в личный чат с ботом.");
              })}><FileText size={16}/> Получить резюме в боте</button>
            : <a className="button" download={selected.result.resume.filename} href={base+"/"+selected.id+"/file"}>
              <FileText size={16}/> Скачать резюме</a>)}
        </div>
        <small>{selected.result.resume?.filename} · {selected.result.resume?.reason}</small>
        {Object.keys(selected.result.facts || {}).length > 0 && <details><summary>Факты, переданные модели</summary>
          <ul>{Object.entries(selected.result.facts!).map(([id,fact]) => <li key={id}>{fact}</li>)}</ul></details>}
      </>}
      <button className="text-button" onClick={() => {
        setQuestion(selected.question); setInstruction(selected.instruction); setVacancy(selected.vacancy || ""); setLanguage(selected.language);
        setResume(selected.resume_id || ""); form.current?.focus();
      }}>Уточнить запрос</button>
    </section>}
    <section className="panel padded"><h2>История за 7 дней</h2>
      {!data ? <p>Загружаем историю…</p> : !data.drafts.length && <p>Здесь появится первый подготовленный ответ.</p>}
      {data?.drafts.map(d => <div className="draft-row" key={d.id}>
        <button className="text-button" aria-pressed={selected?.id === d.id} onClick={() => {
          setActive(d.id); setConfirmDelete(""); setFeedback("");
        }}>{d.question}<small>{statusLabel[d.status]} · {new Date(d.created_at).toLocaleString("ru-RU")}</small></button>
        {confirmDelete === d.id ? <div className="button-row">
          <button className="text-button danger" disabled={busy} onClick={() => void act(async () => {
            await request("/"+d.id, {method:"DELETE"}); setConfirmDelete(""); setRevision(n => n+1);
          })}>Подтвердить удаление</button><button className="text-button" onClick={() => setConfirmDelete("")}>Отмена</button>
        </div> : <button className="text-button" onClick={() => setConfirmDelete(d.id)}>Удалить</button>}
      </div>)}
    </section>
  </div>;
}
