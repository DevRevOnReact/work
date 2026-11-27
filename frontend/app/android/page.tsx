"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Device = {fingerprint:string; expires_at:string};
function code(hash:string) {return hash.slice(0,12).toUpperCase().match(/.{4}/g)?.join(" ");}
export default function AndroidDevices() {
  const [candidate,setCandidate] = useState("");
  const [rows,setRows] = useState<Device[]>([]);
  const [message,setMessage] = useState("");
  const [busy,setBusy] = useState(false);
  useEffect(() => {
    const hash = window.location.hash.slice(1);
    if (/^[a-f0-9]{64}$/.test(hash)) setCandidate(hash);
    api<Device[]>("mobile-devices").then(setRows).catch(() => setMessage("Не удалось загрузить устройства."));
  },[]);
  async function change(id:string, enable:boolean) {
    setBusy(true);
    try {
      await api(enable ? "mobile-devices" : "mobile-devices/"+id, enable ? "POST" : "DELETE", enable ? {fingerprint:id} : undefined);
      if (enable) {setCandidate(""); window.history.replaceState(null,"","/android");}
      setMessage(enable ? "Доступ выдан. Вернитесь в приложение и нажмите «Проверить подключение»." : "Доступ телефона отозван.");
      setRows(await api<Device[]>("mobile-devices"));
    } catch(e) {setMessage(e instanceof Error ? e.message : "Не удалось изменить доступ.");}
    finally {setBusy(false);}
  }
  return <main className="mini-assistant"><h1>JobPilot для Android</h1>
    {candidate && <section className="panel padded"><h2>Подключение телефона</h2>
      <p>Сверьте код с тем, который показывает APK на вашем телефоне. Подключённое устройство получит доступ к черновикам и выбранным для них резюме на 90 дней.</p>
      <h3>{code(candidate)}</h3><button className="button primary" disabled={busy} onClick={()=>void change(candidate,true)}>Код совпадает, подключить</button></section>}
    {message && <p className="notice" role="status">{message}</p>}
    <section className="panel padded"><h2>Установка</h2><p>Android 8 и новее. Установите APK, откройте его и выберите «Подключить через браузер».</p>
      <a className="button primary" href="/downloads/jobpilot-assistant.apk" download>Скачать APK</a></section>
    <section className="panel padded"><h2>Подключённые устройства</h2>{!rows.length && <p>Активных устройств пока нет.</p>}
      {rows.map(r=><div className="resume-row" key={r.fingerprint}><div>{code(r.fingerprint)}<small>До {new Date(r.expires_at).toLocaleDateString("ru-RU")}</small></div>
        <button className="button" disabled={busy} onClick={()=>void change(r.fingerprint,false)}>Отключить</button></div>)}</section>
    <a href="/?view=assistant">Открыть веб-помощника</a></main>;
}
